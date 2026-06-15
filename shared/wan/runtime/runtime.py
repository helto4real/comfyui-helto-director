from __future__ import annotations

from copy import deepcopy
from typing import Any

import torch

from ..config import WAN_MODEL_FAMILY, WAN_MODEL_VERSION
from ..planner import WAN_PLAN_TYPE
from .capabilities import (
    BACKEND_COMFYUI_CORE,
    BACKEND_PLAN_ONLY,
    BACKEND_WAN_VIDEO_WRAPPER,
    backend_capabilities,
    resolve_backend,
    resolve_visual_conditioning,
)
from .debug import build_runtime_debug, error, info, warning
from .prompt_relay import patch_wan_prompt_relay_models, prepare_wan_prompt_relay_payload, validate_segment_lengths
from .visual import apply_comfy_core_visual_keyframes


def build_wan_runtime_outputs(
    *,
    high_noise_model=None,
    low_noise_model=None,
    clip=None,
    vae=None,
    wan_timeline_plan: dict[str, Any],
    negative=None,
    batch_size: int = 1,
) -> tuple[Any, Any, Any, Any, dict[str, Any], dict[str, Any]]:
    plan = deepcopy(wan_timeline_plan)
    _validate_plan(plan)
    config = plan.get("model_specific", {}).get("wan", {}).get("config", {})
    requested_backend = str(config.get("runtime_backend_profile") or BACKEND_PLAN_ONLY)
    resolved_backend, backend_entries = resolve_backend(
        requested_backend,
        high_noise_model=high_noise_model,
        low_noise_model=low_noise_model,
        clip=clip,
        vae=vae,
    )
    capabilities = backend_capabilities(resolved_backend)
    visual = resolve_visual_conditioning(plan, capabilities, resolved_backend)
    prompt_relay = plan.get("model_specific", {}).get("wan", {}).get("prompt_relay", {})
    validation_entries = [_runtime_entry(entry) for entry in backend_entries]
    validation_entries.extend(_visual_validation_entries(visual, resolved_backend))
    diagnostics: list[str] = []
    media_decisions: list[dict[str, Any]] = []
    prompt_debug: dict[str, Any] = {"status": "not_built", "patched": False}
    model_patch_status: dict[str, Any] = {
        "high_noise_model": "not_connected" if high_noise_model is None else "unpatched",
        "low_noise_model": "not_connected" if low_noise_model is None else "unpatched",
    }
    width = int(plan["resolved_output"].get("width") or 1280)
    height = int(plan["resolved_output"].get("height") or 704)
    frame_count = int(plan["resolved_output"].get("frame_count") or 1)
    latent_spec = resolve_wan_latent_spec(
        high_noise_model=high_noise_model,
        low_noise_model=low_noise_model,
        vae=vae,
    )

    try:
        validate_segment_lengths(prompt_relay)
    except ValueError as exc:
        validation_entries.append(error(
            "WAN_PROMPT_RELAY_SEGMENT_LENGTH_MISMATCH",
            str(exc),
            "Regenerate the WAN plan before running the runtime.",
        ))
        raise

    if resolved_backend == BACKEND_PLAN_ONLY:
        validation_entries.append(info(
            "WAN_RUNTIME_BACKEND_PLAN_ONLY",
            "WAN Runtime is in Plan Only mode; no conditioning execution was performed.",
            "Switch Runtime Backend Profile to ComfyUI Core to materialize conditioning.",
        ))
        diagnostics.append("WAN runtime backend is Plan Only; no conditioning execution was performed.")
        video_latent = empty_wan22_video_latent(width, height, frame_count, batch_size, latent_spec)
        runtime_debug = build_runtime_debug(
            plan=plan,
            requested_backend=requested_backend,
            resolved_backend=resolved_backend,
            capabilities=capabilities,
            visual=visual,
            prompt_debug=prompt_debug,
            validation_entries=validation_entries,
            diagnostics=diagnostics,
            media_decisions=media_decisions,
            model_patch_status=model_patch_status,
        )
        return high_noise_model, low_noise_model, [], negative if negative is not None else [], video_latent, runtime_debug

    if resolved_backend == BACKEND_WAN_VIDEO_WRAPPER:
        validation_entries.append(error(
            "WAN_RUNTIME_BACKEND_NOT_AVAILABLE",
            "WAN Runtime backend WanVideoWrapper is not available in this nodepack.",
            "Use Plan Only or ComfyUI Core.",
        ))
        raise ValueError("WAN_RUNTIME_BACKEND_NOT_AVAILABLE: WanVideoWrapper is not available in this nodepack.")

    _validate_comfy_core_inputs(clip, vae, prompt_relay, high_noise_model, low_noise_model, validation_entries)

    runtime_high_model = high_noise_model
    runtime_low_model = low_noise_model
    prompt_debug, positive, runtime_high_model, runtime_low_model = _build_prompt_payload_and_patch_models(
        clip,
        prompt_relay,
        high_noise_model,
        low_noise_model,
        model_patch_status,
        validation_entries,
    )
    runtime_negative = _resolve_negative_conditioning(negative, positive)
    video_latent = empty_wan22_video_latent(width, height, frame_count, batch_size, latent_spec)
    positive, runtime_negative, video_latent, guide_debug = apply_comfy_core_visual_keyframes(
        positive,
        runtime_negative,
        vae,
        video_latent,
        visual,
        width,
        height,
        frame_count,
    )
    diagnostics.extend(guide_debug.get("diagnostics", []))
    media_decisions.extend(guide_debug.get("media_decisions", []))
    validation_entries.append(info(
        "WAN_VISUAL_KEYFRAME_RUNTIME_PAYLOAD_BUILT",
        "Built WAN visual keyframe runtime payload for the selected backend.",
        "Inspect runtime_debug.visual_conditioning for applied and unsupported keyframes.",
    ))
    runtime_debug = build_runtime_debug(
        plan=plan,
        requested_backend=requested_backend,
        resolved_backend=resolved_backend,
        capabilities=capabilities,
        visual=visual,
        prompt_debug=prompt_debug,
        validation_entries=validation_entries,
        diagnostics=diagnostics,
        media_decisions=media_decisions,
        model_patch_status=model_patch_status,
    )
    return runtime_high_model, runtime_low_model, positive, runtime_negative, video_latent, runtime_debug


