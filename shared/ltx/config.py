from __future__ import annotations

from copy import deepcopy
from typing import Any


LTX_CONFIG_SCHEMA_VERSION = "1.0"
LTX_CONFIG_TYPE = "LTX_TIMELINE_CONFIG"
LTX_MODEL_FAMILY = "LTX"
LTX_MODEL_VERSION = "2.3"

RESOLUTION_PROFILE_AUTO = "Auto from Director"
RESOLUTION_PROFILES = (
    RESOLUTION_PROFILE_AUTO,
    "Quick Draft",
    "Draft",
    "Standard",
    "High",
    "Native Resolution",
)

IMAGE_GUIDANCE_MODES = (
    "Section Guides",
    "Reference Images",
    "Disabled",
)
VIDEO_SECTION_MODES = (
    "Source Video Guides",
    "Frame References",
    "Disabled",
)
REFERENCE_MODES = (
    "Prompt Relay",
    "Guide Data",
    "Disabled",
)
AUDIO_MODES = (
    "Mix Timeline Audio",
    "Ignore Timeline Audio",
)
SEGMENT_CONTINUITY_TAIL_FRAME_OPTIONS = (1, 5, 9)
DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES = 5
SEGMENT_SEAM_BLEND_FRAME_OPTIONS = (0, 3, 5)
DEFAULT_SEGMENT_SEAM_BLEND_FRAMES = 3


def create_ltx_timeline_config(
    resolution_profile: str = RESOLUTION_PROFILE_AUTO,
    prompt_relay_epsilon: float = 0.15,
    image_guidance_mode: str = "Section Guides",
    video_section_mode: str = "Source Video Guides",
    reference_mode: str = "Prompt Relay",
    audio_mode: str = "Mix Timeline Audio",
    max_generation_duration: float = 0.0,
    segment_continuity_tail_frames: int = DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES,
    segment_seam_blend_frames: int = DEFAULT_SEGMENT_SEAM_BLEND_FRAMES,
    debug_mode: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": LTX_CONFIG_SCHEMA_VERSION,
        "type": LTX_CONFIG_TYPE,
        "model_family": LTX_MODEL_FAMILY,
        "model_version": LTX_MODEL_VERSION,
        "resolution_profile": _choice(resolution_profile, RESOLUTION_PROFILES, RESOLUTION_PROFILE_AUTO),
        "prompt_relay_epsilon": max(0.0, float(prompt_relay_epsilon)),
        "image_guidance_mode": _choice(image_guidance_mode, IMAGE_GUIDANCE_MODES, "Section Guides"),
        "video_section_mode": _choice(video_section_mode, VIDEO_SECTION_MODES, "Source Video Guides"),
        "reference_mode": _choice(reference_mode, REFERENCE_MODES, "Prompt Relay"),
        "audio_mode": _choice(audio_mode, AUDIO_MODES, "Mix Timeline Audio"),
        "max_generation_duration": _non_negative_float(max_generation_duration),
        "segment_continuity_tail_frames": _segment_tail_frames(segment_continuity_tail_frames),
        "segment_seam_blend_frames": _segment_seam_blend_frames(segment_seam_blend_frames),
        "debug_mode": bool(debug_mode),
        "rules": {
            "divisible_by": 32,
            "frame_rule": "8n+1",
            "temporal_stride": 8,
        },
    }


def normalize_ltx_timeline_config(config: Any) -> dict[str, Any]:
    defaults = create_ltx_timeline_config()
    if not isinstance(config, dict):
        return defaults
    normalized = deepcopy(config)
    for key, value in defaults.items():
        if key not in normalized:
            normalized[key] = deepcopy(value)
    normalized["type"] = LTX_CONFIG_TYPE
    normalized["model_family"] = LTX_MODEL_FAMILY
    normalized["model_version"] = LTX_MODEL_VERSION
    normalized["resolution_profile"] = _choice(
        normalized.get("resolution_profile"),
        RESOLUTION_PROFILES,
        RESOLUTION_PROFILE_AUTO,
    )
    normalized["image_guidance_mode"] = _choice(
        normalized.get("image_guidance_mode"),
        IMAGE_GUIDANCE_MODES,
        "Section Guides",
    )
    normalized["video_section_mode"] = _choice(
        normalized.get("video_section_mode"),
        VIDEO_SECTION_MODES,
        "Source Video Guides",
    )
    normalized["reference_mode"] = _choice(
        normalized.get("reference_mode"),
        REFERENCE_MODES,
        "Prompt Relay",
    )
    normalized["audio_mode"] = _choice(
        normalized.get("audio_mode"),
        AUDIO_MODES,
        "Mix Timeline Audio",
    )
    normalized["prompt_relay_epsilon"] = max(0.0, float(normalized.get("prompt_relay_epsilon", 0.15)))
    normalized["max_generation_duration"] = _non_negative_float(normalized.get("max_generation_duration", 0.0))
    normalized["segment_continuity_tail_frames"] = _segment_tail_frames(
        normalized.get("segment_continuity_tail_frames", DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES)
    )
    normalized["segment_seam_blend_frames"] = _segment_seam_blend_frames(
        normalized.get("segment_seam_blend_frames", DEFAULT_SEGMENT_SEAM_BLEND_FRAMES)
    )
    normalized["debug_mode"] = bool(normalized.get("debug_mode"))
    normalized["rules"] = deepcopy(defaults["rules"]) | dict(normalized.get("rules") or {})
    return normalized


def _choice(value: Any, options: tuple[str, ...], fallback: str) -> str:
    return value if value in options else fallback


def _non_negative_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _segment_tail_frames(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES
    return parsed if parsed in SEGMENT_CONTINUITY_TAIL_FRAME_OPTIONS else DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES


def _segment_seam_blend_frames(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_SEGMENT_SEAM_BLEND_FRAMES
    return parsed if parsed in SEGMENT_SEAM_BLEND_FRAME_OPTIONS else DEFAULT_SEGMENT_SEAM_BLEND_FRAMES
