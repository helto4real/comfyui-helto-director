from __future__ import annotations

from copy import deepcopy
from typing import Any

import torch

from ...contracts.video_timeline import (
    MODEL_LORA_MODEL_WAN_2_2,
    MODEL_LORA_TARGET_HIGH_NOISE,
    MODEL_LORA_TARGET_LOW_NOISE,
    VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
)
from ..config import WAN_MODEL_FAMILY, WAN_MODEL_VERSION
from ..planner import WAN_PLAN_TYPE
from ..bernini import BERNINI_MODEL_MODE
from .bernini import bernini_visual_debug, build_bernini_runtime_payload
from .capabilities import (
    BACKEND_COMFYUI_CORE,
    BACKEND_FMLF_ADVANCED_I2V,
    BACKEND_PLAN_ONLY,
    BACKEND_WAN_VIDEO_WRAPPER,
    backend_capabilities,
    resolve_backend,
    resolve_visual_conditioning,
)
from .debug import build_runtime_debug, error, info, warning
from .continuity import apply_wan_previous_tail_continuity
from .fmlf import build_fmlf_advanced_i2v_payload
from .prompt_relay import patch_wan_prompt_relay_models, prepare_wan_prompt_relay_payload, validate_segment_lengths
from .visual import apply_comfy_core_visual_keyframes, resize_image_tensor
from ...timeline_status import TimelineStatusReporter, ensure_timeline_status_reporter
from ...lora import apply_lora_config_model_only
from ...timeline.planner_context import (
    create_resolved_lora_snapshot,
    resolve_runtime_lora_targets,
)
from ...timeline.take_capture import build_take_capture_metadata
from ...timeline import generation_policy_skips_generation


