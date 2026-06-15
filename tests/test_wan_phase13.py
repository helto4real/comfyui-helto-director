from __future__ import annotations

import copy
import math
from pathlib import Path

import torch
from PIL import Image

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
from shared.wan import build_wan_runtime_outputs, build_wan_timeline_plan, create_wan_timeline_config


def test_phase13_wan_config_defaults_and_legacy_normalization():
    config = create_wan_timeline_config()

    assert config["model_mode"] == "I2V-A14B"
    assert config["prompt_routing"] == "Prompt Relay"
    assert config["prompt_relay_epsilon"] == 0.001
    assert config["visual_conditioning_mode"] == "Timed Keyframes"
    assert config["runtime_backend_profile"] == "Plan Only"
    assert config["debug_mode"] == "Off"

    legacy = create_wan_timeline_config(audio_mode="Plan Timeline Audio", debug_mode=True)
    assert legacy["audio_policy"] == "Final Mix Only"
    assert legacy["debug_mode"] == "Summary"


def test_prompt_relay_segments_and_global_prompt_merge_sum_to_latent_chunks():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.1
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["global_prompt"]["enabled"] = True
    timeline["project"]["global_prompt"]["prompt"] = "cinematic"
    timeline["director_track"]["sections"].extend(
        [
            _text_section("text_a", 0.0, 0.7, "wide shot"),
            _text_section("text_b", 0.7, 2.1, "close shot"),
        ]
    )

    plan, validation, debug = build_wan_timeline_plan(timeline, create_wan_timeline_config(debug_mode="Full"))
    prompt_relay = plan["model_specific"]["wan"]["prompt_relay"]

    assert validation["is_valid"] is True
    assert prompt_relay["enabled"] is True
    assert prompt_relay["global_prompt"] == "cinematic"
    assert prompt_relay["local_prompts"][0]["prompt"] == "wide shot"
    assert prompt_relay["local_prompts"][0]["effective_prompt"] == "cinematic, wide shot"
    assert sum(prompt_relay["segment_lengths"]) == prompt_relay["latent_chunk_count"]
    assert prompt_relay["latent_chunk_count"] == ((plan["resolved_output"]["frame_count"] - 1) // 4) + 1
    assert debug["mode"] == "Full"
    assert debug["details"]["prompt_relay"]["segment_lengths"] == prompt_relay["segment_lengths"]


def test_gap_policy_warning_no_guidance_and_merge():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["director_track"]["sections"].append(_text_section("text_a", 0.0, 1.0, "opening"))

    _plan, warning_validation, _debug = build_wan_timeline_plan(timeline, create_wan_timeline_config(gap_policy="Warning"))
    assert "WAN_GAP_HAS_NO_CONDITIONING" in [entry["code"] for entry in warning_validation["warnings"]]

    merge_plan, merge_validation, _debug = build_wan_timeline_plan(timeline, create_wan_timeline_config(gap_policy="Merge With Previous Prompt"))
    assert all(entry["type"] != "Gap" for entry in merge_plan["section_plan"])
    assert "WAN_GAP_MERGED_WITH_PREVIOUS_PROMPT" in [entry["code"] for entry in merge_validation["info"]]


def test_visual_keyframes_preserve_start_timed_and_end_roles(tmp_path):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 4.0
    for index in range(4):
        asset_id = f"image_{index}"
        path = _write_image(tmp_path / f"image_{index}.png", (index * 40, 20, 200))
        timeline["assets"].append(_asset(asset_id, ASSET_TYPE_IMAGE, path))
        timeline["director_track"]["sections"].append(
            {
                "item_id": f"section_{index}",
                "type": SECTION_TYPE_IMAGE,
                "start_time": float(index),
                "end_time": float(index + 1),
                "image": {"asset_id": asset_id},
                "prompt": f"image prompt {index}",
                "guide_strength": 0.7 + index,
                "crop_mode": "Crop",
            }
        )

    plan, validation, debug = build_wan_timeline_plan(timeline, create_wan_timeline_config(debug_mode="Summary"))
    visual = plan["model_specific"]["wan"]["visual_conditioning"]

    assert validation["is_valid"] is True
    assert [entry["role"] for entry in visual["requested_keyframes"]] == ["Start", "Timed", "Timed", "End"]
    assert [entry["asset_id"] for entry in visual["requested_keyframes"]] == ["image_0", "image_1", "image_2", "image_3"]
    assert debug["summary"]["requested_visual_keyframes"] == 4


def test_video_policy_and_audio_final_mix_validation(tmp_path):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["assets"].extend(
        [
            _asset("video_001", ASSET_TYPE_VIDEO, "/tmp/source.mp4"),
            _asset("audio_001", ASSET_TYPE_AUDIO, "/tmp/audio.wav"),
        ]
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "video_section",
            "type": SECTION_TYPE_VIDEO,
            "start_time": 0.0,
            "end_time": 1.0,
            "video": {"asset_id": "video_001"},
            "prompt": "continue motion",
        }
    )
    timeline["audio_tracks"].append({
        "track_id": "audio_track_001",
        "clips": [
            {
                "item_id": "audio_clip",
                "start_time": 0.0,
                "end_time": 1.0,
                "audio": {"asset_id": "audio_001"},
                "enabled": True,
            }
        ],
    })

    _plan, validation, _debug = build_wan_timeline_plan(timeline, create_wan_timeline_config())
    assert "WAN_VIDEO_SECTION_PROMPT_ONLY" in [entry["code"] for entry in validation["warnings"]]
    assert "WAN_AUDIO_FINAL_MIX_ONLY" in [entry["code"] for entry in validation["info"]]

    _plan, error_validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(unsupported_video_section_policy="Error"),
    )
    assert error_validation["is_valid"] is False
    assert "WAN_UNSUPPORTED_VIDEO_SECTION" in [entry["code"] for entry in error_validation["errors"]]


