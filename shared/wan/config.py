from __future__ import annotations

from copy import deepcopy
from typing import Any


WAN_CONFIG_SCHEMA_VERSION = "1.0"
WAN_CONFIG_TYPE = "WAN_TIMELINE_CONFIG"
WAN_MODEL_FAMILY = "WAN"
WAN_MODEL_VERSION = "2.2"

RESOLUTION_PROFILE_AUTO = "Auto from Director"
RESOLUTION_PROFILES = (
    RESOLUTION_PROFILE_AUTO,
    "Quick Draft",
    "Draft",
    "Standard",
    "High",
    "Native Resolution",
)

MODEL_MODES = (
    "Auto",
    "I2V-A14B",
    "TI2V-5B",
    "T2V-A14B",
)
PROMPT_ROUTING_MODES = (
    "Off",
    "Prompt Relay",
)
VISUAL_CONDITIONING_MODES = (
    "Off",
    "First Image Only",
    "First + Last",
    "Timed Keyframes",
    "Segmented Generation",
    "Auto",
)
UNSUPPORTED_VISUAL_KEYFRAME_POLICIES = (
    "Warn And Use Prompt Only",
    "Warn And Keep In Plan",
    "Error",
)
GAP_POLICIES = (
    "No Guidance Entry",
    "Merge With Previous Prompt",
    "Warning",
)
UNSUPPORTED_VIDEO_SECTION_POLICIES = (
    "Warn And Use Prompt Only",
    "Error",
)
AUDIO_POLICIES = (
    "Final Mix Only",
    "Ignore",
)
RUNTIME_BACKEND_PROFILES = (
    "Auto",
    "ComfyUI Core",
    "WanVideoWrapper",
    "Plan Only",
)
DEBUG_MODES = (
    "Off",
    "Summary",
    "Full",
)


def create_wan_timeline_config(
    resolution_profile: str = RESOLUTION_PROFILE_AUTO,
    model_mode: str = "I2V-A14B",
    prompt_routing: str = "Prompt Relay",
    prompt_relay_epsilon: float = 0.001,
    visual_conditioning_mode: str = "Timed Keyframes",
    unsupported_visual_keyframe_policy: str = "Warn And Keep In Plan",
    gap_policy: str = "Warning",
    unsupported_video_section_policy: str = "Warn And Use Prompt Only",
    audio_policy: str = "Final Mix Only",
    runtime_backend_profile: str = "Plan Only",
    debug_mode: str | bool = "Off",
    audio_mode: str | None = None,
) -> dict[str, Any]:
    if audio_mode is not None:
        audio_policy = _legacy_audio_policy(audio_mode)
    return {
        "schema_version": WAN_CONFIG_SCHEMA_VERSION,
        "type": WAN_CONFIG_TYPE,
        "model_family": WAN_MODEL_FAMILY,
        "model_version": WAN_MODEL_VERSION,
        "resolution_profile": _choice(resolution_profile, RESOLUTION_PROFILES, RESOLUTION_PROFILE_AUTO),
        "model_mode": _choice(model_mode, MODEL_MODES, "I2V-A14B"),
        "prompt_routing": _choice(prompt_routing, PROMPT_ROUTING_MODES, "Prompt Relay"),
        "prompt_relay_epsilon": max(0.0, float(prompt_relay_epsilon)),
        "visual_conditioning_mode": _choice(visual_conditioning_mode, VISUAL_CONDITIONING_MODES, "Timed Keyframes"),
        "unsupported_visual_keyframe_policy": _choice(
            unsupported_visual_keyframe_policy,
            UNSUPPORTED_VISUAL_KEYFRAME_POLICIES,
            "Warn And Keep In Plan",
        ),
        "gap_policy": _choice(gap_policy, GAP_POLICIES, "Warning"),
        "unsupported_video_section_policy": _choice(
            unsupported_video_section_policy,
            UNSUPPORTED_VIDEO_SECTION_POLICIES,
            "Warn And Use Prompt Only",
        ),
        "audio_policy": _choice(audio_policy, AUDIO_POLICIES, "Final Mix Only"),
        "runtime_backend_profile": _choice(runtime_backend_profile, RUNTIME_BACKEND_PROFILES, "Plan Only"),
        "debug_mode": _debug_mode(debug_mode),
        "rules": {
            "divisible_by": 16,
            "frame_rule": "4n+1 latent chunks",
            "temporal_stride": 4,
        },
    }


def normalize_wan_timeline_config(config: Any) -> dict[str, Any]:
    defaults = create_wan_timeline_config()
    if not isinstance(config, dict):
        return defaults
    normalized = deepcopy(config)
    for key, value in defaults.items():
        if key not in normalized:
            normalized[key] = deepcopy(value)
    normalized["type"] = WAN_CONFIG_TYPE
    normalized["model_family"] = WAN_MODEL_FAMILY
    normalized["model_version"] = WAN_MODEL_VERSION
    if "audio_policy" not in normalized and "audio_mode" in normalized:
        normalized["audio_policy"] = _legacy_audio_policy(normalized.get("audio_mode"))
    normalized["resolution_profile"] = _choice(
        normalized.get("resolution_profile"),
        RESOLUTION_PROFILES,
        RESOLUTION_PROFILE_AUTO,
    )
    normalized["model_mode"] = _choice(normalized.get("model_mode"), MODEL_MODES, "I2V-A14B")
    normalized["prompt_routing"] = _choice(normalized.get("prompt_routing"), PROMPT_ROUTING_MODES, "Prompt Relay")
    normalized["prompt_relay_epsilon"] = max(0.0, float(normalized.get("prompt_relay_epsilon", 0.001)))
    normalized["visual_conditioning_mode"] = _choice(
        normalized.get("visual_conditioning_mode"),
        VISUAL_CONDITIONING_MODES,
        "Timed Keyframes",
    )
    normalized["unsupported_visual_keyframe_policy"] = _choice(
        normalized.get("unsupported_visual_keyframe_policy"),
        UNSUPPORTED_VISUAL_KEYFRAME_POLICIES,
        "Warn And Keep In Plan",
    )
    normalized["gap_policy"] = _choice(normalized.get("gap_policy"), GAP_POLICIES, "Warning")
    normalized["unsupported_video_section_policy"] = _choice(
        normalized.get("unsupported_video_section_policy"),
        UNSUPPORTED_VIDEO_SECTION_POLICIES,
        "Warn And Use Prompt Only",
    )
    normalized["audio_policy"] = _choice(normalized.get("audio_policy"), AUDIO_POLICIES, "Final Mix Only")
    normalized["runtime_backend_profile"] = _choice(
        normalized.get("runtime_backend_profile"),
        RUNTIME_BACKEND_PROFILES,
        "Plan Only",
    )
    normalized["debug_mode"] = _debug_mode(normalized.get("debug_mode"))
    normalized["rules"] = deepcopy(defaults["rules"]) | dict(normalized.get("rules") or {})
    normalized.pop("audio_mode", None)
    return normalized


def _choice(value: Any, options: tuple[str, ...], fallback: str) -> str:
    return value if value in options else fallback


def _debug_mode(value: Any) -> str:
    if isinstance(value, bool):
        return "Summary" if value else "Off"
    return _choice(value, DEBUG_MODES, "Off")


def _legacy_audio_policy(value: Any) -> str:
    if value == "Plan Timeline Audio":
        return "Final Mix Only"
    return "Ignore"
