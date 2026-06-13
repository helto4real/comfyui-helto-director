import asyncio
import importlib.util
import sys
from pathlib import Path

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_TYPE_IMAGE,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
)
from shared.ltx import build_ltx_timeline_plan, create_ltx_timeline_config
from shared.timeline import create_default_video_timeline


def get_node_classes():
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
        return asyncio.run(extension.get_node_list())
    finally:
        sys.path = previous_path
        if previous is None:
            sys.modules.pop(sys_module_name, None)
        else:
            sys.modules[sys_module_name] = previous


def test_ltx_nodes_are_registered_with_custom_sockets():
    node_classes = get_node_classes()
    node_ids = [node.define_schema().node_id for node in node_classes]

    assert node_ids == [
        "HeltoVideoTimelineDirector",
        "HeltoLTX23TimelineConfig",
        "HeltoLTX23TimelinePlanner",
    ]

    planner_schema = node_classes[2].define_schema()
    assert [input_item.io_type for input_item in planner_schema.inputs] == [
        "VIDEO_TIMELINE",
        "LTX_TIMELINE_CONFIG",
    ]
    assert [output.io_type for output in planner_schema.outputs] == [
        "LTX_TIMELINE_PLAN",
        "TIMELINE_VALIDATION",
        "DEBUG_INFO",
    ]


def test_ltx_config_defaults_include_locked_rules():
    config = create_ltx_timeline_config()

    assert config["type"] == "LTX_TIMELINE_CONFIG"
    assert config["model_version"] == "2.3"
    assert config["rules"] == {
        "divisible_by": 32,
        "frame_rule": "8n+1",
        "temporal_stride": 8,
    }


def test_ltx_planner_builds_serializable_plan_with_gaps_prompts_and_media():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["global_prompt"]["enabled"] = True
    timeline["project"]["global_prompt"]["prompt"] = "cinematic lighting"
    timeline["assets"].append(
        {
            "asset_id": "image_001",
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/reference.png",
            "name": "reference.png",
        }
    )
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "section_001",
                "type": SECTION_TYPE_TEXT,
                "start_time": 0.0,
                "end_time": 0.5,
                "prompt": "wide shot",
            },
            {
                "item_id": "section_002",
                "type": SECTION_TYPE_IMAGE,
                "start_time": 1.0,
                "end_time": 2.0,
                "image": {"asset_id": "image_001"},
                "prompt": "",
                "guide_strength": 1.0,
            },
        ]
    )

    plan, validation, debug = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())

    assert plan["type"] == "LTX_TIMELINE_PLAN"
    assert plan["resolved_output"]["frame_count"] == 49
    assert plan["resolved_output"]["width"] % 32 == 0
    assert plan["resolved_output"]["height"] % 32 == 0
    assert [entry["type"] for entry in plan["section_plan"]] == [
        "Text",
        "Gap",
        "Image",
    ]
    assert plan["prompt_plan"][0]["effective_prompt"] == "cinematic lighting, wide shot"
    assert plan["prompt_plan"][1]["effective_prompt"] == "cinematic lighting"
    assert plan["media_plan"][0]["asset_id"] == "image_001"
    assert plan["media_plan"][0]["ltx_role"] == "Section Guides"
    assert validation["is_valid"] is True
    assert debug["summary"]["planned_ranges"] == 3


def test_ltx_planner_propagates_invalid_director_timeline_without_crash():
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

    plan, validation, debug = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())

    assert plan["type"] == "LTX_TIMELINE_PLAN"
    assert validation["is_valid"] is False
    assert "TEXT_SECTION_EMPTY_PROMPT" in [entry["code"] for entry in validation["errors"]]
    assert "LTX_DIRECTOR_TIMELINE_INVALID" in [entry["code"] for entry in validation["errors"]]
    assert debug["summary"]["error_count"] == 2
