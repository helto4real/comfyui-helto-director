import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_SOURCE_GENERATED,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    BOUNDARY_MODE_BLEND_SEAM,
    BOUNDARY_MODE_CONTINUOUS_SHOT,
    BOUNDARY_MODE_HARD_CUT,
    BOUNDARY_MODE_TRANSITION,
    LORA_MERGE_MODE_ADD_TO_GLOBAL,
    LORA_MERGE_MODE_DISABLE_LORAS,
    LORA_MERGE_MODE_REPLACE_GLOBAL,
    MODEL_LORA_MODEL_LTX_2_3,
    MODEL_LORA_TARGET_MAIN,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
    TAKE_STATUS_ACCEPTED,
    VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
)
from shared.lora import config as lora_config_module
from shared.ltx import build_ltx_timeline_plan, create_ltx_timeline_config
from shared.ltx.config import normalize_ltx_timeline_config
from shared.timeline import (
    GENERATION_MODE_FORCE_FULL_TIMELINE,
    GENERATION_MODE_FORCE_SELECTED,
    GENERATION_MODE_MISSING_ONLY,
    create_default_video_timeline,
)


def get_node_classes():
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
        return asyncio.run(extension.get_node_list())
    finally:
        sys.path = previous_path
        if previous is None:
            sys.modules.pop(sys_module_name, None)
        else:
            sys.modules[sys_module_name] = previous


def _lora_stack(name: str) -> dict:
    return {
        "version": 1,
        "loras": [
            {
                "enabled": True,
                "name": name,
                "strength_model": 0.8,
                "strength_clip": 0.6,
            }
        ],
        "ui": {"show_strengths": "separate", "match": name},
    }


def _shot(
    shot_id: str,
    section_id: str,
    start_time: float,
    end_time: float,
    merge_mode: str,
    lora_stack: dict,
) -> dict:
    return {
        "shot_id": shot_id,
        "start_time": start_time,
        "end_time": end_time,
        "section_ids": [section_id],
        "lora_overrides": {
            "enabled": True,
            "merge_mode": merge_mode,
            "targets": {
                MODEL_LORA_MODEL_LTX_2_3: {
                    MODEL_LORA_TARGET_MAIN: lora_stack,
                },
            },
        },
    }


def _lora_names(rows: list[dict]) -> list[str]:
    return [row["name"] for row in rows]


def _two_shot_text_timeline() -> dict:
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 3.0
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "section_001",
                "type": SECTION_TYPE_TEXT,
                "start_time": 0.0,
                "end_time": 1.0,
                "prompt": "first shot",
            },
            {
                "item_id": "section_002",
                "type": SECTION_TYPE_TEXT,
                "start_time": 1.0,
                "end_time": 3.0,
                "prompt": "second shot",
            },
        ]
    )
    return timeline


def _two_shot_text_timeline_with_continuity(
    *,
    mode: str = BOUNDARY_MODE_CONTINUOUS_SHOT,
    tail_frames: int = 6,
    blend_frames: int = 3,
    transition_prompt: str = "",
) -> dict:
    timeline = _two_shot_text_timeline()
    timeline["assets"].append(
        {
            "asset_id": "asset_previous_take",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_GENERATED,
            "path": "/tmp/previous.mp4",
            "name": "previous.mp4",
        }
    )
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_section_001",
            "start_time": 0.0,
            "end_time": 1.0,
            "section_ids": ["section_001"],
            "takes": [
                {
                    "take_id": "take_previous",
                    "asset_id": "asset_previous_take",
                    "status": TAKE_STATUS_ACCEPTED,
                }
            ],
            "accepted_take_id": "take_previous",
        },
        {
            "shot_id": "shot_section_002",
            "start_time": 1.0,
            "end_time": 3.0,
            "section_ids": ["section_002"],
        },
    ]
    timeline["sequence"]["boundaries"] = [
        {
            "boundary_id": "boundary_continuous",
            "left_shot_id": "shot_section_001",
            "right_shot_id": "shot_section_002",
            "mode": mode,
            "tail_frames": tail_frames,
            "blend_frames": blend_frames,
            "transition_prompt": transition_prompt,
        }
    ]
    return timeline


