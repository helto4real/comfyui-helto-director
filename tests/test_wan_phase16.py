from __future__ import annotations

import copy
import asyncio
import importlib.util
import math
import sys
from pathlib import Path

import pytest
import torch
from PIL import Image

from shared.contracts.video_timeline import ASSET_SOURCE_FILE_PATH, ASSET_TYPE_AUDIO, ASSET_TYPE_IMAGE, SECTION_TYPE_IMAGE, SECTION_TYPE_TEXT
from shared.timeline import create_default_video_timeline
from shared.wan import build_wan_runtime_outputs, build_wan_timeline_plan, create_wan_timeline_config


def test_phase16_runtime_schema_preserves_existing_contract():
    module_path = Path(__file__).resolve().parents[1]
    module_name = str(module_path).replace(".", "_x_")
    spec = importlib.util.spec_from_file_location(module_name, module_path / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    previous_path = list(sys.path)
    sys.modules[module_name] = module
    try:
        sys.path = [path for path in sys.path if Path(path or ".").resolve() != module_path]
        spec.loader.exec_module(module)
        extension = asyncio.run(module.comfy_entrypoint())
        node_classes = asyncio.run(extension.get_node_list())
        runtime = next(node for node in node_classes if node.define_schema().node_id == "HeltoWAN22TimelineRuntime")
    finally:
        sys.path = previous_path
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous

    schema = runtime.define_schema()
    input_names = [getattr(item, "name", getattr(item, "id", None)) for item in schema.inputs]
    output_names = [getattr(item, "name", getattr(item, "id", None)) for item in schema.outputs]

    assert input_names == [
        "high_noise_model",
        "low_noise_model",
        "clip",
        "vae",
        "wan_timeline_plan",
        "negative",
        "batch_size",
    ]
    assert output_names == [
        "high_noise_model",
        "low_noise_model",
        "positive",
        "negative",
        "video_latent",
        "runtime_debug",
    ]
    assert [hidden.value for hidden in schema.hidden] == ["UNIQUE_ID"]
    assert "model" not in input_names
    assert "WAN_RUNTIME_PAYLOAD" not in output_names


def test_phase16_comfyui_core_i2v_requires_start_image():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core"),
    )

    with pytest.raises(ValueError, match="WAN_REQUIRED_IMAGE_CONDITIONING_MISSING"):
        build_wan_runtime_outputs(
            high_noise_model=FakeModel(),
            clip=FakeClip(),
            vae=FakeVAE(),
            wan_timeline_plan=plan,
        )


def test_phase16_comfyui_core_applies_one_start_image_with_real_core_helper(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=1),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core", debug_mode="Full", resolution_profile="Quick Draft"),
    )

    _high, _low, positive, negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert runtime_debug["output_payload_type"] == "COMFYUI_CORE_CONDITIONING_LATENT"
    assert runtime_debug["visual_conditioning"]["selected_primary_image"]["section_id"] == "section_image_0"
    assert _helper_decision(runtime_debug) == "WanImageToVideo"
    assert positive[0][1]["concat_latent_image"].shape[1] == 16
    assert negative[0][1]["concat_mask"].shape[1] == 1
    assert video_latent["samples"].shape[1] == 16


def test_phase16_comfyui_core_applies_start_end_and_preserves_timed_unsupported(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=4),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core", debug_mode="Full", resolution_profile="Quick Draft"),
    )
    plan_before = copy.deepcopy(plan)

    _high, _low, positive, negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        low_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert plan == plan_before
    assert _helper_decision(runtime_debug) == "WanFirstLastFrameToVideo"
    assert runtime_debug["summary"]["applied_visual_keyframes"] == 2
    assert runtime_debug["summary"]["unsupported_visual_keyframes"] == 2
    assert [entry["role"] for entry in runtime_debug["visual_conditioning"]["unsupported_keyframes"]] == ["Timed", "Timed"]
    assert "Timed visual keyframes are planned and reported" in " ".join(runtime_debug["known_limitations"])
    assert positive[0][1]["concat_latent_image"].shape[1] == 16
    assert negative[0][1]["concat_mask"].shape[1] == 4
    assert video_latent["samples"].shape[1] == 16


