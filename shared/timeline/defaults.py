from __future__ import annotations

from copy import deepcopy

from ..contracts.video_timeline import (
    CROP_MODE_PROJECT_DEFAULT,
    DEFAULT_ALLOW_GAPS,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_AUDIO_FADE_IN_SECONDS,
    DEFAULT_AUDIO_FADE_OUT_SECONDS,
    DEFAULT_AUDIO_VOLUME,
    DEFAULT_AUTO_CLOSE_GAPS,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_FRAME_RATE,
    DEFAULT_MINIMUM_SECTION_DURATION_SECONDS,
    DEFAULT_ORIENTATION,
    DEFAULT_QUALITY_PRESET,
    DEFAULT_USE_NATIVE_AUDIO,
    GLOBAL_PROMPT_POSITION_PREFIX,
    SCHEMA_VERSION,
    SECTION_EDIT_MODE_TRIM_NEIGHBOR,
    SNAP_MODE_FRAMES,
    TIMELINE_DISPLAY_MODE_DEFAULT,
    VIDEO_TIMELINE_TYPE,
)


def create_default_video_timeline() -> dict:
    timeline = {
        "schema_version": SCHEMA_VERSION,
        "type": VIDEO_TIMELINE_TYPE,
        "project": {
            "duration_seconds": DEFAULT_DURATION_SECONDS,
            "frame_rate": DEFAULT_FRAME_RATE,
            "aspect_ratio": DEFAULT_ASPECT_RATIO,
            "orientation": DEFAULT_ORIENTATION,
            "quality_preset": DEFAULT_QUALITY_PRESET,
            "default_crop_mode": CROP_MODE_PROJECT_DEFAULT,
            "settings": {
                "allow_gaps": DEFAULT_ALLOW_GAPS,
                "auto_close_gaps": DEFAULT_AUTO_CLOSE_GAPS,
                "minimum_section_duration_seconds": DEFAULT_MINIMUM_SECTION_DURATION_SECONDS,
                "show_resolved_model_output": False,
            },
            "global_prompt": {
                "enabled": False,
                "prompt": "",
                "position": GLOBAL_PROMPT_POSITION_PREFIX,
                "show_effective_prompt": False,
            },
            "audio": {
                "use_native_audio": DEFAULT_USE_NATIVE_AUDIO,
                "always_normalize": False,
                "normalization_mode": "Integrated LUFS",
                "target_lufs": -16.0,
                "true_peak_limit_db": -1.0,
                "default_volume": DEFAULT_AUDIO_VOLUME,
                "default_fade_in_seconds": DEFAULT_AUDIO_FADE_IN_SECONDS,
                "default_fade_out_seconds": DEFAULT_AUDIO_FADE_OUT_SECONDS,
            },
            "privacy": {
                "mode": False,
            },
            "display": {
                "show_section_labels": True,
                "show_thumbnails": True,
                "show_audio_waveforms": True,
            },
            "metadata": {
                "character_references_enabled": True,
                "character_references": [],
            },
            "model_loras": {
                "lora_config_hi": {
                    "version": 1,
                    "loras": [],
                    "ui": {"show_strengths": "single", "match": ""},
                },
                "lora_config_low": {
                    "version": 1,
                    "loras": [],
                    "ui": {"show_strengths": "single", "match": ""},
                },
            },
        },
        "ui_state": {
            "timeline_display_mode": TIMELINE_DISPLAY_MODE_DEFAULT,
            "section_edit_mode": SECTION_EDIT_MODE_TRIM_NEIGHBOR,
            "snap_mode": SNAP_MODE_FRAMES,
            "view_start_seconds": 0,
            "view_end_seconds": int(DEFAULT_DURATION_SECONDS),
            "selected_item_id": None,
            "state_revision": 0,
        },
        "assets": [],
        "director_track": {
            "track_id": "director",
            "sections": [],
        },
        "audio_tracks": [],
        "model_outputs": {},
        "validation": {
            "is_valid": True,
            "errors": [],
            "warnings": [],
            "info": [],
        },
    }
    return deepcopy(timeline)
