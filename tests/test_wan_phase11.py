import asyncio
import copy
import importlib.util
import sys
from pathlib import Path

import torch

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    BOUNDARY_MODE_HARD_CUT,
    LORA_MERGE_MODE_ADD_TO_GLOBAL,
    LORA_MERGE_MODE_DISABLE_LORAS,
    LORA_MERGE_MODE_REPLACE_GLOBAL,
    MODEL_LORA_MODEL_WAN_2_2,
    MODEL_LORA_TARGET_HIGH_NOISE,
    MODEL_LORA_TARGET_LOW_NOISE,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
)
from shared.lora import config as lora_config_module
from shared.timeline import create_default_video_timeline
from shared.wan import build_wan_timeline_plan, create_wan_timeline_config
from shared.wan.config import normalize_wan_timeline_config
from shared.segmented_executor import build_segment_plan
from shared.wan.runtime.segmented import _apply_wan_segment_continuity


def _load_nodepack():
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
        return module, asyncio.run(extension.get_node_list())
    finally:
        sys.path = previous_path
        if previous is None:
            sys.modules.pop(sys_module_name, None)
        else:
            sys.modules[sys_module_name] = previous


def _timeline_with_start_image_and_three_text_sections():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 10.0
    timeline["project"]["frame_rate"] = 16.0
    timeline["assets"].append(
        {
            "asset_id": "image_001",
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/woman.png",
            "name": "woman.png",
        }
    )
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "image_section",
                "type": SECTION_TYPE_IMAGE,
                "start_time": 0.0,
                "end_time": 79 / 16,
                "image": {"asset_id": "image_001"},
                "prompt": "woman in frame",
            },
            {
                "item_id": "text_001",
                "type": SECTION_TYPE_TEXT,
                "start_time": 79 / 16,
                "end_time": 120 / 16,
                "prompt": "she turns toward the window",
            },
            {
                "item_id": "text_002",
                "type": SECTION_TYPE_TEXT,
                "start_time": 120 / 16,
                "end_time": 140 / 16,
                "prompt": "she walks forward",
            },
            {
                "item_id": "text_003",
                "type": SECTION_TYPE_TEXT,
                "start_time": 140 / 16,
                "end_time": 10.0,
                "prompt": "she looks back",
            },
        ]
    )
    return timeline


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


def _wan_lora_stack(name: str) -> dict:
    return {
        "version": 1,
        "loras": [
            {
                "enabled": True,
                "name": name,
                "strength_model": 0.9,
                "strength_clip": 0.9,
            }
        ],
        "ui": {"show_strengths": "single", "match": name},
    }


def _wan_shot(
    shot_id: str,
    section_id: str,
    start_time: float,
    end_time: float,
    merge_mode: str,
    high_stack: dict,
    low_stack: dict,
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
                MODEL_LORA_MODEL_WAN_2_2: {
                    MODEL_LORA_TARGET_HIGH_NOISE: high_stack,
                    MODEL_LORA_TARGET_LOW_NOISE: low_stack,
                },
            },
        },
    }


def _lora_names(rows: list[dict]) -> list[str]:
    return [row["name"] for row in rows]


