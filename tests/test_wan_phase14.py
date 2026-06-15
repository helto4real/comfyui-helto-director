from __future__ import annotations

import asyncio
import copy
import importlib.util
import math
import sys
from pathlib import Path

import pytest
import torch
from PIL import Image

from shared.contracts.video_timeline import ASSET_SOURCE_FILE_PATH, ASSET_TYPE_IMAGE, SECTION_TYPE_IMAGE, SECTION_TYPE_TEXT
from shared.timeline import create_default_video_timeline
from shared.wan import build_wan_runtime_outputs, build_wan_timeline_plan, create_wan_timeline_config
from shared.wan.runtime.capabilities import select_keyframes_for_capabilities


def test_runtime_schema_uses_dual_model_sockets():
    module, _node_classes = _load_nodepack()
    schema = module.NODE_CLASS_MAPPINGS["HeltoWAN22TimelineRuntime"].define_schema()

    assert _item_names(schema.inputs) == [
        "high_noise_model",
        "low_noise_model",
        "clip",
        "vae",
        "wan_timeline_plan",
        "negative",
        "batch_size",
    ]
    assert _item_names(schema.outputs) == [
        "high_noise_model",
        "low_noise_model",
        "positive",
        "negative",
        "video_latent",
        "runtime_debug",
    ]
    assert "model" not in _item_names(schema.inputs)
    assert "model" not in _item_names(schema.outputs)


def test_plan_only_runtime_succeeds_without_model_clip_or_vae():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(debug_mode="Summary"),
    )

    high_model, low_model, positive, negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        wan_timeline_plan=plan,
    )

    assert high_model is None
    assert low_model is None
    assert positive == []
    assert negative == []
    assert video_latent["samples"].shape[1] == 16
    assert runtime_debug["summary"]["requested_backend"] == "Plan Only"
    assert runtime_debug["summary"]["resolved_backend"] == "Plan Only"
    assert _validation_codes(runtime_debug, "info").count("WAN_RUNTIME_BACKEND_PLAN_ONLY") >= 1


def test_auto_backend_resolves_to_plan_only_or_comfyui_core(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=2),
        create_wan_timeline_config(runtime_backend_profile="Auto", debug_mode="Summary"),
    )

    *_outputs, plan_only_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)
    assert plan_only_debug["summary"]["requested_backend"] == "Auto"
    assert plan_only_debug["summary"]["resolved_backend"] == "Plan Only"

    high_model, _low_model, _positive, _negative, _latent, core_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )
    assert isinstance(high_model, FakeModel)
    assert core_debug["summary"]["requested_backend"] == "Auto"
    assert core_debug["summary"]["resolved_backend"] == "ComfyUI Core"
    assert core_debug["model_patch_status"]["high_noise_model"] == "patched"
    assert core_debug["model_patch_status"]["low_noise_model"] == "not_connected"


def test_comfyui_core_patches_both_models_when_connected(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=2),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core"),
    )
    plan_before = copy.deepcopy(plan)
    high_input = FakeModel("high")
    low_input = FakeModel("low")

    high_model, low_model, positive, negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=high_input,
        low_noise_model=low_input,
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert plan == plan_before
    assert high_model is not high_input
    assert low_model is not low_input
    assert len(high_model.object_patches) == 2
    assert len(low_model.object_patches) == 2
    assert positive[0][1]["concat_latent_image"].shape[1] == 16
    assert negative[0][1]["concat_mask"].shape[1] == 4
    assert video_latent["samples"].shape[1] == 16
    assert runtime_debug["model_patch_status"] == {
        "high_noise_model": "patched",
        "low_noise_model": "patched",
    }


def test_comfyui_core_patches_one_model_and_warns_when_other_missing(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=1),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core"),
    )

    high_model, low_model, _positive, _negative, _latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert isinstance(high_model, FakeModel)
    assert len(high_model.object_patches) == 2
    assert low_model is None
    assert runtime_debug["model_patch_status"]["high_noise_model"] == "patched"
    assert runtime_debug["model_patch_status"]["low_noise_model"] == "not_connected"
    assert "WAN_RUNTIME_REQUIRED_INPUT_MISSING" in _validation_codes(runtime_debug, "warnings")