def test_phase16_wan22_latent_helper_is_used_for_48_channel_vae(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=1),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core", debug_mode="Full", resolution_profile="Quick Draft"),
    )

    _high, _low, positive, _negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel48(),
        clip=FakeClip(),
        vae=FakeVAE48(),
        wan_timeline_plan=plan,
    )

    assert _helper_decision(runtime_debug) == "Wan22ImageToVideoLatent"
    assert video_latent["samples"].shape[1] == 48
    assert "noise_mask" in video_latent
    assert positive[0][1]["prompt"]


def test_phase16_text_capable_core_mode_can_run_without_image_keyframes():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core", model_mode="T2V-A14B", debug_mode="Summary"),
    )

    _high, _low, positive, negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert positive[0][1]["prompt"]
    assert negative[0][1]["pooled_output"].sum().item() == 0
    assert video_latent["samples"].shape[1] == 16
    assert [event["stage"] for event in runtime_debug["status_events"]] == [
        "timeline.prepare",
        "timeline.prompt",
        "timeline.conditioning",
        "timeline.done",
    ]
    assert runtime_debug["status"]["runtime_executed"] is True
    assert runtime_debug["visual_conditioning"]["selected_primary_image"] is None


def test_phase16_missing_media_fails_before_silent_prompt_only_fallback(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=1, write_files=False),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core"),
    )

    with pytest.raises(ValueError, match="RUNTIME_MEDIA_FILE_NOT_FOUND"):
        build_wan_runtime_outputs(
            high_noise_model=FakeModel(),
            clip=FakeClip(),
            vae=FakeVAE(),
            wan_timeline_plan=plan,
        )


def test_phase16_audio_stays_final_mix_metadata_only():
    plan, _validation, _debug = build_wan_timeline_plan(
        _audio_timeline(),
        create_wan_timeline_config(debug_mode="Summary"),
    )

    *_outputs, runtime_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)

    assert runtime_debug["status"]["audio"]["final_mix_only"] is True
    assert "WAN audio conditioning is unsupported" in " ".join(runtime_debug["backend"]["unsupported_features"])


def _helper_decision(runtime_debug: dict) -> str | None:
    for decision in runtime_debug.get("media_decisions", []):
        if decision.get("type") == "comfy_core_helper":
            return decision.get("helper")
    return None


def _text_timeline():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 0.25
    timeline["project"]["frame_rate"] = 8.0
    timeline["director_track"]["sections"].append({
        "item_id": "section_text",
        "type": SECTION_TYPE_TEXT,
        "start_time": 0.0,
        "end_time": 0.25,
        "prompt": "simple prompt",
    })
    return timeline


def _image_timeline(tmp_path: Path, *, count: int, write_files: bool = True):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = float(count) * 0.25
    timeline["project"]["frame_rate"] = 8.0
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
            "start_time": float(index) * 0.25,
            "end_time": float(index + 1) * 0.25,
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
            "end_time": 0.25,
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


class FakeLatentFormat48:
    latent_channels = 48
    spacial_downscale_ratio = 16


class FakeModel:
    latent_format_class = FakeLatentFormat

    def __init__(self):
        self.diffusion_model = FakeDiffusionModel()
        self.latent_format = self.latent_format_class()
        self.object_patches = {}

    def clone(self):
        return self.__class__()

    def get_model_object(self, name):
        if name == "latent_format":
            return self.latent_format
        assert name == "diffusion_model"
        return self.diffusion_model

    def add_object_patch(self, key, patch):
        self.object_patches[key] = patch


class FakeModel48(FakeModel):
    latent_format_class = FakeLatentFormat48


class FakeVAE:
    latent_channels = 16

    def spacial_compression_encode(self):
        return 8

    def encode(self, image):
        frames = int(image.shape[0])
        height = math.ceil(int(image.shape[1]) / 8)
        width = math.ceil(int(image.shape[2]) / 8)
        latent_frames = ((frames - 1) // 4) + 1
        return torch.ones(1, self.latent_channels, latent_frames, height, width)


class FakeVAE48(FakeVAE):
    latent_channels = 48

    def spacial_compression_encode(self):
        return 16

    def encode(self, image):
        frames = int(image.shape[0])
        height = math.ceil(int(image.shape[1]) / 16)
        width = math.ceil(int(image.shape[2]) / 16)
        latent_frames = ((frames - 1) // 4) + 1
        return torch.ones(1, self.latent_channels, latent_frames, height, width)
