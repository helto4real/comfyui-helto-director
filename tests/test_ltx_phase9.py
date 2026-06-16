from __future__ import annotations

import copy
import asyncio
import importlib.util
import json
import math
import struct
import sys
import wave
from pathlib import Path

import av
import numpy as np
import pytest
import torch
from PIL import Image

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    QUALITY_PRESET_QUICK_DRAFT,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
    VIDEO_GUIDANCE_RANGE_FULL_SOURCE,
    VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
    VIDEO_TIMING_FIT_TO_SECTION,
    VIDEO_TIMING_FREEZE_LAST_FRAME,
    VIDEO_TIMING_LOOP,
    VIDEO_TIMING_USE_SOURCE_TIMING,
)
from shared.ltx import build_ltx_runtime_outputs, build_ltx_timeline_plan, create_ltx_timeline_config
from shared.ltx.identity import crop_latent_to_frame_count
from shared.timeline import create_default_video_timeline


def _registered_node(node_id):
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
            if node_class.define_schema().node_id == node_id:
                return node_class
    finally:
        sys.path = previous_path
        if previous is None:
            sys.modules.pop(sys_module_name, None)
        else:
            sys.modules[sys_module_name] = previous
    raise AssertionError(f"{node_id} was not registered.")


def _registered_runtime_node():
    return _registered_node("HeltoLTX23TimelineRuntime")


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
        return [[
            torch.ones((1, 2, 3), dtype=torch.float32),
            {
                "text": tokens["text"],
                "pooled_output": torch.ones((1, 3), dtype=torch.float32),
                "conditioning_lyrics": torch.ones((1, 4), dtype=torch.float32),
            },
        ]]


class FakeAttention:
    pass


class FakeTransformerBlock:
    def __init__(self, support_native_audio=True):
        self.attn2 = FakeAttention()
        if support_native_audio:
            self.audio_attn2 = FakeAttention()


class FakeDiffusionModel:
    def __init__(self, support_native_audio=True):
        self.patchifier = object()
        self.vae_scale_factors = (8, 32, 32)
        self.transformer_blocks = [
            FakeTransformerBlock(support_native_audio),
            FakeTransformerBlock(support_native_audio),
        ]


class FakeModelWrapper:
    def __init__(self, support_native_audio=True):
        self.diffusion_model = FakeDiffusionModel(support_native_audio)


class FakeModel:
    def __init__(self, support_native_audio=True):
        self.support_native_audio = support_native_audio
        self.model = FakeModelWrapper(support_native_audio)
        self.object_patches = {}

    def clone(self):
        return FakeModel(self.support_native_audio)

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


class FakeAudioVAEInner:
    latent_frequency_bins = 16

    def num_of_latents_from_frames(self, frames_number, frame_rate):
        return max(1, int(round(float(frames_number) / float(frame_rate) * 12)))


class FakeAudioVAE:
    latent_channels = 4
    first_stage_model = FakeAudioVAEInner()


def _runtime_args(plan, **overrides):
    args = {
        "model": FakeModel(),
        "clip": FakeClip(),
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


def _character_reference_plan(path: Path, reference_mode="Prompt Relay", duplicate=False):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["project"]["metadata"]["character_references"].append(
        {
            "id": "ref_hero",
            "label": "image1",
            "kind": "character",
            "enabled": True,
            "description": "red jacket hero",
            "strength": 0.9,
            "image": {"path": str(path), "name": path.name},
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 0.5 if duplicate else 1.0,
            "prompt": "follow @image1:character[0.6]",
        }
    )
    if duplicate:
        timeline["director_track"]["sections"].append(
            {
                "item_id": "section_002",
                "type": SECTION_TYPE_TEXT,
                "start_time": 0.5,
                "end_time": 1.0,
                "prompt": "track @image1:character[0.6]",
            }
        )
    plan, validation, _ = build_ltx_timeline_plan(
        timeline,
        create_ltx_timeline_config(reference_mode=reference_mode, debug_mode=True),
    )
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


def _write_test_video(path: Path, frame_count=12, fps=12, width=64, height=36):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for index in range(frame_count):
            value = int(round(index / max(1, frame_count - 1) * 255))
            array = np.zeros((height, width, 3), dtype=np.uint8)
            array[:, :, 0] = value
            array[:, :, 1] = 32
            array[:, :, 2] = 255 - value
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


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


def _video_plan(
    path: Path,
    *,
    timing_mode=VIDEO_TIMING_FIT_TO_SECTION,
    source_in=0.0,
    source_out=None,
    duration=1.0,
    guidance_range=VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
    guidance_frame_count=17,
    prompt="source video guidance",
):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = duration
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["assets"].append(
        {
            "asset_id": "video_001",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": str(path),
            "name": path.name,
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_VIDEO,
            "start_time": 0.0,
            "end_time": duration,
            "video": {"asset_id": "video_001"},
            "prompt": prompt,
            "guide_strength": 0.6,
            "source_in": source_in,
            "source_out": source_out,
            "timing_mode": timing_mode,
            "video_guidance_range": guidance_range,
            "video_guidance_frame_count": guidance_frame_count,
        }
    )
    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())
    assert validation["is_valid"] is True
    return plan


