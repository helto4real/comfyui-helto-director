from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import torch
from PIL import Image

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_IMAGE,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
)
from shared.timeline import create_default_video_timeline
from shared.wan import build_wan_runtime_outputs, build_wan_timeline_plan, create_wan_timeline_config


def test_plan_only_runtime_debug_has_compatibility_report():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(debug_mode="Summary"),
    )

    *_, runtime_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)

    backend = runtime_debug["backend"]
    assert backend["requested_profile"] == "Plan Only"
    assert backend["resolved_profile"] == "Plan Only"
    assert backend["available"] is False
    assert backend["visual_keyframe_support_level"] == "Plan Only debug"
    assert backend["missing_requirements"]
    assert "ComfyUI Core" in backend["recommended_next_action"]
    assert runtime_debug["status"]["runtime_executed"] is False
    assert runtime_debug["status"]["plan_only"] is True


def test_comfyui_core_runtime_debug_has_compatibility_report(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=2),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core", debug_mode="Summary"),
    )

    *_, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        low_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    backend = runtime_debug["backend"]
    assert backend["requested_profile"] == "ComfyUI Core"
    assert backend["resolved_profile"] == "ComfyUI Core"
    assert backend["available"] is True
    assert backend["prompt_relay_supported"] is True
    assert backend["visual_keyframe_support_level"] == "Start and End only"
    assert backend["max_visual_keyframes"] == 2
    assert runtime_debug["status"]["runtime_executed"] is True
    assert runtime_debug["status"]["prompt_relay"]["applied"] is True


def test_auto_plan_only_reports_missing_backend_requirements():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(runtime_backend_profile="Auto", debug_mode="Summary"),
    )

    *_, runtime_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)

    assert runtime_debug["backend"]["requested_profile"] == "Auto"
    assert runtime_debug["backend"]["resolved_profile"] == "Plan Only"
    assert "Auto resolved to Plan Only" in runtime_debug["backend"]["missing_requirements"][0]
    assert "Connect CLIP, VAE" in runtime_debug["backend"]["recommended_next_action"]


def test_four_plus_keyframes_survive_to_runtime_debug_with_reasons(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=5),
        create_wan_timeline_config(runtime_backend_profile="Plan Only", debug_mode="Full"),
    )
    plan_before = copy.deepcopy(plan)

    *_, runtime_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)

    assert plan == plan_before
    assert runtime_debug["summary"]["requested_visual_keyframes"] == 5
    assert runtime_debug["summary"]["applied_visual_keyframes"] == 0
    assert runtime_debug["summary"]["unsupported_visual_keyframes"] == 5
    assert [entry["role"] for entry in runtime_debug["visual_conditioning"]["requested_keyframes"]] == [
        "Start",
        "Timed",
        "Timed",
        "Timed",
        "End",
    ]
    assert all(entry.get("reason") for entry in runtime_debug["visual_conditioning"]["unsupported_keyframes"])
    assert runtime_debug["status"]["visual_keyframes"]["unsupported_reasons"]


def test_text_only_prompt_relay_reports_no_image_conditioning_error():
    plan, validation, _debug = build_wan_timeline_plan(
        _text_timeline(section_count=2),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core", debug_mode="Full"),
    )

    *_, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert validation["is_valid"] is True
    assert runtime_debug["status"]["prompt_relay"]["enabled"] is True
    assert runtime_debug["status"]["prompt_relay"]["supported"] is True
    assert runtime_debug["status"]["prompt_relay"]["applied"] is True
    assert runtime_debug["status"]["visual_keyframes"] == {
        "requested": 0,
        "applied": 0,
        "unsupported": 0,
        "unsupported_reasons": [],
    }
    assert not any("image" in entry.lower() for entry in runtime_debug["backend"]["missing_requirements"])


def test_audio_final_mix_only_is_reported():
    plan, validation, _debug = build_wan_timeline_plan(
        _audio_timeline(),
        create_wan_timeline_config(runtime_backend_profile="Plan Only", debug_mode="Summary"),
    )

    *_, runtime_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)

    assert validation["is_valid"] is True
    assert "WAN_AUDIO_FINAL_MIX_ONLY" in [entry["code"] for entry in validation["info"]]
    assert runtime_debug["backend"]["audio_policy"] == "Final Mix Only"
    assert "WAN audio conditioning is unsupported" in " ".join(runtime_debug["backend"]["unsupported_features"])
    assert runtime_debug["status"]["audio"] == {
        "clip_count": 1,
        "policy": "Final Mix Only",
        "final_mix_only": True,
    }


def test_wan_video_wrapper_unavailable_error_is_clear():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(runtime_backend_profile="WanVideoWrapper"),
    )

    with pytest.raises(ValueError, match="WAN_RUNTIME_BACKEND_NOT_AVAILABLE"):
        build_wan_runtime_outputs(wan_timeline_plan=plan)


def _text_timeline(section_count: int = 1):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = float(section_count)
    for index in range(section_count):
        timeline["director_track"]["sections"].append({
            "item_id": f"section_text_{index}",
            "type": SECTION_TYPE_TEXT,
            "start_time": float(index),
            "end_time": float(index + 1),
            "prompt": f"text prompt {index}",
        })
    return timeline


def _image_timeline(tmp_path: Path, *, count: int):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = float(count)
    for index in range(count):
        asset_id = f"image_{index}"
        path = tmp_path / f"image_{index}.png"
        Image.new("RGB", (12, 8), (30 + index * 25, 80, 180)).save(path)
        timeline["assets"].append({
            "asset_id": asset_id,
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": str(path),
            "name": path.name,
        })
        timeline["director_track"]["sections"].append({
            "item_id": f"section_image_{index}",
            "type": SECTION_TYPE_IMAGE,
            "start_time": float(index),
            "end_time": float(index + 1),
            "image": {"asset_id": asset_id},
            "prompt": f"image prompt {index}",
            "guide_strength": 1.0,
        })
    return timeline


def _audio_timeline():
    timeline = _text_timeline()
    timeline["assets"].append({
        "asset_id": "audio_001",
        "type": ASSET_TYPE_AUDIO,
        "source_kind": ASSET_SOURCE_FILE_PATH,
        "path": "/replace/with/audio.wav",
        "name": "audio.wav",
    })
    timeline["audio_tracks"].append({
        "track_id": "audio_track_001",
        "clips": [{
            "item_id": "audio_clip_001",
            "start_time": 0.0,
            "end_time": 1.0,
            "audio": {"asset_id": "audio_001"},
            "source_in": 0.0,
            "source_out": None,
            "volume": 1.0,
            "fade_in": 0.0,
            "fade_out": 0.0,
            "enabled": True,
            "lane": 0,
        }],
    })
    return timeline


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


class FakeLatentFormat:
    latent_channels = 16
    spacial_downscale_ratio = 8


class FakeModel:
    def __init__(self):
        self.diffusion_model = FakeDiffusionModel()
        self.latent_format = FakeLatentFormat()
        self.object_patches = {}

    def clone(self):
        return FakeModel()

    def get_model_object(self, name):
        if name == "latent_format":
            return self.latent_format
        assert name == "diffusion_model"
        return self.diffusion_model

    def add_object_patch(self, key, patch):
        self.object_patches[key] = patch


class FakeVAE:
    latent_channels = 16

    def spacial_compression_encode(self):
        return 8

    def encode(self, image):
        frames = int(image.shape[0])
        height = math.ceil(int(image.shape[1]) / 8)
        width = math.ceil(int(image.shape[2]) / 8)
        latent_frames = ((frames - 1) // 4) + 1
        return torch.ones(1, 16, latent_frames, height, width)