def test_comfyui_core_errors_when_prompt_relay_enabled_without_model():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core", model_mode="T2V-A14B"),
    )

    with pytest.raises(ValueError, match="WAN_RUNTIME_REQUIRED_INPUT_MISSING.*Prompt Relay"):
        build_wan_runtime_outputs(
            clip=FakeClip(),
            vae=FakeVAE(),
            wan_timeline_plan=plan,
        )


def test_runtime_latent_uses_model_format_not_mismatched_vae():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core", model_mode="T2V-A14B"),
    )

    _high_model, _low_model, _positive, _negative, video_latent, _runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE48(),
        wan_timeline_plan=plan,
    )

    assert video_latent["samples"].shape[1] == 16


def test_visual_keyframe_vae_model_latent_mismatch_fails_clearly(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=1),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core"),
    )

    with pytest.raises(ValueError, match="WAN_RUNTIME_LATENT_FORMAT_MISMATCH"):
        build_wan_runtime_outputs(
            high_noise_model=FakeModel(),
            clip=FakeClip(),
            vae=FakeVAE48(),
            wan_timeline_plan=plan,
        )


def test_missing_keyframe_media_fails_clearly(tmp_path):
    timeline = _image_timeline(tmp_path, count=1, write_files=False)
    plan, _validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core"),
    )

    with pytest.raises(ValueError, match="RUNTIME_MEDIA_FILE_NOT_FOUND"):
        build_wan_runtime_outputs(
            high_noise_model=FakeModel(),
            clip=FakeClip(),
            vae=FakeVAE(),
            wan_timeline_plan=plan,
        )


def test_prompt_relay_segment_mismatch_fails_clearly():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(),
    )
    prompt_relay = plan["model_specific"]["wan"]["prompt_relay"]
    prompt_relay["segment_lengths"] = [1]

    with pytest.raises(ValueError, match="WAN_PROMPT_RELAY_SEGMENT_LENGTH_MISMATCH"):
        build_wan_runtime_outputs(wan_timeline_plan=plan)


def test_visual_keyframe_selector_preserves_start_end_and_marks_timed_unsupported():
    requested = [
        {"section_id": "start", "role": "Start", "frame": 0},
        {"section_id": "mid_a", "role": "Timed", "frame": 12},
        {"section_id": "mid_b", "role": "Timed", "frame": 24},
        {"section_id": "end", "role": "End", "frame": 36},
    ]
    capabilities = {
        "supports_start_image": True,
        "supports_end_image": True,
        "supports_timed_keyframes": False,
        "max_visual_keyframes": 2,
    }

    applied, unsupported = select_keyframes_for_capabilities(requested, capabilities, "ComfyUI Core")

    assert [entry["section_id"] for entry in applied] == ["start", "end"]
    assert [entry["section_id"] for entry in unsupported] == ["mid_a", "mid_b"]
    assert all("Timed visual keyframes" in entry["reason"] for entry in unsupported)


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


def _item_names(items):
    return [getattr(item, "name", getattr(item, "id", None)) for item in items]


def _validation_codes(runtime_debug: dict, bucket: str) -> list[str]:
    return [entry["code"] for entry in runtime_debug["validation"].get(bucket, [])]


def _text_timeline():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["director_track"]["sections"].append({
        "item_id": "section_text",
        "type": SECTION_TYPE_TEXT,
        "start_time": 0.0,
        "end_time": 1.0,
        "prompt": "simple prompt",
    })
    return timeline


def _image_timeline(tmp_path: Path, *, count: int, write_files: bool = True):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = float(count)
    for index in range(count):
        asset_id = f"image_{index}"
        path = tmp_path / f"image_{index}.png"
        if write_files:
            Image.new("RGB", (12, 8), (30 + index * 40, 80, 180)).save(path)
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
    def __init__(self, label="model"):
        self.label = label
        self.diffusion_model = FakeDiffusionModel()
        self.latent_format = FakeLatentFormat()
        self.object_patches = {}

    def clone(self):
        return FakeModel(self.label)

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


class FakeVAE48:
    latent_channels = 48

    def spacial_compression_encode(self):
        return 16

    def encode(self, image):
        frames = int(image.shape[0])
        height = math.ceil(int(image.shape[1]) / 16)
        width = math.ceil(int(image.shape[2]) / 16)
        latent_frames = ((frames - 1) // 4) + 1
        return torch.ones(1, 48, latent_frames, height, width)