def _prompt_timing_plan_with_media_gap(media_path: Path, media_type: str):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 3.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["assets"].append(
        {
            "asset_id": "media_001",
            "type": media_type,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": str(media_path),
            "name": media_path.name,
        }
    )
    media_field = "image" if media_type == ASSET_TYPE_IMAGE else "video"
    middle_section = {
        "item_id": "section_002",
        "type": SECTION_TYPE_IMAGE if media_type == ASSET_TYPE_IMAGE else SECTION_TYPE_VIDEO,
        "start_time": 1.0,
        "end_time": 2.0,
        media_field: {"asset_id": "media_001"},
        "prompt": "",
        "guide_strength": 0.5,
    }
    if media_type == ASSET_TYPE_VIDEO:
        middle_section.update({
            "source_in": 0.0,
            "source_out": None,
            "timing_mode": VIDEO_TIMING_FIT_TO_SECTION,
            "video_guidance_range": VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
            "video_guidance_frame_count": 17,
        })
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "section_001",
                "type": SECTION_TYPE_TEXT,
                "start_time": 0.0,
                "end_time": 1.0,
                "prompt": "opening prompt",
            },
            middle_section,
            {
                "item_id": "section_003",
                "type": SECTION_TYPE_TEXT,
                "start_time": 2.0,
                "end_time": 3.0,
                "prompt": "ending prompt",
            },
        ]
    )
    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())
    assert validation["is_valid"] is True
    return plan


def _gap_then_text_plan():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 1.0,
            "end_time": 2.0,
            "prompt": "late prompt",
        }
    )
    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())
    assert validation["is_valid"] is True
    return plan


def _image_audio_timeline(image_path: Path, audio_path: Path):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["assets"].extend(
        [
            {
                "asset_id": "image_001",
                "type": ASSET_TYPE_IMAGE,
                "source_kind": ASSET_SOURCE_FILE_PATH,
                "path": str(image_path),
                "name": image_path.name,
            },
            {
                "asset_id": "audio_001",
                "type": ASSET_TYPE_AUDIO,
                "source_kind": ASSET_SOURCE_FILE_PATH,
                "path": str(audio_path),
                "name": audio_path.name,
            },
        ]
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_IMAGE,
            "start_time": 0.0,
            "end_time": 1.0,
            "image": {"asset_id": "image_001"},
            "prompt": "practical smoke workflow image",
            "guide_strength": 0.75,
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
                    "volume": 80.0,
                    "fade_in": 0.05,
                    "fade_out": 0.05,
                    "enabled": True,
                    "lane": 0,
                }
            ],
        }
    )
    return timeline


def _frame_red_means(frames: torch.Tensor) -> torch.Tensor:
    return frames[:, :, :, 0].mean(dim=(1, 2))


def test_ltx_runtime_node_schema_io_order():
    schema = _registered_runtime_node().define_schema()

    assert schema.node_id == "HeltoLTX23TimelineRuntime"
    assert [input_item.io_type for input_item in schema.inputs] == [
        "MODEL",
        "CLIP",
        "VAE",
        "LTX_TIMELINE_PLAN",
        "CONDITIONING",
        "LATENT",
        "VAE",
        "LTX_IDENTITY_ANCHOR",
        "SIGMAS",
        "IC_LORA_PARAMETERS",
    ]
    assert schema.inputs[4].id == "negative"
    assert schema.inputs[4].optional is True
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


