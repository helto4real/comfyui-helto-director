from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image

from ...media_cache import resolve_media_path
from ..config import WAN_MODEL_FAMILY, WAN_MODEL_VERSION
from ..planner import WAN_PLAN_TYPE
from .prompt_relay import encode_wan_prompt_relay


def build_wan_runtime_outputs(
    *,
    model,
    clip,
    vae,
    wan_timeline_plan: dict[str, Any],
    negative=None,
    batch_size: int = 1,
) -> tuple[Any, Any, Any, dict[str, Any], dict[str, Any]]:
    plan = deepcopy(wan_timeline_plan)
    _validate_plan(plan)
    config = plan.get("model_specific", {}).get("wan", {}).get("config", {})
    backend = _resolve_backend(config.get("runtime_backend_profile"))
    capabilities = _backend_capabilities(backend)
    visual = _resolved_visual_conditioning(plan, capabilities)
    width = int(plan["resolved_output"].get("width") or 1280)
    height = int(plan["resolved_output"].get("height") or 704)
    frame_count = int(plan["resolved_output"].get("frame_count") or 1)

    if backend == "Plan Only":
        video_latent = empty_wan22_video_latent(vae, width, height, frame_count, batch_size)
        runtime_debug = _runtime_debug(plan, backend, capabilities, visual, {}, ["WAN runtime backend is Plan Only; no conditioning execution was performed."])
        return model, [], negative if negative is not None else [], video_latent, runtime_debug

    if backend == "WanVideoWrapper":
        raise ValueError("WAN runtime backend WanVideoWrapper is not available in this nodepack yet.")

    prompt_debug = {}
    prompt_relay = plan.get("model_specific", {}).get("wan", {}).get("prompt_relay", {})
    if prompt_relay.get("enabled", True):
        runtime_model, positive, prompt_debug = encode_wan_prompt_relay(model, clip, prompt_relay)
    else:
        prompt = _plain_prompt(prompt_relay)
        positive = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
        runtime_model = model
        prompt_debug = {"full_prompt": prompt, "local_prompts": [], "token_ranges": [], "patched": False}

    runtime_negative = _resolve_negative_conditioning(negative, positive)
    video_latent = empty_wan22_video_latent(vae, width, height, frame_count, batch_size)
    positive, runtime_negative, video_latent, guide_debug = _apply_comfy_core_visual_keyframes(
        positive,
        runtime_negative,
        vae,
        video_latent,
        visual,
        width,
        height,
        frame_count,
    )
    runtime_debug = _runtime_debug(plan, backend, capabilities, visual, prompt_debug, guide_debug.get("diagnostics", []))
    runtime_debug["guide_debug"] = guide_debug
    return runtime_model, positive, runtime_negative, video_latent, runtime_debug


