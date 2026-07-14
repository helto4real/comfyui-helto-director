from __future__ import annotations

from pathlib import Path
from typing import Any
import importlib.util

import torch
import numpy as np
from PIL import Image, ImageOps

from ...media_domain import resolve_media_path


def apply_comfy_core_visual_keyframes(
    positive,
    negative,
    vae,
    visual: dict[str, Any],
    width: int,
    height: int,
    frame_count: int,
    batch_size: int,
    latent_spec: dict[str, Any],
    model_mode: str = "I2V-A14B",
    config: dict[str, Any] | None = None,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    applied = visual.get("applied_keyframes") or []
    start_keyframe = next((entry for entry in applied if entry.get("role") == "Start"), None)
    end_keyframe = next((entry for entry in applied if entry.get("role") == "End"), None)
    diagnostics = []
    media_decisions = []
    transient_start = visual.get("transient_start_image")
    has_transient_start = transient_start is not None and hasattr(transient_start, "shape") and int(transient_start.shape[0]) > 0
    should_use_transient_start = has_transient_start and (
        (not start_keyframe and not end_keyframe) or visual.get("continuation_source") == "previous_tail"
    )
    if should_use_transient_start:
        transient_media_id = str(visual.get("continuation_media_id") or "segment_previous_tail")
        transient_kind = str(visual.get("continuation_kind") or "segment_continuity")
        positive, negative, latent, helper_name = execute_comfy_core_visual_helper(
            positive,
            negative,
            vae,
            width,
            height,
            frame_count,
            batch_size,
            transient_start,
            None,
            latent_spec,
            model_mode,
        )
        painter_debug = apply_painter_motion_boost(
            positive,
            negative,
            latent,
            vae,
            width,
            height,
            frame_count,
            transient_start,
            None,
            config,
            helper_name,
        )
        diagnostics.append("WAN continuation segment used the previous decoded tail as transient start image.")
        if start_keyframe or end_keyframe:
            diagnostics.append("WAN continuation tail overrode copied visual keyframes for this segment.")
        decision = {
            "section_id": transient_media_id,
            "loaded": True,
            "role": "Start",
            "transient": True,
            "tensor_shape": _tensor_shape(transient_start),
            "tensor_stats": _tensor_stats(transient_start),
        }
        applied_keyframe = {"role": "Start", "section_id": transient_media_id, "transient": True}
        if transient_kind != "segment_continuity":
            decision["kind"] = transient_kind
            applied_keyframe["kind"] = transient_kind
        media_decisions.append(decision)
        return positive, negative, latent, {
            "applied_keyframes": [applied_keyframe],
            "unsupported_keyframes": visual.get("unsupported_keyframes", []),
            "media_decisions": media_decisions,
            "diagnostics": diagnostics,
            "core_helper": helper_name,
            "painter_motion_boost": painter_debug,
        }
    if not start_keyframe and not end_keyframe:
        diagnostics.append("No WAN visual keyframes were applied by the ComfyUI Core backend.")
        latent = execute_comfy_core_text_to_video_latent(
            positive,
            negative,
            vae,
            width,
            height,
            frame_count,
            batch_size,
            latent_spec,
        )[2]
        return positive, negative, latent, {
            "applied_keyframes": [],
            "unsupported_keyframes": visual.get("unsupported_keyframes", []),
            "media_decisions": media_decisions,
            "diagnostics": diagnostics,
            "painter_motion_boost": _painter_debug_off(config),
        }

    start_image = load_keyframe_image(start_keyframe, width, height, media_decisions, resize=False) if start_keyframe else None
    end_image = load_keyframe_image(end_keyframe, width, height, media_decisions, resize=False) if end_keyframe else None
    positive, negative, latent, helper_name = execute_comfy_core_visual_helper(
        positive,
        negative,
        vae,
        width,
        height,
        frame_count,
        batch_size,
        start_image,
        end_image,
        latent_spec,
        model_mode,
    )
    painter_debug = apply_painter_motion_boost(
        positive,
        negative,
        latent,
        vae,
        width,
        height,
        frame_count,
        start_image,
        end_image,
        config,
        helper_name,
    )
    diagnostics.append(f"ComfyUI Core visual helper used: {helper_name}.")
    return positive, negative, latent, {
        "applied_keyframes": applied,
        "unsupported_keyframes": visual.get("unsupported_keyframes", []),
        "media_decisions": media_decisions,
        "diagnostics": diagnostics,
        "core_helper": helper_name,
        "painter_motion_boost": painter_debug,
    }


def encode_first_last_images(vae, latent, width: int, height: int, frame_count: int, start_image, end_image):
    samples = latent["samples"]
    device = samples.device
    dtype = samples.dtype
    image = torch.ones((max(1, frame_count), height, width, 3), device=device, dtype=dtype) * 0.5
    mask = torch.ones((1, 1, samples.shape[2] * 4, samples.shape[-2], samples.shape[-1]), device=device, dtype=dtype)

    if start_image is not None:
        start_image = start_image.to(device=device, dtype=dtype)
        image[: start_image.shape[0]] = start_image
        mask[:, :, : start_image.shape[0] + 3] = 0.0
    if end_image is not None:
        end_image = end_image.to(device=device, dtype=dtype)
        image[-end_image.shape[0] :] = end_image
        mask[:, :, -end_image.shape[0] :] = 0.0

    concat_latent_image = vae.encode(image[:, :, :, :3])
    if concat_latent_image.shape[1] != samples.shape[1]:
        raise ValueError(
            "WAN_RUNTIME_LATENT_FORMAT_MISMATCH: "
            f"VAE encoded visual keyframes with {concat_latent_image.shape[1]} channels, "
            f"but the WAN runtime latent has {samples.shape[1]} channels. "
            "Use a WAN VAE that matches the connected high/low noise model latent format."
        )
    if concat_latent_image.shape[-2:] != samples.shape[-2:]:
        raise ValueError(
            "WAN_RUNTIME_LATENT_FORMAT_MISMATCH: "
            f"VAE encoded visual keyframes at spatial shape {tuple(concat_latent_image.shape[-2:])}, "
            f"but the WAN runtime latent uses {tuple(samples.shape[-2:])}. "
            "Use a WAN VAE whose spatial compression matches the connected high/low noise model."
        )
    mask = mask.view(1, mask.shape[2] // 4, 4, mask.shape[3], mask.shape[4]).transpose(1, 2)
    return concat_latent_image, mask


def execute_comfy_core_visual_helper(
    positive,
    negative,
    vae,
    width: int,
    height: int,
    frame_count: int,
    batch_size: int,
    start_image,
    end_image,
    latent_spec: dict[str, Any],
    model_mode: str,
):
    nodes_wan = _load_comfy_core_wan_nodes()
    if end_image is not None:
        output = nodes_wan.WanFirstLastFrameToVideo.execute(
            positive,
            negative,
            vae,
            width,
            height,
            frame_count,
            batch_size,
            start_image=start_image,
            end_image=end_image,
        )
        positive, negative, latent = _node_output_args(output)
        _validate_latent_channels(latent, latent_spec, "WanFirstLastFrameToVideo")
        _validate_conditioning_latent_values(positive, latent, "WanFirstLastFrameToVideo")
        return positive, negative, latent, "WanFirstLastFrameToVideo"

    if _should_use_wan22_latent(vae, latent_spec, model_mode):
        output = nodes_wan.Wan22ImageToVideoLatent.execute(
            vae,
            width,
            height,
            frame_count,
            batch_size,
            start_image=start_image,
        )
        (latent,) = _node_output_args(output)
        _validate_latent_channels(latent, latent_spec, "Wan22ImageToVideoLatent", allow_default_when_unset=True)
        return positive, negative, latent, "Wan22ImageToVideoLatent"

    output = nodes_wan.WanImageToVideo.execute(
        positive,
        negative,
        vae,
        width,
        height,
        frame_count,
        batch_size,
        start_image=start_image,
    )
    positive, negative, latent = _node_output_args(output)
    _validate_latent_channels(latent, latent_spec, "WanImageToVideo")
    _validate_conditioning_latent_values(positive, latent, "WanImageToVideo")
    return positive, negative, latent, "WanImageToVideo"


def execute_comfy_core_text_to_video_latent(
    positive,
    negative,
    vae,
    width: int,
    height: int,
    frame_count: int,
    batch_size: int,
    latent_spec: dict[str, Any],
):
    nodes_wan = _load_comfy_core_wan_nodes()
    if _should_use_wan22_latent(vae, latent_spec, "T2V-A14B"):
        output = nodes_wan.Wan22ImageToVideoLatent.execute(
            vae,
            width,
            height,
            frame_count,
            batch_size,
            start_image=None,
        )
        (latent,) = _node_output_args(output)
        return positive, negative, latent
    output = nodes_wan.WanImageToVideo.execute(
        positive,
        negative,
        vae,
        width,
        height,
        frame_count,
        batch_size,
        start_image=None,
    )
    return _node_output_args(output)


def apply_painter_motion_boost(
    positive,
    negative,
    latent: dict[str, Any],
    vae,
    width: int,
    height: int,
    frame_count: int,
    start_image,
    end_image,
    config: dict[str, Any] | None,
    helper_name: str,
) -> dict[str, Any]:
    mode = str((config or {}).get("painter_motion_boost") or "Off")
    amplitude = _painter_amplitude(config)
    debug = {
        "mode": mode if mode in {"Off", "Auto"} else "Off",
        "status": "off",
        "algorithm": None,
        "amplitude": amplitude,
        "helper": helper_name,
        "input_frame_count": int(start_image.shape[0]) if hasattr(start_image, "shape") else 0,
        "protected_chunk_count": 0,
    }
    if debug["mode"] != "Auto":
        return debug
    debug["status"] = "skipped"
    if amplitude <= 1.0:
        debug["reason"] = "amplitude_at_or_below_one"
        return debug
    if start_image is None or not hasattr(start_image, "shape") or int(start_image.shape[0]) <= 0:
        debug["reason"] = "missing_start_image"
        return debug

    if helper_name == "Wan22ImageToVideoLatent":
        applied = _apply_painter_boost_to_wan22_latent(latent, vae, width, height, frame_count, start_image, end_image, amplitude, debug)
    else:
        applied = _apply_painter_boost_to_conditioning(positive, negative, start_image, end_image, amplitude, debug)
    if not applied:
        debug.setdefault("reason", "unsupported_helper_or_missing_latent")
    return debug


def _apply_painter_boost_to_conditioning(positive, negative, start_image, end_image, amplitude: float, debug: dict[str, Any]) -> bool:
    concat_latent = _conditioning_value(positive, "concat_latent_image")
    if concat_latent is None or not hasattr(concat_latent, "shape") or int(concat_latent.shape[2]) <= 1:
        return False
    boosted, details = _boost_concat_latent(concat_latent, start_image, end_image, amplitude)
    if boosted is None:
        debug.update(details)
        return False
    _replace_conditioning_value(positive, "concat_latent_image", boosted)
    _replace_conditioning_value(negative, "concat_latent_image", boosted)
    debug.update(details)
    debug["status"] = "applied"
    return True


def _apply_painter_boost_to_wan22_latent(
    latent: dict[str, Any],
    vae,
    width: int,
    height: int,
    frame_count: int,
    start_image,
    end_image,
    amplitude: float,
    debug: dict[str, Any],
) -> bool:
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if samples is None or not hasattr(samples, "shape") or int(samples.shape[2]) <= 1:
        return False
    if end_image is not None:
        debug["reason"] = "wan22_first_last_boost_deferred"
        return False
    reference = _encode_painter_reference_latent(vae, width, height, frame_count, start_image, samples)
    if reference is None or reference.shape != samples[:1].shape:
        debug["reason"] = "reference_latent_shape_mismatch"
        return False
    boosted, details = _boost_concat_latent(reference, start_image, None, amplitude)
    if boosted is None:
        debug.update(details)
        return False
    mask = latent.get("noise_mask")
    if mask is not None and hasattr(mask, "shape") and mask.shape[2] == samples.shape[2]:
        generated_mask = mask.to(device=samples.device, dtype=samples.dtype)
        while generated_mask.ndim < samples.ndim:
            generated_mask = generated_mask.unsqueeze(1)
        latent["samples"] = samples * (1.0 - generated_mask) + boosted.to(device=samples.device, dtype=samples.dtype).repeat((samples.shape[0],) + (1,) * (boosted.ndim - 1)) * generated_mask
    else:
        protected = int(details.get("protected_chunk_count") or 0)
        next_samples = samples.clone()
        next_samples[:, :, protected:] = boosted.to(device=samples.device, dtype=samples.dtype).repeat((samples.shape[0],) + (1,) * (boosted.ndim - 1))[:, :, protected:]
        latent["samples"] = next_samples
    debug.update(details)
    debug["status"] = "applied"
    return True


def _boost_concat_latent(concat_latent: torch.Tensor, start_image, end_image, amplitude: float) -> tuple[torch.Tensor | None, dict[str, Any]]:
    details = {
        "algorithm": "painter_flf2v" if end_image is not None else "painter_i2v",
        "protected_chunk_count": _latent_chunk_count_for_frames(int(start_image.shape[0])),
    }
    latent_frames = int(concat_latent.shape[2])
    start_chunks = min(latent_frames, _latent_chunk_count_for_frames(int(start_image.shape[0])))
    if end_image is not None and hasattr(end_image, "shape") and int(end_image.shape[0]) > 0:
        end_chunks = min(latent_frames - start_chunks, _latent_chunk_count_for_frames(int(end_image.shape[0])))
        details["protected_chunk_count"] = start_chunks + end_chunks
        boosted = _boost_first_last_latent(concat_latent, start_chunks, end_chunks, amplitude)
    else:
        end_chunks = 0
        boosted = _boost_i2v_latent(concat_latent, start_chunks, amplitude)
    if boosted is None:
        return None, details | {"reason": "no_generated_chunks_to_boost"}
    details["start_protected_chunk_count"] = start_chunks
    details["end_protected_chunk_count"] = end_chunks
    return boosted, details


def _boost_i2v_latent(concat_latent: torch.Tensor, protected_chunks: int, amplitude: float) -> torch.Tensor | None:
    if protected_chunks >= int(concat_latent.shape[2]):
        return None
    base_latent = concat_latent[:, :, protected_chunks - 1:protected_chunks] if protected_chunks > 0 else concat_latent[:, :, 0:1]
    motion_latent = concat_latent[:, :, protected_chunks:]
    diff = motion_latent - base_latent
    diff_mean = diff.mean(dim=(1, 3, 4), keepdim=True)
    diff_centered = diff - diff_mean
    scaled = torch.clamp(base_latent + diff_centered * amplitude + diff_mean, -6, 6)
    boosted = concat_latent.clone()
    boosted[:, :, protected_chunks:] = scaled
    return boosted


def _boost_first_last_latent(concat_latent: torch.Tensor, start_chunks: int, end_chunks: int, amplitude: float) -> torch.Tensor | None:
    latent_frames = int(concat_latent.shape[2])
    middle_start = max(0, start_chunks)
    middle_end = max(middle_start, latent_frames - end_chunks)
    if middle_start >= middle_end:
        return None
    start_l = concat_latent[:, :, max(0, start_chunks - 1):max(1, start_chunks)]
    end_l = concat_latent[:, :, latent_frames - end_chunks:latent_frames - end_chunks + 1] if end_chunks > 0 else concat_latent[:, :, -1:]
    t = torch.linspace(0.0, 1.0, latent_frames, device=concat_latent.device, dtype=concat_latent.dtype).view(1, 1, -1, 1, 1)
    linear_latent = start_l * (1 - t) + end_l * t
    diff = concat_latent - linear_latent
    high_freq_diff = _high_frequency_latent(diff)
    boosted = concat_latent.clone()
    boost_scale = (amplitude - 1.0) * 4.0
    boosted[:, :, middle_start:middle_end] = concat_latent[:, :, middle_start:middle_end] + high_freq_diff[:, :, middle_start:middle_end] * boost_scale
    return boosted


def _high_frequency_latent(diff: torch.Tensor) -> torch.Tensor:
    h = int(diff.shape[-2])
    w = int(diff.shape[-1])
    low_h = max(1, h // 8)
    low_w = max(1, w // 8)
    low_freq = torch.nn.functional.interpolate(diff.reshape(-1, diff.shape[1], h, w), size=(low_h, low_w), mode="area")
    low_freq = torch.nn.functional.interpolate(low_freq, size=(h, w), mode="bilinear", align_corners=False)
    return diff - low_freq.reshape_as(diff)


def _encode_painter_reference_latent(vae, width: int, height: int, frame_count: int, start_image, samples: torch.Tensor) -> torch.Tensor | None:
    try:
        if int(start_image.shape[1]) != height or int(start_image.shape[2]) != width:
            start_image = resize_image_tensor(start_image, width, height)
        image = torch.ones((max(1, frame_count), height, width, 3), device=start_image.device, dtype=start_image.dtype) * 0.5
        image[: min(int(start_image.shape[0]), frame_count)] = start_image[:frame_count]
        encoded = vae.encode(image[:, :, :, :3])
    except Exception:
        return None
    if encoded.shape[-3:] != samples.shape[-3:] or int(encoded.shape[1]) != int(samples.shape[1]):
        return None
    return encoded.to(device=samples.device, dtype=samples.dtype)


def _painter_amplitude(config: dict[str, Any] | None) -> float:
    try:
        return min(2.0, max(1.0, float((config or {}).get("painter_motion_amplitude", 1.15))))
    except (TypeError, ValueError):
        return 1.15


def _painter_debug_off(config: dict[str, Any] | None) -> dict[str, Any]:
    mode = str((config or {}).get("painter_motion_boost") or "Off")
    return {
        "mode": mode if mode in {"Off", "Auto"} else "Off",
        "status": "off" if mode != "Auto" else "skipped",
        "algorithm": None,
        "amplitude": _painter_amplitude(config),
        "helper": None,
        "input_frame_count": 0,
        "protected_chunk_count": 0,
        "reason": "no_visual_conditioning",
    }


def _latent_chunk_count_for_frames(frame_count: int) -> int:
    return max(1, ((max(1, int(frame_count)) - 1) // 4) + 1)


def _conditioning_value(conditioning, key: str):
    if not isinstance(conditioning, (list, tuple)):
        return None
    for item in conditioning:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        _tensor, metadata = item[0], item[1]
        if isinstance(metadata, dict) and key in metadata:
            return metadata[key]
    return None


def _replace_conditioning_value(conditioning, key: str, value) -> None:
    if not isinstance(conditioning, (list, tuple)):
        return
    for item in conditioning:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        _tensor, metadata = item[0], item[1]
        if isinstance(metadata, dict) and key in metadata:
            metadata[key] = value


def load_keyframe_image(
    keyframe: dict[str, Any] | None,
    width: int,
    height: int,
    media_decisions: list[dict[str, Any]] | None = None,
    resize: bool = True,
):
    if not keyframe:
        return None
    path_value = keyframe.get("path")
    if not path_value:
        raise ValueError(f"RUNTIME_MEDIA_FILE_NOT_FOUND: WAN visual keyframe {keyframe.get('section_id')} is missing an image path.")
    try:
        path = resolve_media_path(path_value)
    except FileNotFoundError as exc:
        raise ValueError(f"RUNTIME_MEDIA_FILE_NOT_FOUND: WAN visual keyframe image does not exist: {path_value}") from exc
    if not Path(path).exists():
        raise ValueError(f"RUNTIME_MEDIA_FILE_NOT_FOUND: WAN visual keyframe image does not exist: {path}")
    try:
        with Image.open(path) as image:
            original_size = list(image.size)
            original_orientation = image.getexif().get(274)
            image = ImageOps.exif_transpose(image)
            transposed_size = list(image.size)
            image = image.convert("RGB")
            array = np.array(image, dtype=np.float32) / 255.0
    except Exception as exc:
        raise ValueError(f"RUNTIME_IMAGE_DECODE_FAILED: Could not decode WAN visual keyframe image {path}: {exc}") from exc
    tensor = torch.from_numpy(array).unsqueeze(0)
    output = resize_image_tensor(tensor, width, height) if resize else tensor
    if media_decisions is not None:
        media_decisions.append({
            "section_id": keyframe.get("section_id"),
            "asset_id": keyframe.get("asset_id"),
            "path": str(path),
            "loaded": True,
            "target_width": width,
            "target_height": height,
            "original_size": original_size,
            "exif_orientation": int(original_orientation) if original_orientation is not None else None,
            "exif_transposed_size": transposed_size,
            "tensor_shape": _tensor_shape(output),
            "tensor_stats": _tensor_stats(output),
        })
    return output


def resize_image_tensor(tensor: torch.Tensor, width: int, height: int) -> torch.Tensor:
    channels_first = tensor.movedim(-1, 1)
    resized = torch.nn.functional.interpolate(channels_first, size=(height, width), mode="bilinear", align_corners=False)
    return resized.movedim(1, -1)


def _tensor_shape(tensor: torch.Tensor) -> list[int]:
    return [int(dim) for dim in tensor.shape]


def _tensor_stats(tensor: torch.Tensor) -> dict[str, float]:
    detached = tensor.detach().float()
    return {
        "min": float(detached.min().item()),
        "max": float(detached.max().item()),
        "mean": float(detached.mean().item()),
    }


def conditioning_set_values(conditioning, values):
    try:
        import node_helpers

        return node_helpers.conditioning_set_values(conditioning, values)
    except Exception:
        result = []
        for tensor, metadata in conditioning:
            next_metadata = metadata.copy()
            next_metadata.update(values)
            result.append([tensor, next_metadata])
    return result


def _load_comfy_core_wan_nodes():
    if importlib.util.find_spec("comfy_extras.nodes_wan") is None:
        raise ValueError(
            "WAN_RUNTIME_BACKEND_NOT_AVAILABLE: ComfyUI Core WAN helpers could not be imported. "
            "Run inside a ComfyUI checkout that includes comfy_extras.nodes_wan."
        )
    try:
        import comfy_extras.nodes_wan as nodes_wan
    except Exception as exc:
        if _is_torch_device_initialization_error(exc):
            return _FallbackWanNodes
        raise ValueError(
            "WAN_RUNTIME_BACKEND_NOT_AVAILABLE: ComfyUI Core WAN helpers could not be imported. "
            "Run inside a ComfyUI checkout that includes comfy_extras.nodes_wan."
        ) from exc
    return nodes_wan


def _node_output_args(output) -> tuple[Any, ...]:
    if hasattr(output, "result"):
        result = output.result
    elif hasattr(output, "args"):
        result = output.args
    else:
        result = output
    if isinstance(result, tuple):
        return result
    if isinstance(result, list):
        return tuple(result)
    return (result,)


def _should_use_wan22_latent(vae, latent_spec: dict[str, Any], model_mode: str) -> bool:
    latent_channels = int(latent_spec.get("channels") or 0)
    vae_channels = int(getattr(vae, "latent_channels", 0) or 0)
    if latent_channels == 48:
        return True
    if latent_spec.get("source") == "model_latent_format":
        return False
    return vae_channels == 48 or str(model_mode) == "I2V-A14B" and vae_channels == 48


def _validate_latent_channels(
    latent: dict[str, Any],
    latent_spec: dict[str, Any],
    helper_name: str,
    allow_default_when_unset: bool = False,
) -> None:
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if samples is None or not hasattr(samples, "shape"):
        return
    expected = int(latent_spec.get("channels") or 0)
    actual = int(samples.shape[1])
    if expected <= 0 or (allow_default_when_unset and latent_spec.get("source") == "wan_default"):
        return
    if actual != expected:
        raise ValueError(
            "WAN_RUNTIME_LATENT_FORMAT_MISMATCH: "
            f"ComfyUI Core helper {helper_name} produced {actual} latent channels, "
            f"but the connected WAN model/VAE runtime expected {expected}. "
            "Use matching WAN 2.2 model and VAE wiring, or switch Runtime Backend Profile to Plan Only for inspection."
        )


def _validate_conditioning_latent_values(conditioning, latent: dict[str, Any], helper_name: str) -> None:
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if samples is None or not hasattr(samples, "shape"):
        return
    expected_channels = int(samples.shape[1])
    expected_spatial = tuple(samples.shape[-2:])
    for _tensor, metadata in conditioning or []:
        concat_latent_image = metadata.get("concat_latent_image") if isinstance(metadata, dict) else None
        if concat_latent_image is None or not hasattr(concat_latent_image, "shape"):
            continue
        actual_channels = int(concat_latent_image.shape[1])
        actual_spatial = tuple(concat_latent_image.shape[-2:])
        if actual_channels != expected_channels or actual_spatial != expected_spatial:
            raise ValueError(
                "WAN_RUNTIME_LATENT_FORMAT_MISMATCH: "
                f"ComfyUI Core helper {helper_name} produced visual conditioning with "
                f"{actual_channels} channels at spatial shape {actual_spatial}, "
                f"but the runtime latent has {expected_channels} channels at spatial shape {expected_spatial}. "
                "Use a WAN VAE that matches the connected high/low noise model latent format."
            )


def _is_torch_device_initialization_error(exc: Exception) -> bool:
    text = str(exc)
    return "No CUDA GPUs are available" in text or "Torch not compiled with CUDA enabled" in text


class _FallbackWanNodes:
    class WanImageToVideo:
        @classmethod
        def execute(cls, positive, negative, vae, width, height, length, batch_size, start_image=None, clip_vision_output=None):
            latent = _fallback_latent(batch_size, 16, length, height, width, 8)
            if start_image is not None:
                start_image = resize_image_tensor(start_image[:length], width, height)
                image = torch.ones((length, height, width, start_image.shape[-1]), dtype=start_image.dtype) * 0.5
                image[: start_image.shape[0]] = start_image
                concat_latent_image = vae.encode(image[:, :, :, :3])
                mask = torch.ones((1, 1, latent["samples"].shape[2], concat_latent_image.shape[-2], concat_latent_image.shape[-1]), dtype=start_image.dtype)
                mask[:, :, : ((start_image.shape[0] - 1) // 4) + 1] = 0.0
                values = {"concat_latent_image": concat_latent_image, "concat_mask": mask}
                positive = conditioning_set_values(positive, values)
                negative = conditioning_set_values(negative, values)
            return positive, negative, latent

    class WanFirstLastFrameToVideo:
        @classmethod
        def execute(cls, positive, negative, vae, width, height, length, batch_size, start_image=None, end_image=None, **_kwargs):
            spatial_scale = int(_call_or_value(vae, "spacial_compression_encode", 8) or 8)
            latent_channels = int(getattr(vae, "latent_channels", 16) or 16)
            latent = _fallback_latent(batch_size, latent_channels, length, height, width, spatial_scale)
            start_image = resize_image_tensor(start_image[:length], width, height) if start_image is not None else None
            end_image = resize_image_tensor(end_image[-length:], width, height) if end_image is not None else None
            concat_latent_image, concat_mask = encode_first_last_images(vae, latent, width, height, length, start_image, end_image)
            values = {"concat_latent_image": concat_latent_image, "concat_mask": concat_mask}
            return conditioning_set_values(positive, values), conditioning_set_values(negative, values), latent

    class Wan22ImageToVideoLatent:
        @classmethod
        def execute(cls, vae, width, height, length, batch_size, start_image=None):
            latent = _fallback_latent(1, 48, length, height, width, 16)
            mask = torch.ones((1, 1, latent["samples"].shape[2], latent["samples"].shape[-2], latent["samples"].shape[-1]))
            if start_image is not None:
                start_image = resize_image_tensor(start_image[:length], width, height)
                latent_temp = vae.encode(start_image[:, :, :, :3])
                latent["samples"][:, :, : latent_temp.shape[-3]] = latent_temp[:, :, : latent["samples"].shape[2]]
                mask[:, :, : latent_temp.shape[-3]] = 0.0
            return {
                "samples": latent["samples"].repeat((batch_size,) + (1,) * (latent["samples"].ndim - 1)),
                "noise_mask": mask.repeat((batch_size,) + (1,) * (mask.ndim - 1)),
            },


def _fallback_latent(batch_size: int, channels: int, length: int, height: int, width: int, spatial_scale: int) -> dict[str, torch.Tensor]:
    return {
        "samples": torch.zeros(
            (
                max(1, int(batch_size)),
                int(channels),
                ((max(1, int(length)) - 1) // 4) + 1,
                max(1, int(height) // int(spatial_scale)),
                max(1, int(width) // int(spatial_scale)),
            )
        )
    }


def _call_or_value(obj, name: str, fallback):
    value = getattr(obj, name, None)
    if callable(value):
        return value()
    return value if value is not None else fallback