def _two_shot_text_timeline_with_ready_shots(*ready_shot_ids: str) -> dict:
    timeline = _two_shot_text_timeline()
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_section_001",
            "start_time": 0.0,
            "end_time": 1.0,
            "section_ids": ["section_001"],
        },
        {
            "shot_id": "shot_section_002",
            "start_time": 1.0,
            "end_time": 3.0,
            "section_ids": ["section_002"],
        },
    ]
    for index, shot in enumerate(timeline["sequence"]["shots"], start=1):
        if shot["shot_id"] not in ready_shot_ids:
            continue
        asset_id = f"asset_ready_{index}"
        take_id = f"take_ready_{index}"
        timeline["assets"].append(
            {
                "asset_id": asset_id,
                "type": ASSET_TYPE_VIDEO,
                "source_kind": ASSET_SOURCE_GENERATED,
                "path": f"/tmp/ready_{index}.mp4",
                "name": f"ready_{index}.mp4",
            }
        )
        shot["takes"] = [
            {
                "take_id": take_id,
                "asset_id": asset_id,
                "status": TAKE_STATUS_ACCEPTED,
            }
        ]
        shot["accepted_take_id"] = take_id
    return timeline


def test_ltx_nodes_are_registered_with_custom_sockets():
    node_classes = get_node_classes()
    node_ids = [node.define_schema().node_id for node in node_classes]

    assert node_ids == [
        "HeltoVideoTimelineDirector",
        "HeltoLTX23TimelineConfig",
        "HeltoLTX23TimelinePlanner",
        "HeltoLTX23TimelineRuntime",
        "HeltoLTX23TimelineSegmentedExecutor",
        "HeltoWAN22TimelineConfig",
        "HeltoWAN22TimelinePlanner",
        "HeltoWAN22TimelineRuntime",
        "HeltoWAN22TimelineSegmentedExecutor",
        "HeltoTimelineTakeCapture",
        "HeltoTimelineSequenceAssembler",
        "HeltoLTX23TimelineCropReferenceTail",
        "HeltoLTX23TimelineReferenceImageSelector",
        "HeltoLTX23TimelineIdentityAnchorLatentAware",
        "HeltoLTX23TimelineIdentityAnchorFace",
        "HeltoLTX23TimelineIdentityAnchorCombine",
        "HeltoLTX23TimelineApplyIdentityAnchor",
    ]

    config_schema = node_classes[1].define_schema()
    tail_input = next(input_item for input_item in config_schema.inputs if input_item.id == "segment_continuity_tail_frames")
    assert tail_input.options == ["1", "5", "9"]
    assert tail_input.default == "5"
    seam_blend_input = next(input_item for input_item in config_schema.inputs if input_item.id == "segment_seam_blend_frames")
    assert seam_blend_input.options == ["0", "3", "5"]
    assert seam_blend_input.default == "3"

    planner_schema = node_classes[2].define_schema()
    assert [input_item.io_type for input_item in planner_schema.inputs] == [
        "VIDEO_TIMELINE",
        "LTX_TIMELINE_CONFIG",
        "COMBO",
    ]
    generation_mode_input = planner_schema.inputs[2]
    assert generation_mode_input.id == "generation_mode"
    assert generation_mode_input.default == GENERATION_MODE_MISSING_ONLY
    assert generation_mode_input.options == [
        GENERATION_MODE_MISSING_ONLY,
        GENERATION_MODE_FORCE_SELECTED,
        GENERATION_MODE_FORCE_FULL_TIMELINE,
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
    assert config["max_generation_duration"] == 0.0
    assert config["segment_continuity_tail_frames"] == 5
    assert config["segment_seam_blend_frames"] == 3
    assert config["rules"] == {
        "divisible_by": 32,
        "frame_rule": "8n+1",
        "temporal_stride": 8,
    }


def test_ltx_runtime_lazy_status_skips_model_inputs_for_skipped_plan():
    node_classes = get_node_classes()
    runtime = next(node for node in node_classes if node.define_schema().node_id == "HeltoLTX23TimelineRuntime")
    skipped_plan = {
        "model_specific": {
            "ltx": {
                "generation_policy": {"status": "skipped"},
            }
        }
    }
    targeted_plan = {
        "model_specific": {
            "ltx": {
                "generation_policy": {"status": "targeted"},
            }
        }
    }

    assert runtime.check_lazy_status(skipped_plan, model=None, clip=None, vae=None) == []
    assert runtime.check_lazy_status(targeted_plan, model=None, clip=None, vae=None) == [
        "model",
        "clip",
        "vae",
    ]


def test_ltx_config_normalizes_segment_continuity_tail_frames():
    normalized = create_ltx_timeline_config(segment_continuity_tail_frames=9)
    fallback = create_ltx_timeline_config(segment_continuity_tail_frames=4)

    assert normalized["segment_continuity_tail_frames"] == 9
    assert fallback["segment_continuity_tail_frames"] == 5


def test_ltx_config_normalizes_segment_seam_blend_frames():
    normalized = normalize_ltx_timeline_config({
        "type": "LTX_TIMELINE_CONFIG",
        "segment_seam_blend_frames": "5",
    })
    fallback = normalize_ltx_timeline_config({
        "type": "LTX_TIMELINE_CONFIG",
        "segment_seam_blend_frames": 4,
    })
    legacy = normalize_ltx_timeline_config({"type": "LTX_TIMELINE_CONFIG"})

    assert normalized["segment_seam_blend_frames"] == 5
    assert fallback["segment_seam_blend_frames"] == 3
    assert legacy["segment_seam_blend_frames"] == 3


def test_ltx_config_node_keeps_old_debug_widget_position():
    node_classes = get_node_classes()
    config = node_classes[1].execute(debug_mode=True).result[0]

    assert config["debug_mode"] is True
    assert config["segment_continuity_tail_frames"] == 5
    assert config["segment_seam_blend_frames"] == 3


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

    plan, validation, debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )

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