def empty_wan22_video_latent(vae, width: int, height: int, frame_count: int, batch_size: int = 1) -> dict[str, Any]:
    try:
        import comfy.model_management

        device = comfy.model_management.intermediate_device()
    except Exception:
        device = "cpu"
    latent_channels = int(getattr(vae, "latent_channels", 48) or 48)
    spatial_scale = int(_call_or_value(vae, "spacial_compression_encode", 16) or 16)
    latent = torch.zeros(
        (
            max(1, int(batch_size)),
            latent_channels,
            ((max(1, int(frame_count)) - 1) // 4) + 1,
            max(1, int(height) // spatial_scale),
            max(1, int(width) // spatial_scale),
        ),
        device=device,
    )
    return {"samples": latent}


def zero_out_conditioning(conditioning):
    zeroed = []
    for tensor, metadata in conditioning:
        next_metadata = metadata.copy()
        pooled_output = next_metadata.get("pooled_output")
        if pooled_output is not None:
            next_metadata["pooled_output"] = torch.zeros_like(pooled_output)
        conditioning_lyrics = next_metadata.get("conditioning_lyrics")
        if conditioning_lyrics is not None:
            next_metadata["conditioning_lyrics"] = torch.zeros_like(conditioning_lyrics)
        zeroed.append([torch.zeros_like(tensor), next_metadata])
    return zeroed


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("WAN runtime requires a WAN_TIMELINE_PLAN dictionary.")
    if plan.get("type") != WAN_PLAN_TYPE:
        raise ValueError(f"WAN runtime expected plan type {WAN_PLAN_TYPE}, got {plan.get('type')!r}.")
    if plan.get("model_family") != WAN_MODEL_FAMILY or plan.get("model_version") != WAN_MODEL_VERSION:
        raise ValueError(
            f"WAN runtime expected model {WAN_MODEL_FAMILY} {WAN_MODEL_VERSION}, got {plan.get('model_family')} {plan.get('model_version')}."
        )
    validation = plan.get("validation") or {}
    if validation.get("is_valid") is False:
        codes = ", ".join(str(entry.get("code")) for entry in validation.get("errors", []))
        raise ValueError(f"WAN runtime cannot run an invalid timeline plan: {codes or 'unknown validation error'}.")


def _resolve_backend(value: Any) -> str:
    if value == "ComfyUI Core":
        return "ComfyUI Core"
    if value == "WanVideoWrapper":
        return "WanVideoWrapper"
    return "Plan Only"


def _backend_capabilities(backend: str) -> dict[str, Any]:
    if backend == "ComfyUI Core":
        return {
            "supports_start_image": True,
            "supports_end_image": True,
            "supports_timed_keyframes": False,
            "max_visual_keyframes": 2,
            "supports_prompt_relay": True,
            "supports_video_sections": False,
            "supports_audio_conditioning": False,
        }
    if backend == "WanVideoWrapper":
        return {
            "supports_start_image": None,
            "supports_end_image": None,
            "supports_timed_keyframes": None,
            "max_visual_keyframes": None,
            "supports_prompt_relay": None,
            "supports_video_sections": None,
            "supports_audio_conditioning": None,
        }
    return {
        "supports_start_image": None,
        "supports_end_image": None,
        "supports_timed_keyframes": None,
        "max_visual_keyframes": None,
        "supports_prompt_relay": None,
        "supports_video_sections": None,
        "supports_audio_conditioning": None,
    }


def _resolved_visual_conditioning(plan: dict[str, Any], capabilities: dict[str, Any]) -> dict[str, Any]:
    visual = deepcopy(plan.get("model_specific", {}).get("wan", {}).get("visual_conditioning") or {})
    requested = list(visual.get("requested_keyframes") or [])
    applied = []
    unsupported = []
    max_keyframes = capabilities.get("max_visual_keyframes")
    for keyframe in requested:
        role = keyframe.get("role")
        supported = (
            role == "Start" and capabilities.get("supports_start_image")
        ) or (
            role == "End" and capabilities.get("supports_end_image")
        ) or (
            role == "Timed" and capabilities.get("supports_timed_keyframes")
        )
        if supported and (max_keyframes is None or len(applied) < int(max_keyframes)):
            applied.append({**keyframe, "backend_role": role})
        else:
            reason = "Timed visual keyframes are not supported by the selected backend."
            if supported:
                reason = "Selected backend visual keyframe limit was exceeded."
            unsupported.append({**keyframe, "reason": reason})
    visual["applied_keyframes"] = applied
    visual["unsupported_keyframes"] = unsupported
    visual["backend_capabilities"] = capabilities
    return visual


def _apply_comfy_core_visual_keyframes(
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
    if not start_keyframe and not end_keyframe:
        diagnostics.append("No WAN visual keyframes were applied by the ComfyUI Core backend.")
        return positive, negative, latent, {"applied_keyframes": [], "unsupported_keyframes": visual.get("unsupported_keyframes", []), "diagnostics": diagnostics}

    start_image = _load_keyframe_image(start_keyframe, width, height) if start_keyframe else None
    end_image = _load_keyframe_image(end_keyframe, width, height) if end_keyframe else None
    concat_latent_image, concat_mask = _encode_first_last_images(
        vae,
        latent,
        width,
        height,
        frame_count,
        start_image,
        end_image,
    )
    values = {"concat_latent_image": concat_latent_image, "concat_mask": concat_mask}
    positive = _conditioning_set_values(positive, values)
    negative = _conditioning_set_values(negative, values)
    return positive, negative, latent, {
        "applied_keyframes": applied,
        "unsupported_keyframes": visual.get("unsupported_keyframes", []),
        "diagnostics": diagnostics,
    }


def _encode_first_last_images(vae, latent, width: int, height: int, frame_count: int, start_image, end_image):
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
    mask = mask.view(1, mask.shape[2] // 4, 4, mask.shape[3], mask.shape[4]).transpose(1, 2)
    return concat_latent_image, mask


def _load_keyframe_image(keyframe: dict[str, Any] | None, width: int, height: int):
    if not keyframe:
        return None
    path_value = keyframe.get("path")
    if not path_value:
        raise ValueError(f"WAN visual keyframe {keyframe.get('section_id')} is missing an image path.")
    path = resolve_media_path(path_value)
    if not Path(path).exists():
        raise ValueError(f"WAN visual keyframe image does not exist: {path}")
    with Image.open(path) as image:
        image = image.convert("RGB")
        array = np.array(image, dtype=np.float32) / 255.0
    tensor = torch.from_numpy(array).unsqueeze(0)
    return _resize_image_tensor(tensor, width, height)


def _resize_image_tensor(tensor: torch.Tensor, width: int, height: int) -> torch.Tensor:
    channels_first = tensor.movedim(-1, 1)
    resized = torch.nn.functional.interpolate(channels_first, size=(height, width), mode="bilinear", align_corners=False)
    return resized.movedim(1, -1)


def _conditioning_set_values(conditioning, values):
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


def _plain_prompt(prompt_relay: dict[str, Any]) -> str:
    parts = [str(prompt_relay.get("global_prompt") or "").strip()]
    parts.extend(
        str(segment.get("prompt") or "").strip()
        for segment in prompt_relay.get("local_prompts", [])
        if str(segment.get("prompt") or "").strip()
    )
    return ", ".join(part for part in parts if part)


def _resolve_negative_conditioning(negative, positive):
    return negative if negative is not None else zero_out_conditioning(positive)


def _call_or_value(obj, name: str, fallback):
    value = getattr(obj, name, None)
    if callable(value):
        return value()
    return value if value is not None else fallback


def _runtime_debug(
    plan: dict[str, Any],
    backend: str,
    capabilities: dict[str, Any],
    visual: dict[str, Any],
    prompt_debug: dict[str, Any],
    diagnostics: list[str],
) -> dict[str, Any]:
    config = plan.get("model_specific", {}).get("wan", {}).get("config", {})
    return {
        "type": "DEBUG_INFO",
        "source": "WAN Runtime",
        "enabled": config.get("debug_mode") != "Off",
        "mode": config.get("debug_mode", "Off"),
        "summary": {
            "backend": backend,
            "model_mode": config.get("model_mode"),
            "prompt_relay_patched": bool(prompt_debug.get("patched")),
            "requested_visual_keyframes": len(visual.get("requested_keyframes") or []),
            "applied_visual_keyframes": len(visual.get("applied_keyframes") or []),
            "unsupported_visual_keyframes": len(visual.get("unsupported_keyframes") or []),
            "video_frame_count": plan.get("resolved_output", {}).get("frame_count"),
            "latent_chunk_count": plan.get("resolved_output", {}).get("latent_chunk_count"),
        },
        "backend_capabilities": capabilities,
        "visual_conditioning": {
            "requested_keyframes": visual.get("requested_keyframes") or [],
            "applied_keyframes": visual.get("applied_keyframes") or [],
            "unsupported_keyframes": visual.get("unsupported_keyframes") or [],
        },
        "prompt_relay": prompt_debug,
        "diagnostics": diagnostics,
    }
