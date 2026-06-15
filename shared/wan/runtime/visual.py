from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image

from ...media_cache import resolve_media_path


def apply_comfy_core_visual_keyframes(
    positive,
    negative,
    vae,
    latent: dict[str, Any],
    visual: dict[str, Any],
    width: int,
    height: int,
    frame_count: int,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    applied = visual.get("applied_keyframes") or []
    start_keyframe = next((entry for entry in applied if entry.get("role") == "Start"), None)
    end_keyframe = next((entry for entry in applied if entry.get("role") == "End"), None)
    diagnostics = []
    media_decisions = []
    if not start_keyframe and not end_keyframe:
        diagnostics.append("No WAN visual keyframes were applied by the ComfyUI Core backend.")
        return positive, negative, latent, {
            "applied_keyframes": [],
            "unsupported_keyframes": visual.get("unsupported_keyframes", []),
            "media_decisions": media_decisions,
            "diagnostics": diagnostics,
        }

    start_image = load_keyframe_image(start_keyframe, width, height, media_decisions) if start_keyframe else None
    end_image = load_keyframe_image(end_keyframe, width, height, media_decisions) if end_keyframe else None
    concat_latent_image, concat_mask = encode_first_last_images(
        vae,
        latent,
        width,
        height,
        frame_count,
        start_image,
        end_image,
    )
    values = {"concat_latent_image": concat_latent_image, "concat_mask": concat_mask}
    positive = conditioning_set_values(positive, values)
    negative = conditioning_set_values(negative, values)
    return positive, negative, latent, {
        "applied_keyframes": applied,
        "unsupported_keyframes": visual.get("unsupported_keyframes", []),
        "media_decisions": media_decisions,
        "diagnostics": diagnostics,
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


def load_keyframe_image(keyframe: dict[str, Any] | None, width: int, height: int, media_decisions: list[dict[str, Any]] | None = None):
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
            image = image.convert("RGB")
            array = np.array(image, dtype=np.float32) / 255.0
    except Exception as exc:
        raise ValueError(f"RUNTIME_IMAGE_DECODE_FAILED: Could not decode WAN visual keyframe image {path}: {exc}") from exc
    tensor = torch.from_numpy(array).unsqueeze(0)
    if media_decisions is not None:
        media_decisions.append({
            "section_id": keyframe.get("section_id"),
            "asset_id": keyframe.get("asset_id"),
            "path": str(path),
            "loaded": True,
            "target_width": width,
            "target_height": height,
        })
    return resize_image_tensor(tensor, width, height)


def resize_image_tensor(tensor: torch.Tensor, width: int, height: int) -> torch.Tensor:
    channels_first = tensor.movedim(-1, 1)
    resized = torch.nn.functional.interpolate(channels_first, size=(height, width), mode="bilinear", align_corners=False)
    return resized.movedim(1, -1)


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