def test_ltx_planner_maps_sections_to_shots_and_preserves_boundary_metadata():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "section_001",
                "type": SECTION_TYPE_TEXT,
                "start_time": 0.0,
                "end_time": 1.0,
                "prompt": "first shot",
            },
            {
                "item_id": "section_002",
                "type": SECTION_TYPE_TEXT,
                "start_time": 1.0,
                "end_time": 2.0,
                "prompt": "second shot",
            },
        ]
    )

    plan, validation, debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )
    ltx = plan["model_specific"]["ltx"]

    assert validation["is_valid"] is True
    assert [entry["shot_id"] for entry in plan["section_plan"]] == [
        "shot_section_001",
        "shot_section_002",
    ]
    assert ltx["timeline_structure"]["section_to_shot"] == {
        "section_001": "shot_section_001",
        "section_002": "shot_section_002",
    }
    assert ltx["timeline_structure"]["boundaries"][0]["mode"] == BOUNDARY_MODE_HARD_CUT
    assert ltx["lora_resolution"]["targets"] == [MODEL_LORA_TARGET_MAIN]
    assert ltx["lora_resolution"]["single_generation_loras"][MODEL_LORA_TARGET_MAIN]["loras"] == []
    assert debug["summary"]["shot_count"] == 2
    assert debug["summary"]["boundary_count"] == 1
    assert "selected_shot_id" not in debug["summary"]
    assert "shot_context" not in ltx


