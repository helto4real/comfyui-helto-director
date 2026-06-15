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

AUDIO_MODES = (
    "Ignore Timeline Audio",
    "Plan Timeline Audio",
)


def create_wan_timeline_config(
    resolution_profile: str = RESOLUTION_PROFILE_AUTO,
    audio_mode: str = "Ignore Timeline Audio",
    debug_mode: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": WAN_CONFIG_SCHEMA_VERSION,
        "type": WAN_CONFIG_TYPE,
        "model_family": WAN_MODEL_FAMILY,
        "model_version": WAN_MODEL_VERSION,
        "resolution_profile": _choice(resolution_profile, RESOLUTION_PROFILES, RESOLUTION_PROFILE_AUTO),
        "audio_mode": _choice(audio_mode, AUDIO_MODES, "Ignore Timeline Audio"),
        "debug_mode": bool(debug_mode),
        "rules": {
            "divisible_by": 16,
            "frame_rule": "none",
            "temporal_stride": 1,
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
    normalized["resolution_profile"] = _choice(
        normalized.get("resolution_profile"),
        RESOLUTION_PROFILES,
        RESOLUTION_PROFILE_AUTO,
    )
    normalized["audio_mode"] = _choice(
        normalized.get("audio_mode"),
        AUDIO_MODES,
        "Ignore Timeline Audio",
    )
    normalized["debug_mode"] = bool(normalized.get("debug_mode"))
    normalized["rules"] = deepcopy(defaults["rules"]) | dict(normalized.get("rules") or {})
    return normalized


def _choice(value: Any, options: tuple[str, ...], fallback: str) -> str:
    return value if value in options else fallback
