import json

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    BOUNDARY_MODE_HARD_CUT,
    DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT,
    DEFAULT_VIDEO_GUIDANCE_RANGE,
    GLOBAL_PROMPT_POSITION_SUFFIX,
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
    SHOT_TYPE_GENERATED,
    TAKE_STATUS_CANDIDATE,
    VIDEO_TIMELINE_TYPE,
)
from shared.timeline import (
    create_default_video_timeline,
    detect_director_gaps,
    frame_to_seconds,
    merge_prompts,
    migrate_video_timeline,
    normalize_video_timeline,
    seconds_to_frame,
    time_range_to_frames,
    validate_video_timeline,
)


def test_create_default_video_timeline_shape():
    timeline = create_default_video_timeline()

    assert timeline["schema_version"] == SCHEMA_VERSION
    assert timeline["type"] == VIDEO_TIMELINE_TYPE
    assert timeline["project"]["settings"]["allow_gaps"] is True
    assert timeline["project"]["settings"]["auto_close_gaps"] is False
    assert timeline["project"]["audio"]["use_native_audio"] is False
    assert timeline["project"]["privacy"] == {"mode": False}
    assert timeline["project"]["display"]["show_audio_waveforms"] is True
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


def test_migrate_accepts_json_string():
    timeline = create_default_video_timeline()
    timeline["schema_version"] = "0.9"

    migrated = migrate_video_timeline(json.dumps(timeline))

    assert migrated["schema_version"] == SCHEMA_VERSION
    assert migrated["type"] == VIDEO_TIMELINE_TYPE


def test_legacy_privacy_flags_normalize_to_single_mode():
    timeline = create_default_video_timeline()
    timeline["project"]["privacy"] = {
        "mode": False,
        "hide_media_previews": True,
        "hide_text_prompts": False,
        "encrypt_previews": False,
    }

    normalized = normalize_video_timeline(timeline)

    assert normalized["project"]["privacy"] == {"mode": True}


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