def build_wan_runtime_outputs(
    *,
    high_noise_model=None,
    low_noise_model=None,
    clip=None,
    vae=None,
    wan_timeline_plan: dict[str, Any],
    negative=None,
    batch_size: int = 1,
    status_reporter: TimelineStatusReporter | None = None,
    complete_status: bool = True,
    split_conditioning: bool = False,
    fmlf_prev_latent: dict[str, Any] | None = None,
    fmlf_motion_frames: torch.Tensor | None = None,
    fmlf_video_frame_offset: int = 0,
) -> tuple[Any, Any, Any, Any, dict[str, Any], dict[str, Any]]:
    status_reporter = ensure_timeline_status_reporter(status_reporter, model="wan", total=4)
    status_reporter.report("timeline.prepare", "WAN Runtime: resolving backend")
    plan = deepcopy(wan_timeline_plan)
    _validate_plan(plan)
    generation_policy = plan.get("model_specific", {}).get("wan", {}).get("generation_policy")
    if generation_policy_skips_generation(generation_policy):
        return _build_skipped_wan_runtime_outputs(
            high_noise_model=high_noise_model,
            low_noise_model=low_noise_model,
            negative=negative,
            plan=plan,
            generation_policy=generation_policy,
            batch_size=batch_size,
            status_reporter=status_reporter,
            complete_status=complete_status,
        )
    config = plan.get("model_specific", {}).get("wan", {}).get("config", {})
    is_bernini = str(config.get("model_mode") or "") == BERNINI_MODEL_MODE
    requested_backend = str(config.get("runtime_backend_profile") or BACKEND_PLAN_ONLY)
    resolved_backend, backend_entries = resolve_backend(
        requested_backend,
        high_noise_model=high_noise_model,
        low_noise_model=low_noise_model,
        clip=clip,
        vae=vae,
    )
    capabilities = backend_capabilities(resolved_backend)
    if is_bernini and resolved_backend == BACKEND_COMFYUI_CORE:
        capabilities = {
            **capabilities,
            "supports_bernini": True,
            "supports_video_sections": True,
            "supports_start_image": True,
            "supports_end_image": False,
            "supports_timed_keyframes": False,
            "max_visual_keyframes": 1,
        }
    width = int(plan["resolved_output"].get("width") or 1280)
    height = int(plan["resolved_output"].get("height") or 704)
    frame_count = int(plan["resolved_output"].get("frame_count") or 1)
    diagnostics: list[str] = []
    validation_entries = [_runtime_entry(entry) for entry in backend_entries]
    if resolved_backend != BACKEND_PLAN_ONLY:
        _apply_wan_boundary_conditioning(plan, width, height, diagnostics, validation_entries)
    else:
        _mark_wan_boundary_plan_only(plan)
    visual = resolve_visual_conditioning(plan, capabilities, resolved_backend)
    if is_bernini:
        visual = bernini_visual_debug(plan, visual)
    prompt_relay = plan.get("model_specific", {}).get("wan", {}).get("prompt_relay", {})
    validation_entries.extend(_visual_validation_entries(visual, resolved_backend))
    runtime_loras, lora_diagnostics = _resolve_wan_loras(plan)
    diagnostics.extend(lora_diagnostics)
    lora_report = _build_wan_lora_report(
        runtime_loras,
        applied_by_target={},
        high_noise_model=high_noise_model,
        low_noise_model=low_noise_model,
    )
    media_decisions: list[dict[str, Any]] = []
    prompt_debug: dict[str, Any] = {"status": "not_built", "patched": False}
    model_patch_status: dict[str, Any] = {
        "high_noise_model": "not_connected" if high_noise_model is None else "unpatched",
        "low_noise_model": "not_connected" if low_noise_model is None else "unpatched",
    }
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
        status_reporter.report("timeline.conditioning", "WAN Runtime: preparing plan-only latent")
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
            status_events=status_reporter.snapshot(),
            take_registration=_build_wan_take_registration(
                plan,
                lora_report,
                source="WAN Runtime",
                backend=resolved_backend,
            ),
        )
        _attach_wan_lora_debug(runtime_debug, lora_report)
        if complete_status:
            status_reporter.done("WAN Runtime: ready")
            runtime_debug["status_events"] = status_reporter.snapshot()
        return high_noise_model, low_noise_model, [], negative if negative is not None else [], video_latent, runtime_debug

    if resolved_backend == BACKEND_WAN_VIDEO_WRAPPER:
        validation_entries.append(error(
            "WAN_RUNTIME_BACKEND_NOT_AVAILABLE",
            "WAN Runtime backend WanVideoWrapper is not available in this nodepack.",
            "Use Plan Only or ComfyUI Core.",
        ))
        raise ValueError("WAN_RUNTIME_BACKEND_NOT_AVAILABLE: WanVideoWrapper is not available in this nodepack.")

    if is_bernini:
        _validate_bernini_user_conditioning(plan, validation_entries)
    if resolved_backend == BACKEND_FMLF_ADVANCED_I2V:
        _validate_fmlf_inputs(config, clip, vae, prompt_relay, high_noise_model, low_noise_model, validation_entries)
    else:
        _validate_comfy_core_inputs(clip, vae, prompt_relay, high_noise_model, low_noise_model, validation_entries)
        _validate_comfy_core_visual_requirements(config, visual, validation_entries)

    runtime_high_model = high_noise_model
    runtime_low_model = low_noise_model
    runtime_high_model, runtime_low_model, lora_report, lora_apply_diagnostics = _apply_wan_timeline_loras(
        runtime_loras,
        high_noise_model=runtime_high_model,
        low_noise_model=runtime_low_model,
    )
    diagnostics.extend(lora_apply_diagnostics)
    status_reporter.report("timeline.prompt", "WAN Runtime: building prompt relay")
    prompt_debug, positive, runtime_high_model, runtime_low_model = _build_prompt_payload_and_patch_models(
        clip,
        prompt_relay,
        runtime_high_model,
        runtime_low_model,
        model_patch_status,
        validation_entries,
    )
    if is_bernini and not prompt_relay.get("enabled", True):
        validation_entries.append(warning(
            "BERNINI_PROMPT_RELAY_DISABLED",
            "Bernini is running with Prompt Relay disabled; timeline prompts were merged into one prompt.",
            "Enable Prompt Relay when comparing against manual Bernini workflows that rely on temporal prompt routing.",
        ))
    runtime_negative = _resolve_negative_conditioning(negative, positive)
    fmlf_debug = None
    if is_bernini:
        status_reporter.report("bernini.conditioning", "WAN Runtime: building Bernini conditioning")
        positive, runtime_negative, video_latent, guide_debug = build_bernini_runtime_payload(
            positive,
            runtime_negative,
            vae,
            plan,
            batch_size,
            latent_spec,
            prompt_debug=prompt_debug,
        )
        _append_bernini_source_aspect_warnings(guide_debug, validation_entries)
    elif resolved_backend == BACKEND_FMLF_ADVANCED_I2V:
        status_reporter.report("timeline.conditioning", "WAN Runtime: building FMLF Advanced I2V conditioning")
        positive_high, positive_low, runtime_negative, video_latent, trim_latent, trim_image, next_offset, guide_debug = build_fmlf_advanced_i2v_payload(
            positive,
            runtime_negative,
            vae,
            visual,
            width,
            height,
            frame_count,
            batch_size,
            latent_spec,
            config,
            prev_latent=fmlf_prev_latent,
            motion_frames=fmlf_motion_frames,
            video_frame_offset=fmlf_video_frame_offset,
        )
        fmlf_debug = {
            **guide_debug,
            "trim_latent": int(trim_latent),
            "trim_image": int(trim_image),
            "next_offset": int(next_offset),
        }
        positive = (
            {
                "high": positive_high,
                "low": positive_low,
                "default": positive_high,
                "_helto_wan_conditioning_split": True,
            }
            if split_conditioning
            else positive_high
        )
    else:
        status_reporter.report("timeline.conditioning", "WAN Runtime: applying visual conditioning")
        positive, runtime_negative, video_latent, guide_debug = apply_comfy_core_visual_keyframes(
            positive,
            runtime_negative,
            vae,
            visual,
            width,
            height,
            frame_count,
            batch_size,
            latent_spec,
            str(config.get("model_mode") or "I2V-A14B"),
            config,
        )
    if "applied_keyframes" in guide_debug or "unsupported_keyframes" in guide_debug:
        visual = {
            **visual,
            "applied_keyframes": guide_debug.get("applied_keyframes", visual.get("applied_keyframes") or []),
            "unsupported_keyframes": guide_debug.get("unsupported_keyframes", visual.get("unsupported_keyframes") or []),
        }
    diagnostics.extend(guide_debug.get("diagnostics", []))
    media_decisions.extend(guide_debug.get("media_decisions", []))
    if guide_debug.get("painter_motion_boost") is not None:
        visual = {
            **visual,
            "painter_motion_boost": guide_debug.get("painter_motion_boost"),
        }
    if guide_debug.get("core_helper") and not any(
        decision.get("helper") == guide_debug["core_helper"]
        for decision in media_decisions
    ):
        media_decisions.append({
            "type": "comfy_core_helper",
            "helper": guide_debug["core_helper"],
            "output_payload_type": "COMFYUI_CORE_CONDITIONING_LATENT",
        })
    if guide_debug.get("helper") == "FMLF Advanced I2V":
        media_decisions.append({
            "type": "fmlf_advanced_i2v_helper",
            "helper": "FMLF Advanced I2V",
            "output_payload_type": "FMLF_ADVANCED_I2V_CONDITIONING_LATENT",
        })
    if is_bernini:
        payload_code = "BERNINI_RUNTIME_PAYLOAD_BUILT"
        payload_message = "Built Bernini runtime conditioning payload for the selected backend."
        payload_hint = "Inspect runtime_debug.bernini and runtime_debug.visual_conditioning for applied and deferred media."
    elif resolved_backend == BACKEND_FMLF_ADVANCED_I2V:
        payload_code = "WAN_FMLF_ADVANCED_I2V_PAYLOAD_BUILT"
        payload_message = "Built FMLF Advanced I2V runtime conditioning payload for the selected backend."
        payload_hint = "Inspect runtime_debug.fmlf_advanced_i2v for continuation and high/low conditioning details."
    else:
        payload_code = "WAN_VISUAL_KEYFRAME_RUNTIME_PAYLOAD_BUILT"
        payload_message = "Built WAN visual keyframe runtime payload for the selected backend."
        payload_hint = "Inspect runtime_debug.visual_conditioning for applied and unsupported keyframes."
    validation_entries.append(info(payload_code, payload_message, payload_hint))
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
        status_events=status_reporter.snapshot(),
        fmlf_debug=fmlf_debug,
        take_registration=_build_wan_take_registration(
            plan,
            lora_report,
            source="WAN Runtime",
            backend=resolved_backend,
        ),
    )
    _attach_wan_lora_debug(runtime_debug, lora_report)
    if complete_status:
        status_reporter.done("WAN Runtime: ready")
        runtime_debug["status_events"] = status_reporter.snapshot()
    return runtime_high_model, runtime_low_model, positive, runtime_negative, video_latent, runtime_debug