def test_plan_only_runtime_validates_and_returns_debug():
    plan, _validation, _debug = build_wan_timeline_plan(_text_timeline(), create_wan_timeline_config(debug_mode="Summary"))

    runtime_model, positive, negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert isinstance(runtime_model, FakeModel)
    assert positive == []
    assert negative == []
    assert tuple(video_latent["samples"].shape[:3]) == (1, 48, plan["model_specific"]["wan"]["prompt_relay"]["latent_chunk_count"])
    assert runtime_debug["summary"]["backend"] == "Plan Only"
    assert runtime_debug["summary"]["requested_visual_keyframes"] == 0


def test_comfyui_core_runtime_applies_start_end_and_marks_timed_unsupported(tmp_path):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 3.0
    for index in range(3):
        asset_id = f"image_{index}"
        path = _write_image(tmp_path / f"image_{index}.png", (40 + index * 50, 20, 180))
        timeline["assets"].append(_asset(asset_id, ASSET_TYPE_IMAGE, path))
        timeline["director_track"]["sections"].append(
            {
                "item_id": f"section_{index}",
                "type": SECTION_TYPE_IMAGE,
                "start_time": float(index),
                "end_time": float(index + 1),
                "image": {"asset_id": asset_id},
                "prompt": f"keyframe {index}",
                "guide_strength": 1.0,
            }
        )
    config = create_wan_timeline_config(runtime_backend_profile="ComfyUI Core", debug_mode="Full")
    plan, _validation, _debug = build_wan_timeline_plan(timeline, config)
    plan_before = copy.deepcopy(plan)
    input_negative = [[torch.ones(1, 2), {"tag": "negative"}]]

    runtime_model, positive, negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
        negative=input_negative,
    )

    assert plan == plan_before
    assert input_negative[0][1] == {"tag": "negative"}
    assert len(runtime_model.object_patches) == 2
    assert positive[0][1]["concat_latent_image"].shape[1] == 48
    assert negative[0][1]["concat_mask"].shape[1] == 4
    assert video_latent["samples"].shape[1] == 48
    assert runtime_debug["summary"]["backend"] == "ComfyUI Core"
    assert runtime_debug["summary"]["applied_visual_keyframes"] == 2
    assert runtime_debug["summary"]["unsupported_visual_keyframes"] == 1
    assert runtime_debug["visual_conditioning"]["unsupported_keyframes"][0]["role"] == "Timed"


def _text_timeline():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["director_track"]["sections"].append(_text_section("section_text", 0.0, 1.0, "simple prompt"))
    return timeline


def _text_section(item_id: str, start: float, end: float, prompt: str):
    return {
        "item_id": item_id,
        "type": SECTION_TYPE_TEXT,
        "start_time": start,
        "end_time": end,
        "prompt": prompt,
    }


def _asset(asset_id: str, asset_type: str, path: str):
    return {
        "asset_id": asset_id,
        "type": asset_type,
        "source_kind": ASSET_SOURCE_FILE_PATH,
        "path": path,
        "name": Path(path).name,
    }


def _write_image(path: Path, color: tuple[int, int, int]) -> str:
    Image.new("RGB", (12, 8), color).save(path)
    return str(path)


class FakeTokenizer:
    add_eos = False

    def __call__(self, text):
        tokens = [index for index, part in enumerate(str(text).split(" ")) if part]
        return {"input_ids": tokens}


class FakeClip:
    tokenizer = FakeTokenizer()

    def tokenize(self, prompt):
        return prompt

    def encode_from_tokens_scheduled(self, tokens):
        return [[torch.ones(1, 2), {"prompt": tokens, "pooled_output": torch.ones(1, 2)}]]


class FakeCrossAttention:
    pass


class FakeBlock:
    def __init__(self):
        self.cross_attn = FakeCrossAttention()


class FakeDiffusionModel:
    def __init__(self):
        self.blocks = [FakeBlock(), FakeBlock()]


class FakeModel:
    def __init__(self):
        self.diffusion_model = FakeDiffusionModel()
        self.object_patches = {}

    def clone(self):
        return FakeModel()

    def get_model_object(self, name):
        assert name == "diffusion_model"
        return self.diffusion_model

    def add_object_patch(self, key, patch):
        self.object_patches[key] = patch


class FakeVAE:
    latent_channels = 48

    def spacial_compression_encode(self):
        return 16

    def encode(self, image):
        frames = int(image.shape[0])
        height = math.ceil(int(image.shape[1]) / 16)
        width = math.ceil(int(image.shape[2]) / 16)
        latent_frames = ((frames - 1) // 4) + 1
        return torch.ones(1, 48, latent_frames, height, width)