def test_director_to_ltx_runtime_smoke_graph_with_image_and_audio(tmp_path):
    image_path = tmp_path / "guide.png"
    audio_path = tmp_path / "tone.wav"
    Image.new("RGB", (64, 64), (96, 160, 255)).save(image_path)
    _write_test_wav(audio_path, amplitude=0.4)

    Director = _registered_node("HeltoVideoTimelineDirector")
    Config = _registered_node("HeltoLTX23TimelineConfig")
    Planner = _registered_node("HeltoLTX23TimelinePlanner")
    Runtime = _registered_node("HeltoLTX23TimelineRuntime")

    authored_timeline = _image_audio_timeline(image_path, audio_path)
    video_timeline, director_validation, _director_frame_rate = Director.execute(
        duration_seconds=1.0,
        frame_rate=24.0,
        quality_preset=QUALITY_PRESET_QUICK_DRAFT,
        video_timeline_json=json.dumps(authored_timeline),
    ).result
    ltx_config = Config.execute(debug_mode=True).result[0]
    ltx_plan, planner_validation, planner_debug = Planner.execute(video_timeline, ltx_config).result
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
    ) = Runtime.execute(FakeModel(), FakeClip(), FakeVAE(), ltx_plan, audio_vae=FakeAudioVAE()).result

    assert director_validation["is_valid"] is True
    assert planner_validation["is_valid"] is True
    assert planner_debug["type"] == "DEBUG_INFO"
    assert len(runtime_model.object_patches) == 4
    assert "practical smoke workflow image" in positive[0][1]["text"]
    assert torch.equal(negative[0][0], torch.zeros_like(positive[0][0]))
    assert video_latent["samples"].shape[2] > ((ltx_plan["resolved_output"]["frame_count"] - 1) // 8) + 1
    assert audio_latent["samples"].shape[1] == 4
    assert combined_audio["waveform"].abs().max() > 0.0
    assert guide_data["strengths"] == [0.75]
    assert source_video_images.shape[0] == 1
    assert source_video_audio["waveform"].shape[1] == 2
    assert source_video_frame_rate == ltx_plan["resolved_output"]["frame_rate"]
    assert source_video_frame_count == 0
    assert runtime_debug["enabled"] is True
    assert runtime_debug["summary"]["applied_guides"] == 1
    assert runtime_debug["summary"]["audio_clip_count"] == 1


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
    assert torch.equal(negative[0][0], torch.zeros_like(positive[0][0]))
    assert torch.equal(negative[0][1]["pooled_output"], torch.zeros_like(positive[0][1]["pooled_output"]))
    assert torch.equal(negative[0][1]["conditioning_lyrics"], torch.zeros_like(positive[0][1]["conditioning_lyrics"]))
    assert negative[0][1]["text"] == positive[0][1]["text"]
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


def test_missing_video_media_path_fails_clearly(tmp_path):
    plan = _video_plan(tmp_path / "missing.mp4")

    with pytest.raises(FileNotFoundError, match="Media file not found"):
        build_ltx_runtime_outputs(**_runtime_args(plan))


def test_video_media_without_video_stream_fails_clearly(tmp_path):
    audio_path = tmp_path / "audio_only.wav"
    _write_test_wav(audio_path)
    plan = _video_plan(audio_path)

    with pytest.raises(ValueError, match="no video stream"):
        build_ltx_runtime_outputs(**_runtime_args(plan))


def test_video_section_does_not_require_prompt_for_guidance(tmp_path):
    video_path = tmp_path / "promptless_source.mp4"
    _write_test_video(video_path, frame_count=12, fps=12)
    plan = _video_plan(video_path, prompt="")

    runtime_model, positive, negative, _video_latent, _audio_latent, _combined_audio, guide_data, *_rest, runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    assert runtime_model.object_patches == {}
    assert positive[0][1]["text"] == ""
    assert negative[0][1]["guide_attention_entries"][0]["strength"] == 0.6
    assert guide_data["reference_images"][0]["section_type"] == SECTION_TYPE_VIDEO
    assert runtime_debug["prompt_relay"]["local_prompts"] == []
    assert runtime_debug["summary"]["applied_guides"] == 1


def test_promptless_video_before_text_preserves_late_prompt_timing(tmp_path):
    video_path = tmp_path / "source_then_text.mp4"
    _write_test_video(video_path, frame_count=12, fps=12)
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["assets"].append(
        {
            "asset_id": "video_001",
            "type": ASSET_TYPE_VIDEO,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": str(video_path),
            "name": video_path.name,
        }
    )
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "section_001",
                "type": SECTION_TYPE_VIDEO,
                "start_time": 0.0,
                "end_time": 1.0,
                "video": {"asset_id": "video_001"},
                "prompt": "",
                "guide_strength": 0.6,
                "source_in": 0.0,
                "source_out": None,
                "timing_mode": VIDEO_TIMING_USE_SOURCE_TIMING,
                "video_guidance_range": VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
                "video_guidance_frame_count": 17,
            },
            {
                "item_id": "section_002",
                "type": SECTION_TYPE_TEXT,
                "start_time": 1.0,
                "end_time": 2.0,
                "prompt": "future city continuation",
            },
        ]
    )
    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())
    assert validation["is_valid"] is True

    runtime_model, positive, *_rest, runtime_debug = build_ltx_runtime_outputs(**_runtime_args(plan))

    assert len(runtime_model.object_patches) == 4
    assert positive[0][1]["text"] == " future city continuation future city continuation"
    assert runtime_debug["prompt_relay"]["local_prompts"] == ["future city continuation", "future city continuation"]
    assert [entry["item_id"] for entry in runtime_debug["prompt_relay"]["prompt_sections"]] == ["section_001", "section_002"]
    assert runtime_debug["prompt_relay"]["latent_ranges"][0]["start"] == 0
    assert runtime_debug["prompt_relay"]["latent_ranges"][1]["start"] >= 3


