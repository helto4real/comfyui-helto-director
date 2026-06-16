from __future__ import annotations

from copy import deepcopy
from typing import Any

from ...contracts.validation import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    create_validation_entry,
    create_validation_result,
)


def runtime_entry(
    code: str,
    severity: str,
    message: str,
    hint: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return create_validation_entry(
        code,
        severity,
        "WAN Runtime",
        "Runtime",
        None,
        message,
        hint,
        details,
    )


def build_runtime_validation(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return create_validation_result(entries)


def build_runtime_debug(
    *,
    plan: dict[str, Any],
    requested_backend: str,
    resolved_backend: str,
    capabilities: dict[str, Any],
    visual: dict[str, Any],
    prompt_debug: dict[str, Any],
    validation_entries: list[dict[str, Any]],
    diagnostics: list[str],
    media_decisions: list[dict[str, Any]] | None = None,
    model_patch_status: dict[str, Any] | None = None,
    status_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    wan = plan.get("model_specific", {}).get("wan", {})
    config = wan.get("config", {})
    bernini = _bernini_runtime_debug(wan.get("bernini") or {}, media_decisions or [], diagnostics)
    validation = build_runtime_validation(validation_entries)
    backend = build_backend_report(
        plan=plan,
        requested_backend=requested_backend,
        resolved_backend=resolved_backend,
        capabilities=capabilities,
        visual=visual,
        prompt_debug=prompt_debug,
        validation=validation,
        model_patch_status=model_patch_status or {},
    )
    runtime_debug = {
        "type": "DEBUG_INFO",
        "source": "WAN Runtime",
        "enabled": config.get("debug_mode") != "Off",
        "mode": config.get("debug_mode", "Off"),
        "summary": {
            "requested_backend": requested_backend,
            "resolved_backend": resolved_backend,
            "model_mode": config.get("model_mode"),
            "bernini_task_type": bernini.get("task_type") if bernini.get("enabled") else None,
            "bernini_prompt_prefix_enabled": bool(bernini.get("prompt_prefix_enabled")),
            "prompt_routing": config.get("prompt_routing"),
            "prompt_relay_patched": bool(prompt_debug.get("patched")),
            "prompt_relay_status": prompt_debug.get("status", "not_built"),
            "requested_visual_keyframes": len(visual.get("requested_keyframes") or []),
            "applied_visual_keyframes": len(visual.get("applied_keyframes") or []),
            "unsupported_visual_keyframes": len(visual.get("unsupported_keyframes") or []),
            "video_frame_count": plan.get("resolved_output", {}).get("frame_count"),
            "latent_chunk_count": plan.get("resolved_output", {}).get("latent_chunk_count"),
            "warning_count": len(validation.get("warnings", [])),
            "error_count": len(validation.get("errors", [])),
        },
        "backend": backend,
        "backend_capabilities": deepcopy(capabilities),
        "visual_conditioning": {
            "requested_keyframes": deepcopy(visual.get("requested_keyframes") or []),
            "applied_keyframes": deepcopy(visual.get("applied_keyframes") or []),
            "unsupported_keyframes": deepcopy(visual.get("unsupported_keyframes") or []),
            "selected_primary_image": _selected_primary_image(visual),
            "painter_motion_boost": deepcopy(visual.get("painter_motion_boost") or {}),
        },
        "prompt_relay": deepcopy(prompt_debug),
        "bernini": bernini,
        "model_patch_status": deepcopy(model_patch_status or {}),
        "status_events": deepcopy(status_events or []),
        "media_decisions": deepcopy(media_decisions or []),
        "output_payload_type": _output_payload_type(resolved_backend, media_decisions or []),
        "known_limitations": _known_limitations(plan, visual, capabilities),
        "validation": validation,
        "diagnostics": list(diagnostics),
    }
    runtime_debug["status"] = summarize_wan_runtime_status(plan, runtime_debug, validation)
    return runtime_debug


def build_backend_report(
    *,
    plan: dict[str, Any],
    requested_backend: str,
    resolved_backend: str,
    capabilities: dict[str, Any],
    visual: dict[str, Any],
    prompt_debug: dict[str, Any],
    validation: dict[str, Any],
    model_patch_status: dict[str, Any],
) -> dict[str, Any]:
    config = plan.get("model_specific", {}).get("wan", {}).get("config", {})
    audio_plan = plan.get("audio_plan") or []
    unsupported_features = _unsupported_features(plan, visual, capabilities)
    missing_requirements = _missing_backend_requirements(
        requested_backend,
        resolved_backend,
        capabilities,
        validation,
        model_patch_status,
    )
    available = resolved_backend == "ComfyUI Core" and not missing_requirements
    return {
        "requested_profile": requested_backend,
        "resolved_profile": resolved_backend,
        "available": available,
        "capabilities": deepcopy(capabilities),
        "missing_requirements": missing_requirements,
        "prompt_relay_supported": capabilities.get("supports_prompt_relay") is True,
        "visual_keyframe_support_level": _visual_keyframe_support_level(resolved_backend, capabilities),
        "max_visual_keyframes": capabilities.get("max_visual_keyframes"),
        "audio_policy": config.get("audio_policy", "Final Mix Only"),
        "audio_clip_count": len(audio_plan),
        "unsupported_features": unsupported_features,
        "recommended_next_action": _recommended_next_action(
            requested_backend,
            resolved_backend,
            missing_requirements,
            unsupported_features,
            prompt_debug,
        ),
    }


def summarize_wan_runtime_status(plan: dict[str, Any], runtime_result: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    backend = runtime_result.get("backend") or {}
    summary = runtime_result.get("summary") or {}
    visual = runtime_result.get("visual_conditioning") or {}
    prompt_relay = runtime_result.get("prompt_relay") or {}
    unsupported = visual.get("unsupported_keyframes") or []
    audio_plan = plan.get("audio_plan") or []
    return {
        "runtime_executed": backend.get("resolved_profile") == "ComfyUI Core" and bool(backend.get("available")),
        "plan_only": backend.get("resolved_profile") == "Plan Only",
        "prompt_relay": {
            "enabled": plan.get("model_specific", {}).get("wan", {}).get("prompt_relay", {}).get("enabled", False),
            "supported": bool(backend.get("prompt_relay_supported")),
            "applied": bool(summary.get("prompt_relay_patched")),
            "status": summary.get("prompt_relay_status", prompt_relay.get("status", "not_built")),
        },
        "visual_keyframes": {
            "requested": int(summary.get("requested_visual_keyframes") or 0),
            "applied": int(summary.get("applied_visual_keyframes") or 0),
            "unsupported": int(summary.get("unsupported_visual_keyframes") or 0),
            "unsupported_reasons": sorted({
                str(entry.get("reason"))
                for entry in unsupported
                if entry.get("reason")
            }),
        },
        "audio": {
            "clip_count": len(audio_plan),
            "policy": backend.get("audio_policy", "Final Mix Only"),
            "final_mix_only": bool(audio_plan) and backend.get("audio_policy", "Final Mix Only") == "Final Mix Only",
        },
        "validation": {
            "is_valid": validation.get("is_valid", True),
            "warning_count": len(validation.get("warnings", [])),
            "error_count": len(validation.get("errors", [])),
        },
        "recommended_next_action": backend.get("recommended_next_action", ""),
    }


def _missing_backend_requirements(
    requested_backend: str,
    resolved_backend: str,
    capabilities: dict[str, Any],
    validation: dict[str, Any],
    model_patch_status: dict[str, Any],
) -> list[str]:
    if resolved_backend == "WanVideoWrapper":
        return ["WanVideoWrapper backend adapter is not implemented in this nodepack."]
    if resolved_backend == "Plan Only":
        if requested_backend == "Auto":
            return ["Auto resolved to Plan Only because CLIP, VAE, and at least one WAN model phase were not all connected."]
        return ["Runtime Backend Profile is Plan Only; no execution backend was requested."]
    missing = [
        entry.get("message", "")
        for entry in validation.get("errors", [])
        if entry.get("code") == "WAN_RUNTIME_REQUIRED_INPUT_MISSING"
    ]
    if capabilities.get("supports_prompt_relay") and all(value == "not_connected" for value in model_patch_status.values()):
        missing.append("Prompt Relay requires at least one connected high_noise_model or low_noise_model.")
    return [entry for entry in missing if entry]


def _unsupported_features(plan: dict[str, Any], visual: dict[str, Any], capabilities: dict[str, Any]) -> list[str]:
    features = []
    bernini_enabled = bool(plan.get("model_specific", {}).get("wan", {}).get("bernini", {}).get("enabled"))
    if visual.get("unsupported_keyframes"):
        features.append("Some requested visual keyframes are unsupported by the resolved backend.")
    if capabilities.get("supports_timed_keyframes") is not True and any(
        keyframe.get("role") == "Timed"
        for keyframe in visual.get("requested_keyframes", [])
    ):
        features.append("Timed visual keyframes are planned only for the resolved backend.")
    if plan.get("audio_plan"):
        features.append("WAN audio conditioning is unsupported; audio clips are final-mix metadata only.")
    has_video_sections = any(entry.get("section_type") == "Video" for entry in plan.get("media_plan", []))
    if has_video_sections:
        if bernini_enabled and capabilities.get("supports_video_sections") is True:
            features.append("Bernini uses the first Video Section as source_video; additional video sections are deferred.")
        else:
            features.append("WAN Video Sections are prompt-only fallback metadata.")
        if capabilities.get("supports_video_sections") is not True:
            features.append("WAN source-video conditioning is not supported by the resolved backend.")
    return features


def _selected_primary_image(visual: dict[str, Any]) -> dict[str, Any] | None:
    for entry in visual.get("applied_keyframes") or []:
        if entry.get("role") == "Start":
            return {
                "section_id": entry.get("section_id"),
                "asset_id": entry.get("asset_id"),
                "role": entry.get("role"),
                "frame": entry.get("frame"),
            }
    return None


def _output_payload_type(resolved_backend: str, media_decisions: list[dict[str, Any]]) -> str:
    if resolved_backend == "Plan Only":
        return "WAN_RUNTIME_BACKEND_PLAN_ONLY"
    for decision in media_decisions:
        if decision.get("output_payload_type"):
            return str(decision["output_payload_type"])
    if resolved_backend == "ComfyUI Core":
        return "COMFYUI_CORE_CONDITIONING_LATENT"
    return "UNAVAILABLE"


def _known_limitations(plan: dict[str, Any], visual: dict[str, Any], capabilities: dict[str, Any]) -> list[str]:
    limitations = []
    bernini = plan.get("model_specific", {}).get("wan", {}).get("bernini", {})
    if capabilities.get("supports_timed_keyframes") is not True:
        limitations.append("Timed visual keyframes are planned and reported but not applied by the selected backend.")
    if plan.get("audio_plan"):
        limitations.append("WAN audio conditioning is not implemented; audio clips remain final-mix metadata only.")
    if any(entry.get("section_type") == "Video" for entry in plan.get("media_plan", [])) and not bernini.get("enabled"):
        limitations.append("WAN source-video conditioning is not implemented.")
    if visual.get("unsupported_keyframes"):
        limitations.append("Some requested visual keyframes are preserved in debug but unsupported by the selected backend.")
    if bernini.get("enabled") and bernini.get("ignored_timeline_media"):
        limitations.append("Bernini uses only the first usable timeline image/video as source/background context; ignored timeline media is reported in runtime_debug.bernini.")
    return limitations


def _bernini_runtime_debug(
    bernini_plan: dict[str, Any],
    media_decisions: list[dict[str, Any]],
    diagnostics: list[str],
) -> dict[str, Any]:
    debug = deepcopy(bernini_plan)
    if not debug:
        return {}
    debug["runtime_media_decisions"] = [
        deepcopy(decision)
        for decision in media_decisions
        if (
            decision.get("bernini_role")
            or decision.get("helper") == "BerniniConditioning"
            or str(decision.get("output_payload_type") or "").startswith("COMFYUI_CORE_BERNINI")
        )
    ]
    debug["runtime_diagnostics"] = [
        entry
        for entry in diagnostics
        if "Bernini" in str(entry)
    ]
    return debug


def _visual_keyframe_support_level(resolved_backend: str, capabilities: dict[str, Any]) -> str:
    if resolved_backend == "Plan Only":
        return "Plan Only debug"
    if resolved_backend == "WanVideoWrapper":
        return "Unavailable"
    if capabilities.get("supports_timed_keyframes") is True:
        return "Timed keyframes"
    if capabilities.get("supports_start_image") and capabilities.get("supports_end_image"):
        return "Start and End only"
    if capabilities.get("supports_start_image"):
        return "Start only"
    return "Prompt only"


def _recommended_next_action(
    requested_backend: str,
    resolved_backend: str,
    missing_requirements: list[str],
    unsupported_features: list[str],
    prompt_debug: dict[str, Any],
) -> str:
    if resolved_backend == "WanVideoWrapper":
        return "Use Plan Only or ComfyUI Core; WanVideoWrapper integration is not implemented."
    if resolved_backend == "Plan Only":
        if requested_backend == "Auto":
            return "Connect CLIP, VAE, and at least one WAN model phase, or keep Plan Only for debug."
        return "Switch Runtime Backend Profile to ComfyUI Core and connect the required backend inputs to execute supported conditioning."
    if missing_requirements:
        return "Connect the missing backend requirements listed in runtime_debug.backend.missing_requirements."
    if unsupported_features:
        return "Inspect unsupported_features and unsupported_keyframes; use Start/End image guidance for the current ComfyUI Core backend."
    if prompt_debug.get("patched"):
        return "Continue to a compatible WAN sampler using the patched high/low model outputs, conditioning, and video_latent."
    return "Continue to a compatible WAN sampler, or enable Prompt Relay for temporal prompt routing."


def info(code: str, message: str, hint: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
    return runtime_entry(code, SEVERITY_INFO, message, hint, details)


def warning(code: str, message: str, hint: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
    return runtime_entry(code, SEVERITY_WARNING, message, hint, details)


def error(code: str, message: str, hint: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
    return runtime_entry(code, SEVERITY_ERROR, message, hint, details)