def _build_skipped_wan_runtime_outputs(
    *,
    high_noise_model=None,
    low_noise_model=None,
    negative=None,
    plan: dict[str, Any],
    generation_policy: dict[str, Any] | None,
    batch_size: int,
    status_reporter: TimelineStatusReporter,
    complete_status: bool,
) -> tuple[Any, Any, Any, Any, dict[str, Any], dict[str, Any]]:
    width = int(plan["resolved_output"].get("width") or 1280)
    height = int(plan["resolved_output"].get("height") or 704)
    frame_count = int(plan["resolved_output"].get("frame_count") or 1)
    frame_rate = float(plan["resolved_output"].get("frame_rate") or 24.0)
    latent_spec = {"channels": 16, "spatial_scale": 8, "source": "wan_default"}
    video_latent = empty_wan22_video_latent(width, height, frame_count, batch_size, latent_spec)
    status_reporter.report("timeline.skip", "WAN Runtime: generation skipped by Director policy")
    if complete_status:
        status_reporter.done("WAN Runtime: generation skipped")
    runtime_debug = {
        "type": "DEBUG_INFO",
        "source": "WAN Runtime",
        "enabled": True,
        "summary": {
            "generation_required": False,
            "generation_status": generation_policy.get("status") if isinstance(generation_policy, dict) else "skipped",
            "generation_skip_reason": generation_policy.get("skip_reason") if isinstance(generation_policy, dict) else None,
            "generation_mode": generation_policy.get("mode") if isinstance(generation_policy, dict) else None,
            "generation_target_shot_id": generation_policy.get("target_shot_id") if isinstance(generation_policy, dict) else None,
            "take_registration_ready": False,
            "take_registration_shot_ids": [],
            "video_latent_shape": tuple(video_latent["samples"].shape),
            "frame_rate": frame_rate,
        },
        "generation_policy": deepcopy(generation_policy),
        "diagnostics": ["Generation skipped; no WAN backend, prompt, visual conditioning, or LoRA work was performed."],
        "status_events": status_reporter.snapshot(),
    }
    return high_noise_model, low_noise_model, [], negative if negative is not None else [], video_latent, runtime_debug


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