def test_promptless_image_between_text_sections_borrows_next_prompt_and_preserves_timing(tmp_path):
    image_path = tmp_path / "middle.png"
    Image.new("RGB", (64, 64), (20, 80, 180)).save(image_path)
    plan = _prompt_timing_plan_with_media_gap(image_path, ASSET_TYPE_IMAGE)

    runtime_model, _positive, *_rest, runtime_debug = build_ltx_runtime_outputs(**_runtime_args(plan))

    assert len(runtime_model.object_patches) == 4
    assert runtime_debug["prompt_relay"]["local_prompts"] == ["opening prompt", "ending prompt", "ending prompt"]
    assert [entry["item_id"] for entry in runtime_debug["prompt_relay"]["prompt_sections"]] == [
        "section_001",
        "section_002",
        "section_003",
    ]
    first_range, image_range, ending_range = runtime_debug["prompt_relay"]["latent_ranges"]
    assert image_range["start"] >= first_range["end"]
    assert ending_range["start"] >= image_range["end"]


def test_promptless_image_with_global_prompt_stays_in_prompt_relay(tmp_path):
    image_path = tmp_path / "global_image.png"
    Image.new("RGB", (64, 64), (20, 80, 180)).save(image_path)
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
            "path": str(image_path),
            "name": image_path.name,
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_IMAGE,
            "start_time": 0.0,
            "end_time": 1.0,
            "image": {"asset_id": "image_001"},
            "prompt": "",
            "guide_strength": 0.5,
        }
    )
    plan, validation, _ = build_ltx_timeline_plan(timeline, create_ltx_timeline_config())
    assert validation["is_valid"] is True

    runtime_model, positive, *_rest, runtime_debug = build_ltx_runtime_outputs(**_runtime_args(plan))

    assert len(runtime_model.object_patches) == 4
    assert positive[0][1]["text"] == "cinematic lighting cinematic lighting"
    assert runtime_debug["prompt_relay"]["local_prompts"] == ["cinematic lighting"]
    assert runtime_debug["prompt_relay"]["prompt_sections"][0]["item_id"] == "section_001"


def test_timeline_gap_before_text_preserves_late_prompt_timing():
    plan = _gap_then_text_plan()

    runtime_model, _positive, *_rest, runtime_debug = build_ltx_runtime_outputs(**_runtime_args(plan))

    assert len(runtime_model.object_patches) == 4
    assert runtime_debug["prompt_relay"]["local_prompts"] == ["late prompt"]
    assert runtime_debug["prompt_relay"]["prompt_sections"][0]["item_id"] == "section_001"
    assert runtime_debug["prompt_relay"]["latent_ranges"][0]["start"] >= 3


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


