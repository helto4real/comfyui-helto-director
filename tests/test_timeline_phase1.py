import json

import pytest

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_SOURCE_GENERATED,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    BOUNDARY_MODE_BLEND_SEAM,
    BOUNDARY_MODE_CONTINUOUS_SHOT,
    BOUNDARY_MODE_HARD_CUT,
    DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT,
    DEFAULT_VIDEO_GUIDANCE_RANGE,
    GLOBAL_PROMPT_POSITION_SUFFIX,
    LORA_MERGE_MODE_ADD_TO_GLOBAL,
    MODEL_LORA_MODEL_LTX_2_3,
    MODEL_LORA_MODEL_WAN_2_2,
    MODEL_LORA_TARGET_HIGH_NOISE,
    MODEL_LORA_TARGET_LOW_NOISE,
    MODEL_LORA_TARGET_MAIN,
    SCHEMA_VERSION,
    SEQUENCE_ID_MAIN,
    SEQUENCE_NAME_MAIN,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
    SHOT_TYPE_EXTENDED,
    SHOT_TYPE_GENERATED,
    SHOT_TYPE_IMPORTED,
    TAKE_STATUS_ACCEPTED,
    TAKE_STATUS_CANDIDATE,
    VIDEO_TIMELINE_TYPE,
)
from shared.timeline import (
    create_default_video_timeline,
    detect_director_gaps,
    extract_shot_timeline,
    frame_to_seconds,
    merge_prompts,
    migrate_video_timeline,
    normalize_video_timeline,
    seconds_to_frame,
    ShotExtractionError,
    time_range_to_frames,
    validate_video_timeline,
)
from shared.timeline.global_settings import (
    default_global_settings,
    load_global_settings,
    save_global_settings,
)


def _error_codes(validation: dict) -> list[str]:
    return [entry["code"] for entry in validation["errors"]]


def _warning_codes(validation: dict) -> list[str]:
    return [entry["code"] for entry in validation["warnings"]]


def _shot_extraction_timeline(
    *,
    incoming_mode: str = BOUNDARY_MODE_HARD_CUT,
    outgoing_mode: str = BOUNDARY_MODE_HARD_CUT,
    outgoing_tail_frames: int = 5,
    outgoing_blend_frames: int = 3,
) -> dict:
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 8.0
    timeline["project"]["global_prompt"] = {
        "enabled": True,
        "prompt": "shared look",
        "position": GLOBAL_PROMPT_POSITION_SUFFIX,
    }
    timeline["project"]["metadata"]["character_references"] = [
        {
            "id": "hero_ref",
            "label": "hero",
            "kind": "character",
            "enabled": True,
            "description": "",
            "strength": 1.0,
            "image": {"path": "/mnt/media/hero.png"},
        }
    ]
    timeline["project"]["model_loras"]["global"][MODEL_LORA_MODEL_LTX_2_3][
        MODEL_LORA_TARGET_MAIN
    ]["ui"]["match"] = "cinematic"
    timeline["assets"] = [
        {
            "asset_id": "asset_prev_take",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_GENERATED,
            "path": "/mnt/output/prev.mp4",
            "name": "prev.mp4",
        },
        {
            "asset_id": "asset_next_clip",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_GENERATED,
            "path": "/mnt/output/next.mp4",
            "name": "next.mp4",
        },
        {
            "asset_id": "asset_source_video",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/source.mp4",
            "name": "source.mp4",
        },
    ]
    timeline["director_track"]["sections"] = [
        {
            "item_id": "prev_text",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 2.0,
            "prompt": "previous",
        },
        {
            "item_id": "middle_text",
            "type": SECTION_TYPE_TEXT,
            "start_time": 2.5,
            "end_time": 4.0,
            "prompt": "middle",
        },
        {
            "item_id": "middle_video",
            "type": SECTION_TYPE_VIDEO,
            "start_time": 4.0,
            "end_time": 5.0,
            "video": {"asset_id": "asset_source_video"},
            "prompt": "extend source",
            "source_in": 1.25,
        },
        {
            "item_id": "next_text",
            "type": SECTION_TYPE_TEXT,
            "start_time": 5.0,
            "end_time": 8.0,
            "prompt": "next",
        },
    ]
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_prev",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 2.0,
            "section_ids": ["prev_text"],
            "takes": [
                {
                    "take_id": "take_prev",
                    "asset_id": "asset_prev_take",
                    "status": TAKE_STATUS_ACCEPTED,
                }
            ],
            "accepted_take_id": "take_prev",
        },
        {
            "shot_id": "shot_middle",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 2.0,
            "end_time": 5.0,
            "section_ids": ["middle_text", "middle_video"],
            "lora_overrides": {
                "enabled": True,
                "merge_mode": LORA_MERGE_MODE_ADD_TO_GLOBAL,
                "targets": {
                    MODEL_LORA_MODEL_LTX_2_3: {
                        MODEL_LORA_TARGET_MAIN: {
                            "version": 1,
                            "loras": [],
                            "ui": {"show_strengths": "single", "match": "shot look"},
                        }
                    }
                },
            },
        },
        {
            "shot_id": "shot_next",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 5.0,
            "end_time": 8.0,
            "section_ids": ["next_text"],
            "takes": [
                {
                    "take_id": "take_next",
                    "asset_id": "asset_next_take",
                    "status": TAKE_STATUS_ACCEPTED,
                }
            ],
            "accepted_take_id": "take_next",
            "clip_instance": {"asset_id": "asset_next_clip"},
        },
    ]
    timeline["sequence"]["boundaries"] = [
        {
            "boundary_id": "boundary_prev_middle",
            "left_shot_id": "shot_prev",
            "right_shot_id": "shot_middle",
            "mode": incoming_mode,
            "tail_frames": 6,
            "blend_frames": 2,
            "metadata": {"side": "incoming"},
        },
        {
            "boundary_id": "boundary_middle_next",
            "left_shot_id": "shot_middle",
            "right_shot_id": "shot_next",
            "mode": outgoing_mode,
            "tail_frames": outgoing_tail_frames,
            "blend_frames": outgoing_blend_frames,
            "metadata": {"side": "outgoing"},
        },
    ]
    return timeline


