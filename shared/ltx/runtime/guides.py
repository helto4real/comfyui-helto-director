from __future__ import annotations

from typing import Any

import torch


def apply_guide_data(positive, negative, vae, latent: dict[str, Any], guide_data: dict[str, Any], iclora_parameters=None):
    latent_image = latent["samples"].clone()
    noise_mask = _clone_noise_mask(latent, latent_image)
    scale_factors = getattr(vae, "downscale_index_formula", (8, 32, 32))
    latent_downscale_factor = _reference_downscale_factor(iclora_parameters)

    images = guide_data.get("images", [])
    insert_frames = guide_data.get("insert_frames", [])
    strengths = guide_data.get("strengths", [])
    applied = 0

    for index, image in enumerate(images):
        strength = float(strengths[index]) if index < len(strengths) else 1.0
        if strength <= 0.0:
            continue
        frame_index = int(insert_frames[index]) if index < len(insert_frames) else 0
        positive, negative, latent_image, noise_mask = _apply_one_guide(
            positive,
            negative,
            vae,
            latent_image,
            noise_mask,
            image,
            frame_index,
            strength,
            scale_factors,
            latent_downscale_factor,
        )
        applied += 1

    output = dict(latent)
    output["samples"] = latent_image
    output["noise_mask"] = noise_mask
    return positive, negative, output, {"applied_guides": applied}


