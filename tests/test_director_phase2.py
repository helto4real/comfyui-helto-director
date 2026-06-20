import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from shared.contracts.video_timeline import SECTION_TYPE_TEXT
from shared.lora import config as lora_config_module
from shared.timeline import create_default_video_timeline
from shared.privacy import CRYPTO_AVAILABLE, encrypt_state
import pytest
import folder_paths


def get_video_timeline_director():
    module_path = Path(__file__).resolve().parents[1]
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
        "lora_config_hi",
        "lora_config_low",
    ]
    assert "IMAGE" not in input_types
    assert "VIDEO" not in input_types
    assert "AUDIO" not in input_types
    assert "width" not in input_ids
    assert "height" not in input_ids
    assert schema.inputs[input_ids.index("aspect_ratio")].options == ["16:9", "4:3", "3:2", "21:9", "1:1"]
    assert schema.inputs[input_ids.index("orientation")].options == ["Landscape", "Portrait"]
    assert schema.inputs[input_ids.index("video_timeline_json")].extra_dict["hidden"] is True
    assert input_types[input_ids.index("lora_config_hi")] == "HELTO_LORA_CONFIG"
    assert input_types[input_ids.index("lora_config_low")] == "HELTO_LORA_CONFIG"


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


def test_director_embeds_connected_lora_configs_in_timeline_data(monkeypatch):
    monkeypatch.setattr(
        lora_config_module,
        "_available_loras",
        lambda: ["hi.safetensors", "low.safetensors"],
    )
    monkeypatch.setattr(folder_paths, "get_filename_list", lambda category: ["hi.safetensors", "low.safetensors"] if category == "loras" else [])
    VideoTimelineDirector = get_video_timeline_director()

    output_timeline, validation, _frame_rate = VideoTimelineDirector.execute(
        lora_config_hi={"loras": [{"enabled": True, "name": "hi.safetensors", "strength_model": 0.8}]},
        lora_config_low={"loras": [{"enabled": True, "name": "low.safetensors", "strength_model": 0.4}]},
    ).result

    assert validation["is_valid"] is True
    model_loras = output_timeline["project"]["model_loras"]
    assert model_loras["lora_config_hi"]["loras"][0]["name"] == "hi.safetensors"
    assert model_loras["lora_config_hi"]["loras"][0]["strength_clip"] == 0.8
    assert model_loras["lora_config_low"]["loras"][0]["name"] == "low.safetensors"


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


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for privacy encryption tests")
def test_director_decrypts_private_timeline_json():
    VideoTimelineDirector = get_video_timeline_director()
    timeline = create_default_video_timeline()
    timeline["project"]["privacy"]["mode"] = True
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "private prompt",
        }
    )
    envelope = encrypt_state({"timeline": timeline})

    output_timeline, validation, _frame_rate = VideoTimelineDirector.execute(
        video_timeline_json=json.dumps(envelope)
    ).result

    assert validation["is_valid"] is True
    assert output_timeline["project"]["privacy"] == {"mode": True}
    assert output_timeline["director_track"]["sections"][0]["prompt"] == "private prompt"


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