def test_create_default_video_timeline_shape():
    timeline = create_default_video_timeline()
    global_settings = default_global_settings()

    assert timeline["schema_version"] == SCHEMA_VERSION
    assert timeline["type"] == VIDEO_TIMELINE_TYPE
    assert global_settings["timeline"]["allow_gaps"] is True
    assert global_settings["timeline"]["auto_close_gaps"] is False
    assert global_settings["timeline"]["minimum_section_duration_seconds"] == 0.25
    assert global_settings["storage"]["asset_root_directory"] == ""
    assert timeline["project"]["audio"]["use_native_audio"] is False
    assert "settings" not in timeline["project"]
    assert "privacy" not in timeline["project"]
    assert "display" not in timeline["project"]
    assert timeline["project"]["identity"]["project_id"].startswith("proj_")
    assert timeline["project"]["identity"]["name"] == "Untitled Project"
    assert timeline["project"]["storage"]["schema_version"] == 2
    assert "asset_root_directory" not in timeline["project"]["storage"]
    assert timeline["project"]["identity"]["project_id"] in timeline["project"]["storage"]["project_directory_name"]
    assert timeline["project"]["metadata"]["character_references_enabled"] is True
    assert timeline["project"]["metadata"]["character_references"] == []
    assert timeline["ui_state"]["view_start_seconds"] == 0
    assert timeline["ui_state"]["view_end_seconds"] == 5
    assert timeline["assets"] == []
    assert timeline["sequence"] == {
        "sequence_id": SEQUENCE_ID_MAIN,
        "name": SEQUENCE_NAME_MAIN,
        "shots": [],
        "boundaries": [],
    }
    assert timeline["director_track"]["sections"] == []
    assert timeline["audio_tracks"] == []
    assert timeline["project"]["model_loras"] == {
        "schema_version": 2,
        "global": {
            MODEL_LORA_MODEL_LTX_2_3: {
                MODEL_LORA_TARGET_MAIN: {
                    "version": 1,
                    "loras": [],
                    "ui": {"show_strengths": "single", "match": ""},
                },
            },
            MODEL_LORA_MODEL_WAN_2_2: {
                MODEL_LORA_TARGET_HIGH_NOISE: {
                    "version": 1,
                    "loras": [],
                    "ui": {"show_strengths": "single", "match": ""},
                },
                MODEL_LORA_TARGET_LOW_NOISE: {
                    "version": 1,
                    "loras": [],
                    "ui": {"show_strengths": "single", "match": ""},
                },
            },
        },
    }


def test_global_settings_defaults_save_clear_and_validate_asset_root(tmp_path):
    assert load_global_settings(tmp_path) == default_global_settings()

    saved = save_global_settings(
        {
            "storage": {"asset_root_directory": str(tmp_path / "assets")},
            "timeline": {
                "show_resolved_model_output": True,
                "allow_gaps": False,
                "auto_close_gaps": True,
                "minimum_section_duration_seconds": 1.5,
            },
            "privacy": {"mode": False},
        },
        tmp_path,
    )

    assert saved["storage"]["asset_root_directory"] == str(tmp_path / "assets")
    assert saved["timeline"]["allow_gaps"] is False
    assert saved["timeline"]["minimum_section_duration_seconds"] == 1.5
    assert load_global_settings(tmp_path) == saved

    cleared = save_global_settings({"storage": {"asset_root_directory": ""}}, tmp_path)
    assert cleared["storage"]["asset_root_directory"] == ""

    with pytest.raises(ValueError, match="GLOBAL_ASSET_ROOT_NOT_ABSOLUTE"):
        save_global_settings({"storage": {"asset_root_directory": "relative/assets"}}, tmp_path)


def test_migrate_accepts_json_string():
    timeline = create_default_video_timeline()
    timeline["schema_version"] = "0.9"

    migrated = migrate_video_timeline(json.dumps(timeline))

    assert migrated["schema_version"] == SCHEMA_VERSION
    assert migrated["type"] == VIDEO_TIMELINE_TYPE


def test_global_owned_project_fields_are_stripped_on_normalize():
    timeline = create_default_video_timeline()
    timeline["project"]["settings"] = {
        "show_resolved_model_output": True,
        "allow_gaps": False,
        "auto_close_gaps": True,
        "minimum_section_duration_seconds": 2.0,
    }
    timeline["project"]["privacy"] = {
        "mode": False,
        "hide_media_previews": True,
        "hide_text_prompts": False,
        "encrypt_previews": False,
    }
    timeline["project"]["display"] = {
        "show_section_labels": False,
        "show_thumbnails": False,
        "show_audio_waveforms": False,
    }
    timeline["project"]["global_prompt"]["show_effective_prompt"] = True
    timeline["project"]["audio"]["always_normalize"] = True
    timeline["project"]["storage"]["asset_root_directory"] = "/tmp/timeline_assets"

    normalized = normalize_video_timeline(timeline)

    assert "settings" not in normalized["project"]
    assert "privacy" not in normalized["project"]
    assert "display" not in normalized["project"]
    assert "show_effective_prompt" not in normalized["project"]["global_prompt"]
    assert "always_normalize" not in normalized["project"]["audio"]
    assert "asset_root_directory" not in normalized["project"]["storage"]


def test_normalization_fills_safe_defaults_and_preserves_unknown_fields():
    timeline = {
        "type": VIDEO_TIMELINE_TYPE,
        "project": {"duration_seconds": 10.0},
        "director_track": {
            "sections": [
                {
                    "type": SECTION_TYPE_IMAGE,
                    "start_time": 1.0,
                    "end_time": 2.0,
                    "custom_note": "keep me",
                }
            ]
        },
    }

    normalized = normalize_video_timeline(timeline)
    section = normalized["director_track"]["sections"][0]

    assert normalized["project"]["frame_rate"] == 24.0
    assert normalized["project"]["duration_seconds"] == 10.0
    assert normalized["ui_state"]["view_start_seconds"] == 0
    assert normalized["ui_state"]["view_end_seconds"] == 5
    assert section["custom_note"] == "keep me"
    assert section["image"] is None
    assert section["guide_strength"] == 1.0
    assert normalized["project"]["identity"]["project_id"].startswith("proj_")
    assert normalized["project"]["identity"]["project_id"] in normalized["project"]["storage"]["project_directory_name"]
    assert "privacy" not in normalized["project"]


def test_project_identity_storage_normalizes_and_preserves_stable_directory_name():
    timeline = {
        "type": VIDEO_TIMELINE_TYPE,
        "project": {
            "identity": {"project_id": "proj_custom123", "name": "First Name"},
            "storage": {
                "schema_version": 1,
                "asset_root_directory": "/tmp/timeline_assets",
                "project_directory_name": "first_name_proj_custom123",
            },
        },
    }

    normalized = normalize_video_timeline(timeline)
    assert normalized["project"]["identity"] == {
        "project_id": "proj_custom123",
        "name": "First Name",
    }
    assert normalized["project"]["storage"] == {
        "schema_version": 2,
        "project_directory_name": "first_name_proj_custom123",
    }

    normalized["project"]["identity"]["name"] = "Renamed Project"
    renamed = normalize_video_timeline(normalized)
    assert renamed["project"]["identity"]["name"] == "Renamed Project"
    assert renamed["project"]["storage"]["project_directory_name"] == "first_name_proj_custom123"


