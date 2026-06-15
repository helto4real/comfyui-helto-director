from __future__ import annotations

from copy import deepcopy
from typing import Any


BACKEND_PLAN_ONLY = "Plan Only"
BACKEND_COMFYUI_CORE = "ComfyUI Core"
BACKEND_WAN_VIDEO_WRAPPER = "WanVideoWrapper"


def resolve_backend(profile: Any, *, high_noise_model=None, low_noise_model=None, clip=None, vae=None) -> tuple[str, list[dict[str, Any]]]:
    entries = []
    if profile == BACKEND_COMFYUI_CORE:
        return BACKEND_COMFYUI_CORE, entries
    if profile == BACKEND_WAN_VIDEO_WRAPPER:
        return BACKEND_WAN_VIDEO_WRAPPER, entries
    if profile == "Auto":
        if clip is not None and vae is not None and (high_noise_model is not None or low_noise_model is not None):
            resolved = BACKEND_COMFYUI_CORE
        else:
            resolved = BACKEND_PLAN_ONLY
        entries.append({
            "code": "WAN_RUNTIME_BACKEND_AUTO_RESOLVED",
            "severity": "Info",
            "message": f"WAN Runtime Auto backend resolved to {resolved}.",
            "details": {"resolved_backend": resolved},
        })
        return resolved, entries
    return BACKEND_PLAN_ONLY, entries


def backend_capabilities(backend: str) -> dict[str, Any]:
    if backend == BACKEND_COMFYUI_CORE:
        return {
            "supports_start_image": True,
            "supports_end_image": True,
            "supports_timed_keyframes": False,
            "max_visual_keyframes": 2,
            "supports_prompt_relay": True,
            "supports_video_sections": False,
            "supports_audio_conditioning": False,
            "supports_i2v_a14b": True,
        }
    if backend == BACKEND_WAN_VIDEO_WRAPPER:
        return {
            "supports_start_image": None,
            "supports_end_image": None,
            "supports_timed_keyframes": None,
            "max_visual_keyframes": None,
            "supports_prompt_relay": None,
            "supports_video_sections": None,
            "supports_audio_conditioning": None,
            "supports_i2v_a14b": None,
        }
    return {
        "supports_start_image": None,
        "supports_end_image": None,
        "supports_timed_keyframes": None,
        "max_visual_keyframes": None,
        "supports_prompt_relay": None,
        "supports_video_sections": None,
        "supports_audio_conditioning": None,
        "supports_i2v_a14b": None,
    }


def resolve_visual_conditioning(plan: dict[str, Any], capabilities: dict[str, Any], backend: str) -> dict[str, Any]:
    visual = deepcopy(plan.get("model_specific", {}).get("wan", {}).get("visual_conditioning") or {})
    requested = list(visual.get("requested_keyframes") or [])
    applied, unsupported = select_keyframes_for_capabilities(requested, capabilities, backend)
    visual["requested_keyframes"] = requested
    visual["applied_keyframes"] = applied
    visual["unsupported_keyframes"] = unsupported
    visual["backend_capabilities"] = capabilities
    return visual


def select_keyframes_for_capabilities(
    requested: list[dict[str, Any]],
    capabilities: dict[str, Any],
    backend: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if backend == BACKEND_PLAN_ONLY:
        return [], [
            {**keyframe, "reason": "No runtime backend execution is active in Plan Only mode."}
            for keyframe in requested
        ]

    max_keyframes = capabilities.get("max_visual_keyframes")
    if max_keyframes is None and capabilities.get("supports_timed_keyframes") is True:
        return [{**keyframe, "backend_role": keyframe.get("role")} for keyframe in requested], []

    selected: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    start = next((entry for entry in requested if entry.get("role") == "Start"), None)
    end = next((entry for entry in requested if entry.get("role") == "End"), None)
    timed = [entry for entry in requested if entry.get("role") == "Timed"]
    limit = len(requested) if max_keyframes is None else max(0, int(max_keyframes))

    if start and capabilities.get("supports_start_image") and len(selected) < limit:
        selected.append({**start, "backend_role": "Start"})
    elif start:
        unsupported.append({**start, "reason": _unsupported_reason(start, capabilities, limit, selected)})

    if end and capabilities.get("supports_end_image") and len(selected) < limit:
        selected.append({**end, "backend_role": "End"})
    elif end:
        unsupported.append({**end, "reason": _unsupported_reason(end, capabilities, limit, selected)})

    remaining_slots = max(0, limit - len(selected))
    selected_timed = []
    if capabilities.get("supports_timed_keyframes") and remaining_slots > 0:
        selected_timed = _evenly_spaced(timed, remaining_slots)
        selected.extend({**entry, "backend_role": "Timed"} for entry in selected_timed)

    selected_ids = {id(entry) for entry in selected_timed}
    for entry in timed:
        if id(entry) not in selected_ids:
            unsupported.append({**entry, "reason": _unsupported_reason(entry, capabilities, limit, selected)})

    selected_section_ids = {entry.get("section_id") for entry in selected}
    for entry in requested:
        if entry.get("role") not in {"Start", "End", "Timed"} and entry.get("section_id") not in selected_section_ids:
            unsupported.append({**entry, "reason": "Unsupported visual keyframe role."})

    return sorted(selected, key=lambda item: item.get("frame") or 0), sorted(unsupported, key=lambda item: item.get("frame") or 0)


def _unsupported_reason(keyframe: dict[str, Any], capabilities: dict[str, Any], limit: int, selected: list[dict[str, Any]]) -> str:
    role = keyframe.get("role")
    if role == "Timed":
        return "Timed visual keyframes are not supported by the selected backend."
    if len(selected) >= limit:
        return "Selected backend visual keyframe limit was exceeded."
    return f"{role} visual keyframes are not supported by the selected backend."


def _evenly_spaced(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit >= len(items):
        return list(items)
    if limit <= 0 or not items:
        return []
    if limit == 1:
        return [items[len(items) // 2]]
    last = len(items) - 1
    indices = sorted({round(index * last / (limit - 1)) for index in range(limit)})
    return [items[index] for index in indices[:limit]]