def test_ltx_missing_only_skips_when_all_shots_are_ready():
    timeline = _two_shot_text_timeline_with_ready_shots("shot_section_001", "shot_section_002")

    plan, validation, debug = build_ltx_timeline_plan(timeline, create_ltx_timeline_config(debug_mode=True))
    ltx = plan["model_specific"]["ltx"]

    assert validation["is_valid"] is True
    assert [entry["code"] for entry in validation["info"]] == [
        "GENERATION_SKIPPED_ALL_SHOTS_READY"
    ]
    assert plan["section_plan"] == []
    assert ltx["segmented_generation"]["segments"] == []
    assert ltx["generation_policy"]["status"] == "skipped"
    assert debug["summary"]["generation_status"] == "skipped"
    assert debug["summary"]["generation_skip_reason"] == "all_shots_ready"


def test_ltx_missing_only_targets_selected_missing_shot():
    timeline = _two_shot_text_timeline()
    timeline["ui_state"]["selected_item_id"] = "section_002"

    plan, validation, debug = build_ltx_timeline_plan(timeline, create_ltx_timeline_config(debug_mode=True))

    assert validation["is_valid"] is True
    assert plan["model_specific"]["ltx"]["generation_policy"]["target_shot_id"] == "shot_section_002"
    assert [entry["item_id"] for entry in plan["section_plan"]] == ["section_002"]
    assert plan["section_plan"][0]["start_time"] == 0.0
    assert debug["summary"]["generation_target_shot_id"] == "shot_section_002"


def test_ltx_missing_only_targets_earliest_missing_when_selected_shot_is_ready():
    timeline = _two_shot_text_timeline_with_ready_shots("shot_section_001")
    timeline["ui_state"]["selected_item_id"] = "shot_section_001"

    plan, validation, debug = build_ltx_timeline_plan(timeline, create_ltx_timeline_config(debug_mode=True))

    assert validation["is_valid"] is True
    assert plan["model_specific"]["ltx"]["generation_policy"]["target_shot_id"] == "shot_section_002"
    assert [entry["item_id"] for entry in plan["section_plan"]] == ["section_002"]
    assert debug["summary"]["generation_target_shot_id"] == "shot_section_002"


def test_ltx_force_selected_regenerates_ready_selected_shot():
    timeline = _two_shot_text_timeline_with_ready_shots("shot_section_001", "shot_section_002")
    timeline["ui_state"]["selected_item_id"] = "shot_section_002"

    plan, validation, debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(debug_mode=True),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )

    assert validation["is_valid"] is True
    assert plan["model_specific"]["ltx"]["generation_policy"]["target_shot_id"] == "shot_section_002"
    assert [entry["item_id"] for entry in plan["section_plan"]] == ["section_002"]
    assert debug["summary"]["generation_status"] == "targeted"


def test_ltx_legacy_saved_widget_shot_id_targets_shot_with_deprecation_warning():
    timeline = _two_shot_text_timeline()

    plan, validation, debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(debug_mode=True),
        generation_mode="shot_section_002",
    )
    policy = plan["model_specific"]["ltx"]["generation_policy"]

    assert validation["is_valid"] is True
    assert [entry["code"] for entry in validation["warnings"]] == [
        "GENERATION_LEGACY_SHOT_ID_DEPRECATED"
    ]
    assert policy["mode"] == GENERATION_MODE_MISSING_ONLY
    assert policy["legacy_shot_id"] == "shot_section_002"
    assert policy["legacy_shot_id_source"] == "generation_mode"
    assert policy["target_shot_id"] == "shot_section_002"
    assert [entry["item_id"] for entry in plan["section_plan"]] == ["section_002"]
    assert debug["summary"]["generation_legacy_shot_id"] == "shot_section_002"