def test_project_storage_directory_regenerates_when_missing_or_mismatched():
    normalized = normalize_video_timeline(
        {
            "type": VIDEO_TIMELINE_TYPE,
            "project": {
                "identity": {"project_id": "proj_realid", "name": "My Scene"},
                "storage": {"project_directory_name": "wrong_proj_other"},
            },
        }
    )

    assert normalized["project"]["storage"]["project_directory_name"] == "my_scene_proj_realid"


def test_normalization_fills_sequence_shot_boundary_and_take_defaults():
    timeline = {
        "type": VIDEO_TIMELINE_TYPE,
        "project": {},
        "sequence": {
            "shots": [
                {
                    "shot_id": "shot_custom",
                    "section_ids": [123, None, "section_b"],
                    "takes": [{"take_id": "take_custom", "status": "Nope"}],
                    "clip_instance": {"asset_id": 42, "speed": "bad"},
                    "lora_overrides": {"merge_mode": "Nope", "targets": []},
                }
            ],
            "boundaries": [{"boundary_id": "boundary_custom", "mode": "Nope"}],
        },
    }

    normalized = normalize_video_timeline(timeline)
    sequence = normalized["sequence"]
    shot = sequence["shots"][0]
    boundary = sequence["boundaries"][0]
    take = shot["takes"][0]

    assert sequence["sequence_id"] == SEQUENCE_ID_MAIN
    assert sequence["name"] == SEQUENCE_NAME_MAIN
    assert shot["shot_id"] == "shot_custom"
    assert shot["type"] == SHOT_TYPE_GENERATED
    assert shot["section_ids"] == ["123", "section_b"]
    assert shot["lora_overrides"] == {
        "enabled": False,
        "merge_mode": "Inherit Global",
        "targets": {},
    }
    assert shot["clip_instance"]["asset_id"] == "42"
    assert shot["clip_instance"]["speed"] == 1.0
    assert take["take_id"] == "take_custom"
    assert take["status"] == TAKE_STATUS_CANDIDATE
    assert take["resolved_loras"] is None
    assert boundary["boundary_id"] == "boundary_custom"
    assert boundary["mode"] == BOUNDARY_MODE_HARD_CUT


def test_normalization_migrates_flat_sections_to_generated_shots():
    timeline = {
        "type": VIDEO_TIMELINE_TYPE,
        "project": {
            "model_loras": {
                "lora_config_hi": {
                    "loras": [{"enabled": True, "name": "hi.safetensors"}]
                },
                "lora_config_low": {
                    "loras": [{"enabled": True, "name": "low.safetensors"}]
                },
            }
        },
        "assets": [
            {
                "asset_id": "image_001",
                "type": ASSET_TYPE_IMAGE,
                "source_kind": ASSET_SOURCE_FILE_PATH,
                "path": "/mnt/media/reference.png",
            },
            {
                "asset_id": "video_001",
                "type": ASSET_TYPE_VIDEO,
                "source_kind": ASSET_SOURCE_FILE_PATH,
                "path": "/mnt/media/source.mp4",
            },
        ],
        "director_track": {
            "sections": [
                {
                    "item_id": "intro",
                    "type": SECTION_TYPE_TEXT,
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "prompt": "intro prompt",
                    "metadata": {"custom": True},
                },
                {
                    "item_id": "image/ref",
                    "type": SECTION_TYPE_IMAGE,
                    "start_time": 1.0,
                    "end_time": 2.0,
                    "image": {"asset_id": "image_001"},
                    "prompt": "image prompt",
                    "custom_note": "keep",
                },
                {
                    "item_id": "video ref",
                    "type": SECTION_TYPE_VIDEO,
                    "start_time": 3.0,
                    "end_time": 4.0,
                    "video": {"asset_id": "video_001"},
                    "prompt": "video prompt",
                    "source_in": 0.5,
                },
            ]
        },
        "audio_tracks": [
            {
                "track_id": "music",
                "clips": [
                    {
                        "item_id": "audio_1",
                        "audio": "/mnt/media/music.wav",
                        "start_time": 0.0,
                        "end_time": 4.0,
                    }
                ],
            }
        ],
    }

    normalized = normalize_video_timeline(timeline)
    sequence = normalized["sequence"]
    shots = sequence["shots"]
    boundaries = sequence["boundaries"]
    model_loras = normalized["project"]["model_loras"]

    assert normalized["director_track"]["sections"][0]["prompt"] == "intro prompt"
    assert normalized["director_track"]["sections"][1]["image"] == {
        "asset_id": "image_001"
    }
    assert normalized["director_track"]["sections"][1]["custom_note"] == "keep"
    assert normalized["director_track"]["sections"][2]["source_in"] == 0.5
    assert normalized["audio_tracks"][0]["clips"][0]["audio"] == "/mnt/media/music.wav"
    assert [shot["shot_id"] for shot in shots] == [
        "shot_intro",
        "shot_image_ref",
        "shot_video_ref",
    ]
    assert [shot["section_ids"] for shot in shots] == [
        ["intro"],
        ["image/ref"],
        ["video ref"],
    ]
    assert [(shot["start_time"], shot["end_time"]) for shot in shots] == [
        (0.0, 1.0),
        (1.0, 2.0),
        (3.0, 4.0),
    ]
    assert all(shot["type"] == SHOT_TYPE_GENERATED for shot in shots)
    assert all(shot["takes"] == [] for shot in shots)
    assert all(shot["accepted_take_id"] is None for shot in shots)
    assert all(shot["clip_instance"] is None for shot in shots)
    assert boundaries == [
        {
            "boundary_id": "boundary_shot_intro_to_shot_image_ref",
            "left_shot_id": "shot_intro",
            "right_shot_id": "shot_image_ref",
            "mode": BOUNDARY_MODE_HARD_CUT,
            "tail_frames": 5,
            "blend_frames": 3,
            "transition_prompt": "",
            "reuse_character_refs": True,
            "reuse_style": True,
            "metadata": {},
        }
    ]
    assert "lora_config_hi" not in model_loras
    assert "lora_config_low" not in model_loras
    assert model_loras["global"][MODEL_LORA_MODEL_LTX_2_3][MODEL_LORA_TARGET_MAIN]["loras"] == []
    assert model_loras["global"][MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_HIGH_NOISE]["loras"] == []
    assert model_loras["global"][MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_LOW_NOISE]["loras"] == []
    assert "image" not in shots[1]
    assert "video" not in shots[2]
    assert "thumbnail" not in json.dumps(sequence)
    assert "waveform" not in json.dumps(sequence)


