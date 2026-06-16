from __future__ import annotations

from copy import deepcopy
from typing import Any

import torch

from ..bernini import BERNINI_MODEL_MODE, BERNINI_SYSTEM_PROMPTS
from ..planner import _build_prompt_relay, _latent_chunk_count
from ...ltx.runtime.audio import mix_timeline_audio
from ...segmented_executor import (
    SegmentSpillStore,
    blend_segment_seam,
    build_segment_plan,
    decode_latent_images,
    post_decode_memory_cleanup,
    previous_tail,
    sample_latent,
    segment_seam_blend_frames,
    segment_seed,
    stitch_spilled_segment_images,
    trim_visible_segment_images,
)
from ...timeline_status import TimelineStatusReporter, ensure_timeline_status_reporter
from .runtime import build_wan_runtime_outputs


WAN_SINGLE_PHASE_MODEL_MODES = {"TI2V-5B"}
VRAM_UNLOAD_OFF = "Off"
VRAM_UNLOAD_BETWEEN_HIGH_LOW = "Between High Low"
VRAM_UNLOAD_BEFORE_DECODE = "Before Decode"
VRAM_UNLOAD_BETWEEN_HIGH_LOW_AND_DECODE = "Between High Low And Decode"


def build_wan_segmented_executor_outputs(
    *,
    high_noise_model=None,
    low_noise_model=None,
    clip=None,
    vae=None,
    wan_timeline_plan: dict[str, Any],
    seed: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    denoise: float,
    seed_mode: str,
    phase_split_step: int | float | str | None = None,
    negative=None,
    batch_size: int = 1,
    status_reporter: TimelineStatusReporter | None = None,
):
    plan = deepcopy(wan_timeline_plan)
    segmented = plan.get("model_specific", {}).get("wan", {}).get("segmented_generation", {})
    segments = list(segmented.get("segments") or [])
    if not segments:
        frame_count = int(plan.get("resolved_output", {}).get("frame_count") or 1)
        segments = [{
            "id": "gen_001",
            "index": 0,
            "start_frame": 0,
            "end_frame_exclusive": frame_count,
            "frame_count": frame_count,
            "visible_frame_count": frame_count,
            "generation_frame_count": frame_count,
            "trim_leading_frames": 0,
            "trim_trailing_frames": 0,
            "continuity": {"mode": "initial"},
        }]

    status_reporter = ensure_timeline_status_reporter(
        status_reporter,
        model="wan",
        total=(len(segments) * 12) + 4,
    )
    status_reporter.report("timeline.prepare", f"WAN Executor: preparing {len(segments)} segment(s)")
    privacy_mode = bool(plan.get("project", {}).get("privacy", {}).get("mode"))
    store = SegmentSpillStore(privacy_mode=privacy_mode)
    spill_records = []
    previous_images = None
    previous_latent = None
    segment_debug = []
    cleanup_events = []
    config = plan.get("model_specific", {}).get("wan", {}).get("config", {})
    configured_seam_blend_frames = segment_seam_blend_frames(
        config.get("segment_seam_blend_frames", 3) if isinstance(config, dict) else 3
    )
    try:
        for index, segment in enumerate(segments):
            segment_index = index + 1
            segment_count = len(segments)
            tail = None
            if previous_images is not None:
                tail = previous_tail(previous_images, segment.get("continuity", {}).get("continuity_frame_count") or 1)
            status_reporter.report(
                "timeline.conditioning",
                f"WAN Executor: segment {segment_index}/{segment_count} - conditioning",
                segment_index=segment_index,
                segment_count=segment_count,
                frame_count=segment.get("generation_frame_count"),
            )
            segment_plan = build_segment_plan(
                plan,
                segment,
                model_key="wan",
                previous_tail_images=tail,
                prompt_relay_builder=_build_segment_prompt_relay,
            )
            _apply_wan_segment_continuity(segment_plan, tail)
            (
                runtime_high_model,
                runtime_low_model,
                positive,
                runtime_negative,
                video_latent,
                runtime_debug,
            ) = build_wan_runtime_outputs(
                high_noise_model=high_noise_model,
                low_noise_model=low_noise_model,
                clip=clip,
                vae=vae,
                wan_timeline_plan=segment_plan,
                negative=negative,
                batch_size=batch_size,
                status_reporter=status_reporter,
                complete_status=False,
                split_conditioning=True,
                fmlf_prev_latent=previous_latent,
                fmlf_motion_frames=tail,
                fmlf_video_frame_offset=int(segment.get("start_frame") or 0),
            )
            segment_seed_value = segment_seed(seed, index, seed_mode)
            sampled, sampling_debug = sample_wan_segment_latent(
                high_noise_model=runtime_high_model,
                low_noise_model=runtime_low_model,
                positive=positive,
                negative=runtime_negative,
                latent=video_latent,
                model_mode=str(segment_plan.get("model_specific", {}).get("wan", {}).get("config", {}).get("model_mode") or "I2V-A14B"),
                seed=segment_seed_value,
                steps=steps,
                cfg=cfg,
                sampler_name=sampler_name,
                scheduler=scheduler,
                denoise=denoise,
                phase_split_step=phase_split_step,
                vram_unload_policy=str(segment_plan.get("model_specific", {}).get("wan", {}).get("config", {}).get("vram_unload_policy") or VRAM_UNLOAD_OFF),
                status_reporter=status_reporter,
                segment_index=segment_index,
                segment_count=segment_count,
            )
            status_reporter.report(
                "timeline.decode",
                f"WAN Executor: segment {segment_index}/{segment_count} - decoding",
                segment_index=segment_index,
                segment_count=segment_count,
                frame_count=segment.get("generation_frame_count"),
            )
            images = decode_latent_images(vae, sampled)
            visible_images = trim_visible_segment_images(images, segment)
            visible_images, seam_blend_debug = blend_segment_seam(
                visible_images,
                previous_images,
                configured_seam_blend_frames if index > 0 else 0,
            )
            next_tail_count = (
                int(segments[index + 1].get("continuity", {}).get("continuity_frame_count") or 1)
                if index + 1 < len(segments)
                else 1
            )
            next_previous_frame_count = max(next_tail_count, configured_seam_blend_frames)
            previous_images = previous_tail(visible_images.detach().cpu(), next_previous_frame_count)
            previous_latent = (
                _previous_latent_tail(sampled, next_previous_frame_count)
                if index + 1 < len(segments)
                else None
            )
            status_reporter.report(
                "timeline.spill",
                f"WAN Executor: segment {segment_index}/{segment_count} - saving {'encrypted ' if privacy_mode else ''}segment frames",
                segment_index=segment_index,
                segment_count=segment_count,
                frame_count=int(visible_images.shape[0]),
                encrypted_spill=privacy_mode,
            )
            record = store.write_segment(segment, visible_images)
            spill_records.append(record)
            status_reporter.report(
                "timeline.cleanup",
                f"WAN Executor: segment {segment_index}/{segment_count} - releasing memory",
                segment_index=segment_index,
                segment_count=segment_count,
            )
            cleanup_events.append(post_decode_memory_cleanup(f"post_decode_{segment.get('id') or index + 1}"))
            wan_debug = runtime_debug.get("wan", runtime_debug) if isinstance(runtime_debug, dict) else {}
            visual_debug = wan_debug.get("visual_conditioning", {}) if isinstance(wan_debug, dict) else {}
            segment_debug.append({
                "id": segment.get("id"),
                "seed": segment_seed_value,
                "generation_frame_count": segment.get("generation_frame_count"),
                "visible_frame_count": segment.get("visible_frame_count"),
                "trim_leading_frames": segment.get("trim_leading_frames"),
                "decoded_frame_count": int(images.shape[0]),
                "spilled_frame_count": int(visible_images.shape[0]),
                "continuity": segment.get("continuity"),
                "actual_tail_frame_count": int(tail.shape[0]) if tail is not None else 0,
                "actual_tail_shape": [int(dim) for dim in tail.shape] if tail is not None else [],
                "seam_blend": seam_blend_debug,
                "sampling": sampling_debug,
                "bernini": wan_debug.get("bernini"),
                "fmlf_advanced_i2v": wan_debug.get("fmlf_advanced_i2v"),
                "visual_conditioning": {
                    "requested_keyframes": visual_debug.get("requested_keyframes") or [],
                    "applied_keyframes": visual_debug.get("applied_keyframes") or [],
                    "media_decisions": visual_debug.get("media_decisions") or [],
                },
            })
            del segment_plan, runtime_high_model, runtime_low_model, positive, runtime_negative, video_latent, sampled, images, visible_images

        status_reporter.report("timeline.stitch", "Timeline Executor: stitching segments")
        final_images = stitch_spilled_segment_images(
            spill_records,
            store,
            final_frame_count=int(plan.get("resolved_output", {}).get("frame_count") or 1),
        )
        cleanup_summary = store.cleanup()
        status_reporter.report("timeline.audio", "Timeline Executor: mixing audio")
        combined_audio, audio_diagnostics = mix_timeline_audio(_wan_plan_as_audio_mix_plan(plan))
        status_reporter.done("Timeline Executor: done")
        debug = {
            "enabled": bool(segmented.get("enabled")),
            "model": "wan",
            "segment_count": len(segments),
            "segment_storage": cleanup_summary,
            "post_decode_cleanup": cleanup_events,
            "status_events": status_reporter.snapshot(),
            "segments": segment_debug,
            "stitching": {
                "output_frame_count": int(final_images.shape[0]),
                "target_frame_count": int(plan.get("resolved_output", {}).get("frame_count") or 1),
                "audio_policy": "global_full_mix",
            },
            "diagnostics": [
                *(segmented.get("diagnostics") or []),
                *audio_diagnostics,
            ],
        }
        return final_images, combined_audio, float(plan.get("resolved_output", {}).get("frame_rate") or 24.0), debug
    except Exception:
        store.cleanup()
        raise