def test_ltx_programmatic_legacy_shot_id_takes_precedence_over_generation_mode():
    timeline = _two_shot_text_timeline()
    timeline["ui_state"]["selected_item_id"] = "shot_section_001"

    plan, validation, _debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
        shot_id="shot_section_002",
    )
    policy = plan["model_specific"]["ltx"]["generation_policy"]

    assert validation["is_valid"] is True
    assert [entry["code"] for entry in validation["warnings"]] == [
        "GENERATION_LEGACY_SHOT_ID_DEPRECATED"
    ]
    assert policy["mode"] == GENERATION_MODE_FORCE_SELECTED
    assert policy["legacy_shot_id_source"] == "shot_id"
    assert policy["target_shot_id"] == "shot_section_002"
    assert [entry["item_id"] for entry in plan["section_plan"]] == ["section_002"]


def test_ltx_missing_legacy_shot_id_blocks_instead_of_falling_back():
    timeline = _two_shot_text_timeline()

    plan, validation, debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(debug_mode=True),
        generation_mode="missing_legacy_shot",
    )
    policy = plan["model_specific"]["ltx"]["generation_policy"]

    assert validation["is_valid"] is False
    assert [entry["code"] for entry in validation["errors"]] == [
        "GENERATION_LEGACY_SHOT_NOT_FOUND"
    ]
    assert [entry["code"] for entry in validation["warnings"]] == [
        "GENERATION_LEGACY_SHOT_ID_DEPRECATED"
    ]
    assert policy["status"] == "blocked"
    assert policy["target_shot_id"] is None
    assert plan["section_plan"] == []
    assert "shot_context" not in plan["model_specific"]["ltx"]
    assert debug["summary"]["generation_block_reason"] == "legacy_shot_not_found"


def test_ltx_planner_plans_selected_shot_timeline():
    timeline = _two_shot_text_timeline()
    timeline["ui_state"]["selected_item_id"] = "shot_section_002"

    plan, validation, debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(debug_mode=True),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )
    ltx = plan["model_specific"]["ltx"]
    shot_context = ltx["shot_context"]

    assert validation["is_valid"] is True
    assert plan["project"]["duration_seconds"] == 2.0
    assert len(plan["section_plan"]) == 1
    assert plan["section_plan"][0]["item_id"] == "section_002"
    assert plan["section_plan"][0]["shot_id"] == "shot_section_002"
    assert plan["section_plan"][0]["start_time"] == 0.0
    assert plan["section_plan"][0]["end_time"] == 2.0
    assert ltx["timeline_structure"]["section_to_shot"] == {
        "section_002": "shot_section_002",
    }
    assert shot_context["shot_id"] == "shot_section_002"
    assert shot_context["original_start_time"] == 1.0
    assert shot_context["original_end_time"] == 3.0
    assert shot_context["duration_seconds"] == 2.0
    assert ltx["timeline_structure"]["metadata"]["shot_extraction"] == shot_context
    assert debug["summary"]["selected_shot_id"] == "shot_section_002"
    assert debug["summary"]["shot_original_start_time"] == 1.0
    assert debug["summary"]["shot_original_end_time"] == 3.0
    assert debug["summary"]["shot_duration_seconds"] == 2.0
    assert debug["summary"]["shot_continuity_policy"] == "none"
    assert debug["summary"]["shot_continuity_status"] == "not_requested"
    assert debug["details"]["shot_context"] == shot_context