def test_flat_section_migration_is_idempotent_and_uses_duplicate_suffixes():
    timeline = {
        "type": VIDEO_TIMELINE_TYPE,
        "project": {},
        "director_track": {
            "sections": [
                {
                    "item_id": "A/B",
                    "type": SECTION_TYPE_TEXT,
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "prompt": "first",
                },
                {
                    "item_id": "A B",
                    "type": SECTION_TYPE_TEXT,
                    "start_time": 1.0000005,
                    "end_time": 2.0,
                    "prompt": "second",
                },
                {
                    "item_id": "gap",
                    "type": SECTION_TYPE_TEXT,
                    "start_time": 2.25,
                    "end_time": 3.0,
                    "prompt": "third",
                },
            ]
        },
    }

    normalized = normalize_video_timeline(timeline)
    normalized_again = normalize_video_timeline(normalized)

    assert [shot["shot_id"] for shot in normalized["sequence"]["shots"]] == [
        "shot_A_B",
        "shot_A_B_2",
        "shot_gap",
    ]
    assert [boundary["boundary_id"] for boundary in normalized["sequence"]["boundaries"]] == [
        "boundary_shot_A_B_to_shot_A_B_2"
    ]
    assert normalized_again["sequence"] == normalized["sequence"]


def test_malformed_or_missing_sequence_migrates_from_sections():
    timeline = {
        "type": VIDEO_TIMELINE_TYPE,
        "project": {},
        "sequence": "not-a-sequence",
        "director_track": {
            "sections": [
                {
                    "item_id": "section_001",
                    "type": SECTION_TYPE_TEXT,
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "prompt": "text",
                }
            ]
        },
    }
    missing_sequence = {key: value for key, value in timeline.items() if key != "sequence"}

    normalized_malformed = normalize_video_timeline(timeline)
    normalized_missing = normalize_video_timeline(missing_sequence)

    assert normalized_malformed["sequence"]["shots"][0]["shot_id"] == "shot_section_001"
    assert normalized_missing["sequence"] == normalized_malformed["sequence"]


def test_existing_sequence_shots_are_preserved_instead_of_regenerated():
    timeline = {
        "type": VIDEO_TIMELINE_TYPE,
        "project": {},
        "director_track": {
            "sections": [
                {
                    "item_id": "section_001",
                    "type": SECTION_TYPE_TEXT,
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "prompt": "text",
                }
            ]
        },
        "sequence": {
            "shots": [
                {
                    "shot_id": "shot_authored",
                    "start_time": 10.0,
                    "end_time": 11.0,
                    "section_ids": ["custom_section"],
                }
            ],
            "boundaries": [
                {
                    "boundary_id": "boundary_authored",
                    "left_shot_id": "shot_previous",
                    "right_shot_id": "shot_authored",
                    "mode": BOUNDARY_MODE_HARD_CUT,
                }
            ],
        },
    }

    normalized = normalize_video_timeline(timeline)

    assert [shot["shot_id"] for shot in normalized["sequence"]["shots"]] == [
        "shot_authored"
    ]
    assert normalized["sequence"]["shots"][0]["start_time"] == 10.0
    assert normalized["sequence"]["shots"][0]["section_ids"] == ["custom_section"]
    assert [boundary["boundary_id"] for boundary in normalized["sequence"]["boundaries"]] == [
        "boundary_authored"
    ]


def test_json_roundtrip_preserves_migrated_sequence_data():
    timeline = {
        "type": VIDEO_TIMELINE_TYPE,
        "project": {},
        "director_track": {
            "sections": [
                {
                    "item_id": "section_001",
                    "type": SECTION_TYPE_TEXT,
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "prompt": "text",
                }
            ]
        },
    }

    normalized = normalize_video_timeline(timeline)
    roundtripped = normalize_video_timeline(json.dumps(normalized))

    assert roundtripped["sequence"] == normalized["sequence"]


def test_extract_generated_shot_creates_local_timeline_with_shifted_sections():
    timeline = _shot_extraction_timeline()

    result = extract_shot_timeline(timeline, "shot_middle")
    local = result["timeline"]
    context = result["shot_context"]
    local_shot = local["sequence"]["shots"][0]

    assert local["project"]["duration_seconds"] == 3.0
    assert local["director_track"]["sections"] == [
        {
            "item_id": "middle_text",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.5,
            "end_time": 2.0,
            "prompt": "middle",
        },
        {
            "item_id": "middle_video",
            "type": SECTION_TYPE_VIDEO,
            "start_time": 2.0,
            "end_time": 3.0,
            "video": {"asset_id": "asset_source_video"},
            "prompt": "extend source",
            "source_in": 1.25,
            "guide_strength": 1.0,
            "crop_mode": "Project Default",
            "source_out": None,
            "timing_mode": "Fit to Section",
            "video_guidance_range": "Last Frames",
            "video_guidance_frame_count": 17,
        },
    ]
    assert local["sequence"]["boundaries"] == []
    assert local_shot["shot_id"] == "shot_middle"
    assert local_shot["type"] == SHOT_TYPE_GENERATED
    assert local_shot["start_time"] == 0.0
    assert local_shot["end_time"] == 3.0
    assert local_shot["section_ids"] == ["middle_text", "middle_video"]
    assert context["shot_id"] == "shot_middle"
    assert context["original_start_time"] == 2.0
    assert context["original_end_time"] == 5.0
    assert context["time_offset_seconds"] == 2.0
    assert local["sequence"]["metadata"]["shot_extraction"] == context


def test_extract_imported_shot_preserves_clip_metadata():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["assets"].append(
        {
            "asset_id": "asset_imported",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/imported.mp4",
        }
    )
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_imported",
            "type": SHOT_TYPE_IMPORTED,
            "start_time": 0.0,
            "end_time": 2.0,
            "clip_instance": {
                "asset_id": "asset_imported",
                "source_in": 0.25,
                "source_out": 2.25,
            },
        }
    ]

    result = extract_shot_timeline(timeline, "shot_imported")
    local = result["timeline"]
    local_shot = local["sequence"]["shots"][0]

    assert local["project"]["duration_seconds"] == 2.0
    assert local["director_track"]["sections"] == []
    assert local_shot["type"] == SHOT_TYPE_IMPORTED
    assert local_shot["clip_instance"] == {
        "asset_id": "asset_imported",
        "source_in": 0.25,
        "source_out": 2.25,
        "speed": 1.0,
        "enabled": True,
    }