def test_character_reference_tag_creates_hidden_tail_guide_and_replaces_prompt(tmp_path):
    reference_path = tmp_path / "hero.png"
    Image.new("RGB", (64, 64), (220, 30, 20)).save(reference_path)
    plan = _character_reference_plan(reference_path)

    _, positive, negative, video_latent, _, _, guide_data, *_rest, runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    assert runtime_debug["prompt_relay"]["local_prompts"] == ["follow red jacket hero"]
    assert "@" not in runtime_debug["prompt_relay"]["full_prompt"]
    assert guide_data["hidden_reference_count"] == 1
    assert guide_data["strengths"] == [0.6]
    assert guide_data["insert_frames"] == [guide_data["clean_latent_frames"] * 8]
    assert guide_data["reference_images"][0]["label"] == "image1"
    assert guide_data["reference_images"][0]["hidden_tail"] is True
    assert guide_data["reference_images"][0]["image"].shape == guide_data["images"][0].shape
    assert video_latent["samples"].shape[2] > guide_data["clean_latent_frames"]
    assert positive[0][1]["guide_attention_entries"][0]["strength"] == 0.6
    assert negative[0][1]["guide_attention_entries"][0]["strength"] == 0.6
    assert runtime_debug["character_references"]["guide_count"] == 1

    cropped = crop_latent_to_frame_count(
        video_latent,
        guide_data["clean_latent_frames"],
        guide_data["hidden_reference_count"],
    )
    assert cropped["samples"].shape[2] == guide_data["clean_latent_frames"]
    assert cropped["noise_mask"].shape[2] == guide_data["clean_latent_frames"]


def test_character_references_work_in_guide_data_mode_without_prompt_relay(tmp_path):
    reference_path = tmp_path / "hero-guide-data.png"
    Image.new("RGB", (64, 64), (80, 200, 120)).save(reference_path)
    plan = _character_reference_plan(reference_path, reference_mode="Guide Data")

    runtime_model, positive, _negative, _video_latent, _, _, guide_data, *_rest, runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    assert runtime_model.object_patches == {}
    assert positive[0][1]["text"] == "follow red jacket hero"
    assert guide_data["hidden_reference_count"] == 1
    assert guide_data["reference_images"][0]["hidden_tail"] is True
    assert runtime_debug["prompt_relay"]["local_prompts"] == ["follow red jacket hero"]
    assert runtime_debug["character_references"]["active"] is True


def test_duplicate_character_reference_same_strength_reuses_hidden_guide(tmp_path):
    reference_path = tmp_path / "hero-dedupe.png"
    Image.new("RGB", (64, 64), (30, 80, 220)).save(reference_path)
    plan = _character_reference_plan(reference_path, duplicate=True)
    specs = plan["model_specific"]["ltx"]["character_references"]["guide_specs"]

    _, _positive, _negative, _video_latent, _, _, guide_data, *_rest, runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    assert len(specs) == 1
    assert specs[0]["section_id"] == "section_001,section_002"
    assert guide_data["hidden_reference_count"] == 1
    assert len([entry for entry in guide_data["reference_images"] if entry.get("hidden_tail")]) == 1
    assert runtime_debug["character_references"]["guide_count"] == 1


def test_video_section_creates_source_guide_data_and_outputs(tmp_path):
    video_path = tmp_path / "source.mp4"
    _write_test_video(video_path, frame_count=12, fps=12)
    plan = _video_plan(video_path, source_in=0.25, source_out=0.75)

    _, positive, negative, video_latent, _, _, guide_data, source_images, source_audio, source_fps, source_count, runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    metadata = guide_data["reference_images"][0]
    assert guide_data["strengths"] == [0.6]
    assert guide_data["images"][0].shape[0] == 17
    assert metadata["section_type"] == SECTION_TYPE_VIDEO
    assert metadata["source_fps"] == pytest.approx(12.0)
    assert metadata["decoded_frame_count"] == 12
    assert metadata["trimmed_frame_count"] == 6
    assert metadata["guidance_range"] == VIDEO_GUIDANCE_RANGE_LAST_FRAMES
    assert metadata["guidance_frame_count"] == 17
    assert metadata["guidance_source_range"]["start_frame"] == 3
    assert metadata["guidance_source_range"]["end_frame_exclusive"] == 9
    assert metadata["requested_frame_count"] == 24
    assert metadata["selected_frame_count"] == 17
    assert metadata["source_range"]["start_frame"] == 3
    assert metadata["source_range"]["end_frame_exclusive"] == 9
    assert positive[0][1]["guide_attention_entries"][0]["strength"] == 0.6
    assert negative[0][1]["guide_attention_entries"][0]["strength"] == 0.6
    assert video_latent["samples"].shape[2] > guide_data["clean_latent_frames"]
    assert source_images.shape[0] == 6
    assert source_audio["waveform"].shape[-1] == math.ceil(6 / 12 * 44100)
    assert source_fps == pytest.approx(12.0)
    assert source_count == 6
    assert runtime_debug["summary"]["applied_guides"] == 1