@pytest.mark.parametrize(
    ("mode", "policy"),
    [
        (BOUNDARY_MODE_CONTINUOUS_SHOT, "continuous"),
        (BOUNDARY_MODE_BLEND_SEAM, "blend"),
        (BOUNDARY_MODE_TRANSITION, "transition"),
    ],
)
def test_ltx_planner_marks_available_boundary_conditioning_as_applied(mode, policy):
    timeline = _two_shot_text_timeline_with_continuity(
        mode=mode,
        transition_prompt="bridge through smoke" if mode == BOUNDARY_MODE_TRANSITION else "",
    )
    timeline["ui_state"]["selected_item_id"] = "shot_section_002"

    plan, validation, debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(debug_mode=True),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )
    ltx = plan["model_specific"]["ltx"]
    continuity = ltx["continuity_context"]
    conditioning = ltx["boundary_conditioning"]

    assert validation["is_valid"] is True
    assert validation["warnings"] == []
    assert continuity["policy"] == policy
    assert continuity["source_status"] == "available"
    assert continuity["model_status"] == "applied"
    assert continuity["clip_reference"]["asset_id"] == "asset_previous_take"
    assert conditioning["policy"] == policy
    assert conditioning["model_status"] == "applied"
    assert conditioning["asset_id"] == "asset_previous_take"
    assert conditioning["requested_tail_frames"] == 6
    assert conditioning["effective_tail_frames"] == 9
    assert conditioning["media_item_id"] == "boundary_tail_boundary_continuous"
    media = next(entry for entry in plan["media_plan"] if entry["item_id"] == conditioning["media_item_id"])
    assert media["transient"] is True
    assert media["insert_frame"] == 0
    assert media["video_guidance_frame_count"] == 9
    assert debug["summary"]["shot_continuity_policy"] == policy
    assert debug["summary"]["shot_continuity_status"] == "applied"
    assert debug["details"]["continuity_context"] == continuity
    assert debug["details"]["boundary_conditioning"] == conditioning


def test_ltx_planner_merges_transition_prompt_into_first_prompt_region_only():
    timeline = _two_shot_text_timeline_with_continuity(
        mode=BOUNDARY_MODE_TRANSITION,
        transition_prompt="a glowing match cut",
    )
    timeline["director_track"]["sections"][1]["end_time"] = 2.0
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_003",
            "type": SECTION_TYPE_TEXT,
            "start_time": 2.0,
            "end_time": 3.0,
            "prompt": "second region",
        }
    )
    timeline["sequence"]["shots"][1]["section_ids"] = ["section_002", "section_003"]
    timeline["ui_state"]["selected_item_id"] = "shot_section_002"

    plan, validation, _debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(debug_mode=True),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )

    assert validation["is_valid"] is True
    prompts = plan["prompt_plan"]
    assert prompts[0]["item_id"] == "section_002"
    assert prompts[0]["runtime_prompt"] == "a glowing match cut. second shot"
    assert prompts[0]["boundary_transition_prompt_applied"] is True
    assert prompts[1]["item_id"] == "section_003"
    assert prompts[1]["runtime_prompt"] == "second region"


def test_ltx_planner_resolves_zero_boundary_tail_to_one_guide_frame():
    timeline = _two_shot_text_timeline_with_continuity(tail_frames=0)
    timeline["ui_state"]["selected_item_id"] = "shot_section_002"

    plan, validation, _debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(debug_mode=True),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )

    conditioning = plan["model_specific"]["ltx"]["boundary_conditioning"]
    media = next(entry for entry in plan["media_plan"] if entry["item_id"] == conditioning["media_item_id"])
    assert validation["is_valid"] is True
    assert conditioning["requested_tail_frames"] == 0
    assert conditioning["effective_tail_frames"] == 1
    assert media["video_guidance_frame_count"] == 1


def test_ltx_planner_warns_when_continuity_source_is_missing():
    timeline = _two_shot_text_timeline_with_continuity()
    timeline["sequence"]["shots"][0]["takes"] = []
    timeline["sequence"]["shots"][0]["accepted_take_id"] = None
    timeline["ui_state"]["selected_item_id"] = "shot_section_002"

    plan, validation, debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(debug_mode=True),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )
    continuity = plan["model_specific"]["ltx"]["continuity_context"]
    conditioning = plan["model_specific"]["ltx"]["boundary_conditioning"]

    assert validation["is_valid"] is True
    assert [entry["code"] for entry in validation["warnings"]] == [
        "LTX_SHOT_CONTINUITY_SOURCE_MISSING"
    ]
    assert continuity["policy"] == "continuous"
    assert continuity["source_status"] == "unavailable"
    assert continuity["model_status"] == "unavailable"
    assert continuity["clip_reference"] is None
    assert conditioning["model_status"] == "unavailable"
    assert conditioning["fallback_reason"] == "SHOT_CONTINUITY_PREVIOUS_CLIP_MISSING"
    assert debug["summary"]["shot_continuity_status"] == "unavailable"