def test_extract_extended_shot_preserves_source_video_section():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 12.0
    timeline["assets"].append(
        {
            "asset_id": "asset_source",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/source.mp4",
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "source_section",
            "type": SECTION_TYPE_VIDEO,
            "start_time": 10.0,
            "end_time": 12.0,
            "video": {"asset_id": "asset_source"},
            "prompt": "continue",
            "source_in": 3.5,
        }
    )
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_extended",
            "type": SHOT_TYPE_EXTENDED,
            "start_time": 10.0,
            "end_time": 12.0,
            "section_ids": ["source_section"],
        }
    ]

    result = extract_shot_timeline(timeline, "shot_extended")
    local_section = result["timeline"]["director_track"]["sections"][0]

    assert result["timeline"]["project"]["duration_seconds"] == 2.0
    assert result["timeline"]["sequence"]["shots"][0]["type"] == SHOT_TYPE_EXTENDED
    assert local_section["start_time"] == 0.0
    assert local_section["end_time"] == 2.0
    assert local_section["video"] == {"asset_id": "asset_source"}
    assert local_section["source_in"] == 3.5


def test_shot_extraction_preserves_project_assets_references_and_loras():
    timeline = _shot_extraction_timeline()
    normalized = normalize_video_timeline(timeline)

    result = extract_shot_timeline(timeline, "shot_middle")
    local = result["timeline"]

    assert local["assets"] == normalized["assets"]
    assert local["project"]["global_prompt"] == normalized["project"]["global_prompt"]
    assert local["project"]["audio"] == normalized["project"]["audio"]
    assert "privacy" not in local["project"]
    assert "display" not in local["project"]
    assert "settings" not in local["project"]
    assert local["project"]["metadata"]["character_references"] == normalized["project"]["metadata"]["character_references"]
    assert local["project"]["model_loras"]["global"][MODEL_LORA_MODEL_LTX_2_3][
        MODEL_LORA_TARGET_MAIN
    ]["ui"]["match"] == "cinematic"
    assert local["sequence"]["shots"][0]["lora_overrides"]["targets"][
        MODEL_LORA_MODEL_LTX_2_3
    ][MODEL_LORA_TARGET_MAIN]["ui"]["match"] == "shot look"
    for asset in local["assets"]:
        assert "thumbnail" not in asset
        assert "waveform" not in asset
    for section in local["director_track"]["sections"]:
        assert "thumbnail" not in section
        assert "waveform" not in section


def test_shot_boundary_context_marks_hard_cut_as_no_continuity():
    timeline = _shot_extraction_timeline()

    context = extract_shot_timeline(timeline, "shot_middle")["shot_context"]["boundary_context"]

    assert context["previous_shot_id"] == "shot_prev"
    assert context["next_shot_id"] == "shot_next"
    assert context["incoming_boundary"]["mode"] == BOUNDARY_MODE_HARD_CUT
    assert context["outgoing_boundary"]["mode"] == BOUNDARY_MODE_HARD_CUT
    assert context["previous_accepted_take_id"] == "take_prev"
    assert context["previous_clip_asset_id"] == "asset_prev_take"
    assert context["next_accepted_take_id"] == "take_next"
    assert context["next_clip_asset_id"] == "asset_next_clip"
    assert context["continuity_policy"] == "none"
    assert context["tail_frames"] == 0
    assert context["blend_frames"] == 0
    assert context["incoming_continuity"]["status"] == "not_requested"
    assert context["incoming_continuity"]["clip_reference"] is None


def test_shot_boundary_context_allows_continuous_shot_tail():
    timeline = _shot_extraction_timeline(
        incoming_mode=BOUNDARY_MODE_CONTINUOUS_SHOT,
        outgoing_mode=BOUNDARY_MODE_CONTINUOUS_SHOT,
        outgoing_tail_frames=9,
    )

    context = extract_shot_timeline(timeline, "shot_middle")["shot_context"]["boundary_context"]

    assert context["incoming_boundary"]["mode"] == BOUNDARY_MODE_CONTINUOUS_SHOT
    assert context["incoming_continuity"]["status"] == "available"
    assert context["incoming_continuity"]["clip_reference"] == {
        "source_kind": "accepted_take",
        "shot_id": "shot_prev",
        "take_id": "take_prev",
        "asset_id": "asset_prev_take",
    }
    assert context["outgoing_boundary"]["mode"] == BOUNDARY_MODE_CONTINUOUS_SHOT
    assert context["outgoing_continuity_policy"] == "continuous"
    assert context["continuity_policy"] == "continuous"
    assert context["tail_frames"] == 9
    assert context["blend_frames"] == 0


def test_shot_boundary_context_warns_when_continuity_source_clip_is_missing():
    timeline = _shot_extraction_timeline(incoming_mode=BOUNDARY_MODE_CONTINUOUS_SHOT)
    timeline["sequence"]["shots"][0]["takes"] = []
    timeline["sequence"]["shots"][0]["accepted_take_id"] = None

    context = extract_shot_timeline(timeline, "shot_middle")["shot_context"]["boundary_context"]
    incoming = context["incoming_continuity"]

    assert incoming["policy"] == "continuous"
    assert incoming["status"] == "unavailable"
    assert incoming["warning_code"] == "SHOT_CONTINUITY_PREVIOUS_CLIP_MISSING"
    assert incoming["clip_reference"] is None


def test_shot_boundary_context_preserves_blend_seam_frames():
    timeline = _shot_extraction_timeline(
        incoming_mode=BOUNDARY_MODE_BLEND_SEAM,
        outgoing_mode=BOUNDARY_MODE_BLEND_SEAM,
        outgoing_tail_frames=7,
        outgoing_blend_frames=4,
    )

    context = extract_shot_timeline(timeline, "shot_middle")["shot_context"]["boundary_context"]

    assert context["incoming_continuity"]["status"] == "available"
    assert context["outgoing_boundary"]["mode"] == BOUNDARY_MODE_BLEND_SEAM
    assert context["outgoing_boundary"]["blend_frames"] == 4
    assert context["outgoing_continuity_policy"] == "blend"
    assert context["continuity_policy"] == "blend"
    assert context["tail_frames"] == 7
    assert context["blend_frames"] == 4


def test_shot_extraction_missing_shot_raises_clear_error():
    with pytest.raises(ShotExtractionError, match="Shot 'missing' was not found"):
        extract_shot_timeline(_shot_extraction_timeline(), "missing")