@pytest.mark.parametrize(
    ("timing_mode", "expected_count"),
    [
        (VIDEO_TIMING_FIT_TO_SECTION, 17),
        (VIDEO_TIMING_USE_SOURCE_TIMING, 9),
        (VIDEO_TIMING_LOOP, 17),
        (VIDEO_TIMING_FREEZE_LAST_FRAME, 17),
    ],
)
def test_video_timing_modes_select_deterministic_guide_frames(tmp_path, timing_mode, expected_count):
    video_path = tmp_path / f"{timing_mode.replace(' ', '_')}.mp4"
    _write_test_video(video_path, frame_count=12, fps=12)
    plan = _video_plan(video_path, timing_mode=timing_mode)

    *_, guide_data, _source_images, _source_audio, _source_fps, _source_count, _runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    guide_frames = guide_data["images"][0]
    metadata = guide_data["reference_images"][0]
    red_means = _frame_red_means(guide_frames)
    assert guide_frames.shape[0] == expected_count
    assert metadata["timing_mode"] == timing_mode
    assert metadata["guidance_range"] == VIDEO_GUIDANCE_RANGE_LAST_FRAMES
    assert metadata["selected_frame_count"] == expected_count
    if timing_mode == VIDEO_TIMING_USE_SOURCE_TIMING:
        assert metadata["requested_frame_count"] == 12
        assert torch.all(red_means[1:] >= red_means[:-1] - 0.02)
    elif timing_mode == VIDEO_TIMING_LOOP:
        assert metadata["requested_frame_count"] == 24
        assert torch.isclose(red_means[0], red_means[12], atol=0.08)
    elif timing_mode == VIDEO_TIMING_FREEZE_LAST_FRAME:
        assert metadata["requested_frame_count"] == 24
        assert torch.isclose(red_means[-1], red_means[-2], atol=0.03)
        assert red_means[-1] > red_means[0]
    else:
        assert metadata["requested_frame_count"] == 24
        assert red_means[-1] > red_means[0]


def test_use_source_timing_caps_video_guide_to_section_frame_count(tmp_path):
    video_path = tmp_path / "long_source.mp4"
    _write_test_video(video_path, frame_count=30, fps=30)
    plan = _video_plan(video_path, timing_mode=VIDEO_TIMING_USE_SOURCE_TIMING, duration=0.5)

    *_, guide_data, source_images, _source_audio, source_fps, source_count, _runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    metadata = guide_data["reference_images"][0]
    assert metadata["decoded_frame_count"] == 30
    assert metadata["trimmed_frame_count"] == 30
    assert metadata["requested_frame_count"] == 12
    assert metadata["selected_frame_count"] == 9
    assert guide_data["images"][0].shape[0] == 9
    assert source_images.shape[0] == 30
    assert source_fps == pytest.approx(30.0)
    assert source_count == 30


def test_full_source_range_preserves_video_guide_timing_behavior(tmp_path):
    video_path = tmp_path / "full_source.mp4"
    _write_test_video(video_path, frame_count=30, fps=30)
    plan = _video_plan(
        video_path,
        timing_mode=VIDEO_TIMING_USE_SOURCE_TIMING,
        duration=0.5,
        guidance_range=VIDEO_GUIDANCE_RANGE_FULL_SOURCE,
    )

    *_, guide_data, source_images, _source_audio, _source_fps, source_count, _runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    metadata = guide_data["reference_images"][0]
    assert metadata["guidance_range"] == VIDEO_GUIDANCE_RANGE_FULL_SOURCE
    assert metadata["guidance_source_range"]["start_frame"] == 0
    assert metadata["guidance_source_range"]["end_frame_exclusive"] == 30
    assert metadata["requested_frame_count"] == 12
    assert metadata["selected_frame_count"] == 9
    assert guide_data["images"][0].shape[0] == 9
    assert source_images.shape[0] == 30
    assert source_count == 30


