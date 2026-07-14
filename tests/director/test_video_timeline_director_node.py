import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from shared.contracts.video_timeline import SECTION_TYPE_TEXT
from shared.timeline import create_default_video_timeline
import pytest


def get_video_timeline_director():
    module_path = Path(__file__).resolve().parents[2]
    sys_module_name = str(module_path).replace(".", "_x_")
    spec = importlib.util.spec_from_file_location(
        sys_module_name,
        module_path / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)

    previous = sys.modules.get(sys_module_name)
    previous_path = list(sys.path)
    sys.modules[sys_module_name] = module
    try:
        sys.path = [
            path
            for path in sys.path
            if Path(path or ".").resolve() != module_path
        ]
        spec.loader.exec_module(module)
        extension = asyncio.run(module.comfy_entrypoint())
        return asyncio.run(extension.get_node_list())[0]
    finally:
        sys.path = previous_path
        if previous is None:
            sys.modules.pop(sys_module_name, None)
        else:
            sys.modules[sys_module_name] = previous
def test_director_schema_has_project_widgets_and_no_media_inputs():
    VideoTimelineDirector = get_video_timeline_director()
    schema = VideoTimelineDirector.define_schema()
    input_ids = [input_item.id for input_item in schema.inputs]
    input_types = [input_item.io_type for input_item in schema.inputs]

    assert input_ids == [
        "duration_seconds",
        "frame_rate",
        "aspect_ratio",
        "orientation",
        "quality_preset",
        "video_timeline_json",
        "privacy_mode_reference",
        "private_execution",
    ]
    assert "IMAGE" not in input_types
    assert "VIDEO" not in input_types
    assert "AUDIO" not in input_types
    assert "width" not in input_ids
    assert "height" not in input_ids
    assert schema.inputs[input_ids.index("aspect_ratio")].options == ["16:9", "4:3", "3:2", "21:9", "1:1"]
    assert schema.inputs[input_ids.index("orientation")].options == ["Landscape", "Portrait"]
    assert schema.inputs[input_ids.index("video_timeline_json")].extra_dict["hidden"] is True
    assert schema.inputs[input_ids.index("privacy_mode_reference")].extra_dict["hidden"] is True
    assert schema.inputs[input_ids.index("private_execution")].extra_dict["hidden"] is True


def test_director_runs_without_frontend_state():
    VideoTimelineDirector = get_video_timeline_director()
    timeline, validation, frame_rate = VideoTimelineDirector.execute().result

    assert timeline["type"] == "VIDEO_TIMELINE"
    assert timeline["project"]["duration_seconds"] == 5.0
    assert frame_rate == timeline["project"]["frame_rate"] == 24.0
    assert validation["is_valid"] is True
    assert [entry["code"] for entry in validation["info"]] == ["DIRECTOR_GAP"]


def test_director_applies_visible_widgets_as_authoritative_fields():
    VideoTimelineDirector = get_video_timeline_director()
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 12.0
    timeline["project"]["aspect_ratio"] = "1:1"
    timeline["project"]["metadata"]["character_references"].append(
        {
            "id": "ref_hero",
            "label": "image1",
            "kind": "character",
            "enabled": True,
            "description": "red jacket and black bob haircut",
            "strength": 0.75,
            "image": {"path": "/mnt/media/hero.png", "name": "hero.png"},
        }
    )
    timeline["ui_state"]["view_start_seconds"] = 2
    timeline["ui_state"]["view_end_seconds"] = 4

    output_timeline, validation, frame_rate = VideoTimelineDirector.execute(
        duration_seconds=12.0,
        frame_rate=30.0,
        aspect_ratio="16:9",
        orientation="Portrait",
        quality_preset="High",
        video_timeline_json=json.dumps(timeline),
    ).result

    assert validation["is_valid"] is True
    assert output_timeline["project"]["duration_seconds"] == 12.0
    assert output_timeline["project"]["frame_rate"] == 30.0
    assert frame_rate == 30.0
    assert output_timeline["project"]["aspect_ratio"] == "16:9"
    assert output_timeline["project"]["orientation"] == "Portrait"
    assert output_timeline["project"]["quality_preset"] == "High"
    assert output_timeline["project"]["metadata"]["character_references"][0]["id"] == "ref_hero"
    assert output_timeline["project"]["metadata"]["character_references"][0]["description"] == "red jacket and black bob haircut"
    assert output_timeline["ui_state"]["view_start_seconds"] == 2
    assert output_timeline["ui_state"]["view_end_seconds"] == 4


def test_director_drops_legacy_lora_config_fields_from_timeline_data():
    VideoTimelineDirector = get_video_timeline_director()
    timeline = create_default_video_timeline()
    timeline["project"]["model_loras"] = {
        "lora_config_hi": {"loras": [{"enabled": True, "name": "hi.safetensors"}]},
        "lora_config_low": {"loras": [{"enabled": True, "name": "low.safetensors"}]},
    }

    output_timeline, validation, _frame_rate = VideoTimelineDirector.execute(
        video_timeline_json=json.dumps(timeline),
    ).result

    assert validation["is_valid"] is True
    model_loras = output_timeline["project"]["model_loras"]
    assert "lora_config_hi" not in model_loras
    assert "lora_config_low" not in model_loras
    assert model_loras["schema_version"] == 2


def test_director_outputs_validation_for_invalid_timeline():
    VideoTimelineDirector = get_video_timeline_director()
    timeline = create_default_video_timeline()
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "",
        }
    )

    output_timeline, validation, _frame_rate = VideoTimelineDirector.execute(
        video_timeline_json=json.dumps(timeline)
    ).result

    assert validation["is_valid"] is False
    assert output_timeline["validation"] == validation
    assert [entry["code"] for entry in validation["errors"]] == [
        "TEXT_SECTION_EMPTY_PROMPT"
    ]


def test_director_rejects_local_private_timeline_decryption_without_managed_execution():
    VideoTimelineDirector = get_video_timeline_director()
    envelope = {
        "encrypted": True,
        "schema": "helto.timeline-director",
        "version": 1,
        "ciphertext": "synthetic-test-value",
    }

    with pytest.raises(ValueError, match="requires managed execution"):
        VideoTimelineDirector.execute(video_timeline_json=json.dumps(envelope))


def test_director_invalid_json_returns_validation_error_not_crash():
    VideoTimelineDirector = get_video_timeline_director()
    output_timeline, validation, _frame_rate = VideoTimelineDirector.execute(
        video_timeline_json="{not json"
    ).result

    assert output_timeline["type"] == "VIDEO_TIMELINE"
    assert validation["is_valid"] is False
    assert [entry["code"] for entry in validation["errors"]] == [
        "TIMELINE_JSON_INVALID"
    ]