def test_shot_extraction_does_not_mutate_full_timeline_workflow_shape():
    timeline = _shot_extraction_timeline()
    normalized_before = normalize_video_timeline(timeline)

    result = extract_shot_timeline(timeline, "shot_middle")
    normalized_after = normalize_video_timeline(timeline)

    assert normalized_after == normalized_before
    assert len(normalized_before["sequence"]["shots"]) == 3
    assert len(normalized_before["director_track"]["sections"]) == 4
    assert result["timeline"]["sequence"]["shots"][0]["shot_id"] == "shot_middle"
    assert len(result["timeline"]["sequence"]["shots"]) == 1


def test_normalization_drops_legacy_lora_fields_and_creates_model_targets():
    timeline = create_default_video_timeline()
    timeline["project"]["model_loras"] = {
        "lora_config_hi": {"loras": [{"enabled": True, "name": "hi.safetensors"}]},
        "lora_config_low": {"loras": [{"enabled": True, "name": "low.safetensors"}]},
    }

    normalized = normalize_video_timeline(timeline)
    model_loras = normalized["project"]["model_loras"]

    assert "lora_config_hi" not in model_loras
    assert "lora_config_low" not in model_loras
    assert model_loras["schema_version"] == 2
    assert model_loras["global"][MODEL_LORA_MODEL_LTX_2_3][MODEL_LORA_TARGET_MAIN]["loras"] == []
    assert model_loras["global"][MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_HIGH_NOISE]["loras"] == []
    assert model_loras["global"][MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_LOW_NOISE]["loras"] == []


def test_validation_accepts_valid_shot_timeline_and_project_lora_defaults():
    timeline = create_default_video_timeline()
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "valid prompt",
        }
    )

    validation = validate_video_timeline(timeline)

    assert validation["is_valid"] is True
    assert _error_codes(validation) == []


def test_validation_reports_invalid_shot_timing_and_overlap():
    timeline = create_default_video_timeline()
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_bad",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 1.0,
            "end_time": 1.0,
        },
        {
            "shot_id": "shot_a",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 2.0,
        },
        {
            "shot_id": "shot_b",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 1.5,
            "end_time": 3.0,
        },
    ]

    validation = validate_video_timeline(timeline)
    codes = _error_codes(validation)

    assert "SHOT_INVALID_TIME_RANGE" in codes
    assert "SHOT_OVERLAP" in codes


def test_validation_reports_invalid_section_and_boundary_references():
    timeline = create_default_video_timeline()
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_001",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 1.0,
            "section_ids": ["missing_section"],
        }
    ]
    timeline["sequence"]["boundaries"] = [
        {
            "boundary_id": "boundary_001",
            "left_shot_id": "shot_001",
            "right_shot_id": "missing_shot",
            "mode": BOUNDARY_MODE_HARD_CUT,
        }
    ]

    validation = validate_video_timeline(timeline)
    codes = _error_codes(validation)

    assert "SHOT_SECTION_NOT_FOUND" in codes
    assert "BOUNDARY_RIGHT_SHOT_NOT_FOUND" in codes


def test_validation_reports_invalid_raw_boundary_mode_and_take_status():
    timeline = create_default_video_timeline()
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_001",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 1.0,
            "takes": [{"take_id": "take_001", "status": "Nope"}],
        },
        {
            "shot_id": "shot_002",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 1.0,
            "end_time": 2.0,
        },
    ]
    timeline["sequence"]["boundaries"] = [
        {
            "boundary_id": "boundary_001",
            "left_shot_id": "shot_001",
            "right_shot_id": "shot_002",
            "mode": "Jump Cut",
        }
    ]

    validation = validate_video_timeline(timeline)
    codes = _error_codes(validation)

    assert "BOUNDARY_MODE_INVALID" in codes
    assert "TAKE_STATUS_INVALID" in codes


def test_validation_reports_stale_accepted_take_and_missing_take_asset():
    timeline = create_default_video_timeline()
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_001",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 1.0,
            "takes": [
                {
                    "take_id": "take_001",
                    "status": TAKE_STATUS_CANDIDATE,
                    "asset_id": "missing_asset",
                }
            ],
            "accepted_take_id": "stale_take",
        }
    ]

    validation = validate_video_timeline(timeline)
    codes = _error_codes(validation)

    assert "SHOT_ACCEPTED_TAKE_NOT_FOUND" in codes
    assert "TAKE_ASSET_NOT_FOUND" in codes


def test_validation_reports_missing_imported_clip_asset_and_clip_reference_error():
    timeline = create_default_video_timeline()
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_imported",
            "type": "Imported",
            "start_time": 0.0,
            "end_time": 1.0,
        },
        {
            "shot_id": "shot_stale_clip",
            "type": "Imported",
            "start_time": 1.0,
            "end_time": 2.0,
            "clip_instance": {"asset_id": "missing_asset"},
        },
    ]

    validation = validate_video_timeline(timeline)

    assert "IMPORTED_SHOT_MISSING_CLIP_ASSET" in _warning_codes(validation)
    assert "SHOT_CLIP_INSTANCE_ASSET_NOT_FOUND" in _error_codes(validation)


def test_validation_reports_invalid_project_lora_target_and_legacy_loras_are_ignored():
    timeline = create_default_video_timeline()
    timeline["project"]["model_loras"] = {
        "lora_config_hi": {"loras": [{"enabled": True, "name": "hi.safetensors"}]},
        "global": {
            MODEL_LORA_MODEL_LTX_2_3: {
                MODEL_LORA_TARGET_HIGH_NOISE: {
                    "version": 1,
                    "loras": [],
                    "ui": {"show_strengths": "single", "match": ""},
                },
            }
        },
    }

    validation = validate_video_timeline(timeline)
    normalized = normalize_video_timeline(timeline)

    assert "MODEL_LORA_TARGET_INVALID" in _error_codes(validation)
    assert "lora_config_hi" not in normalized["project"]["model_loras"]