def test_wan_nodes_register_with_custom_sockets_and_mappings():
    module, node_classes = _load_nodepack()
    node_ids = [node_class.define_schema().node_id for node_class in node_classes]

    assert "HeltoWAN22TimelineConfig" in node_ids
    assert "HeltoWAN22TimelinePlanner" in node_ids
    assert "HeltoWAN22TimelineRuntime" in node_ids
    assert "HeltoWAN22TimelineSegmentedExecutor" in node_ids
    assert "HeltoWAN22TimelineConfig" in module.NODE_CLASS_MAPPINGS
    assert "HeltoWAN22TimelinePlanner" in module.NODE_CLASS_MAPPINGS
    assert "HeltoWAN22TimelineRuntime" in module.NODE_CLASS_MAPPINGS
    assert "HeltoWAN22TimelineSegmentedExecutor" in module.NODE_CLASS_MAPPINGS
    assert module.NODE_DISPLAY_NAME_MAPPINGS["HeltoWAN22TimelineConfig"] == "WAN 2.2 Timeline Config"
    assert module.NODE_DISPLAY_NAME_MAPPINGS["HeltoWAN22TimelinePlanner"] == "WAN 2.2 Timeline Planner"
    assert module.NODE_DISPLAY_NAME_MAPPINGS["HeltoWAN22TimelineRuntime"] == "WAN 2.2 Timeline Runtime"

    config_schema = module.NODE_CLASS_MAPPINGS["HeltoWAN22TimelineConfig"].define_schema()
    planner_schema = module.NODE_CLASS_MAPPINGS["HeltoWAN22TimelinePlanner"].define_schema()
    executor_schema = module.NODE_CLASS_MAPPINGS["HeltoWAN22TimelineSegmentedExecutor"].define_schema()
    assert [output.io_type for output in config_schema.outputs] == ["WAN_TIMELINE_CONFIG"]
    tail_input = next(input_item for input_item in config_schema.inputs if input_item.id == "segment_continuity_tail_frames")
    assert tail_input.options == ["1", "5", "9"]
    assert tail_input.default == "5"
    seam_blend_input = next(input_item for input_item in config_schema.inputs if input_item.id == "segment_seam_blend_frames")
    assert seam_blend_input.options == ["0", "3", "5"]
    assert seam_blend_input.default == "3"
    painter_boost_input = next(input_item for input_item in config_schema.inputs if input_item.id == "painter_motion_boost")
    painter_amplitude_input = next(input_item for input_item in config_schema.inputs if input_item.id == "painter_motion_amplitude")
    backend_profile_input = next(input_item for input_item in config_schema.inputs if input_item.id == "runtime_backend_profile")
    fmlf_mode_input = next(input_item for input_item in config_schema.inputs if input_item.id == "fmlf_continuation_mode")
    assert "FMLF Advanced I2V" in backend_profile_input.options
    assert fmlf_mode_input.options == ["SVI", "AUTO_CONTINUE"]
    assert fmlf_mode_input.default == "SVI"
    assert painter_boost_input.options == ["Off", "Auto"]
    assert painter_boost_input.default == "Off"
    assert painter_amplitude_input.default == 1.15
    executor_input_ids = [input_item.id for input_item in executor_schema.inputs]
    assert "phase_split_step" in executor_input_ids
    assert "phase_split_percent" not in executor_input_ids
    split_input = next(input_item for input_item in executor_schema.inputs if input_item.id == "phase_split_step")
    assert split_input.io_type == "INT"
    assert split_input.default == 10
    assert [input_item.io_type for input_item in planner_schema.inputs] == [
        "VIDEO_TIMELINE",
        "WAN_TIMELINE_CONFIG",
        "STRING",
    ]
    shot_input = planner_schema.inputs[2]
    assert shot_input.id == "shot_id"
    assert shot_input.default == ""
    assert [output.io_type for output in planner_schema.outputs] == [
        "WAN_TIMELINE_PLAN",
        "TIMELINE_VALIDATION",
        "DEBUG_INFO",
    ]


def test_wan_config_defaults_include_skeleton_rules():
    config = create_wan_timeline_config()

    assert config["type"] == "WAN_TIMELINE_CONFIG"
    assert config["model_family"] == "WAN"
    assert config["model_version"] == "2.2"
    assert config["resolution_profile"] == "Auto from Director"
    assert config["model_mode"] == "I2V-A14B"
    assert config["prompt_routing"] == "Prompt Relay"
    assert config["bernini_task_prompt"] == "Auto"
    assert config["visual_conditioning_mode"] == "Timed Keyframes"
    assert config["audio_policy"] == "Final Mix Only"
    assert config["runtime_backend_profile"] == "Plan Only"
    assert config["max_generation_duration"] == 0.0
    assert config["segment_continuity_tail_frames"] == 5
    assert config["segment_seam_blend_frames"] == 3
    assert config["vram_unload_policy"] == "Off"
    assert config["fmlf_continuation_mode"] == "SVI"
    assert config["painter_motion_boost"] == "Off"
    assert config["painter_motion_amplitude"] == 1.15
    assert config["rules"] == {
        "divisible_by": 16,
        "frame_rule": "4n+1 latent chunks",
        "temporal_stride": 4,
    }


