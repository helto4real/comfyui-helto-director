from __future__ import annotations

from pathlib import Path
from typing import Any
import importlib.util

import torch
import numpy as np
from PIL import Image, ImageOps

from ...media_cache import resolve_media_path


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
        diagnostics.append("WAN continuation segment used the previous decoded tail as transient start image.")
        if start_keyframe or end_keyframe:
            diagnostics.append("WAN continuation tail overrode copied visual keyframes for this segment.")
        media_decisions.append({
            "section_id": "segment_previous_tail",
            "loaded": True,
            "role": "Start",
            "transient": True,
            "tensor_shape": _tensor_shape(transient_start),
            "tensor_stats": _tensor_stats(transient_start),
        })
        return positive, negative, latent, {
            "applied_keyframes": [{"role": "Start", "section_id": "segment_previous_tail", "transient": True}],
            "unsupported_keyframes": visual.get("unsupported_keyframes", []),
            "media_decisions": media_decisions,
            "diagnostics": diagnostics,
            "core_helper": helper_name,
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
    diagnostics.append(f"ComfyUI Core visual helper used: {helper_name}.")
    return positive, negative, latent, {
        "applied_keyframes": applied,
        "unsupported_keyframes": visual.get("unsupported_keyframes", []),
        "media_decisions": media_decisions,
        "diagnostics": diagnostics,
        "core_helper": helper_name,
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