def test_validation_accepts_valid_shot_lora_override_and_reports_invalid_merge_mode():
    valid_timeline = create_default_video_timeline()
    valid_timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "valid prompt",
        }
    )
    valid_timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_001",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 1.0,
            "section_ids": ["section_001"],
            "lora_overrides": {
                "enabled": True,
                "merge_mode": LORA_MERGE_MODE_ADD_TO_GLOBAL,
                "targets": {
                    MODEL_LORA_MODEL_LTX_2_3: {
                        MODEL_LORA_TARGET_MAIN: {
                            "version": 1,
                            "loras": [],
                            "ui": {"show_strengths": "single", "match": ""},
                        }
                    }
                },
            },
        }
    ]
    invalid_timeline = create_default_video_timeline()
    invalid_timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_bad_lora",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 1.0,
            "lora_overrides": {"enabled": True, "merge_mode": "Sideways"},
        }
    ]

    valid_validation = validate_video_timeline(valid_timeline)
    invalid_validation = validate_video_timeline(invalid_timeline)

    assert "SHOT_LORA_MERGE_MODE_INVALID" not in _error_codes(valid_validation)
    assert valid_validation["is_valid"] is True
    assert "SHOT_LORA_MERGE_MODE_INVALID" in _error_codes(invalid_validation)


def test_validation_checks_take_resolved_loras_snapshot_shape():
    valid_timeline = create_default_video_timeline()
    valid_timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_001",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 1.0,
            "takes": [
                {
                    "take_id": "take_001",
                    "status": TAKE_STATUS_CANDIDATE,
                    "model_family": "WAN",
                    "model_version": "2.2",
                    "resolved_loras": {
                        "model_family": "WAN",
                        "model_version": "2.2",
                        "targets": {
                            MODEL_LORA_TARGET_HIGH_NOISE: [
                                {"name": "high.safetensors", "strength_model": 0.8}
                            ],
                            MODEL_LORA_TARGET_LOW_NOISE: [
                                {"name": "low.safetensors", "strength_model": 0.6}
                            ],
                        },
                    },
                }
            ],
        }
    ]
    invalid_timeline = create_default_video_timeline()
    invalid_timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_001",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 1.0,
            "takes": [
                {
                    "take_id": "take_001",
                    "status": TAKE_STATUS_CANDIDATE,
                    "model_family": "LTX",
                    "model_version": "2.3",
                    "resolved_loras": {
                        "model_family": "LTX",
                        "model_version": "2.3",
                        "targets": {
                            MODEL_LORA_TARGET_HIGH_NOISE: [
                                {"name": "wrong.safetensors", "thumbnail": "data:image/png;base64,AAAA"}
                            ]
                        },
                    },
                }
            ],
        }
    ]

    valid_validation = validate_video_timeline(valid_timeline)
    invalid_validation = validate_video_timeline(invalid_timeline)

    assert "TAKE_RESOLVED_LORAS_TARGET_INVALID" not in _error_codes(valid_validation)
    assert "TAKE_RESOLVED_LORAS_TARGET_INVALID" in _error_codes(invalid_validation)
    assert "TAKE_RESOLVED_LORAS_EMBEDDED_MEDIA_NOT_ALLOWED" in _error_codes(invalid_validation)


def test_validation_warns_on_lora_change_across_continuous_boundary_but_not_hard_cut():
    timeline = create_default_video_timeline()
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_a",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 0.0,
            "end_time": 1.0,
        },
        {
            "shot_id": "shot_b",
            "type": SHOT_TYPE_GENERATED,
            "start_time": 1.0,
            "end_time": 2.0,
            "lora_overrides": {
                "enabled": True,
                "merge_mode": "Replace Global",
                "targets": {
                    MODEL_LORA_MODEL_LTX_2_3: {
                        MODEL_LORA_TARGET_MAIN: {
                            "version": 1,
                            "loras": [],
                            "ui": {"show_strengths": "single", "match": "different"},
                        }
                    }
                },
            },
        },
    ]
    continuous = json.loads(json.dumps(timeline))
    continuous["sequence"]["boundaries"] = [
        {
            "boundary_id": "boundary_continuous",
            "left_shot_id": "shot_a",
            "right_shot_id": "shot_b",
            "mode": BOUNDARY_MODE_CONTINUOUS_SHOT,
        }
    ]
    blend = json.loads(json.dumps(timeline))
    blend["sequence"]["boundaries"] = [
        {
            "boundary_id": "boundary_blend",
            "left_shot_id": "shot_a",
            "right_shot_id": "shot_b",
            "mode": BOUNDARY_MODE_BLEND_SEAM,
        }
    ]
    hard_cut = json.loads(json.dumps(timeline))
    hard_cut["sequence"]["boundaries"] = [
        {
            "boundary_id": "boundary_hard",
            "left_shot_id": "shot_a",
            "right_shot_id": "shot_b",
            "mode": BOUNDARY_MODE_HARD_CUT,
        }
    ]

    assert "BOUNDARY_LORA_STACK_MISMATCH" in _warning_codes(validate_video_timeline(continuous))
    assert "BOUNDARY_LORA_STACK_MISMATCH" in _warning_codes(validate_video_timeline(blend))
    assert "BOUNDARY_LORA_STACK_MISMATCH" not in _warning_codes(validate_video_timeline(hard_cut))


def test_video_section_normalization_defaults_to_tail_guidance():
    timeline = create_default_video_timeline()
    timeline["assets"].append(
        {
            "asset_id": "video_001",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/source.mp4",
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_VIDEO,
            "start_time": 0.0,
            "end_time": 1.0,
            "video": {"asset_id": "video_001"},
            "prompt": "extend",
        }
    )

    normalized = normalize_video_timeline(timeline)
    section = normalized["director_track"]["sections"][0]

    assert section["video_guidance_range"] == DEFAULT_VIDEO_GUIDANCE_RANGE
    assert section["video_guidance_frame_count"] == DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT


def test_normalization_fills_asset_defaults():
    timeline = create_default_video_timeline()
    timeline["assets"].append(
        {
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/reference.png",
        }
    )

    normalized = normalize_video_timeline(timeline)
    asset = normalized["assets"][0]

    assert asset["asset_id"] == "asset_001"
    assert asset["name"] == "reference.png"
    assert asset["metadata"] == {}


def test_character_reference_metadata_normalizes():
    timeline = create_default_video_timeline()
    timeline["project"]["metadata"]["character_references"].append(
        {
            "label": "Hero",
            "strength": 3.0,
            "image": {
                "path": "/mnt/media/hero.png",
                "name": "hero.png",
                "thumbnail": "not-normalized-away",
            },
        }
    )

    normalized = normalize_video_timeline(timeline)
    reference = normalized["project"]["metadata"]["character_references"][0]

    assert normalized["project"]["metadata"]["character_references_enabled"] is True
    assert reference["id"] == "image1"
    assert reference["label"] == "image1"
    assert reference["kind"] == "character"
    assert reference["enabled"] is True
    assert reference["description"] == ""
    assert reference["strength"] == 1.0
    assert reference["image"]["path"] == "/mnt/media/hero.png"
    assert "asset_id" not in reference["image"]