def test_wan_config_normalizes_vram_unload_policy():
    normalized = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "vram_unload_policy": "Between High Low And Decode",
    })
    fallback = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "vram_unload_policy": "Unexpected",
    })
    legacy = normalize_wan_timeline_config({"type": "WAN_TIMELINE_CONFIG"})

    assert normalized["vram_unload_policy"] == "Between High Low And Decode"
    assert fallback["vram_unload_policy"] == "Off"
    assert legacy["vram_unload_policy"] == "Off"


def test_wan_config_normalizes_segment_continuity_tail_frames():
    normalized = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "segment_continuity_tail_frames": "9",
    })
    fallback = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "segment_continuity_tail_frames": 4,
    })
    legacy = normalize_wan_timeline_config({"type": "WAN_TIMELINE_CONFIG"})

    assert normalized["segment_continuity_tail_frames"] == 9
    assert fallback["segment_continuity_tail_frames"] == 5
    assert legacy["segment_continuity_tail_frames"] == 5


def test_wan_config_normalizes_segment_seam_blend_frames():
    normalized = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "segment_seam_blend_frames": "5",
    })
    fallback = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "segment_seam_blend_frames": 4,
    })
    legacy = normalize_wan_timeline_config({"type": "WAN_TIMELINE_CONFIG"})

    assert normalized["segment_seam_blend_frames"] == 5
    assert fallback["segment_seam_blend_frames"] == 3
    assert legacy["segment_seam_blend_frames"] == 3


def test_wan_config_normalizes_painter_motion_boost():
    normalized = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "painter_motion_boost": "Auto",
        "painter_motion_amplitude": 1.8,
    })
    low = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "painter_motion_boost": "Unexpected",
        "painter_motion_amplitude": 0.25,
    })
    high = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "painter_motion_amplitude": 5.0,
    })
    invalid = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "painter_motion_amplitude": "bad",
    })

    assert normalized["painter_motion_boost"] == "Auto"
    assert normalized["painter_motion_amplitude"] == 1.8
    assert low["painter_motion_boost"] == "Off"
    assert low["painter_motion_amplitude"] == 1.0
    assert high["painter_motion_amplitude"] == 2.0
    assert invalid["painter_motion_amplitude"] == 1.15


def test_wan_config_normalizes_fmlf_continuation_mode():
    normalized = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "fmlf_continuation_mode": "AUTO_CONTINUE",
    })
    fallback = normalize_wan_timeline_config({
        "type": "WAN_TIMELINE_CONFIG",
        "fmlf_continuation_mode": "Unexpected",
    })
    legacy = normalize_wan_timeline_config({"type": "WAN_TIMELINE_CONFIG"})

    assert normalized["fmlf_continuation_mode"] == "AUTO_CONTINUE"
    assert fallback["fmlf_continuation_mode"] == "SVI"
    assert legacy["fmlf_continuation_mode"] == "SVI"


def test_wan_config_node_keeps_old_vram_and_debug_widget_positions():
    module, _node_classes = _load_nodepack()
    config_node = module.NODE_CLASS_MAPPINGS["HeltoWAN22TimelineConfig"]
    config = config_node.execute(
        vram_unload_policy="Between High Low And Decode",
        debug_mode="Full",
    ).result[0]

    assert config["vram_unload_policy"] == "Between High Low And Decode"
    assert config["debug_mode"] == "Full"
    assert config["segment_continuity_tail_frames"] == 5
    assert config["segment_seam_blend_frames"] == 3