def test_ltx_planner_force_selected_requires_selected_shot_without_crashing():
    timeline = _two_shot_text_timeline()

    plan, validation, debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(debug_mode=True),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )

    assert validation["is_valid"] is False
    assert [entry["code"] for entry in validation["errors"]] == [
        "GENERATION_SELECTED_SHOT_REQUIRED"
    ]
    assert plan["project"]["duration_seconds"] == 3.0
    assert len(plan["section_plan"]) == 0
    assert "shot_context" not in plan["model_specific"]["ltx"]
    assert debug["summary"]["generation_status"] == "blocked"
    assert debug["summary"]["shot_selection_error"] == "selected_shot_required"


def test_ltx_planner_resolves_shot_loras_and_warns_when_runtime_switching_is_deferred(monkeypatch):
    monkeypatch.setattr(
        lora_config_module,
        "_available_loras",
        lambda: [
            "global.safetensors",
            "add.safetensors",
            "replace.safetensors",
        ],
    )
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 3.0
    timeline["project"]["model_loras"]["global"][MODEL_LORA_MODEL_LTX_2_3][MODEL_LORA_TARGET_MAIN] = _lora_stack("global")
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "section_add",
                "type": SECTION_TYPE_TEXT,
                "start_time": 0.0,
                "end_time": 1.0,
                "prompt": "add stack",
            },
            {
                "item_id": "section_replace",
                "type": SECTION_TYPE_TEXT,
                "start_time": 1.0,
                "end_time": 2.0,
                "prompt": "replace stack",
            },
            {
                "item_id": "section_disable",
                "type": SECTION_TYPE_TEXT,
                "start_time": 2.0,
                "end_time": 3.0,
                "prompt": "disable stack",
            },
        ]
    )
    timeline["sequence"]["shots"] = [
        _shot(
            "shot_add",
            "section_add",
            0.0,
            1.0,
            LORA_MERGE_MODE_ADD_TO_GLOBAL,
            _lora_stack("add"),
        ),
        _shot(
            "shot_replace",
            "section_replace",
            1.0,
            2.0,
            LORA_MERGE_MODE_REPLACE_GLOBAL,
            _lora_stack("replace"),
        ),
        _shot(
            "shot_disable",
            "section_disable",
            2.0,
            3.0,
            LORA_MERGE_MODE_DISABLE_LORAS,
            _lora_stack("add"),
        ),
    ]

    plan, validation, _debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )
    lora_resolution = plan["model_specific"]["ltx"]["lora_resolution"]
    loras_by_section = {
        entry["item_id"]: entry["effective_loras"][MODEL_LORA_TARGET_MAIN]["loras"]
        for entry in lora_resolution["section_loras"]
    }

    assert _lora_names(loras_by_section["section_add"]) == ["global.safetensors", "add.safetensors"]
    assert _lora_names(loras_by_section["section_replace"]) == ["replace.safetensors"]
    assert loras_by_section["section_disable"] == []
    assert lora_resolution["single_generation_loras"] is None
    assert lora_resolution["execution_strategy"] == "defer_per_shot_lora_execution"
    assert "LTX_SHOT_LORA_STACKS_DIFFER" in [entry["code"] for entry in validation["warnings"]]


