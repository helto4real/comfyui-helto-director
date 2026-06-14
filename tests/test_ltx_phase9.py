from __future__ import annotations

import copy
import asyncio
import importlib.util
import math
import struct
import sys
import wave
from pathlib import Path

import pytest
import torch
from PIL import Image

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_IMAGE,
    QUALITY_PRESET_QUICK_DRAFT,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
)
from shared.ltx import build_ltx_runtime_outputs, build_ltx_timeline_plan, create_ltx_timeline_config
from shared.timeline import create_default_video_timeline


def _registered_runtime_node():
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
        for node_class in asyncio.run(extension.get_node_list()):
            if node_class.define_schema().node_id == "HeltoLTX23TimelineRuntime":
                return node_class
    finally:
        sys.path = previous_path
        if previous is None:
            sys.modules.pop(sys_module_name, None)
        else:
            sys.modules[sys_module_name] = previous
    raise AssertionError("HeltoLTX23TimelineRuntime was not registered.")


class FakeRawTokenizer:
    add_eos = False

    def __call__(self, text):
        tokens = [part for part in str(text or "").replace(",", " ").split() if part]
        return {"input_ids": list(range(len(tokens)))}


class FakeTokenizerWrapper:
    def __init__(self):
        self.inner = type("InnerTokenizer", (), {"tokenizer": FakeRawTokenizer()})()


class FakeClip:
    def __init__(self):
        self.tokenizer = FakeTokenizerWrapper()
        self.encoded = []

    def tokenize(self, text):
        self.last_text = text
        return {"text": text}

    def encode_from_tokens_scheduled(self, tokens):
        self.encoded.append(tokens)
        return [["conditioning", {"text": tokens["text"]}]]


class FakeAttention:
    pass


class FakeTransformerBlock:
    def __init__(self):
        self.attn2 = FakeAttention()
        self.audio_attn2 = FakeAttention()


class FakeDiffusionModel:
    def __init__(self):
        self.patchifier = object()
        self.vae_scale_factors = (8, 32, 32)
        self.transformer_blocks = [FakeTransformerBlock(), FakeTransformerBlock()]


class FakeModelWrapper:
    def __init__(self):
        self.diffusion_model = FakeDiffusionModel()


class FakeModel:
    def __init__(self):
        self.model = FakeModelWrapper()
        self.object_patches = {}

    def clone(self):
        return FakeModel()

    def get_model_object(self, name):
        assert name == "diffusion_model"
        return self.model.diffusion_model

    def add_object_patch(self, key, value):
        self.object_patches[key] = value


class FakeVAE:
    downscale_index_formula = (8, 32, 32)

    def encode(self, pixels):
        frames = ((pixels.shape[0] - 1) // 8) + 1
        height = max(1, pixels.shape[1] // 32)
        width = max(1, pixels.shape[2] // 32)
        return torch.ones((1, 128, frames, height, width), dtype=torch.float32)


def _runtime_args(plan, **overrides):
    args = {
        "model": FakeModel(),
        "clip": FakeClip(),
        "negative": [["negative", {}]],
        "vae": FakeVAE(),
        "ltx_timeline_plan": plan,
    }
    args.update(overrides)
    return args


def _text_plan(duration=1.0, prompt="wide shot"):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = duration
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": duration,
            "prompt": prompt,
        }
    )
    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())
    assert validation["is_valid"] is True
    return plan


def _image_plan(path: Path):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["project"]["global_prompt"]["enabled"] = True
    timeline["project"]["global_prompt"]["prompt"] = "cinematic lighting"
    timeline["assets"].append(
        {
            "asset_id": "image_001",
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": str(path),
            "name": path.name,
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_IMAGE,
            "start_time": 0.0,
            "end_time": 1.0,
            "image": {"asset_id": "image_001"},
            "prompt": "subject detail",
            "guide_strength": 0.5,
        }
    )
    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())
    assert validation["is_valid"] is True
    return plan


def _write_test_wav(path: Path, duration=1.0, frequency=440.0, amplitude=0.6):
    sample_rate = 44100
    total = int(sample_rate * duration)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        for index in range(total):
            value = int(amplitude * math.sin(2.0 * math.pi * frequency * index / sample_rate) * 32767)
            output.writeframes(struct.pack("<h", value))


def _audio_plan(path: Path):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["assets"].append(
        {
            "asset_id": "audio_001",
            "type": ASSET_TYPE_AUDIO,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": str(path),
            "name": path.name,
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "audio reactive scene",
        }
    )
    timeline["audio_tracks"].append(
        {
            "track_id": "audio_track_001",
            "clips": [
                {
                    "item_id": "audio_clip_001",
                    "audio": {"asset_id": "audio_001"},
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "source_in": 0.0,
                    "source_out": 1.0,
                    "volume": 50.0,
                    "fade_in": 0.1,
                    "fade_out": 0.1,
                    "enabled": True,
                    "lane": 0,
                }
            ],
        }
    )
    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())
    assert validation["is_valid"] is True
    return plan