def test_wan_planner_builds_serializable_text_plan_with_gap_no_guidance():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["global_prompt"]["enabled"] = True
    timeline["project"]["global_prompt"]["prompt"] = "cinematic lighting"
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 0.5,
            "prompt": "wide shot",
        }
    )

    plan, validation, debug = build_wan_timeline_plan(timeline, create_wan_timeline_config(debug_mode=True))

    assert plan["type"] == "WAN_TIMELINE_PLAN"
    assert plan["model_family"] == "WAN"
    assert plan["model_version"] == "2.2"
    assert plan["resolved_output"]["requested_frame_count"] == 48
    assert plan["resolved_output"]["frame_count"] == 49
    assert plan["resolved_output"]["frame_count_rule"] == "WAN video length is rounded up to 4n+1 frames."
    assert plan["resolved_output"]["width"] % 16 == 0
    assert plan["resolved_output"]["height"] % 16 == 0
    assert [entry["type"] for entry in plan["section_plan"]] == ["Text", "Gap"]
    assert plan["section_plan"][0]["start_frame"] == 0
    assert plan["section_plan"][0]["end_frame_exclusive"] == 12
    assert plan["section_plan"][1]["role"] == "No Guidance"
    assert plan["prompt_plan"][0]["effective_prompt"] == "cinematic lighting, wide shot"
    assert plan["prompt_plan"][1]["effective_prompt"] == ""
    assert plan["model_specific"]["wan"]["runtime_status"] == "Runtime backend selected by WAN Timeline Runtime"
    assert sum(plan["model_specific"]["wan"]["prompt_relay"]["segment_lengths"]) == plan["model_specific"]["wan"]["prompt_relay"]["latent_chunk_count"]
    assert validation["is_valid"] is True
    assert "WAN_GAP_HAS_NO_CONDITIONING" in [entry["code"] for entry in validation["warnings"]]
    assert debug["source"] == "WAN Planner"
    assert debug["enabled"] is True
    assert debug["summary"]["planned_ranges"] == 2


def test_wan_planner_maps_sections_to_shots_and_preserves_boundary_metadata():
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

    plan, validation, debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(debug_mode="Full"),
    )
    wan = plan["model_specific"]["wan"]

    assert validation["is_valid"] is True
    assert [entry["shot_id"] for entry in plan["section_plan"]] == [
        "shot_section_001",
        "shot_section_002",
    ]
    assert [entry["shot_id"] for entry in wan["prompt_relay"]["local_prompts"]] == [
        "shot_section_001",
        "shot_section_002",
    ]
    assert wan["timeline_structure"]["section_to_shot"] == {
        "section_001": "shot_section_001",
        "section_002": "shot_section_002",
    }
    assert wan["timeline_structure"]["boundaries"][0]["mode"] == BOUNDARY_MODE_HARD_CUT
    assert wan["lora_resolution"]["targets"] == [
        MODEL_LORA_TARGET_HIGH_NOISE,
        MODEL_LORA_TARGET_LOW_NOISE,
    ]
    assert wan["lora_resolution"]["single_generation_loras"][MODEL_LORA_TARGET_HIGH_NOISE]["loras"] == []
    assert wan["lora_resolution"]["single_generation_loras"][MODEL_LORA_TARGET_LOW_NOISE]["loras"] == []
    assert debug["summary"]["shot_count"] == 2
    assert debug["summary"]["boundary_count"] == 1
    assert debug["details"]["timeline_structure"]["boundaries"][0]["mode"] == BOUNDARY_MODE_HARD_CUT
    assert "selected_shot_id" not in debug["summary"]
    assert "shot_context" not in wan