def sample_wan_segment_latent(
    *,
    high_noise_model=None,
    low_noise_model=None,
    positive,
    negative,
    latent: dict[str, Any],
    model_mode: str,
    seed: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    denoise: float,
    phase_split_step: int | float | str | None = None,
    vram_unload_policy: str = VRAM_UNLOAD_OFF,
    status_reporter: TimelineStatusReporter | None = None,
    segment_index: int | None = None,
    segment_count: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    positive_high, positive_low, positive_default, conditioning_split = _split_phase_conditioning(positive)
    if _uses_single_phase_sampling(model_mode):
        model = high_noise_model or low_noise_model
        if model is None:
            raise ValueError("WAN segmented executor needs one connected model for TI2V-5B single-phase sampling.")
        if status_reporter is not None:
            status_reporter.report(
                "timeline.sample",
                f"WAN Executor: segment {segment_index}/{segment_count} - sampling",
                segment_index=segment_index,
                segment_count=segment_count,
            )
        sampled = sample_latent(
            model=model,
            positive=positive_default,
            negative=negative,
            latent=latent,
            seed=seed,
            steps=steps,
            cfg=cfg,
            sampler_name=sampler_name,
            scheduler=scheduler,
            denoise=denoise,
            force_full_denoise=True,
        )
        model_role = "high_noise_model" if high_noise_model is not None else "low_noise_model"
        unload_events = _maybe_unload_before_decode(model, None, vram_unload_policy, role=model_role)
        _report_unload_events(status_reporter, unload_events, segment_index, segment_count)
        return sampled, {
            "sampling_policy": "single_phase",
            "model_mode": model_mode,
            "seed": int(seed),
            "steps": int(steps),
            "phase_split_step": None,
            "split_step": None,
            "phases": [{"role": "single", "model": model_role}],
            "vram_unload_policy": vram_unload_policy,
            "unload_events": unload_events,
        }

    if high_noise_model is None or low_noise_model is None:
        raise ValueError(
            "WAN segmented executor two-phase sampling requires both high_noise_model and low_noise_model "
            f"for model mode {model_mode}."
        )
    steps = max(1, int(steps))
    if steps < 2:
        raise ValueError("WAN segmented executor two-phase sampling requires at least 2 total steps.")
    split_step = _normalize_split_step(steps, phase_split_step)
    if status_reporter is not None:
        status_reporter.report(
            "wan.sample.high_noise",
            f"WAN Executor: segment {segment_index}/{segment_count} - high-noise sampling",
            segment_index=segment_index,
            segment_count=segment_count,
        )
    sampled_high = sample_latent(
        model=high_noise_model,
        positive=positive_high,
        negative=negative,
        latent=latent,
        seed=seed,
        steps=steps,
        cfg=cfg,
        sampler_name=sampler_name,
        scheduler=scheduler,
        denoise=denoise,
        start_step=0,
        last_step=split_step,
        force_full_denoise=False,
    )
    unload_events = _maybe_unload_between_phases(high_noise_model, low_noise_model, vram_unload_policy)
    _report_unload_events(status_reporter, unload_events, segment_index, segment_count)
    if status_reporter is not None:
        status_reporter.report(
            "wan.sample.low_noise",
            f"WAN Executor: segment {segment_index}/{segment_count} - low-noise sampling",
            segment_index=segment_index,
            segment_count=segment_count,
        )
    sampled_low = sample_latent(
        model=low_noise_model,
        positive=positive_low,
        negative=negative,
        latent=sampled_high,
        seed=seed,
        steps=steps,
        cfg=cfg,
        sampler_name=sampler_name,
        scheduler=scheduler,
        denoise=denoise,
        disable_noise=True,
        start_step=split_step,
        last_step=steps,
        force_full_denoise=True,
    )
    before_decode_events = _maybe_unload_before_decode(low_noise_model, high_noise_model, vram_unload_policy, role="low_noise_model")
    unload_events.extend(before_decode_events)
    _report_unload_events(status_reporter, before_decode_events, segment_index, segment_count)
    return sampled_low, {
        "sampling_policy": "two_phase",
        "model_mode": model_mode,
        "seed": int(seed),
        "steps": int(steps),
        "phase_split_step": int(split_step),
        "split_step": int(split_step),
        "phases": [
            {
                "role": "high_noise",
                "model": "high_noise_model",
                "start_step": 0,
                "last_step": int(split_step),
                "force_full_denoise": False,
                "disable_noise": False,
                "conditioning": "positive_high" if conditioning_split else "positive",
            },
            {
                "role": "low_noise",
                "model": "low_noise_model",
                "start_step": int(split_step),
                "last_step": int(steps),
                "force_full_denoise": True,
                "disable_noise": True,
                "conditioning": "positive_low" if conditioning_split else "positive",
            },
        ],
        "vram_unload_policy": vram_unload_policy,
        "unload_events": unload_events,
    }


def _split_phase_conditioning(positive):
    if isinstance(positive, dict) and positive.get("_helto_wan_conditioning_split"):
        high = positive.get("high")
        low = positive.get("low")
        default = positive.get("default")
        return high if high is not None else default, low if low is not None else default, default if default is not None else high, True
    return positive, positive, positive, False


def _previous_latent_tail(latent: dict[str, Any], frame_count: int) -> dict[str, Any] | None:
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if not torch.is_tensor(samples) or int(samples.shape[2]) <= 0:
        return None
    latent_frames = ((max(1, int(frame_count)) - 1) // 4) + 1
    tail = samples[:, :, -min(int(samples.shape[2]), latent_frames) :].detach().cpu().clone()
    return {"samples": tail}


def _report_unload_events(
    status_reporter: TimelineStatusReporter | None,
    unload_events: list[dict[str, Any]],
    segment_index: int | None,
    segment_count: int | None,
) -> None:
    if status_reporter is None:
        return
    for event in unload_events:
        if not event.get("attempted"):
            continue
        stage = str(event.get("stage") or "")
        if stage == "between_high_low":
            label = f"WAN Executor: segment {segment_index}/{segment_count} - unloading high-noise model"
        elif stage == "before_decode":
            label = f"WAN Executor: segment {segment_index}/{segment_count} - unloading model before decode"
        else:
            label = f"WAN Executor: segment {segment_index}/{segment_count} - unloading VRAM"
        status_reporter.report(
            "wan.vram.unload",
            label,
            segment_index=segment_index,
            segment_count=segment_count,
        )


def _uses_single_phase_sampling(model_mode: str) -> bool:
    return str(model_mode or "") in WAN_SINGLE_PHASE_MODEL_MODES


def _normalize_split_step(steps: int, phase_split_step: int | float | str | None = None) -> int:
    steps = max(2, int(steps))
    fallback = max(1, steps // 2)
    try:
        if phase_split_step is None:
            raise ValueError
        if isinstance(phase_split_step, float) and not phase_split_step.is_integer():
            raise ValueError
        value = int(str(phase_split_step).strip())
    except (TypeError, ValueError):
        value = fallback
    split = int(value)
    return min(max(1, split), steps - 1)


def _maybe_unload_between_phases(high_noise_model, low_noise_model, policy: str) -> list[dict[str, Any]]:
    if policy not in {VRAM_UNLOAD_BETWEEN_HIGH_LOW, VRAM_UNLOAD_BETWEEN_HIGH_LOW_AND_DECODE}:
        return []
    if high_noise_model is None or high_noise_model is low_noise_model:
        return [{
            "stage": "between_high_low",
            "role": "high_noise_model",
            "attempted": False,
            "success": False,
            "reason": "model_missing_or_reused",
        }]
    return [_unload_model("between_high_low", "high_noise_model", high_noise_model)]


def _maybe_unload_before_decode(model, other_model, policy: str, role: str = "low_noise_model") -> list[dict[str, Any]]:
    if policy not in {VRAM_UNLOAD_BEFORE_DECODE, VRAM_UNLOAD_BETWEEN_HIGH_LOW_AND_DECODE}:
        return []
    if model is None or model is other_model:
        return [{
            "stage": "before_decode",
            "role": role,
            "attempted": False,
            "success": False,
            "reason": "model_missing_or_reused",
        }]
    return [_unload_model("before_decode", role, model)]


def _unload_model(stage: str, role: str, model) -> dict[str, Any]:
    event = {
        "stage": stage,
        "role": role,
        "attempted": True,
        "success": False,
    }
    try:
        import comfy.model_management

        comfy.model_management.synchronize()
        comfy.model_management.unload_model_and_clones(
            model,
            unload_additional_models=True,
            all_devices=False,
        )
        comfy.model_management.soft_empty_cache(force=True)
        event["success"] = True
    except Exception as exc:
        event["error"] = str(exc)
    return event


def _build_segment_prompt_relay(
    segment_plan: dict[str, Any],
    section_plan: list[dict[str, Any]],
    prompt_plan: list[dict[str, Any]],
    frame_count: int,
) -> dict[str, Any]:
    config = segment_plan.get("model_specific", {}).get("wan", {}).get("config", {})
    latent_chunk_count = _latent_chunk_count(frame_count)
    return _build_prompt_relay(
        segment_plan,
        config,
        section_plan,
        prompt_plan,
        frame_count,
        latent_chunk_count,
        segment_plan.get("model_specific", {}).get("wan", {}).get("bernini", {}).get("character_references"),
    )


def _apply_wan_segment_continuity(segment_plan: dict[str, Any], tail) -> None:
    wan = segment_plan.get("model_specific", {}).get("wan", {})
    _reset_segment_visual_conditioning(segment_plan, has_continuity=tail is not None)
    if tail is None:
        return
    wan["segment_continuity"] = {
        "mode": "previous_tail",
        "previous_tail_images": tail,
        "frame_count": int(tail.shape[0]),
    }
    visual = wan.get("visual_conditioning")
    if isinstance(visual, dict):
        visual["transient_start_image"] = tail
        visual["continuation_source"] = "previous_tail"
        visual["requested_keyframes"] = []
        visual["applied_keyframes"] = []
        visual["unsupported_keyframes"] = []
    bernini = wan.get("bernini")
    if isinstance(bernini, dict) and bernini.get("enabled"):
        bernini["segment_continuity"] = wan["segment_continuity"]
        if bernini.get("task_type") == "r2v":
            bernini["task_type"] = "rv2v"
            bernini["system_prompt"] = BERNINI_SYSTEM_PROMPTS["rv2v"]
            bernini["selection_reason"] = "Continuation segment uses the previous decoded tail as Bernini source_video with reference images."
        elif bernini.get("task_type") == "t2v":
            bernini["task_type"] = "v2v"
            bernini["system_prompt"] = BERNINI_SYSTEM_PROMPTS["v2v"]
            bernini["selection_reason"] = "Continuation segment uses the previous decoded tail as Bernini source_video."


def _reset_segment_visual_conditioning(segment_plan: dict[str, Any], *, has_continuity: bool) -> None:
    wan = segment_plan.get("model_specific", {}).get("wan", {})
    previous = wan.get("visual_conditioning") if isinstance(wan.get("visual_conditioning"), dict) else {}
    if has_continuity:
        requested = []
    else:
        requested = _segment_visual_keyframes(segment_plan)
    wan["visual_conditioning"] = {
        "mode": previous.get("mode") or wan.get("config", {}).get("visual_conditioning_mode") or "Timed Keyframes",
        "requested_keyframes": requested,
        "applied_keyframes": [],
        "unsupported_keyframes": [],
        "backend_capabilities": previous.get("backend_capabilities") or {
            "supports_start_image": None,
            "supports_end_image": None,
            "supports_timed_keyframes": None,
            "max_visual_keyframes": None,
            "supports_prompt_relay": None,
            "supports_video_sections": None,
            "supports_audio_conditioning": None,
        },
        "selection_policy": previous.get("selection_policy") or "Segment-local visual keyframes only.",
        "segment_local": True,
    }


def _segment_visual_keyframes(segment_plan: dict[str, Any]) -> list[dict[str, Any]]:
    prompts_by_id = {entry.get("item_id"): entry for entry in segment_plan.get("prompt_plan", [])}
    sections_by_id = {entry.get("item_id"): entry for entry in segment_plan.get("section_plan", [])}
    temporal_stride = int(
        segment_plan.get("model_specific", {}).get("wan", {}).get("config", {}).get("rules", {}).get("temporal_stride")
        or 4
    )
    image_media = [
        media for media in segment_plan.get("media_plan", [])
        if media.get("section_type") == "Image" and media.get("asset_id") and media.get("path")
    ]
    image_media.sort(key=lambda media: sections_by_id.get(media.get("item_id"), {}).get("start_frame", 0))
    requested = []
    for index, media in enumerate(image_media):
        section = sections_by_id.get(media.get("item_id"), {})
        prompt = prompts_by_id.get(media.get("item_id"), {})
        requested.append({
            "role": _keyframe_role(index, len(image_media)),
            "section_id": media.get("item_id"),
            "asset_id": media.get("asset_id"),
            "path": media.get("path"),
            "time": section.get("start_time"),
            "frame": int(section.get("start_frame") or 0),
            "latent_chunk": int(section.get("start_frame") or 0) // max(1, temporal_stride),
            "guide_strength": float(media.get("guide_strength") if media.get("guide_strength") is not None else 1.0),
            "crop_mode": media.get("crop_mode"),
            "prompt": prompt.get("raw_prompt", ""),
            "effective_prompt": prompt.get("effective_prompt", ""),
        })
    return requested


def _keyframe_role(index: int, count: int) -> str:
    if index == 0:
        return "Start"
    if index == count - 1:
        return "End"
    return "Timed"


def _wan_plan_as_audio_mix_plan(plan: dict[str, Any]) -> dict[str, Any]:
    # The LTX audio mixer is timeline-generic except for this config key.
    output = deepcopy(plan)
    output.setdefault("model_specific", {}).setdefault("ltx", {}).setdefault("config", {})["audio_mode"] = "Mix Timeline Audio"
    return output