def test_ltx_planner_builds_hidden_generation_segments_when_duration_is_capped():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 3.0
    timeline["project"]["frame_rate"] = 8.0
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 3.0,
            "prompt": "slow dolly shot",
        }
    )

    plan, validation, _debug = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(max_generation_duration=1.0),
    )

    segmented = plan["model_specific"]["ltx"]["segmented_generation"]
    assert validation["is_valid"] is True
    assert segmented["enabled"] is True
    assert [segment["visible_frame_count"] for segment in segmented["segments"]] == [8, 8, 9]
    assert segmented["segments"][1]["trim_leading_frames"] == 5
    assert segmented["segments"][1]["continuity"]["continuity_frame_count"] == 5
    assert segmented["segments"][1]["generation_frame_count"] == 17
    assert segmented["segments"][1]["continuity"]["source"] == "previous_tail"


def test_ltx_planner_passes_video_guidance_fields_to_media_plan():
    timeline = create_default_video_timeline()
    timeline["assets"].append(
        {
            "asset_id": "video_001",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/source.mp4",
            "name": "source.mp4",
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
            "video_guidance_range": VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
            "video_guidance_frame_count": 17,
        }
    )

    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())

    assert validation["is_valid"] is True
    assert plan["media_plan"][0]["video_guidance_range"] == VIDEO_GUIDANCE_RANGE_LAST_FRAMES
    assert plan["media_plan"][0]["video_guidance_frame_count"] == 17


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


def test_ltx_planner_records_character_reference_specs_and_replaces_prompt_tags():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["metadata"]["character_references"].append(
        {
            "id": "ref_hero",
            "label": "image1",
            "kind": "character",
            "enabled": True,
            "description": "red jacket hero",
            "strength": 0.9,
            "image": {"path": "/mnt/media/hero.png", "name": "hero.png"},
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "follow @image1:character[0.8] through fog",
        }
    )

    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config(debug_mode=True))
    references = plan["model_specific"]["ltx"]["character_references"]

    assert validation["is_valid"] is True
    assert references["active"] is True
    assert references["guide_specs"][0]["label"] == "image1"
    assert references["guide_specs"][0]["strength"] == 0.8
    assert references["guide_specs"][0]["image"]["path"] == "/mnt/media/hero.png"
    assert references["section_usage"][0]["runtime_prompt"] == "follow red jacket hero through fog"
    assert plan["prompt_plan"][0]["raw_prompt"] == "follow @image1:character[0.8] through fog"
    assert plan["prompt_plan"][0]["runtime_prompt"] == "follow red jacket hero through fog"
    assert plan["prompt_plan"][0]["effective_prompt"] == "follow red jacket hero through fog"


def test_ltx_planner_errors_for_unknown_active_character_reference_tag():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["metadata"]["character_references"].append(
        {
            "id": "ref_hero",
            "label": "image1",
            "kind": "character",
            "enabled": True,
            "description": "red jacket hero",
            "strength": 1.0,
            "image": {"path": "/mnt/media/hero.png", "name": "hero.png"},
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "follow @image2:character",
        }
    )

    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())
    error_codes = [entry["code"] for entry in validation["errors"]]

    assert validation["is_valid"] is False
    assert "LTX_CHARACTER_REFERENCE_UNKNOWN" in error_codes
    assert plan["model_specific"]["ltx"]["character_references"]["unknown_tags"] == ["@image2:character"]


def test_ltx_reference_mode_disabled_strips_tags_without_specs():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["metadata"]["character_references"].append(
        {
            "id": "ref_hero",
            "label": "image1",
            "kind": "character",
            "enabled": True,
            "description": "red jacket hero",
            "strength": 1.0,
            "image": {"path": "/mnt/media/hero.png", "name": "hero.png"},
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "follow @image1:character through fog",
        }
    )

    plan, validation, _ = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(reference_mode="Disabled", debug_mode=True),
    )
    references = plan["model_specific"]["ltx"]["character_references"]

    assert validation["is_valid"] is True
    assert plan["model_specific"]["ltx"]["prompt_relay"]["enabled"] is False
    assert references["active"] is False
    assert references["guide_specs"] == []
    assert plan["prompt_plan"][0]["runtime_prompt"] == "follow through fog"