def _apply_wan_boundary_conditioning(
    plan: dict[str, Any],
    width: int,
    height: int,
    diagnostics: list[str],
    validation_entries: list[dict[str, Any]],
) -> None:
    wan = plan.get("model_specific", {}).get("wan", {})
    boundary = wan.get("boundary_conditioning")
    if not isinstance(boundary, dict) or boundary.get("model_status") != "applied":
        return
    if not _active_segment_allows_boundary_conditioning(wan):
        boundary["runtime_status"] = "skipped_segment"
        wan["boundary_conditioning_runtime"] = _safe_boundary_runtime_debug(boundary)
        return
    try:
        tail, metadata = _decode_wan_boundary_tail_frames(boundary, width, height)
    except Exception as exc:
        message = f"WAN boundary conditioning could not decode the previous clip tail: {exc}"
        boundary.update(
            {
                "model_status": "unavailable",
                "status": "unavailable",
                "runtime_status": "unavailable",
                "fallback_reason": "runtime_boundary_tail_decode_failed",
                "message": message,
            }
        )
        wan["boundary_conditioning_runtime"] = _safe_boundary_runtime_debug(boundary)
        validation_entries.append(warning(
            "WAN_BOUNDARY_CONDITIONING_UNAVAILABLE",
            message,
            "Generate normally, or verify the previous accepted/imported clip still exists and can be decoded.",
            _safe_boundary_runtime_debug(boundary),
        ))
        diagnostics.append(message)
        return

    boundary.update(
        {
            **metadata,
            "runtime_status": "applied",
            "selected_frame_count": int(tail.shape[0]),
            "message": "WAN runtime applied previous-tail boundary conditioning.",
        }
    )
    media_item_id = _boundary_conditioning_media_item_id(boundary)
    apply_wan_previous_tail_continuity(
        plan,
        tail,
        media_item_id=media_item_id,
        source="boundary",
        boundary_conditioning=boundary,
    )
    wan["boundary_conditioning_runtime"] = _safe_boundary_runtime_debug(boundary)
    diagnostics.append(
        "WAN boundary conditioning used "
        f"{int(tail.shape[0])} previous-tail frame(s) as transient start guidance."
    )