def test_last_frames_guidance_selects_tail_after_source_trim(tmp_path):
    video_path = tmp_path / "tail_source.mp4"
    _write_test_video(video_path, frame_count=30, fps=30)
    plan = _video_plan(video_path, source_in=0.2, source_out=0.8, guidance_frame_count=9)

    *_, guide_data, source_images, _source_audio, _source_fps, source_count, _runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    metadata = guide_data["reference_images"][0]
    assert metadata["source_range"]["start_frame"] == 6
    assert metadata["source_range"]["end_frame_exclusive"] == 24
    assert metadata["guidance_range"] == VIDEO_GUIDANCE_RANGE_LAST_FRAMES
    assert metadata["guidance_frame_count"] == 9
    assert metadata["guidance_source_range"]["start_frame"] == 15
    assert metadata["guidance_source_range"]["end_frame_exclusive"] == 24
    assert metadata["requested_frame_count"] == 24
    assert metadata["selected_frame_count"] == 17
    assert guide_data["images"][0].shape[0] == 17
    assert source_images.shape[0] == 18
    assert source_count == 18


def test_non_ltx_frame_count_tail_guidance_clamps_to_compatible_window(tmp_path):
    video_path = tmp_path / "tail_count.mp4"
    _write_test_video(video_path, frame_count=20, fps=20)
    plan = _video_plan(
        video_path,
        timing_mode=VIDEO_TIMING_USE_SOURCE_TIMING,
        guidance_frame_count=10,
    )

    *_, guide_data, _source_images, _source_audio, _source_fps, _source_count, _runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan)
    )

    metadata = guide_data["reference_images"][0]
    assert metadata["guidance_frame_count"] == 10
    assert metadata["guidance_source_range"]["start_frame"] == 10
    assert metadata["guidance_source_range"]["end_frame_exclusive"] == 20
    assert metadata["requested_frame_count"] == 10
    assert metadata["selected_frame_count"] == 9
    assert guide_data["images"][0].shape[0] == 9


def test_connected_negative_conditioning_is_used_and_receives_guides(tmp_path):
    image_path = tmp_path / "guide.png"
    Image.new("RGB", (64, 64), (255, 32, 128)).save(image_path)
    plan = _image_plan(image_path)
    input_negative = [[
        torch.full((1, 2, 3), 2.0, dtype=torch.float32),
        {"text": "connected negative", "pooled_output": torch.full((1, 3), 3.0, dtype=torch.float32)},
    ]]

    _, _positive, negative, *_rest = build_ltx_runtime_outputs(**_runtime_args(plan, negative=input_negative))

    assert negative[0][1]["text"] == "connected negative"
    assert torch.equal(negative[0][0], input_negative[0][0])
    assert negative[0][1]["guide_attention_entries"][0]["strength"] == 0.5
    assert "guide_attention_entries" not in input_negative[0][1]


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


def test_native_audio_enabled_creates_empty_audio_latent_for_supported_model():
    plan = _text_plan()
    plan["project"]["audio"]["use_native_audio"] = True

    *_, audio_latent, _combined_audio, _guide_data, _source_images, _source_audio, _source_fps, _source_count, runtime_debug = build_ltx_runtime_outputs(
        **_runtime_args(plan, audio_vae=FakeAudioVAE())
    )

    assert audio_latent["type"] == "audio"
    assert audio_latent["samples"].shape[1] == 4
    assert audio_latent["samples"].shape[3] == 16
    assert any("Native audio is enabled" in entry for entry in runtime_debug["diagnostics"])
    assert not any("No audio_vae connected" in entry for entry in runtime_debug["diagnostics"])


def test_native_audio_enabled_fails_for_non_native_audio_model():
    plan = _text_plan()
    plan["project"]["audio"]["use_native_audio"] = True

    with pytest.raises(ValueError, match="does not support native audio"):
        build_ltx_runtime_outputs(**_runtime_args(plan, model=FakeModel(support_native_audio=False)))


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
