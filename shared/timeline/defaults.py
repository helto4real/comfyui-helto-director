from __future__ import annotations

from copy import deepcopy

from ..contracts.video_timeline import (
    BOUNDARY_MODE_HARD_CUT,
    CROP_MODE_PROJECT_DEFAULT,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_AUDIO_FADE_IN_SECONDS,
    DEFAULT_AUDIO_FADE_OUT_SECONDS,
    DEFAULT_AUDIO_VOLUME,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_FRAME_RATE,
    DEFAULT_ORIENTATION,
    DEFAULT_QUALITY_PRESET,
    DEFAULT_USE_NATIVE_AUDIO,
    GLOBAL_PROMPT_POSITION_PREFIX,
    LORA_MERGE_MODE_INHERIT_GLOBAL,
    MODEL_LORA_SCHEMA_VERSION,
    MODEL_LORA_TARGET_DESCRIPTORS,
    SCHEMA_VERSION,
    SECTION_EDIT_MODE_TRIM_NEIGHBOR,
    SEQUENCE_ID_MAIN,
    SEQUENCE_NAME_MAIN,
    SHOT_TYPE_GENERATED,
    TAKE_STATUS_CANDIDATE,
    SNAP_MODE_FRAMES,
    TIMELINE_DISPLAY_MODE_DEFAULT,
    VIDEO_TIMELINE_TYPE,
)
from .project_storage import (
    create_default_project_identity,
    create_default_project_storage,
)


def create_default_lora_stack() -> dict:
    return {
        "version": 1,
        "loras": [],
        "ui": {"show_strengths": "single", "match": ""},
    }


def create_default_video_timeline() -> dict:
    project_identity = create_default_project_identity()
    timeline = {
        "schema_version": SCHEMA_VERSION,
        "type": VIDEO_TIMELINE_TYPE,
        "project": {
            "identity": project_identity,
            "duration_seconds": DEFAULT_DURATION_SECONDS,
            "frame_rate": DEFAULT_FRAME_RATE,
            "aspect_ratio": DEFAULT_ASPECT_RATIO,
            "orientation": DEFAULT_ORIENTATION,
            "quality_preset": DEFAULT_QUALITY_PRESET,
            "default_crop_mode": CROP_MODE_PROJECT_DEFAULT,
            "global_prompt": {
                "enabled": False,
                "prompt": "",
                "position": GLOBAL_PROMPT_POSITION_PREFIX,
            },
            "audio": {
                "use_native_audio": DEFAULT_USE_NATIVE_AUDIO,
                "normalization_mode": "Integrated LUFS",
                "target_lufs": -16.0,
                "true_peak_limit_db": -1.0,
                "default_volume": DEFAULT_AUDIO_VOLUME,
                "default_fade_in_seconds": DEFAULT_AUDIO_FADE_IN_SECONDS,
                "default_fade_out_seconds": DEFAULT_AUDIO_FADE_OUT_SECONDS,
            },
            "metadata": {
                "character_references_enabled": True,
                "character_references": [],
            },
            "storage": create_default_project_storage(
                project_id=project_identity["project_id"],
                name=project_identity["name"],
            ),
            "model_loras": create_default_project_model_loras(),
        },
        "ui_state": {
            "timeline_display_mode": TIMELINE_DISPLAY_MODE_DEFAULT,
            "section_edit_mode": SECTION_EDIT_MODE_TRIM_NEIGHBOR,
            "snap_mode": SNAP_MODE_FRAMES,
            "view_start_seconds": 0,
            "view_end_seconds": int(DEFAULT_DURATION_SECONDS),
            "selected_item_id": None,
            "selected_item_ids": [],
            "state_revision": 0,
        },
        "assets": [],
        "sequence": create_default_sequence(),
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


def create_default_project_model_loras() -> dict:
    return {
        "schema_version": MODEL_LORA_SCHEMA_VERSION,
        "global": {
            model_key: {
                target_key: create_default_lora_stack()
                for target_key in descriptor["targets"]
            }
            for model_key, descriptor in MODEL_LORA_TARGET_DESCRIPTORS.items()
        },
    }


def create_default_sequence() -> dict:
    return {
        "sequence_id": SEQUENCE_ID_MAIN,
        "name": SEQUENCE_NAME_MAIN,
        "shots": [],
        "boundaries": [],
    }


def create_default_shot(index: int = 1) -> dict:
    return {
        "shot_id": f"shot_{index:03d}",
        "name": "",
        "type": SHOT_TYPE_GENERATED,
        "start_time": 0.0,
        "end_time": 0.0,
        "section_ids": [],
        "lora_overrides": {
            "enabled": False,
            "merge_mode": LORA_MERGE_MODE_INHERIT_GLOBAL,
            "targets": {},
        },
        "takes": [],
        "accepted_take_id": None,
        "clip_instance": None,
        "metadata": {},
    }


def create_default_boundary(index: int = 1) -> dict:
    return {
        "boundary_id": f"boundary_{index:03d}",
        "left_shot_id": None,
        "right_shot_id": None,
        "mode": BOUNDARY_MODE_HARD_CUT,
        "tail_frames": 5,
        "blend_frames": 3,
        "transition_prompt": "",
        "reuse_character_refs": True,
        "reuse_style": True,
        "metadata": {},
    }


def create_default_take(index: int = 1) -> dict:
    return {
        "take_id": f"take_{index:03d}",
        "asset_id": None,
        "status": TAKE_STATUS_CANDIDATE,
        "seed": None,
        "model_family": "",
        "model_version": "",
        "plan_hash": "",
        "prompt_hash": "",
        "resolved_loras": None,
        "metadata": {},
    }


def create_default_clip_instance() -> dict:
    return {
        "asset_id": None,
        "source_in": 0.0,
        "source_out": None,
        "speed": 1.0,
        "enabled": True,
    }
