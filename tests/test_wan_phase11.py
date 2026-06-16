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
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
)
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
    assert [output.io_type for output in config_schema.outputs] == ["WAN_TIMELINE_CONFIG"]
    tail_input = next(input_item for input_item in config_schema.inputs if input_item.id == "segment_continuity_tail_frames")
    assert tail_input.options == ["1", "5", "9"]
    assert tail_input.default == "5"
    assert [input_item.io_type for input_item in planner_schema.inputs] == [
        "VIDEO_TIMELINE",
        "WAN_TIMELINE_CONFIG",
    ]
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
    assert config["vram_unload_policy"] == "Off"
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