def resolve_wan_latent_spec(*, high_noise_model=None, low_noise_model=None, vae=None) -> dict[str, Any]:
    latent_format = _model_latent_format(high_noise_model) or _model_latent_format(low_noise_model)
    if latent_format is not None:
        return {
            "channels": int(getattr(latent_format, "latent_channels", 16) or 16),
            "spatial_scale": int(getattr(latent_format, "spacial_downscale_ratio", 8) or 8),
            "source": "model_latent_format",
        }
    if vae is not None:
        return {
            "channels": int(getattr(vae, "latent_channels", 16) or 16),
            "spatial_scale": int(_call_or_value(vae, "spacial_compression_encode", 8) or 8),
            "source": "vae",
        }
    return {
        "channels": 16,
        "spatial_scale": 8,
        "source": "wan_default",
    }


def empty_wan22_video_latent(
    width: int,
    height: int,
    frame_count: int,
    batch_size: int = 1,
    latent_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        import comfy.model_management

        device = comfy.model_management.intermediate_device()
    except Exception:
        device = "cpu"
    latent_spec = latent_spec or {"channels": 16, "spatial_scale": 8}
    latent_channels = int(latent_spec.get("channels") or 16)
    spatial_scale = int(latent_spec.get("spatial_scale") or 8)
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


def _model_latent_format(model):
    if model is None or not hasattr(model, "get_model_object"):
        return None
    try:
        return model.get_model_object("latent_format")
    except Exception:
        return None


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


def _validate_comfy_core_inputs(clip, vae, prompt_relay, high_noise_model, low_noise_model, validation_entries):
    missing = []
    if clip is None:
        missing.append("clip")
    if vae is None:
        missing.append("vae")
    if missing:
        validation_entries.append(error(
            "WAN_RUNTIME_REQUIRED_INPUT_MISSING",
            f"ComfyUI Core WAN runtime requires {', '.join(missing)}.",
            "Connect the missing backend input(s), or switch Runtime Backend Profile to Plan Only.",
            {"missing": missing},
        ))
        raise ValueError(f"WAN_RUNTIME_REQUIRED_INPUT_MISSING: ComfyUI Core WAN runtime requires {', '.join(missing)}.")
    if prompt_relay.get("enabled", True) and high_noise_model is None and low_noise_model is None:
        validation_entries.append(error(
            "WAN_RUNTIME_REQUIRED_INPUT_MISSING",
            "WAN Prompt Relay requires at least one connected high or low noise model.",
            "Connect high_noise_model and/or low_noise_model, disable Prompt Relay, or switch Runtime Backend Profile to Plan Only.",
        ))
        raise ValueError("WAN_RUNTIME_REQUIRED_INPUT_MISSING: WAN Prompt Relay requires at least one connected high or low noise model.")


def _build_prompt_payload_and_patch_models(
    clip,
    prompt_relay,
    high_noise_model,
    low_noise_model,
    model_patch_status: dict[str, Any],
    validation_entries: list[dict[str, Any]],
):
    if prompt_relay.get("enabled", True):
        try:
            positive, prompt_debug, mask_fn = prepare_wan_prompt_relay_payload(clip, prompt_relay)
            if mask_fn is None:
                prompt_debug["status"] = "plain_prompt"
                return prompt_debug, positive, high_noise_model, low_noise_model
            patched_high, patched_low = patch_wan_prompt_relay_models(high_noise_model, low_noise_model, mask_fn)
        except Exception as exc:
            validation_entries.append(error(
                "WAN_RUNTIME_PROMPT_RELAY_PATCH_UNAVAILABLE",
                f"WAN Prompt Relay patching failed: {exc}",
                "Use compatible WAN models or switch Prompt Routing off.",
            ))
            if isinstance(exc, ValueError) and str(exc).startswith("WAN_RUNTIME_PROMPT_RELAY_PATCH_UNAVAILABLE"):
                raise
            raise ValueError(f"WAN_RUNTIME_PROMPT_RELAY_PATCH_UNAVAILABLE: {exc}") from exc
        prompt_debug["patched"] = True
        prompt_debug["status"] = "patched"
        validation_entries.append(info(
            "WAN_PROMPT_RELAY_RUNTIME_PAYLOAD_BUILT",
            "Built WAN Prompt Relay runtime payload.",
            "Both connected WAN model phases are patched independently.",
        ))
        if high_noise_model is not None:
            model_patch_status["high_noise_model"] = "patched"
        else:
            validation_entries.append(warning(
                "WAN_RUNTIME_REQUIRED_INPUT_MISSING",
                "high_noise_model is not connected; only the low noise model was patched.",
                "Connect high_noise_model for a complete dual-model WAN 2.2 workflow.",
            ))
        if low_noise_model is not None:
            model_patch_status["low_noise_model"] = "patched"
        else:
            validation_entries.append(warning(
                "WAN_RUNTIME_REQUIRED_INPUT_MISSING",
                "low_noise_model is not connected; only the high noise model was patched.",
                "Connect low_noise_model for a complete dual-model WAN 2.2 workflow.",
            ))
        return prompt_debug, positive, patched_high, patched_low

    prompt = _plain_prompt(prompt_relay)
    positive = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
    prompt_debug = {
        "full_prompt": prompt,
        "local_prompts": [],
        "token_ranges": [],
        "patched": False,
        "status": "merged_prompt_fallback",
    }
    validation_entries.append(warning(
        "WAN_RUNTIME_USING_MERGED_PROMPT_FALLBACK",
        "WAN Prompt Relay is disabled; runtime used a merged prompt fallback.",
        "Enable Prompt Relay in WAN Config for temporal prompt routing.",
    ))
    return prompt_debug, positive, high_noise_model, low_noise_model


def _visual_validation_entries(visual: dict[str, Any], backend: str) -> list[dict[str, Any]]:
    entries = []
    unsupported = visual.get("unsupported_keyframes") or []
    if not unsupported:
        return entries
    if backend == BACKEND_PLAN_ONLY:
        entries.append(info(
            "WAN_RUNTIME_BACKEND_PLAN_ONLY",
            "Plan Only preserved requested visual keyframes without applying them.",
            "Switch to ComfyUI Core to apply supported Start/End keyframes.",
        ))
        return entries
    timed = [entry for entry in unsupported if entry.get("role") == "Timed"]
    if timed:
        entries.append(warning(
            "WAN_TIMED_KEYFRAME_UNSUPPORTED_BY_BACKEND",
            "Timed visual keyframes are unsupported by the selected WAN backend.",
            "They remain visible in runtime_debug.unsupported_keyframes.",
            {"count": len(timed)},
        ))
    other = [entry for entry in unsupported if entry.get("role") != "Timed"]
    if other:
        entries.append(warning(
            "WAN_VISUAL_KEYFRAME_UNSUPPORTED_BY_BACKEND",
            "Some visual keyframes are unsupported by the selected WAN backend.",
            "Inspect runtime_debug.visual_conditioning.unsupported_keyframes.",
            {"count": len(other)},
        ))
    return entries


def _runtime_entry(entry: dict[str, Any]) -> dict[str, Any]:
    severity = entry.get("severity", "Info")
    code = entry.get("code", "WAN_RUNTIME_INFO")
    message = entry.get("message", "")
    details = entry.get("details", {})
    if severity == "Warning":
        return warning(code, message, details=details)
    if severity == "Error":
        return error(code, message, details=details)
    return info(code, message, details=details)


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