def test_ltx_runtime_node_schema_io_order():
    schema = _registered_runtime_node().define_schema()

    assert schema.node_id == "HeltoLTX23TimelineRuntime"
    assert [input_item.io_type for input_item in schema.inputs] == [
        "MODEL",
        "CLIP",
        "CONDITIONING",
        "VAE",
        "LTX_TIMELINE_PLAN",
        "LATENT",
        "VAE",
        "LTX_IDENTITY_ANCHOR",
        "SIGMAS",
        "IC_LORA_PARAMETERS",
    ]
    assert [output.io_type for output in schema.outputs] == [
        "MODEL",
        "CONDITIONING",
        "CONDITIONING",
        "LATENT",
        "LATENT",
        "AUDIO",
        "GUIDE_DATA",
        "IMAGE",
        "AUDIO",
        "FLOAT",
        "INT",
        "DEBUG_INFO",
    ]
    assert [output.id for output in schema.outputs] == [
        "model",
        "positive",
        "negative",
        "video_latent",
        "audio_latent",
        "combined_audio",
        "guide_data",
        "source_video_images",
        "source_video_audio",
        "source_video_frame_rate",
        "source_video_frame_count",
        "runtime_debug",
    ]


def test_text_only_timeline_outputs_patched_model_latents_audio_and_debug():
    plan = _text_plan()

    (
        runtime_model,
        positive,
        negative,
        video_latent,
        audio_latent,
        combined_audio,
        guide_data,
        source_video_images,
        source_video_audio,
        source_video_frame_rate,
        source_video_frame_count,
        runtime_debug,
    ) = build_ltx_runtime_outputs(**_runtime_args(plan))

    assert len(runtime_model.object_patches) == 4
    assert "wide shot" in positive[0][1]["text"]
    assert negative == [["negative", {}]]
    assert video_latent["samples"].shape[2] == plan["resolved_output"]["frame_count"] // 8 + 1
    assert audio_latent["samples"].shape == (1, 0, 0, 0)
    assert combined_audio["waveform"].shape[1] == 2
    assert guide_data["strengths"] == [0.0]
    assert source_video_images.shape[0] == 1
    assert source_video_audio["waveform"].shape[1] == 2
    assert source_video_frame_rate == plan["resolved_output"]["frame_rate"]
    assert source_video_frame_count == 0
    assert runtime_debug["type"] == "DEBUG_INFO"
    assert runtime_debug["summary"]["applied_guides"] == 0
    assert runtime_debug["summary"]["audio_clip_count"] == 0
    assert any("No audio_vae connected" in entry for entry in runtime_debug["diagnostics"])


def test_planner_validation_errors_fail_clearly():
    plan = _text_plan()
    plan["validation"] = {
        "is_valid": False,
        "errors": [{"code": "LTX_DIRECTOR_TIMELINE_INVALID"}],
        "warnings": [],
        "info": [],
    }

    with pytest.raises(ValueError, match="LTX_DIRECTOR_TIMELINE_INVALID"):
        build_ltx_runtime_outputs(**_runtime_args(plan))


def test_missing_image_media_path_fails_clearly(tmp_path):
    plan = _image_plan(tmp_path / "missing.png")

    with pytest.raises(FileNotFoundError):
        build_ltx_runtime_outputs(**_runtime_args(plan))


def test_missing_audio_media_path_fails_clearly(tmp_path):
    plan = _audio_plan(tmp_path / "missing.wav")

    with pytest.raises(FileNotFoundError):
        build_ltx_runtime_outputs(**_runtime_args(plan))


def test_image_section_creates_guide_data_and_applies_guide_behavior(tmp_path):
    image_path = tmp_path / "guide.png"
    Image.new("RGB", (64, 64), (255, 32, 128)).save(image_path)
    plan = _image_plan(image_path)

    _, positive, negative, video_latent, _, _, guide_data, *_rest, runtime_debug = build_ltx_runtime_outputs(**_runtime_args(plan))

    assert guide_data["strengths"] == [0.5]
    assert guide_data["insert_frames"] == [0]
    assert len(guide_data["images"]) == 1
    assert video_latent["samples"].shape[2] > guide_data["clean_latent_frames"]
    assert positive[0][1]["guide_attention_entries"][0]["strength"] == 0.5
    assert negative[0][1]["guide_attention_entries"][0]["strength"] == 0.5
    assert runtime_debug["summary"]["applied_guides"] == 1


def test_generated_wav_audio_mixes_with_volume_and_fades(tmp_path):
    audio_path = tmp_path / "tone.wav"
    _write_test_wav(audio_path)
    plan = _audio_plan(audio_path)

    *_, combined_audio, _guide_data, _source_images, _source_audio, _source_fps, _source_count, runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    waveform = combined_audio["waveform"]
    expected_samples = math.ceil(plan["resolved_output"]["frame_count"] / plan["resolved_output"]["frame_rate"] * 44100)
    assert waveform.shape == (1, 2, expected_samples)
    assert waveform.abs().max() > 0.1
    assert waveform.abs().max() < 0.35
    assert waveform[:, :, :100].abs().max() < 0.05
    assert waveform[:, :, -100:].abs().max() < 0.05
    assert runtime_debug["summary"]["audio_clip_count"] == 1


def test_optional_latent_is_cloned_without_mutating_input():
    plan = _text_plan()
    samples = torch.zeros((1, 128, 4, 12, 12), dtype=torch.float32)
    optional_latent = {"samples": samples, "downscale_ratio_spacial": 32}

    _, _, _, video_latent, *_ = build_ltx_runtime_outputs(**_runtime_args(plan, optional_latent=optional_latent))

    assert video_latent is not optional_latent
    assert video_latent["samples"] is not samples
    assert torch.equal(samples, torch.zeros_like(samples))
    assert video_latent["samples"].shape == samples.shape


def test_runtime_does_not_mutate_input_plan():
    plan = _text_plan()
    original = copy.deepcopy(plan)

    build_ltx_runtime_outputs(**_runtime_args(plan))

    assert plan == original
