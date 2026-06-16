from __future__ import annotations

from copy import deepcopy
from typing import Any

from .bernini import (
    BERNINI_MODEL_MODE,
    BERNINI_TASK_PROMPT_AUTO,
    BERNINI_TASK_PROMPT_MODES,
)


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
    BERNINI_MODEL_MODE,
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
    "FMLF Advanced I2V",
    "WanVideoWrapper",
    "Plan Only",
)
FMLF_CONTINUATION_MODES = (
    "SVI",
    "AUTO_CONTINUE",
)
DEBUG_MODES = (
    "Off",
    "Summary",
    "Full",
)
VRAM_UNLOAD_POLICIES = (
    "Off",
    "Between High Low",
    "Before Decode",
    "Between High Low And Decode",
)
PAINTER_MOTION_BOOST_MODES = (
    "Off",
    "Auto",
)
SEGMENT_CONTINUITY_TAIL_FRAME_OPTIONS = (1, 5, 9)
DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES = 5
SEGMENT_SEAM_BLEND_FRAME_OPTIONS = (0, 3, 5)
DEFAULT_SEGMENT_SEAM_BLEND_FRAMES = 3
DEFAULT_PAINTER_MOTION_AMPLITUDE = 1.15


def create_wan_timeline_config(
    resolution_profile: str = RESOLUTION_PROFILE_AUTO,
    model_mode: str = "I2V-A14B",
    prompt_routing: str = "Prompt Relay",
    prompt_relay_epsilon: float = 0.001,
    bernini_task_prompt: str = BERNINI_TASK_PROMPT_AUTO,
    visual_conditioning_mode: str = "Timed Keyframes",
    unsupported_visual_keyframe_policy: str = "Warn And Keep In Plan",
    gap_policy: str = "Warning",
    unsupported_video_section_policy: str = "Warn And Use Prompt Only",
    audio_policy: str = "Final Mix Only",
    runtime_backend_profile: str = "Plan Only",
    max_generation_duration: float = 0.0,
    segment_continuity_tail_frames: int = DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES,
    segment_seam_blend_frames: int = DEFAULT_SEGMENT_SEAM_BLEND_FRAMES,
    fmlf_continuation_mode: str = "SVI",
    vram_unload_policy: str = "Off",
    debug_mode: str | bool = "Off",
    painter_motion_boost: str = "Off",
    painter_motion_amplitude: float = DEFAULT_PAINTER_MOTION_AMPLITUDE,
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
        "bernini_task_prompt": _choice(bernini_task_prompt, BERNINI_TASK_PROMPT_MODES, BERNINI_TASK_PROMPT_AUTO),
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
        "fmlf_continuation_mode": _choice(fmlf_continuation_mode, FMLF_CONTINUATION_MODES, "SVI"),
        "max_generation_duration": _non_negative_float(max_generation_duration),
        "segment_continuity_tail_frames": _segment_tail_frames(segment_continuity_tail_frames),
        "segment_seam_blend_frames": _segment_seam_blend_frames(segment_seam_blend_frames),
        "vram_unload_policy": _choice(vram_unload_policy, VRAM_UNLOAD_POLICIES, "Off"),
        "debug_mode": _debug_mode(debug_mode),
        "painter_motion_boost": _choice(painter_motion_boost, PAINTER_MOTION_BOOST_MODES, "Off"),
        "painter_motion_amplitude": _clamped_float(
            painter_motion_amplitude,
            DEFAULT_PAINTER_MOTION_AMPLITUDE,
            1.0,
            2.0,
        ),
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
    normalized["bernini_task_prompt"] = _choice(
        normalized.get("bernini_task_prompt"),
        BERNINI_TASK_PROMPT_MODES,
        BERNINI_TASK_PROMPT_AUTO,
    )
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
    normalized["fmlf_continuation_mode"] = _choice(
        normalized.get("fmlf_continuation_mode"),
        FMLF_CONTINUATION_MODES,
        "SVI",
    )
    normalized["max_generation_duration"] = _non_negative_float(normalized.get("max_generation_duration", 0.0))
    normalized["segment_continuity_tail_frames"] = _segment_tail_frames(
        normalized.get("segment_continuity_tail_frames", DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES)
    )
    normalized["segment_seam_blend_frames"] = _segment_seam_blend_frames(
        normalized.get("segment_seam_blend_frames", DEFAULT_SEGMENT_SEAM_BLEND_FRAMES)
    )
    normalized["vram_unload_policy"] = _choice(
        normalized.get("vram_unload_policy"),
        VRAM_UNLOAD_POLICIES,
        "Off",
    )
    normalized["debug_mode"] = _debug_mode(normalized.get("debug_mode"))
    normalized["painter_motion_boost"] = _choice(
        normalized.get("painter_motion_boost"),
        PAINTER_MOTION_BOOST_MODES,
        "Off",
    )
    normalized["painter_motion_amplitude"] = _clamped_float(
        normalized.get("painter_motion_amplitude"),
        DEFAULT_PAINTER_MOTION_AMPLITUDE,
        1.0,
        2.0,
    )
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


def _non_negative_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _clamped_float(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return min(maximum, max(minimum, parsed))


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