def _apply_one_guide(positive, negative, vae, latent_image, noise_mask, image, frame_index, strength, scale_factors, latent_downscale_factor):
    _, _, latent_length, latent_height, latent_width = latent_image.shape
    if latent_downscale_factor > 1 and (latent_width % latent_downscale_factor != 0 or latent_height % latent_downscale_factor != 0):
        raise ValueError(
            f"Latent spatial size {latent_width}x{latent_height} must be divisible by reference_downscale_factor {latent_downscale_factor}."
        )

    time_scale_factor = int(scale_factors[0])
    keep_frames = ((image.shape[0] - 1) // time_scale_factor) * time_scale_factor + 1
    resolved_frame = frame_index
    if frame_index < 0:
        resolved_frame = max((latent_length - 1) * time_scale_factor + 1 + frame_index, 0)
    causal_fix = resolved_frame == 0 or keep_frames == 1
    working_image = image
    if not causal_fix:
        working_image = torch.cat([working_image[:1], working_image], dim=0)

    encoded_pixels, guide_latent = _encode_guide(vae, latent_width, latent_height, working_image, scale_factors, latent_downscale_factor)
    if not causal_fix:
        guide_latent = guide_latent[:, :, 1:, :, :]
        encoded_pixels = encoded_pixels[1:]

    guide_latent_shape = list(guide_latent.shape[2:])
    guide_mask = None
    if latent_downscale_factor > 1:
        guide_latent, guide_mask = _dilate_latent(guide_latent, latent_downscale_factor)

    frame_index, latent_index = _latent_index(positive, latent_length, len(encoded_pixels), frame_index, scale_factors, latent_image.shape)
    if latent_index + guide_latent.shape[2] > latent_length:
        raise ValueError("Guide image conditioning frames exceed the length of the latent sequence.")

    positive = _add_keyframe_index(positive, frame_index, guide_latent, scale_factors, latent_downscale_factor, causal_fix)
    negative = _add_keyframe_index(negative, frame_index, guide_latent, scale_factors, latent_downscale_factor, causal_fix)
    latent_image, noise_mask = _append_keyframe(
        latent_image,
        noise_mask,
        guide_latent,
        strength,
        guide_mask=guide_mask,
        latent_downscale_factor=latent_downscale_factor,
    )
    pre_filter_count = guide_latent.shape[2] * guide_latent.shape[3] * guide_latent.shape[4]
    positive = _append_guide_attention_entry(positive, pre_filter_count, guide_latent_shape, strength)
    negative = _append_guide_attention_entry(negative, pre_filter_count, guide_latent_shape, strength)
    return positive, negative, latent_image, noise_mask


def _encode_guide(vae, latent_width, latent_height, images, scale_factors, latent_downscale_factor):
    time_scale_factor, width_scale_factor, height_scale_factor = scale_factors
    images = images[:(images.shape[0] - 1) // time_scale_factor * time_scale_factor + 1]
    target_width = int(latent_width * width_scale_factor / latent_downscale_factor)
    target_height = int(latent_height * height_scale_factor / latent_downscale_factor)
    pixels = _common_upscale(images, target_width, target_height)
    guide_latent = vae.encode(pixels[:, :, :, :3])
    return pixels, guide_latent


def _common_upscale(images, target_width, target_height):
    try:
        import comfy.utils

        return comfy.utils.common_upscale(
            images.movedim(-1, 1),
            target_width,
            target_height,
            "bilinear",
            crop="center",
        ).movedim(1, -1)
    except Exception:
        resized = torch.nn.functional.interpolate(
            images.movedim(-1, 1),
            size=(target_height, target_width),
            mode="bilinear",
            align_corners=False,
        )
        return resized.movedim(1, -1)


def _dilate_latent(guide_latent, latent_downscale_factor):
    scale = int(latent_downscale_factor)
    if scale <= 1:
        return guide_latent, None
    dilated_shape = guide_latent.shape[:3] + (guide_latent.shape[3] * scale, guide_latent.shape[4] * scale)
    dilated = torch.zeros(dilated_shape, device=guide_latent.device, dtype=guide_latent.dtype)
    dilated[..., ::scale, ::scale] = guide_latent
    mask = torch.full(
        (dilated.shape[0], 1, dilated.shape[2], dilated.shape[3], dilated.shape[4]),
        -1.0,
        device=guide_latent.device,
        dtype=guide_latent.dtype,
    )
    mask[..., ::scale, ::scale] = 1.0
    return dilated, mask


def _latent_index(positive, latent_length, guide_length, frame_index, scale_factors, latent_shape=None):
    time_scale_factor = int(scale_factors[0])
    _, num_keyframes = _keyframe_indexes(positive, latent_shape)
    latent_count = latent_length - num_keyframes
    frame_index = frame_index if frame_index >= 0 else max((latent_count - 1) * time_scale_factor + 1 + frame_index, 0)
    if guide_length > 1 and frame_index != 0:
        frame_index = (frame_index - 1) // time_scale_factor * time_scale_factor + 1
    latent_index = (frame_index + time_scale_factor - 1) // time_scale_factor
    return frame_index, latent_index


def _add_keyframe_index(conditioning, frame_index, guiding_latent, scale_factors, latent_downscale_factor=1, causal_fix=None):
    try:
        from comfy.ldm.lightricks.symmetric_patchifier import SymmetricPatchifier, latent_to_pixel_coords
    except Exception:
        return _conditioning_set_values(conditioning, {"keyframe_idxs": []})

    keyframe_idxs, _ = _keyframe_indexes(conditioning)
    _, latent_coords = SymmetricPatchifier(1, start_end=True).patchify(guiding_latent)
    if causal_fix is None:
        causal_fix = frame_index == 0 or guiding_latent.shape[2] == 1
    pixel_coords = latent_to_pixel_coords(latent_coords, scale_factors, causal_fix=causal_fix)
    pixel_coords[:, 0] += frame_index
    if latent_downscale_factor > 1:
        spatial_end_offset = (latent_downscale_factor - 1) * torch.tensor(scale_factors[1:], device=pixel_coords.device).view(1, -1, 1, 1)
        pixel_coords[:, 1:, :, 1:] += spatial_end_offset.to(pixel_coords.dtype)
    if keyframe_idxs is None:
        keyframe_idxs = pixel_coords
    else:
        keyframe_idxs = torch.cat([keyframe_idxs, pixel_coords], dim=2)
    return _conditioning_set_values(conditioning, {"keyframe_idxs": keyframe_idxs})


def _append_keyframe(latent_image, noise_mask, guiding_latent, strength, guide_mask=None, latent_downscale_factor=1):
    if latent_image.shape[1] > guiding_latent.shape[1]:
        pad_len = latent_image.shape[1] - guiding_latent.shape[1]
        guiding_latent = torch.nn.functional.pad(guiding_latent, pad=(0, 0, 0, 0, 0, 0, 0, pad_len), value=0)
    if guide_mask is not None:
        target_h = max(noise_mask.shape[3], guide_mask.shape[3])
        target_w = max(noise_mask.shape[4], guide_mask.shape[4])
        if noise_mask.shape[3] == 1 or noise_mask.shape[4] == 1:
            noise_mask = noise_mask.expand(-1, -1, -1, target_h, target_w)
        if guide_mask.shape[3] == 1 or guide_mask.shape[4] == 1:
            guide_mask = guide_mask.expand(-1, -1, -1, target_h, target_w)
        mask = guide_mask - strength
    else:
        mask = torch.full(
            (noise_mask.shape[0], 1, guiding_latent.shape[2], noise_mask.shape[3], noise_mask.shape[4]),
            max(0.0, 1.0 - strength),
            dtype=noise_mask.dtype,
            device=noise_mask.device,
        )
    return torch.cat([latent_image, guiding_latent], dim=2), torch.cat([noise_mask, mask], dim=2)


def _append_guide_attention_entry(conditioning, pre_filter_count, latent_shape, strength):
    existing = []
    for item in conditioning:
        entries = item[1].get("guide_attention_entries")
        if entries is not None:
            existing = entries
            break
    entries = [*existing, {
        "pre_filter_count": pre_filter_count,
        "strength": strength,
        "pixel_mask": None,
        "latent_shape": latent_shape,
    }]
    return _conditioning_set_values(conditioning, {"guide_attention_entries": entries})


def _keyframe_indexes(conditioning, latent_shape=None):
    for item in conditioning:
        keyframe_idxs = item[1].get("keyframe_idxs")
        if keyframe_idxs is not None:
            try:
                return keyframe_idxs, int(keyframe_idxs.shape[2])
            except Exception:
                return keyframe_idxs, 0
    return None, 0


def _clone_noise_mask(latent, latent_image):
    if "noise_mask" in latent and torch.is_tensor(latent["noise_mask"]):
        return latent["noise_mask"].clone()
    batch, _, latent_frames, _, _ = latent_image.shape
    return torch.ones((batch, 1, latent_frames, 1, 1), dtype=torch.float32, device=latent_image.device)


def _reference_downscale_factor(iclora_parameters):
    if not isinstance(iclora_parameters, dict):
        return 1
    try:
        return max(1, round(float(iclora_parameters.get("reference_downscale_factor", 1))))
    except (TypeError, ValueError):
        return 1


def set_conditioning_values(conditioning, values):
    try:
        import node_helpers

        return node_helpers.conditioning_set_values(conditioning, values)
    except Exception:
        return [[item[0], {**item[1], **values}] for item in conditioning]


def _conditioning_set_values(conditioning, values):
    return set_conditioning_values(conditioning, values)