def test_character_reference_validation_and_prompt_warnings():
    timeline = create_default_video_timeline()
    timeline["project"]["metadata"]["character_references"] = [
        {
            "id": "ref_1",
            "label": "image1",
            "kind": "character",
            "enabled": False,
            "description": "",
            "strength": 1.0,
            "image": {"path": "/mnt/media/hero.png"},
        },
        {
            "id": "ref_2",
            "label": "image1",
            "kind": "character",
            "enabled": True,
            "description": "",
            "strength": 1.0,
            "image": {"path": "/mnt/media/other.png", "thumbnail": "data:image/png;base64,AAAA"},
        },
        {
            "id": "ref_3",
            "label": "image2",
            "kind": "character",
            "enabled": True,
            "description": "",
            "strength": 1.0,
            "image": None,
        },
    ]
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "@image1:character and @image3:character",
        }
    )

    validation = validate_video_timeline(timeline)
    error_codes = [entry["code"] for entry in validation["errors"]]
    warning_codes = [entry["code"] for entry in validation["warnings"]]

    assert "CHARACTER_REFERENCE_DUPLICATE_LABEL" in error_codes
    assert "CHARACTER_REFERENCE_EMBEDDED_MEDIA_NOT_ALLOWED" in error_codes
    assert "CHARACTER_REFERENCE_MISSING_IMAGE" in error_codes
    assert "PROMPT_REFERENCE_UNKNOWN" in warning_codes


def test_character_reference_global_toggle_warns_disabled_not_unknown():
    timeline = create_default_video_timeline()
    timeline["project"]["metadata"]["character_references_enabled"] = False
    timeline["project"]["metadata"]["character_references"] = [
        {
            "id": "ref_1",
            "label": "image1",
            "kind": "character",
            "enabled": True,
            "description": "",
            "strength": 1.0,
            "image": {"path": "/mnt/media/hero.png"},
        },
    ]
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "@image1:character and @image2:character",
        }
    )

    validation = validate_video_timeline(timeline)
    warning_codes = [entry["code"] for entry in validation["warnings"]]

    assert validation["errors"] == []
    assert warning_codes.count("PROMPT_REFERENCE_DISABLED") == 2
    assert "PROMPT_REFERENCE_UNKNOWN" not in warning_codes


def test_text_section_empty_prompt_gives_error():
    timeline = create_default_video_timeline()
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": " ",
        }
    )

    validation = validate_video_timeline(timeline)

    assert validation["is_valid"] is False
    assert [entry["code"] for entry in validation["errors"]] == [
        "TEXT_SECTION_EMPTY_PROMPT"
    ]


def test_image_section_missing_image_gives_error():
    timeline = create_default_video_timeline()
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_IMAGE,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "",
        }
    )

    validation = validate_video_timeline(timeline)

    assert validation["is_valid"] is False
    assert "IMAGE_SECTION_MISSING_IMAGE" in [
        entry["code"] for entry in validation["errors"]
    ]


def test_media_reference_to_asset_validates():
    timeline = create_default_video_timeline()
    timeline["assets"].append(
        {
            "asset_id": "image_001",
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/reference.png",
            "name": "reference.png",
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_IMAGE,
            "start_time": 0.0,
            "end_time": 1.0,
            "image": {"asset_id": "image_001"},
            "prompt": "",
        }
    )

    validation = validate_video_timeline(timeline)

    assert validation["is_valid"] is True
    assert validation["errors"] == []


def test_missing_asset_reference_gives_error():
    timeline = create_default_video_timeline()
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_IMAGE,
            "start_time": 0.0,
            "end_time": 1.0,
            "image": {"asset_id": "missing_asset"},
            "prompt": "",
        }
    )

    validation = validate_video_timeline(timeline)

    assert validation["is_valid"] is False
    assert [entry["code"] for entry in validation["errors"]] == [
        "IMAGE_SECTION_MEDIA_ASSET_NOT_FOUND"
    ]


def test_embedded_media_payload_gives_error():
    timeline = create_default_video_timeline()
    timeline["assets"].append(
        {
            "asset_id": "image_001",
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/reference.png",
            "waveform": [0.1, 0.2],
        }
    )

    validation = validate_video_timeline(timeline)

    assert validation["is_valid"] is False
    assert [entry["code"] for entry in validation["errors"]] == [
        "ASSET_EMBEDDED_MEDIA_NOT_ALLOWED"
    ]


def test_director_gap_gives_info_not_error():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 3.0
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 1.0,
            "end_time": 2.0,
            "prompt": "middle",
        }
    )

    gaps = detect_director_gaps(timeline)
    validation = validate_video_timeline(timeline)

    assert gaps == [
        {
            "type": "No Guidance",
            "start_time": 0.0,
            "end_time": 1.0,
            "duration_seconds": 1.0,
        },
        {
            "type": "No Guidance",
            "start_time": 2.0,
            "end_time": 3.0,
            "duration_seconds": 1.0,
        },
    ]
    assert validation["is_valid"] is True
    assert [entry["code"] for entry in validation["info"]] == [
        "DIRECTOR_GAP",
        "DIRECTOR_GAP",
    ]


def test_validation_uses_global_gap_policy_and_minimum_duration():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 3.0
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_short",
            "type": SECTION_TYPE_TEXT,
            "start_time": 1.0,
            "end_time": 1.5,
            "prompt": "short middle",
        }
    )
    global_settings = default_global_settings()
    global_settings["timeline"]["allow_gaps"] = False
    global_settings["timeline"]["minimum_section_duration_seconds"] = 1.0

    validation = validate_video_timeline(timeline, global_settings)

    assert "SECTION_BELOW_MINIMUM_DURATION" in _error_codes(validation)
    assert [entry["code"] for entry in validation["errors"]].count("DIRECTOR_GAP") == 2


def test_time_mapping_uses_exclusive_end_frame():
    assert seconds_to_frame(1.5, 24.0) == 36
    assert frame_to_seconds(48, 24.0) == 2.0
    assert time_range_to_frames(1.0, 2.0, 24.0) == {
        "start_frame": 24,
        "end_frame_exclusive": 48,
        "frame_count": 24,
    }


def test_merge_prompts_prefix_suffix_and_empty_prompt():
    assert merge_prompts("section", "global", True) == "global, section"
    assert (
        merge_prompts("section", "global", True, GLOBAL_PROMPT_POSITION_SUFFIX)
        == "section, global"
    )
    assert merge_prompts("", "global", True) == "global"
    assert merge_prompts("section", "global", False) == "section"