def test_wan_planner_plans_selected_shot_timeline_with_boundary_context():
    timeline = _two_shot_text_timeline()

    plan, validation, debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(debug_mode="Full"),
        shot_id="shot_section_002",
    )
    wan = plan["model_specific"]["wan"]
    shot_context = wan["shot_context"]

    assert validation["is_valid"] is True
    assert plan["project"]["duration_seconds"] == 2.0
    assert len(plan["section_plan"]) == 1
    assert plan["section_plan"][0]["item_id"] == "section_002"
    assert plan["section_plan"][0]["shot_id"] == "shot_section_002"
    assert plan["section_plan"][0]["start_time"] == 0.0
    assert plan["section_plan"][0]["end_time"] == 2.0
    assert wan["timeline_structure"]["section_to_shot"] == {
        "section_002": "shot_section_002",
    }
    assert shot_context["shot_id"] == "shot_section_002"
    assert shot_context["original_start_time"] == 1.0
    assert shot_context["original_end_time"] == 3.0
    assert shot_context["duration_seconds"] == 2.0
    assert shot_context["boundary_context"]["previous_shot_id"] == "shot_section_001"
    assert shot_context["boundary_context"]["incoming_boundary"]["mode"] == BOUNDARY_MODE_HARD_CUT
    assert wan["timeline_structure"]["metadata"]["shot_extraction"] == shot_context
    assert debug["summary"]["selected_shot_id"] == "shot_section_002"
    assert debug["summary"]["shot_original_start_time"] == 1.0
    assert debug["summary"]["shot_original_end_time"] == 3.0
    assert debug["summary"]["shot_duration_seconds"] == 2.0
    assert debug["details"]["shot_context"] == shot_context


def test_wan_planner_invalid_shot_id_marks_plan_invalid_without_crashing():
    timeline = _two_shot_text_timeline()

    plan, validation, debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(debug_mode="Full"),
        shot_id="missing_shot",
    )

    assert validation["is_valid"] is False
    assert [entry["code"] for entry in validation["errors"]] == [
        "SHOT_SELECTION_NOT_FOUND"
    ]
    assert plan["project"]["duration_seconds"] == 3.0
    assert len(plan["section_plan"]) == 2
    assert "shot_context" not in plan["model_specific"]["wan"]
    assert debug["summary"]["selected_shot_id"] == "missing_shot"
    assert "missing_shot" in debug["summary"]["shot_selection_error"]


def test_wan_planner_resolves_shot_loras_and_warns_when_runtime_switching_is_deferred(monkeypatch):
    monkeypatch.setattr(
        lora_config_module,
        "_available_loras",
        lambda: [
            "high_global.safetensors",
            "low_global.safetensors",
            "high_add.safetensors",
            "low_add.safetensors",
            "high_replace.safetensors",
            "low_replace.safetensors",
        ],
    )
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 3.0
    timeline["project"]["model_loras"]["global"][MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_HIGH_NOISE] = _wan_lora_stack("high_global")
    timeline["project"]["model_loras"]["global"][MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_LOW_NOISE] = _wan_lora_stack("low_global")
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
        _wan_shot(
            "shot_add",
            "section_add",
            0.0,
            1.0,
            LORA_MERGE_MODE_ADD_TO_GLOBAL,
            _wan_lora_stack("high_add"),
            _wan_lora_stack("low_add"),
        ),
        _wan_shot(
            "shot_replace",
            "section_replace",
            1.0,
            2.0,
            LORA_MERGE_MODE_REPLACE_GLOBAL,
            _wan_lora_stack("high_replace"),
            _wan_lora_stack("low_replace"),
        ),
        _wan_shot(
            "shot_disable",
            "section_disable",
            2.0,
            3.0,
            LORA_MERGE_MODE_DISABLE_LORAS,
            _wan_lora_stack("high_add"),
            _wan_lora_stack("low_add"),
        ),
    ]

    plan, validation, _debug = build_wan_timeline_plan(timeline, create_wan_timeline_config())
    lora_resolution = plan["model_specific"]["wan"]["lora_resolution"]
    loras_by_section = {
        entry["item_id"]: entry["effective_loras"]
        for entry in lora_resolution["section_loras"]
    }

    assert _lora_names(loras_by_section["section_add"][MODEL_LORA_TARGET_HIGH_NOISE]["loras"]) == [
        "high_global.safetensors",
        "high_add.safetensors",
    ]
    assert _lora_names(loras_by_section["section_add"][MODEL_LORA_TARGET_LOW_NOISE]["loras"]) == [
        "low_global.safetensors",
        "low_add.safetensors",
    ]
    assert _lora_names(loras_by_section["section_replace"][MODEL_LORA_TARGET_HIGH_NOISE]["loras"]) == ["high_replace.safetensors"]
    assert _lora_names(loras_by_section["section_replace"][MODEL_LORA_TARGET_LOW_NOISE]["loras"]) == ["low_replace.safetensors"]
    assert loras_by_section["section_disable"][MODEL_LORA_TARGET_HIGH_NOISE]["loras"] == []
    assert loras_by_section["section_disable"][MODEL_LORA_TARGET_LOW_NOISE]["loras"] == []
    assert lora_resolution["single_generation_loras"] is None
    assert lora_resolution["execution_strategy"] == "defer_per_shot_lora_execution"
    assert "WAN_SHOT_LORA_STACKS_DIFFER" in [entry["code"] for entry in validation["warnings"]]