def _mark_wan_boundary_plan_only(plan: dict[str, Any]) -> None:
    wan = plan.get("model_specific", {}).get("wan", {})
    boundary = wan.get("boundary_conditioning")
    if not isinstance(boundary, dict) or boundary.get("model_status") != "applied":
        return
    boundary["runtime_status"] = "not_executed"
    wan["boundary_conditioning_runtime"] = _safe_boundary_runtime_debug(boundary)


def _active_segment_allows_boundary_conditioning(wan: dict[str, Any]) -> bool:
    segment = wan.get("active_generation_segment")
    if not isinstance(segment, dict):
        return True
    try:
        start_frame = int(segment.get("start_frame") or 0)
    except (TypeError, ValueError):
        start_frame = 0
    return start_frame <= 0


def _decode_wan_boundary_tail_frames(
    boundary: dict[str, Any],
    width: int,
    height: int,
) -> tuple[torch.Tensor, dict[str, Any]]:
    try:
        from ...ltx.runtime.media import decode_video_frames, select_video_guidance_range, trim_video_source_frames
    except Exception as exc:
        raise ValueError("PyAV video decoding support is required for WAN boundary conditioning.") from exc
    media = {
        "item_id": _boundary_conditioning_media_item_id(boundary),
        "path": boundary.get("path"),
        "source_in": boundary.get("source_in"),
        "source_out": boundary.get("source_out"),
        "video_guidance_frame_count": int(boundary.get("effective_tail_frames") or 1),
        "video_guidance_range": VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
    }
    decoded, source_fps, decoded_count = decode_video_frames(str(media.get("path") or ""))
    trimmed, trim_metadata = trim_video_source_frames(decoded, source_fps, media)
    guidance_frames, guidance_metadata = select_video_guidance_range(trimmed, media, trim_metadata)
    selected = _wan_compatible_video_tail_frames(guidance_frames)
    if int(selected.shape[1]) != height or int(selected.shape[2]) != width:
        selected = resize_image_tensor(selected, width, height)
    return selected, {
        "source_fps": float(source_fps),
        "decoded_frame_count": int(decoded_count),
        "trimmed_frame_count": int(trim_metadata["trimmed_frame_count"]),
        "selected_frame_count": int(selected.shape[0]),
        "source_range": trim_metadata["source_range"],
        **guidance_metadata,
        "tensor_shape": _tensor_shape(selected),
        "tensor_stats": _tensor_stats(selected),
    }


