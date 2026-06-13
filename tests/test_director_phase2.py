import json

from nodes.video_timeline_director.node import VideoTimelineDirector
from shared.contracts.video_timeline import SECTION_TYPE_TEXT
from shared.timeline import create_default_video_timeline


def test_director_schema_has_project_widgets_and_no_media_inputs():
    schema = VideoTimelineDirector.define_schema()
    input_ids = [input_item.id for input_item in schema.inputs]
    input_types = [input_item.io_type for input_item in schema.inputs]

    assert input_ids == [
        "duration_seconds",
        "frame_rate",
        "aspect_ratio",
        "orientation",
        "quality_preset",
        "zoom_level",
        "video_timeline_json",
    ]
    assert "IMAGE" not in input_types
    assert "VIDEO" not in input_types
    assert "AUDIO" not in input_types
    assert "width" not in input_ids
    assert "height" not in input_ids
    assert schema.inputs[-1].extra_dict["hidden"] is True


def test_director_runs_without_frontend_state():
    timeline, validation = VideoTimelineDirector.execute().result

    assert timeline["type"] == "VIDEO_TIMELINE"
    assert timeline["project"]["duration_seconds"] == 5.0
    assert validation["is_valid"] is True
    assert [entry["code"] for entry in validation["info"]] == ["DIRECTOR_GAP"]


def test_director_applies_visible_widgets_as_authoritative_fields():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 12.0
    timeline["project"]["aspect_ratio"] = "1:1"
    timeline["ui_state"]["zoom_level"] = 0.5

    output_timeline, validation = VideoTimelineDirector.execute(
        duration_seconds=12.0,
        frame_rate=30.0,
        aspect_ratio="9:16",
        orientation="Portrait",
        quality_preset="High",
        zoom_level=2.25,
        video_timeline_json=json.dumps(timeline),
    ).result

    assert validation["is_valid"] is True
    assert output_timeline["project"]["duration_seconds"] == 12.0
    assert output_timeline["project"]["frame_rate"] == 30.0
    assert output_timeline["project"]["aspect_ratio"] == "9:16"
    assert output_timeline["project"]["orientation"] == "Portrait"
    assert output_timeline["project"]["quality_preset"] == "High"
    assert output_timeline["ui_state"]["zoom_level"] == 2.25


def test_director_outputs_validation_for_invalid_timeline():
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

    output_timeline, validation = VideoTimelineDirector.execute(
        video_timeline_json=json.dumps(timeline)
    ).result

    assert validation["is_valid"] is False
    assert output_timeline["validation"] == validation
    assert [entry["code"] for entry in validation["errors"]] == [
        "TEXT_SECTION_EMPTY_PROMPT"
    ]


def test_director_invalid_json_returns_validation_error_not_crash():
    output_timeline, validation = VideoTimelineDirector.execute(
        video_timeline_json="{not json"
    ).result

    assert output_timeline["type"] == "VIDEO_TIMELINE"
    assert validation["is_valid"] is False
    assert [entry["code"] for entry in validation["errors"]] == [
        "TIMELINE_JSON_INVALID"
    ]