def test_wan_planner_builds_hidden_segments_and_requires_vanilla_start_or_end_frame():
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

    plan, validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(max_generation_duration=1.0),
    )

    segmented = plan["model_specific"]["wan"]["segmented_generation"]
    assert segmented["enabled"] is True
    assert [segment["visible_frame_count"] for segment in segmented["segments"]] == [8, 8, 9]
    assert segmented["segments"][1]["trim_leading_frames"] == 5
    assert segmented["segments"][1]["continuity"]["continuity_frame_count"] == 5
    assert segmented["segments"][1]["generation_frame_count"] == 13
    assert "WAN_SEGMENTED_GENERATION_REQUIRES_START_OR_END_FRAME" in [entry["code"] for entry in validation["errors"]]


def test_wan_segment_padding_does_not_create_extra_hidden_generation_with_start_image():
    timeline = _timeline_with_start_image_and_three_text_sections()

    plan, validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(max_generation_duration=5.0),
    )

    segmented = plan["model_specific"]["wan"]["segmented_generation"]
    assert validation["is_valid"] is True
    assert plan["resolved_output"]["requested_frame_count"] == 160
    assert plan["resolved_output"]["frame_count"] == 161
    assert segmented["enabled"] is True
    assert [segment["visible_frame_count"] for segment in segmented["segments"]] == [79, 82]
    assert segmented["segments"][1]["trim_leading_frames"] == 5
    assert segmented["segments"][1]["generation_frame_count"] == 89
    assert segmented["segments"][1]["continuity"]["continuity_frame_count"] == 5
    assert segmented["segments"][1]["continuity"]["source"] == "previous_tail"


def test_wan_segment_plan_does_not_leak_original_start_keyframe_into_continuation():
    timeline = _timeline_with_start_image_and_three_text_sections()

    plan, validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(max_generation_duration=5.0),
    )

    assert validation["is_valid"] is True
    segment = plan["model_specific"]["wan"]["segmented_generation"]["segments"][1]
    tail = torch.zeros((5, 512, 384, 3), dtype=torch.float32)
    segment_plan = build_segment_plan(plan, segment, model_key="wan", previous_tail_images=tail)
    _apply_wan_segment_continuity(segment_plan, tail=tail)
    visual = segment_plan["model_specific"]["wan"]["visual_conditioning"]

    assert "image_section" not in segment["source_section_ids"]
    assert visual["requested_keyframes"] == []
    assert visual["applied_keyframes"] == []
    assert visual["continuation_source"] == "previous_tail"
    assert visual["transient_start_image"] is tail


def test_wan_bernini_allows_text_first_segmented_generation():
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

    plan, validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(model_mode="Bernini-A14B", max_generation_duration=1.0),
    )

    assert validation["is_valid"] is True
    assert plan["model_specific"]["wan"]["segmented_generation"]["enabled"] is True
    assert plan["model_specific"]["wan"]["bernini"]["task_type"] == "t2v"


