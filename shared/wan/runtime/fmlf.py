from __future__ import annotations

from typing import Any

import torch

from .visual import load_keyframe_image, resize_image_tensor


FMLF_CONTINUATION_SVI = "SVI"
FMLF_CONTINUATION_AUTO = "AUTO_CONTINUE"


def build_fmlf_advanced_i2v_payload(
    positive,
    negative,
    vae,
    visual: dict[str, Any],
    width: int,
    height: int,
    frame_count: int,
    batch_size: int,
    latent_spec: dict[str, Any],
    config: dict[str, Any],
    *,
    prev_latent: dict[str, Any] | None = None,
    motion_frames: torch.Tensor | None = None,
    video_frame_offset: int = 0,
):
    import node_helpers

    model_mode = str(config.get("model_mode") or "I2V-A14B")
    if model_mode != "I2V-A14B":
        raise ValueError(
            "WAN_FMLF_UNSUPPORTED_MODEL_MODE: "
            f"FMLF Advanced I2V supports I2V-A14B in this nodepack, got {model_mode}."
        )
    if vae is None:
        raise ValueError("WAN_FMLF_REQUIRED_INPUT_MISSING: FMLF Advanced I2V requires a VAE.")

    mode = _continuation_mode(config)
    latent_channels = int(latent_spec.get("channels") or getattr(vae, "latent_channels", 16) or 16)
    spatial_scale = int(latent_spec.get("spatial_scale") or _call_or_value(vae, "spacial_compression_encode", 8) or 8)
    latent_t = ((max(1, int(frame_count)) - 1) // 4) + 1
    latent_h = max(1, int(height) // spatial_scale)
    latent_w = max(1, int(width) // spatial_scale)
    device = _intermediate_device()

    latent = torch.zeros(
        (max(1, int(batch_size)), latent_channels, latent_t, latent_h, latent_w),
        device=device,
    )
    start_image, end_image, media_decisions = _resolve_fmlf_images(visual, width, height)
    motion_frames = _prepare_motion_frames(motion_frames, width, height)
    has_motion_frames = motion_frames is not None and int(motion_frames.shape[0]) > 0
    has_prev_latent = _valid_prev_latent(prev_latent)
    continue_frames = int(motion_frames.shape[0]) if has_motion_frames else 0
    trim_image = continue_frames if has_motion_frames else 0
    next_offset = max(0, int(video_frame_offset or 0)) + int(frame_count)

    if mode == FMLF_CONTINUATION_SVI:
        positive_high, positive_low, negative_out, debug = _build_svi_payload(
            positive,
            negative,
            vae,
            latent,
            start_image,
            prev_latent,
            latent_channels,
            latent_t,
            latent_h,
            latent_w,
            node_helpers,
        )
        debug.update({
            "continuation_mode": mode,
            "used_prev_latent": has_prev_latent,
            "used_motion_frames": has_motion_frames,
            "motion_frame_count": int(motion_frames.shape[0]) if has_motion_frames else 0,
            "trim_image": int(trim_image),
            "trim_latent": 0,
            "next_offset": int(next_offset),
            "anchor_source": _anchor_source(media_decisions, start_image),
            "media_decisions": _sanitize_media_decisions(media_decisions),
        })
        return positive_high, positive_low, negative_out, {"samples": latent}, 0, trim_image, next_offset, debug

    positive_high, positive_low, negative_out, debug = _build_image_motion_payload(
        positive,
        negative,
        vae,
        latent,
        start_image,
        end_image,
        motion_frames,
        frame_count,
        width,
        height,
        latent_channels,
        node_helpers,
    )
    debug.update({
        "continuation_mode": mode,
        "used_prev_latent": False,
        "used_motion_frames": has_motion_frames,
        "motion_frame_count": int(motion_frames.shape[0]) if has_motion_frames else 0,
        "trim_image": int(trim_image),
        "trim_latent": 0,
        "next_offset": int(next_offset),
        "anchor_source": _anchor_source(media_decisions, start_image),
        "media_decisions": _sanitize_media_decisions(media_decisions),
        "fallback_reason": "missing_prev_latent" if mode == FMLF_CONTINUATION_SVI else None,
    })
    return positive_high, positive_low, negative_out, {"samples": latent}, 0, trim_image, next_offset, debug


def _build_svi_payload(
    positive,
    negative,
    vae,
    latent: torch.Tensor,
    start_image,
    prev_latent: dict[str, Any] | None,
    latent_channels: int,
    latent_t: int,
    latent_h: int,
    latent_w: int,
    node_helpers,
):
    if start_image is not None and int(start_image.shape[0]) > 0:
        anchor_latent = vae.encode(start_image[:1, :, :, :3])
    else:
        anchor_latent = torch.zeros(
            (1, latent_channels, 1, latent_h, latent_w),
            device=latent.device,
            dtype=latent.dtype,
        )
    anchor_latent = anchor_latent.to(device=latent.device, dtype=latent.dtype)
    prev_samples = None
    if _valid_prev_latent(prev_latent):
        prev_samples = prev_latent["samples"].to(device=latent.device, dtype=latent.dtype)
        if int(prev_samples.shape[1]) != latent_channels:
            raise ValueError(
                "WAN_FMLF_LATENT_FORMAT_MISMATCH: "
                f"FMLF Advanced I2V previous latent has {int(prev_samples.shape[1])} channels, expected {latent_channels}."
            )
        motion_latent = prev_samples[:, :, -min(int(prev_samples.shape[2]), max(1, latent_t - 1)) :].clone()
        if int(motion_latent.shape[-2]) != latent_h or int(motion_latent.shape[-1]) != latent_w:
            motion_latent = torch.nn.functional.interpolate(
                motion_latent,
                size=(int(motion_latent.shape[2]), latent_h, latent_w),
                mode="nearest",
            )
    else:
        motion_latent = torch.zeros(
            (1, latent_channels, 0, latent_h, latent_w),
            device=latent.device,
            dtype=latent.dtype,
        )
    padding_size = max(0, latent_t - int(anchor_latent.shape[2]) - int(motion_latent.shape[2]))
    padding = torch.zeros(
        (1, latent_channels, padding_size, latent_h, latent_w),
        device=latent.device,
        dtype=latent.dtype,
    )
    padding = _process_latent_padding(padding, latent_channels)
    image_cond_latent = torch.cat([anchor_latent, motion_latent, padding], dim=2)[:, :, :latent_t]
    if int(image_cond_latent.shape[2]) < latent_t:
        extra = torch.zeros(
            (1, latent_channels, latent_t - int(image_cond_latent.shape[2]), latent_h, latent_w),
            device=latent.device,
            dtype=latent.dtype,
        )
        image_cond_latent = torch.cat([image_cond_latent, _process_latent_padding(extra, latent_channels)], dim=2)
    mask = torch.ones((1, 1, latent_t, latent_h, latent_w), device=latent.device, dtype=latent.dtype)
    mask[:, :, :1] = 0.0
    positive_high = node_helpers.conditioning_set_values(positive, {
        "concat_latent_image": image_cond_latent,
        "concat_mask": mask,
    })
    positive_low = node_helpers.conditioning_set_values(positive, {
        "concat_latent_image": image_cond_latent,
        "concat_mask": mask,
    })
    negative_out = node_helpers.conditioning_set_values(negative, {
        "concat_latent_image": image_cond_latent,
        "concat_mask": mask,
    })
    return positive_high, positive_low, negative_out, {
        "helper": "FMLF Advanced I2V",
        "algorithm": "svi_latent_continuation",
        "prev_latent_shape": _tensor_shape(prev_samples),
        "motion_latent_shape": _tensor_shape(motion_latent),
        "concat_latent_shape": _tensor_shape(image_cond_latent),
        "concat_mask_shape": _tensor_shape(mask),
        "conditioning_split": True,
    }


def _build_image_motion_payload(
    positive,
    negative,
    vae,
    latent: torch.Tensor,
    start_image,
    end_image,
    motion_frames,
    frame_count: int,
    width: int,
    height: int,
    latent_channels: int,
    node_helpers,
):
    length = max(1, int(frame_count))
    latent_t = int(latent.shape[2])
    image = torch.ones((length, height, width, 3), device=latent.device, dtype=latent.dtype) * 0.5
    mask_high = torch.ones((1, 1, latent_t * 4, latent.shape[-2], latent.shape[-1]), device=latent.device, dtype=latent.dtype)
    mask_low = mask_high.clone()
    if motion_frames is not None and int(motion_frames.shape[0]) > 0:
        motion = motion_frames.to(device=latent.device, dtype=latent.dtype)[:length]
        image[: int(motion.shape[0])] = motion[:, :, :, :3]
        protected = _latent_chunks_for_frames(int(motion.shape[0])) * 4
        mask_high[:, :, :protected] = 0.0
        mask_low[:, :, :protected] = 0.0
    elif start_image is not None and int(start_image.shape[0]) > 0:
        start = start_image.to(device=latent.device, dtype=latent.dtype)[:1]
        image[:1] = start[:, :, :, :3]
        mask_high[:, :, :4] = 0.0
        mask_low[:, :, :4] = 0.0
    if end_image is not None and int(end_image.shape[0]) > 0:
        end = end_image.to(device=latent.device, dtype=latent.dtype)[-1:]
        image[-1:] = end[:, :, :, :3]
        mask_high[:, :, -1:] = 0.0
        mask_low[:, :, -1:] = 0.0
    concat_latent = vae.encode(image[:, :, :, :3]).to(device=latent.device, dtype=latent.dtype)
    if int(concat_latent.shape[1]) != latent_channels:
        raise ValueError(
            "WAN_FMLF_LATENT_FORMAT_MISMATCH: "
            f"FMLF Advanced I2V encoded {int(concat_latent.shape[1])} latent channels, expected {latent_channels}."
        )
    mask_high = mask_high.view(1, mask_high.shape[2] // 4, 4, mask_high.shape[3], mask_high.shape[4]).transpose(1, 2)
    mask_low = mask_low.view(1, mask_low.shape[2] // 4, 4, mask_low.shape[3], mask_low.shape[4]).transpose(1, 2)
    positive_high = node_helpers.conditioning_set_values(positive, {
        "concat_latent_image": concat_latent,
        "concat_mask": mask_high,
    })
    positive_low = node_helpers.conditioning_set_values(positive, {
        "concat_latent_image": concat_latent,
        "concat_mask": mask_low,
    })
    negative_out = node_helpers.conditioning_set_values(negative, {
        "concat_latent_image": concat_latent,
        "concat_mask": mask_high,
    })
    return positive_high, positive_low, negative_out, {
        "helper": "FMLF Advanced I2V",
        "algorithm": "auto_continue_motion_frames",
        "concat_latent_shape": _tensor_shape(concat_latent),
        "high_mask_shape": _tensor_shape(mask_high),
        "low_mask_shape": _tensor_shape(mask_low),
        "conditioning_split": True,
    }


def _resolve_fmlf_images(visual: dict[str, Any], width: int, height: int):
    media_decisions: list[dict[str, Any]] = []
    applied = visual.get("applied_keyframes") or []
    start_keyframe = next((entry for entry in applied if entry.get("role") == "Start"), None)
    end_keyframe = next((entry for entry in applied if entry.get("role") == "End"), None)
    transient_start = visual.get("transient_start_image")
    start_image = None
    if transient_start is not None and hasattr(transient_start, "shape") and int(transient_start.shape[0]) > 0:
        transient_media_id = str(visual.get("continuation_media_id") or "segment_previous_tail")
        transient_kind = str(visual.get("continuation_kind") or "segment_continuity")
        start_image = transient_start[-1:].detach().clone()
        if int(start_image.shape[1]) != height or int(start_image.shape[2]) != width:
            start_image = resize_image_tensor(start_image, width, height)
        decision = {
            "section_id": transient_media_id,
            "loaded": True,
            "role": "Start",
            "transient": True,
            "tensor_shape": _tensor_shape(start_image),
        }
        if transient_kind != "segment_continuity":
            decision["kind"] = transient_kind
        media_decisions.append(decision)
    elif start_keyframe is not None:
        start_image = load_keyframe_image(start_keyframe, width, height, media_decisions, resize=True)
    end_image = load_keyframe_image(end_keyframe, width, height, media_decisions, resize=True) if end_keyframe else None
    return start_image, end_image, media_decisions


def _prepare_motion_frames(frames, width: int, height: int):
    if frames is None or not torch.is_tensor(frames) or int(frames.shape[0]) <= 0:
        return None
    output = frames.detach().clone()
    if int(output.shape[1]) != height or int(output.shape[2]) != width:
        output = resize_image_tensor(output, width, height)
    return output


def _continuation_mode(config: dict[str, Any]) -> str:
    value = str(config.get("fmlf_continuation_mode") or FMLF_CONTINUATION_SVI)
    return value if value in {FMLF_CONTINUATION_SVI, FMLF_CONTINUATION_AUTO} else FMLF_CONTINUATION_SVI


def _valid_prev_latent(prev_latent: dict[str, Any] | None) -> bool:
    samples = prev_latent.get("samples") if isinstance(prev_latent, dict) else None
    return torch.is_tensor(samples) and int(samples.shape[2]) > 0


def _latent_chunks_for_frames(frame_count: int) -> int:
    return ((max(1, int(frame_count)) - 1) // 4) + 1


def _process_latent_padding(tensor: torch.Tensor, latent_channels: int) -> torch.Tensor:
    try:
        import comfy.latent_formats

        if int(latent_channels) == 48:
            return comfy.latent_formats.Wan22().process_out(tensor)
        return comfy.latent_formats.Wan21().process_out(tensor)
    except Exception:
        return tensor


def _intermediate_device():
    try:
        import comfy.model_management

        return comfy.model_management.intermediate_device()
    except Exception:
        return "cpu"


def _call_or_value(obj, name: str, fallback):
    value = getattr(obj, name, fallback)
    if callable(value):
        try:
            return value()
        except Exception:
            return fallback
    return value


def _tensor_shape(tensor) -> list[int]:
    return [int(dim) for dim in tensor.shape] if hasattr(tensor, "shape") else []


def _sanitize_media_decisions(media_decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized = []
    for decision in media_decisions:
        sanitized.append({
            key: value
            for key, value in decision.items()
            if key not in {"path", "filename", "name"}
        })
    return sanitized


def _anchor_source(media_decisions: list[dict[str, Any]], start_image) -> str:
    for decision in media_decisions:
        if decision.get("role") == "Start" and decision.get("loaded"):
            section_id = decision.get("section_id")
            return str(section_id or ("transient_start_image" if decision.get("transient") else "timeline_start_keyframe"))
    if start_image is not None and hasattr(start_image, "shape") and int(start_image.shape[0]) > 0:
        return "unknown_start_image"
    return "none"
