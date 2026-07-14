from __future__ import annotations

import gc
from copy import deepcopy
from typing import Any, Callable, Protocol

import torch

SEED_MODES = (
    "Increment Per Segment",
    "Reuse Seed",
)
SEGMENT_SEAM_BLEND_FRAME_OPTIONS = (0, 3, 5)
DEFAULT_SEGMENT_SEAM_BLEND_FRAMES = 3


class SegmentSpillStore(Protocol):
    """Minimal store surface consumed by generic segment stitching."""

    def read_segment(self, record: dict[str, Any]) -> torch.Tensor: ...

    def cleanup(self) -> dict[str, Any]: ...


def segment_seed(seed: int, segment_index: int, seed_mode: str) -> int:
    base = int(seed) & 0xFFFFFFFFFFFFFFFF
    if seed_mode == "Reuse Seed":
        return base
    return (base + int(segment_index)) & 0xFFFFFFFFFFFFFFFF


def segment_seam_blend_frames(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_SEGMENT_SEAM_BLEND_FRAMES
    return parsed if parsed in SEGMENT_SEAM_BLEND_FRAME_OPTIONS else DEFAULT_SEGMENT_SEAM_BLEND_FRAMES


def sample_latent(
    *,
    model,
    positive,
    negative,
    latent: dict[str, Any],
    seed: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    denoise: float,
    disable_noise: bool = False,
    start_step: int | None = None,
    last_step: int | None = None,
    force_full_denoise: bool = False,
    sigmas=None,
) -> dict[str, Any]:
    try:
        import comfy.sample
        import comfy.utils
        import latent_preview
    except Exception as exc:
        raise RuntimeError("Segmented executor requires ComfyUI sampling modules to be importable.") from exc

    latent_image = latent["samples"]
    latent_image = comfy.sample.fix_empty_latent_channels(
        model,
        latent_image,
        latent.get("downscale_ratio_spacial", None),
        latent.get("downscale_ratio_temporal", None),
    )
    if disable_noise:
        noise = torch.zeros(latent_image.size(), dtype=latent_image.dtype, layout=latent_image.layout, device="cpu")
    else:
        batch_inds = latent.get("batch_index")
        noise = comfy.sample.prepare_noise(latent_image, int(seed), batch_inds)
    effective_steps = external_sigmas_step_count(sigmas) if sigmas is not None else int(steps)
    callback = latent_preview.prepare_callback(model, effective_steps)
    samples = comfy.sample.sample(
        model,
        noise,
        effective_steps,
        float(cfg),
        str(sampler_name),
        str(scheduler),
        positive,
        negative,
        latent_image,
        denoise=float(denoise),
        disable_noise=bool(disable_noise),
        start_step=start_step,
        last_step=last_step,
        force_full_denoise=bool(force_full_denoise),
        noise_mask=latent.get("noise_mask"),
        sigmas=sigmas,
        callback=callback,
        disable_pbar=not comfy.utils.PROGRESS_BAR_ENABLED,
        seed=int(seed),
    )
    output = dict(latent)
    output.pop("downscale_ratio_spacial", None)
    output.pop("downscale_ratio_temporal", None)
    output["samples"] = samples
    return output


def external_sigmas_step_count(sigmas) -> int:
    try:
        sigma_count = int(sigmas.shape[-1])
    except AttributeError:
        try:
            sigma_count = len(sigmas)
        except TypeError as exc:
            raise ValueError("Connected sigmas input must be a sequence or tensor with at least two values.") from exc
    if sigma_count < 2:
        raise ValueError("Connected sigmas input must contain at least two values to define a sampling schedule.")
    return sigma_count - 1


def decode_latent_images(vae, latent: dict[str, Any]) -> torch.Tensor:
    samples = latent["samples"]
    if getattr(samples, "is_nested", False):
        samples = samples.unbind()[0]
    images = vae.decode(samples)
    if len(images.shape) == 5:
        images = images.reshape(-1, images.shape[-3], images.shape[-2], images.shape[-1])
    return images


def stitch_segment_images(
    decoded_segments: list[dict[str, Any]],
    *,
    final_frame_count: int,
) -> torch.Tensor:
    frames: list[torch.Tensor] = []
    for entry in decoded_segments:
        images = entry["images"]
        segment = entry["segment"]
        trim_leading = max(0, int(segment.get("trim_leading_frames") or 0))
        trim_trailing = max(0, int(segment.get("trim_trailing_frames") or 0))
        end = images.shape[0] - trim_trailing if trim_trailing else images.shape[0]
        trimmed = images[trim_leading:end]
        visible = int(segment.get("visible_frame_count") or segment.get("frame_count") or trimmed.shape[0])
        trimmed = trimmed[:visible]
        if trimmed.shape[0] > 0:
            frames.append(trimmed)
    if not frames:
        return torch.zeros((1, 1, 1, 3), dtype=torch.float32)
    output = torch.cat(frames, dim=0)
    final_frame_count = max(1, int(final_frame_count or output.shape[0]))
    if output.shape[0] > final_frame_count:
        return output[:final_frame_count]
    if output.shape[0] < final_frame_count:
        pad = output[-1:].repeat((final_frame_count - output.shape[0], 1, 1, 1))
        output = torch.cat([output, pad], dim=0)
    return output


def managed_segment_spill_store(*, privacy_mode: bool):
    from .timeline.managed_install import director_privacy_pack
    from .timeline.managed_segment_spills import (
        SEGMENT_ARTIFACT_RESOURCE_ID,
        DirectorManagedSegmentSpillStore,
    )

    handle = director_privacy_pack().artifacts(SEGMENT_ARTIFACT_RESOURCE_ID)
    return DirectorManagedSegmentSpillStore(handle, private=privacy_mode)


def cleanup_spill_store_once(store: SegmentSpillStore, state: dict[str, Any]) -> dict[str, Any] | None:
    if state.get("cleaned"):
        return state.get("summary")
    state["cleaned"] = True
    summary = store.cleanup()
    state["summary"] = summary
    return summary


def trim_visible_segment_images(images: torch.Tensor, segment: dict[str, Any]) -> torch.Tensor:
    trim_leading = max(0, int(segment.get("trim_leading_frames") or 0))
    trim_trailing = max(0, int(segment.get("trim_trailing_frames") or 0))
    end = images.shape[0] - trim_trailing if trim_trailing else images.shape[0]
    trimmed = images[trim_leading:end]
    visible = int(segment.get("visible_frame_count") or segment.get("frame_count") or trimmed.shape[0])
    return trimmed[:visible]


def blend_segment_seam(
    current_visible_images: torch.Tensor,
    previous_tail_images: torch.Tensor | None,
    blend_frames: int,
) -> tuple[torch.Tensor, dict[str, Any]]:
    configured = _safe_non_negative_int(blend_frames)
    debug = {
        "configured_frame_count": configured,
        "actual_frame_count": 0,
        "status": "skipped",
        "reason": None,
    }
    if configured <= 0:
        debug["reason"] = "disabled"
        return current_visible_images, debug
    if not torch.is_tensor(current_visible_images) or current_visible_images.shape[0] <= 0:
        debug["reason"] = "empty_current_segment"
        return current_visible_images, debug
    if previous_tail_images is None or not torch.is_tensor(previous_tail_images) or previous_tail_images.shape[0] <= 0:
        debug["reason"] = "missing_previous_tail"
        return current_visible_images, debug
    actual = min(configured, int(current_visible_images.shape[0]), int(previous_tail_images.shape[0]))
    if actual <= 0:
        debug["reason"] = "no_available_frames"
        return current_visible_images, debug
    if tuple(previous_tail_images.shape[1:]) != tuple(current_visible_images.shape[1:]):
        debug["reason"] = "frame_shape_mismatch"
        return current_visible_images, debug
    previous_anchor = previous_tail_images[-1:].to(
        device=current_visible_images.device,
        dtype=current_visible_images.dtype,
    )
    while previous_anchor.ndim < current_visible_images.ndim:
        previous_anchor = previous_anchor.unsqueeze(0)
    alpha = torch.linspace(
        1.0 / (actual + 1),
        actual / (actual + 1),
        actual,
        device=current_visible_images.device,
        dtype=current_visible_images.dtype,
    )
    alpha = alpha.reshape((actual,) + (1,) * (current_visible_images.ndim - 1))
    blended = current_visible_images.clone()
    blended[:actual] = previous_anchor * (1.0 - alpha) + current_visible_images[:actual] * alpha
    debug["actual_frame_count"] = actual
    debug["status"] = "applied"
    return blended, debug


def stitch_spilled_segment_images(
    records: list[dict[str, Any]],
    store: SegmentSpillStore,
    *,
    final_frame_count: int,
) -> torch.Tensor:
    if not records:
        return torch.zeros((1, 1, 1, 3), dtype=torch.float32)
    first = store.read_segment(records[0])
    final_frame_count = max(1, int(final_frame_count or first.shape[0]))
    if first.ndim != 4:
        raise ValueError(f"Spilled segment '{records[0].get('segment_id')}' is not an IMAGE batch tensor.")
    output_shape = (final_frame_count, *first.shape[1:])
    output = torch.empty(output_shape, dtype=first.dtype)
    cursor = _copy_segment_frames(output, first, 0)
    last_frame = first[-1:].clone() if first.shape[0] > 0 else torch.zeros((1, *first.shape[1:]), dtype=first.dtype)
    del first
    for record in records[1:]:
        if cursor >= final_frame_count:
            break
        tensor = store.read_segment(record)
        if tuple(tensor.shape[1:]) != tuple(output.shape[1:]):
            raise ValueError(
                f"Spilled segment '{record.get('segment_id')}' has shape {tuple(tensor.shape)}, "
                f"expected frame shape {tuple(output.shape[1:])}."
            )
        cursor = _copy_segment_frames(output, tensor, cursor)
        if tensor.shape[0] > 0:
            last_frame = tensor[-1:].clone()
        del tensor
    if cursor < final_frame_count:
        output[cursor:final_frame_count] = last_frame.repeat((final_frame_count - cursor, 1, 1, 1))
    return output


def post_decode_memory_cleanup(stage: str = "post_decode") -> dict[str, Any]:
    event = {
        "stage": stage,
        "attempted": True,
        "success": True,
        "warnings": [],
    }
    try:
        gc.collect()
    except Exception as exc:
        event["success"] = False
        event["warnings"].append(f"gc.collect failed: {exc}")
    try:
        import comfy.model_management

        comfy.model_management.synchronize()
        comfy.model_management.soft_empty_cache(force=True)
        cleanup = getattr(comfy.model_management, "cleanup_models_gc", None)
        if callable(cleanup):
            cleanup()
    except Exception as exc:
        event["success"] = False
        event["warnings"].append(f"ComfyUI memory cleanup failed: {exc}")
    return event


def previous_tail(images: torch.Tensor, frame_count: int) -> torch.Tensor | None:
    if images is None or not torch.is_tensor(images) or images.shape[0] <= 0:
        return None
    return images[-max(1, int(frame_count or 1)) :].detach().clone()


def _safe_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _copy_segment_frames(output: torch.Tensor, tensor: torch.Tensor, cursor: int) -> int:
    if tensor.shape[0] <= 0 or cursor >= output.shape[0]:
        return cursor
    count = min(int(tensor.shape[0]), int(output.shape[0]) - int(cursor))
    output[cursor: cursor + count] = tensor[:count]
    return cursor + count


def build_segment_plan(
    plan: dict[str, Any],
    segment: dict[str, Any],
    *,
    model_key: str,
    previous_tail_images: torch.Tensor | None = None,
    prompt_relay_builder: Callable[[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    segment_plan = deepcopy(plan)
    frame_rate = float(plan.get("resolved_output", {}).get("frame_rate") or 24.0)
    segment_start = int(segment.get("start_frame") or 0)
    segment_end = int(segment.get("end_frame_exclusive") or segment_start)
    trim_leading = int(segment.get("trim_leading_frames") or 0)
    generation_frame_count = max(1, int(segment.get("generation_frame_count") or segment.get("frame_count") or 1))
    segment_plan["resolved_output"] = {
        **segment_plan.get("resolved_output", {}),
        "frame_count": generation_frame_count,
        "requested_frame_count": generation_frame_count,
        "duration_seconds": generation_frame_count / frame_rate,
        "generation_duration_seconds": generation_frame_count / frame_rate,
    }
    segment_plan["section_plan"] = _slice_sections(
        plan.get("section_plan", []),
        segment_start,
        segment_end,
        trim_leading,
    )
    section_ids = {entry.get("item_id") for entry in segment_plan["section_plan"]}
    segment_plan["prompt_plan"] = _slice_prompts(plan.get("prompt_plan", []), section_ids, bool(segment.get("continuity", {}).get("prompt_hint")))
    segment_plan["media_plan"] = _slice_media_plan(
        plan.get("media_plan", []),
        section_ids,
        segment_start,
        segment_end,
        trim_leading,
    )
    segment_plan["audio_plan"] = _slice_audio_plan(plan.get("audio_plan", []), segment_start, segment_end, frame_rate, trim_leading)
    model_specific = segment_plan.setdefault("model_specific", {}).setdefault(model_key, {})
    model_specific["active_generation_segment"] = deepcopy(segment)
    if previous_tail_images is not None:
        model_specific["segment_continuity"] = {
            "mode": "previous_tail",
            "previous_tail_images": previous_tail_images,
            "frame_count": int(previous_tail_images.shape[0]),
        }
    if prompt_relay_builder is not None:
        model_specific["prompt_relay"] = prompt_relay_builder(
            segment_plan,
            segment_plan["section_plan"],
            segment_plan["prompt_plan"],
            generation_frame_count,
        )
    return segment_plan


def _slice_sections(sections: list[dict[str, Any]], start: int, end: int, trim_leading: int) -> list[dict[str, Any]]:
    output = []
    for section in sections:
        section_start = int(section.get("start_frame") or 0)
        section_end = int(section.get("end_frame_exclusive") or section_start)
        overlap_start = max(start, section_start)
        overlap_end = min(end, section_end)
        if overlap_end <= overlap_start:
            continue
        item = deepcopy(section)
        local_start = overlap_start - start + trim_leading
        local_end = overlap_end - start + trim_leading
        item["start_frame"] = local_start
        item["end_frame_exclusive"] = local_end
        item["frame_count"] = max(0, local_end - local_start)
        item["start_time"] = section.get("start_time")
        item["end_time"] = section.get("end_time")
        output.append(item)
    return output


def _slice_prompts(prompts: list[dict[str, Any]], section_ids: set[Any], continuation_hint: bool) -> list[dict[str, Any]]:
    output = []
    prefix = "Continuing from the previous segment, same subject, setting, style, and motion. "
    for prompt in prompts:
        if prompt.get("item_id") not in section_ids:
            continue
        item = deepcopy(prompt)
        if continuation_hint and item.get("type") != "Gap":
            base = str(item.get("runtime_prompt") or item.get("raw_prompt") or item.get("effective_prompt") or "").strip()
            if base and not base.startswith(prefix):
                item["runtime_prompt"] = prefix + base
        output.append(item)
    return output


def _slice_media_plan(
    media_plan: list[dict[str, Any]],
    section_ids: set[Any],
    start: int,
    end: int,
    trim_leading: int,
) -> list[dict[str, Any]]:
    output = []
    for entry in media_plan:
        if entry.get("item_id") in section_ids:
            output.append(deepcopy(entry))
            continue
        if not entry.get("transient"):
            continue
        insert_frame = _optional_int(entry.get("insert_frame"))
        if insert_frame is None or insert_frame < start or insert_frame >= end:
            continue
        item = deepcopy(entry)
        item["insert_frame"] = insert_frame - start + trim_leading
        output.append(item)
    return output


def _slice_audio_plan(audio_plan: list[dict[str, Any]], start: int, end: int, frame_rate: float, trim_leading: int) -> list[dict[str, Any]]:
    output = []
    for entry in audio_plan:
        entry_start = int(entry.get("start_frame") or 0)
        entry_end = int(entry.get("end_frame_exclusive") or entry_start)
        overlap_start = max(start, entry_start)
        overlap_end = min(end, entry_end)
        if overlap_end <= overlap_start:
            continue
        item = deepcopy(entry)
        item["start_frame"] = overlap_start - start + trim_leading
        item["end_frame_exclusive"] = overlap_end - start + trim_leading
        original_start_time = float(entry.get("start_time") or 0.0)
        clipped_offset = max(0.0, (overlap_start - entry_start) / frame_rate)
        item["start_time"] = item["start_frame"] / frame_rate
        item["end_time"] = item["end_frame_exclusive"] / frame_rate
        item["source_in"] = float(entry.get("source_in") or 0.0) + clipped_offset
        if original_start_time and overlap_start > entry_start:
            item["source_in"] = float(entry.get("source_in") or 0.0) + ((overlap_start / frame_rate) - original_start_time)
        output.append(item)
    return output