def test_wan_planner_preserves_unsupported_media_audio_with_warnings():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["assets"].extend(
        [
            {
                "asset_id": "image_001",
                "type": ASSET_TYPE_IMAGE,
                "source_kind": ASSET_SOURCE_FILE_PATH,
                "path": "/mnt/media/reference.png",
                "name": "reference.png",
            },
            {
                "asset_id": "video_001",
                "type": ASSET_TYPE_VIDEO,
                "source_kind": ASSET_SOURCE_FILE_PATH,
                "path": "/mnt/media/source.mp4",
                "name": "source.mp4",
            },
            {
                "asset_id": "audio_001",
                "type": ASSET_TYPE_AUDIO,
                "source_kind": ASSET_SOURCE_FILE_PATH,
                "path": "/mnt/media/sound.wav",
                "name": "sound.wav",
            },
        ]
    )
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "section_image",
                "type": SECTION_TYPE_IMAGE,
                "start_time": 0.0,
                "end_time": 0.5,
                "image": {"asset_id": "image_001"},
                "prompt": "",
                "guide_strength": 0.7,
            },
            {
                "item_id": "section_video",
                "type": SECTION_TYPE_VIDEO,
                "start_time": 0.5,
                "end_time": 1.0,
                "video": {"asset_id": "video_001"},
                "prompt": "continue",
                "source_in": 1.0,
                "source_out": 2.0,
                "video_guidance_range": "Last Frames",
                "video_guidance_frame_count": 17,
            },
        ]
    )
    timeline["audio_tracks"].append(
        {
            "track_id": "audio_track_001",
            "clips": [
                {
                    "item_id": "audio_clip_001",
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "audio": {"asset_id": "audio_001"},
                    "volume": 0.5,
                    "fade_in": 0.1,
                    "fade_out": 0.2,
                    "enabled": True,
                    "lane": 0,
                }
            ],
        }
    )

    plan, validation, debug = build_wan_timeline_plan(timeline, create_wan_timeline_config())

    assert validation["is_valid"] is True
    warning_codes = [entry["code"] for entry in validation["warnings"]]
    assert warning_codes == ["WAN_VIDEO_SECTION_PROMPT_ONLY"]
    info_codes = [entry["code"] for entry in validation["info"]]
    assert "WAN_VISUAL_KEYFRAMES_PLANNED" in info_codes
    assert "WAN_AUDIO_FINAL_MIX_ONLY" in info_codes
    assert [entry["asset_id"] for entry in plan["media_plan"]] == ["image_001", "video_001"]
    assert plan["media_plan"][0]["wan_role"] == "Visual Keyframe Candidate"
    assert plan["media_plan"][1]["source_in"] == 1.0
    assert plan["media_plan"][1]["video_guidance_frame_count"] == 17
    assert plan["audio_plan"][0]["asset_id"] == "audio_001"
    assert plan["audio_plan"][0]["volume"] == 0.5
    assert debug["summary"]["warning_count"] == 1


def test_wan_planner_propagates_invalid_director_timeline_without_crash():
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

    plan, validation, debug = build_wan_timeline_plan(timeline, create_wan_timeline_config())

    assert plan["type"] == "WAN_TIMELINE_PLAN"
    assert validation["is_valid"] is False
    assert "TEXT_SECTION_EMPTY_PROMPT" in [entry["code"] for entry in validation["errors"]]
    assert "WAN_DIRECTOR_TIMELINE_INVALID" in [entry["code"] for entry in validation["errors"]]
    assert debug["summary"]["error_count"] == 2


def test_wan_planner_does_not_mutate_inputs():
    timeline = create_default_video_timeline()
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "wide shot",
        }
    )
    config = create_wan_timeline_config(debug_mode=True)
    timeline_before = copy.deepcopy(timeline)
    config_before = copy.deepcopy(config)

    build_wan_timeline_plan(timeline, config)

    assert timeline == timeline_before
    assert config == config_before