def _wan_compatible_video_tail_frames(frames: torch.Tensor) -> torch.Tensor:
    keep = ((max(1, int(frames.shape[0])) - 1) // 4) * 4 + 1
    return frames[-keep:]


def _boundary_conditioning_media_item_id(boundary: dict[str, Any]) -> str:
    return f"boundary_tail_{str(boundary.get('boundary_id') or boundary.get('asset_id') or 'incoming')}"


def _safe_boundary_runtime_debug(boundary: dict[str, Any]) -> dict[str, Any]:
    safe_keys = {
        "type",
        "mode",
        "policy",
        "model_status",
        "runtime_status",
        "status",
        "source_status",
        "boundary_id",
        "source_shot_id",
        "target_shot_id",
        "asset_id",
        "asset_type",
        "source_kind",
        "take_id",
        "requested_tail_frames",
        "effective_tail_frames",
        "selected_frame_count",
        "blend_frames",
        "transition_prompt_applied",
        "fallback_reason",
        "source_fps",
        "decoded_frame_count",
        "trimmed_frame_count",
        "source_range",
        "guidance_range",
        "guidance_frame_count",
        "guidance_source_range",
        "tensor_shape",
        "tensor_stats",
        "message",
    }
    return {
        key: deepcopy(boundary.get(key))
        for key in sorted(safe_keys)
        if key in boundary
    }


def _tensor_shape(tensor) -> list[int]:
    return [int(dim) for dim in tensor.shape] if hasattr(tensor, "shape") else []


def _tensor_stats(tensor) -> dict[str, float]:
    if not torch.is_tensor(tensor):
        return {}
    detached = tensor.detach().float()
    return {
        "min": float(detached.min().item()),
        "max": float(detached.max().item()),
        "mean": float(detached.mean().item()),
    }


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


def _validate_fmlf_inputs(config: dict[str, Any], clip, vae, prompt_relay, high_noise_model, low_noise_model, validation_entries):
    model_mode = str(config.get("model_mode") or "I2V-A14B")
    if model_mode != "I2V-A14B":
        validation_entries.append(error(
            "WAN_FMLF_UNSUPPORTED_MODEL_MODE",
            f"FMLF Advanced I2V supports I2V-A14B only, got {model_mode}.",
            "Select WAN model mode I2V-A14B or switch Runtime Backend Profile to ComfyUI Core.",
            {"model_mode": model_mode},
        ))
        raise ValueError(f"WAN_FMLF_UNSUPPORTED_MODEL_MODE: FMLF Advanced I2V supports I2V-A14B only, got {model_mode}.")
    missing = []
    if clip is None:
        missing.append("clip")
    if vae is None:
        missing.append("vae")
    if missing:
        validation_entries.append(error(
            "WAN_FMLF_REQUIRED_INPUT_MISSING",
            f"FMLF Advanced I2V runtime requires {', '.join(missing)}.",
            "Connect the missing backend input(s), or switch Runtime Backend Profile to Plan Only.",
            {"missing": missing},
        ))
        raise ValueError(f"WAN_FMLF_REQUIRED_INPUT_MISSING: FMLF Advanced I2V runtime requires {', '.join(missing)}.")
    if prompt_relay.get("enabled", True) and high_noise_model is None and low_noise_model is None:
        validation_entries.append(error(
            "WAN_FMLF_REQUIRED_INPUT_MISSING",
            "FMLF Advanced I2V Prompt Relay requires at least one connected high or low noise model.",
            "Connect high_noise_model and/or low_noise_model, disable Prompt Relay, or switch Runtime Backend Profile to Plan Only.",
        ))
        raise ValueError("WAN_FMLF_REQUIRED_INPUT_MISSING: FMLF Advanced I2V Prompt Relay requires at least one connected high or low noise model.")
    if high_noise_model is None or low_noise_model is None:
        validation_entries.append(warning(
            "WAN_FMLF_TWO_PHASE_MODEL_PAIR_RECOMMENDED",
            "FMLF Advanced I2V A14B segmented sampling expects both high_noise_model and low_noise_model.",
            "Connect both model phases before using the segmented executor.",
        ))


def _validate_comfy_core_visual_requirements(config: dict[str, Any], visual: dict[str, Any], validation_entries):
    model_mode = str(config.get("model_mode") or "I2V-A14B")
    if model_mode != "I2V-A14B":
        return
    has_start_keyframe = any(entry.get("role") == "Start" for entry in visual.get("applied_keyframes") or [])
    if has_start_keyframe:
        return
    if _has_previous_tail_start_conditioning(visual):
        validation_entries.append(info(
            "WAN_CONTINUATION_TAIL_IMAGE_CONDITIONING",
            "WAN I2V continuation image conditioning is satisfied by the previous segment tail.",
            "Inspect runtime_debug.visual_conditioning.media_decisions for segment_previous_tail.",
        ))
        return
    validation_entries.append(error(
        "WAN_REQUIRED_IMAGE_CONDITIONING_MISSING",
        "WAN 2.2 I2V-A14B ComfyUI Core execution requires at least one usable Image Section keyframe.",
        "Add an Image Section to the timeline, or select a text-capable WAN model mode before executing.",
    ))
    raise ValueError(
        "WAN_REQUIRED_IMAGE_CONDITIONING_MISSING: WAN 2.2 I2V-A14B ComfyUI Core execution requires at least one usable Image Section keyframe."
    )


def _has_previous_tail_start_conditioning(visual: dict[str, Any]) -> bool:
    if visual.get("continuation_source") != "previous_tail":
        return False
    transient_start = visual.get("transient_start_image")
    if transient_start is None or not hasattr(transient_start, "shape"):
        return False
    try:
        return int(transient_start.shape[0]) > 0
    except Exception:
        return False


def _validate_bernini_user_conditioning(plan: dict[str, Any], validation_entries: list[dict[str, Any]]) -> None:
    bernini = plan.get("model_specific", {}).get("wan", {}).get("bernini") or {}
    if not bernini.get("enabled") or bernini.get("has_user_conditioning"):
        return
    details = {
        "timeline_image_count": bernini.get("timeline_image_count"),
        "timeline_video_count": bernini.get("timeline_video_count"),
        "timeline_prompt_count": bernini.get("timeline_prompt_count"),
        "reference_image_count": bernini.get("reference_image_count"),
        "has_user_prompt_text": bernini.get("has_user_prompt_text"),
        "has_media_conditioning": bernini.get("has_media_conditioning"),
        "has_reference_conditioning": bernini.get("has_reference_conditioning"),
    }
    validation_entries.append(error(
        "BERNINI_NO_USER_CONDITIONING",
        "Bernini received no user prompt, no timeline media, and no character reference images.",
        "Add a Text Section, Image Section, Video Section, character reference tag, or verify the Director timeline is connected/serialized.",
        details,
    ))
    raise ValueError(
        "BERNINI_NO_USER_CONDITIONING: Bernini received no user prompt, no timeline media, and no character reference images. "
        "Add a Text Section, Image Section, Video Section, character reference tag, or verify the Director timeline is connected/serialized."
    )


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


def _append_bernini_source_aspect_warnings(
    guide_debug: dict[str, Any],
    validation_entries: list[dict[str, Any]],
) -> None:
    for decision in guide_debug.get("media_decisions") or []:
        if decision.get("bernini_role") != "source_video_single_frame" or not decision.get("aspect_mismatch"):
            continue
        validation_entries.append(warning(
            "BERNINI_SOURCE_ASPECT_MISMATCH",
            "Bernini source image aspect ratio does not match the output canvas.",
            "Match the project aspect/orientation to the source image or crop the image manually to the intended output framing.",
            {
                "source_aspect_ratio": decision.get("source_aspect_ratio"),
                "target_aspect_ratio": decision.get("target_aspect_ratio"),
                "source_size": decision.get("exif_transposed_size") or decision.get("original_size"),
                "target_size": [decision.get("target_width"), decision.get("target_height")],
                "resize": decision.get("comfy_source_video_resize"),
            },
        ))
        return


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


def _resolve_wan_loras(plan: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    lora_resolution = plan.get("model_specific", {}).get("wan", {}).get("lora_resolution")
    resolved = resolve_runtime_lora_targets(
        lora_resolution,
        target_keys=[MODEL_LORA_TARGET_HIGH_NOISE, MODEL_LORA_TARGET_LOW_NOISE],
    )
    return resolved, list(resolved.get("warnings") or [])


def _apply_wan_timeline_loras(
    runtime_loras: dict[str, Any],
    *,
    high_noise_model=None,
    low_noise_model=None,
) -> tuple[Any, Any, dict[str, Any], list[str]]:
    targets = runtime_loras.get("targets") if isinstance(runtime_loras.get("targets"), dict) else {}
    high_config = targets.get(MODEL_LORA_TARGET_HIGH_NOISE, {"version": 1, "loras": [], "ui": {}})
    low_config = targets.get(MODEL_LORA_TARGET_LOW_NOISE, {"version": 1, "loras": [], "ui": {}})
    applied_by_target = {
        MODEL_LORA_TARGET_HIGH_NOISE: [],
        MODEL_LORA_TARGET_LOW_NOISE: [],
    }
    diagnostics: list[str] = []
    if high_config.get("loras"):
        if high_noise_model is None:
            diagnostics.append("Resolved WAN high_noise LoRA stack is configured, but high_noise_model is not connected.")
        else:
            high_noise_model, applied_by_target[MODEL_LORA_TARGET_HIGH_NOISE] = apply_lora_config_model_only(
                model=high_noise_model,
                lora_config=high_config,
            )
            diagnostics.append("WAN runtime applied resolved high_noise LoRAs to high_noise_model.")
    if low_config.get("loras"):
        if low_noise_model is None:
            diagnostics.append("Resolved WAN low_noise LoRA stack is configured, but low_noise_model is not connected.")
        else:
            low_noise_model, applied_by_target[MODEL_LORA_TARGET_LOW_NOISE] = apply_lora_config_model_only(
                model=low_noise_model,
                lora_config=low_config,
            )
            diagnostics.append("WAN runtime applied resolved low_noise LoRAs to low_noise_model.")
    return (
        high_noise_model,
        low_noise_model,
        _build_wan_lora_report(
            runtime_loras,
            applied_by_target=applied_by_target,
            high_noise_model=high_noise_model,
            low_noise_model=low_noise_model,
        ),
        diagnostics,
    )


def _build_wan_lora_report(
    runtime_loras: dict[str, Any],
    *,
    applied_by_target: dict[str, list[dict[str, Any]]],
    high_noise_model=None,
    low_noise_model=None,
) -> dict[str, Any]:
    targets = runtime_loras.get("targets") if isinstance(runtime_loras.get("targets"), dict) else {}
    target_inputs = {
        MODEL_LORA_TARGET_HIGH_NOISE: ("high_noise_model", high_noise_model is not None),
        MODEL_LORA_TARGET_LOW_NOISE: ("low_noise_model", low_noise_model is not None),
    }
    target_reports = {}
    warnings = list(runtime_loras.get("warnings") or [])
    for target_key in (MODEL_LORA_TARGET_HIGH_NOISE, MODEL_LORA_TARGET_LOW_NOISE):
        stack = deepcopy(targets.get(target_key, {"version": 1, "loras": [], "ui": {}}))
        applied = [dict(row) for row in applied_by_target.get(target_key, [])]
        input_name, is_connected = target_inputs[target_key]
        target_warnings = []
        if stack.get("loras") and not is_connected:
            target_warnings.append(f"{input_name} is not connected; resolved {target_key} LoRAs were not applied.")
        warnings.extend(target_warnings)
        target_reports[target_key] = {
            "applies_to": [input_name],
            "model_input": input_name,
            "model_connected": is_connected,
            "resolved": stack,
            "resolved_count": len(stack.get("loras") or []),
            "applied": applied,
            "applied_count": len(applied),
            "applied_names": [row.get("name") for row in applied],
            "warnings": target_warnings,
        }
    return {
        "model": runtime_loras.get("model") or MODEL_LORA_MODEL_WAN_2_2,
        "source_scope": runtime_loras.get("source_scope"),
        "resolved_targets": [MODEL_LORA_TARGET_HIGH_NOISE, MODEL_LORA_TARGET_LOW_NOISE],
        "requires_per_shot_execution": bool(runtime_loras.get("requires_per_shot_execution")),
        "warnings": warnings,
        "targets": target_reports,
        "take_snapshot": create_resolved_lora_snapshot(
            model_family=WAN_MODEL_FAMILY,
            model_version=WAN_MODEL_VERSION,
            targets={
                MODEL_LORA_TARGET_HIGH_NOISE: target_reports[MODEL_LORA_TARGET_HIGH_NOISE]["resolved"],
                MODEL_LORA_TARGET_LOW_NOISE: target_reports[MODEL_LORA_TARGET_LOW_NOISE]["resolved"],
            },
            source_scope=str(runtime_loras.get("source_scope") or ""),
        ),
    }


def _attach_wan_lora_debug(runtime_debug: dict[str, Any], lora_report: dict[str, Any]) -> None:
    runtime_debug["loras"] = lora_report
    targets = lora_report.get("targets") if isinstance(lora_report.get("targets"), dict) else {}
    runtime_debug.setdefault("summary", {})
    runtime_debug["summary"]["lora_target_count"] = len(targets)
    runtime_debug["summary"]["lora_resolved_count"] = sum(
        int(target.get("resolved_count") or 0)
        for target in targets.values()
        if isinstance(target, dict)
    )
    runtime_debug["summary"]["lora_applied_count"] = sum(
        int(target.get("applied_count") or 0)
        for target in targets.values()
        if isinstance(target, dict)
    )


def _build_wan_take_registration(
    plan: dict[str, Any],
    lora_report: dict[str, Any],
    *,
    source: str,
    backend: str,
) -> dict[str, Any] | None:
    boundary_conditioning = plan.get("model_specific", {}).get("wan", {}).get("boundary_conditioning", {})
    if not isinstance(boundary_conditioning, dict):
        boundary_conditioning = {}
    return build_take_capture_metadata(
        plan,
        model_key="wan",
        model_family=WAN_MODEL_FAMILY,
        model_version=WAN_MODEL_VERSION,
        source=source,
        resolved_loras=(lora_report or {}).get("take_snapshot"),
        model_specific={
            "runtime": "single",
            "backend": backend,
            "lora_source_scope": (lora_report or {}).get("source_scope"),
            "boundary_conditioning": _take_boundary_conditioning(boundary_conditioning),
        },
    )


def _take_boundary_conditioning(boundary_conditioning: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(boundary_conditioning, dict):
        return {}
    return _safe_boundary_runtime_debug(boundary_conditioning)


def _call_or_value(obj, name: str, fallback):
    value = getattr(obj, name, None)
    if callable(value):
        return value()
    return value if value is not None else fallback
