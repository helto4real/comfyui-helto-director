from __future__ import annotations

import asyncio
import copy
import importlib.util
import math
import sys
from pathlib import Path

import av
import numpy as np
import pytest
import torch
from PIL import Image

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_SOURCE_GENERATED,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    BOUNDARY_MODE_CONTINUOUS_SHOT,
    BOUNDARY_MODE_TRANSITION,
    MODEL_LORA_TARGET_HIGH_NOISE,
    MODEL_LORA_TARGET_LOW_NOISE,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    TAKE_STATUS_ACCEPTED,
)
from shared.timeline import (
    GENERATION_MODE_FORCE_FULL_TIMELINE,
    GENERATION_MODE_FORCE_SELECTED,
    apply_take_registration,
    create_default_video_timeline,
    validate_video_timeline,
)
from shared.timeline.take_capture import TAKE_CAPTURE_TYPE
from shared.wan import build_wan_runtime_outputs, build_wan_timeline_plan, create_wan_timeline_config
from shared.wan.runtime import runtime as wan_runtime
from shared.wan.runtime.capabilities import select_keyframes_for_capabilities
from shared.lora import config as lora_config_module


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


def test_wan_runtime_skipped_plan_returns_no_take_registration_without_backend_inputs():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(debug_mode="Summary"),
    )
    plan["section_plan"] = []
    plan["prompt_plan"] = []
    plan["media_plan"] = []
    plan["audio_plan"] = []
    plan["model_specific"]["wan"]["generation_policy"] = {
        "status": "skipped",
        "skip_reason": "all_shots_ready",
        "mode": "Missing Only",
    }

    *_outputs, runtime_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)

    assert runtime_debug["summary"]["generation_required"] is False
    assert runtime_debug["summary"]["generation_status"] == "skipped"
    assert runtime_debug["summary"]["take_registration_ready"] is False
    assert "take_registration" not in runtime_debug


def test_wan_shot_runtime_emits_take_registration_metadata():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(debug_mode="Summary"),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )
    plan["model_specific"]["wan"]["lora_resolution"]["single_generation_loras"] = {
        MODEL_LORA_TARGET_HIGH_NOISE: _lora_stack("hi.safetensors"),
        MODEL_LORA_TARGET_LOW_NOISE: _lora_stack("low.safetensors"),
    }

    *_outputs, runtime_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)

    metadata = runtime_debug["take_registration"]
    assert metadata["type"] == TAKE_CAPTURE_TYPE
    assert metadata["schema_version"] == 1
    assert metadata["shot_id"] == "shot_section_text"
    assert metadata["shot_ids"] == ["shot_section_text"]
    assert metadata["registration_ready"] is True
    assert metadata["capture_blockers"] == []
    assert metadata["expected_asset_type"] == ASSET_TYPE_VIDEO
    assert metadata["take"]["take_id"] == "take_wan_shot_section_text_generated"
    assert metadata["take"]["model_family"] == "WAN"
    assert metadata["take"]["model_version"] == "2.2"
    assert metadata["take"]["resolved_loras"]["targets"][MODEL_LORA_TARGET_HIGH_NOISE][0]["name"] == "hi.safetensors"
    assert metadata["take"]["resolved_loras"]["targets"][MODEL_LORA_TARGET_LOW_NOISE][0]["name"] == "low.safetensors"
    assert metadata["shot_context"]["original_start_time"] == 0.0
    assert metadata["shot_context"]["original_end_time"] == 1.0
    assert metadata["model_specific"]["wan"]["backend"] == "Plan Only"
    assert metadata["asset_suggestion"]["source_kind"] == "Generated"
    assert metadata["asset_suggestion"]["name"] == metadata["suggested_asset_name"]
    assert metadata["asset"].get("path") is None
    assert "simple prompt" not in str(metadata)
    assert runtime_debug["summary"]["take_registration_ready"] is True
    assert runtime_debug["summary"]["take_registration_shot_ids"] == ["shot_section_text"]

    registered = apply_take_registration(
        _text_timeline(),
        metadata,
        generated_asset_path="/tmp/output/wan_shot.mp4",
    )
    assert registered["take_id"] == "take_wan_shot_section_text_generated"
    assert validate_video_timeline(registered["timeline"])["is_valid"] is True


def test_wan_runtime_reports_shot_continuity_debug():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(debug_mode="Summary"),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )
    plan["model_specific"]["wan"]["continuity_context"] = {
        "policy": "continuous",
        "source_status": "available",
        "model_status": "applied",
        "boundary_id": "boundary_continuous",
        "source_shot_id": "shot_previous",
        "target_shot_id": "shot_section_text",
        "tail_frames": 6,
        "effective_tail_frames": 9,
        "blend_frames": 0,
        "clip_reference": {
            "source_kind": "accepted_take",
            "shot_id": "shot_previous",
            "take_id": "take_previous",
            "asset_id": "asset_previous_take",
        },
    }
    plan["model_specific"]["wan"]["boundary_conditioning"] = {
        "policy": "continuous",
        "mode": BOUNDARY_MODE_CONTINUOUS_SHOT,
        "model_status": "applied",
        "boundary_id": "boundary_continuous",
        "source_shot_id": "shot_previous",
        "target_shot_id": "shot_section_text",
        "asset_id": "asset_previous_take",
        "requested_tail_frames": 6,
        "effective_tail_frames": 9,
        "clip_reference": {
            "source_kind": "accepted_take",
            "shot_id": "shot_previous",
            "take_id": "take_previous",
            "asset_id": "asset_previous_take",
        },
    }

    *_outputs, runtime_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)

    assert runtime_debug["summary"]["shot_continuity_policy"] == "continuous"
    assert runtime_debug["summary"]["shot_continuity_status"] == "applied"
    assert runtime_debug["summary"]["boundary_conditioning_status"] == "applied"
    assert runtime_debug["summary"]["boundary_conditioning_runtime_status"] == "not_executed"
    assert runtime_debug["continuity"]["clip_reference"]["asset_id"] == "asset_previous_take"
    assert runtime_debug["boundary_conditioning"]["asset_id"] == "asset_previous_take"


def test_wan_runtime_applies_boundary_tail_as_core_transient_start_and_take_metadata(tmp_path):
    video_path = tmp_path / "previous.mp4"
    _write_test_video(video_path, frame_count=12, fps=8)
    plan, validation, _debug = build_wan_timeline_plan(
        _two_shot_boundary_timeline(video_path, tail_frames=6),
        create_wan_timeline_config(
            runtime_backend_profile="ComfyUI Core",
            debug_mode="Full",
            resolution_profile="Quick Draft",
        ),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )

    assert validation["is_valid"] is True
    *_outputs, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert runtime_debug["summary"]["shot_continuity_status"] == "applied"
    assert runtime_debug["summary"]["boundary_conditioning_status"] == "applied"
    assert runtime_debug["summary"]["boundary_conditioning_runtime_status"] == "applied"
    boundary = runtime_debug["boundary_conditioning"]
    assert boundary["boundary_id"] == "boundary_previous_next"
    assert boundary["requested_tail_frames"] == 6
    assert boundary["effective_tail_frames"] == 9
    assert boundary["selected_frame_count"] == 9
    assert boundary["tensor_shape"] == [
        9,
        plan["resolved_output"]["height"],
        plan["resolved_output"]["width"],
        3,
    ]
    assert "path" not in boundary

    applied = runtime_debug["visual_conditioning"]["applied_keyframes"]
    assert applied == [
        {
            "role": "Start",
            "section_id": "boundary_tail_boundary_previous_next",
            "transient": True,
            "kind": "boundary_conditioning",
        }
    ]
    decision = next(
        item for item in runtime_debug["media_decisions"]
        if item.get("section_id") == "boundary_tail_boundary_previous_next"
    )
    assert decision["kind"] == "boundary_conditioning"
    assert decision["transient"] is True
    assert decision["tensor_shape"] == boundary["tensor_shape"]

    metadata = runtime_debug["take_registration"]
    boundary_metadata = metadata["take"]["metadata"]["model_specific"]["wan"]["boundary_conditioning"]
    assert boundary_metadata["model_status"] == "applied"
    assert boundary_metadata["runtime_status"] == "applied"
    assert boundary_metadata["boundary_id"] == "boundary_previous_next"
    assert boundary_metadata["effective_tail_frames"] == 9
    assert boundary_metadata["selected_frame_count"] == 9
    assert "path" not in boundary_metadata


def test_wan_runtime_applies_boundary_tail_as_bernini_source_video(tmp_path):
    video_path = tmp_path / "previous.mp4"
    _write_test_video(video_path, frame_count=12, fps=8)
    plan, validation, _debug = build_wan_timeline_plan(
        _two_shot_boundary_timeline(video_path, tail_frames=6),
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            debug_mode="Full",
            resolution_profile="Quick Draft",
        ),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )

    assert validation["is_valid"] is True
    *_outputs, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert runtime_debug["summary"]["boundary_conditioning_runtime_status"] == "applied"
    assert runtime_debug["bernini"]["task_type"] == "v2v"
    decision = next(
        item for item in runtime_debug["bernini"]["runtime_media_decisions"]
        if item.get("section_id") == "boundary_tail_boundary_previous_next"
    )
    assert decision["kind"] == "boundary_conditioning"
    assert decision["bernini_role"] == "source_video"
    assert decision["source_video_frame_count"] == 9
    assert decision["boundary_id"] == "boundary_previous_next"
    assert any("boundary conditioning" in item.lower() for item in runtime_debug["bernini"]["runtime_diagnostics"])


def test_wan_runtime_applies_boundary_tail_for_fmlf_without_prev_latent(tmp_path):
    video_path = tmp_path / "previous.mp4"
    _write_test_video(video_path, frame_count=12, fps=8)
    plan, validation, _debug = build_wan_timeline_plan(
        _two_shot_boundary_timeline(video_path, tail_frames=6),
        create_wan_timeline_config(
            runtime_backend_profile="FMLF Advanced I2V",
            debug_mode="Full",
            resolution_profile="Quick Draft",
        ),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )

    assert validation["is_valid"] is True
    _high, _low, positive, _negative, _latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        low_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
        split_conditioning=True,
    )

    assert positive["_helto_wan_conditioning_split"] is True
    assert runtime_debug["summary"]["boundary_conditioning_runtime_status"] == "applied"
    fmlf = runtime_debug["fmlf_advanced_i2v"]
    assert fmlf["used_prev_latent"] is False
    assert fmlf["anchor_source"] == "boundary_tail_boundary_previous_next"
    decision = next(
        item for item in fmlf["media_decisions"]
        if item.get("section_id") == "boundary_tail_boundary_previous_next"
    )
    assert decision["kind"] == "boundary_conditioning"
    assert decision["transient"] is True
    assert "path" not in decision


def test_wan_runtime_does_not_copy_boundary_tail_into_later_segment(tmp_path):
    missing_video_path = tmp_path / "missing_previous.mp4"
    plan, validation, _debug = build_wan_timeline_plan(
        _two_shot_boundary_timeline(missing_video_path, tail_frames=6),
        create_wan_timeline_config(
            model_mode="T2V-A14B",
            runtime_backend_profile="ComfyUI Core",
            debug_mode="Full",
            resolution_profile="Quick Draft",
        ),
        generation_mode=GENERATION_MODE_FORCE_SELECTED,
    )
    plan["model_specific"]["wan"]["active_generation_segment"] = {
        "id": "gen_002",
        "start_frame": 9,
        "end_frame_exclusive": 17,
    }

    assert validation["is_valid"] is True
    *_outputs, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert runtime_debug["summary"]["boundary_conditioning_runtime_status"] == "skipped_segment"
    assert not any(
        item.get("section_id") == "boundary_tail_boundary_previous_next"
        for item in runtime_debug["media_decisions"]
    )


def test_auto_backend_resolves_to_plan_only_or_comfyui_core(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=2),
        create_wan_timeline_config(runtime_backend_profile="Auto", debug_mode="Summary"),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
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
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
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


def test_wan_runtime_applies_resolved_high_low_loras_to_models_and_not_clip(tmp_path, monkeypatch):
    calls = []

    def fake_apply_model_lora(*, model, lora_config):
        calls.append((model.label, [row["name"] for row in lora_config["loras"]]))
        return FakeModel(f"{model.label}+{lora_config['loras'][0]['name']}"), list(lora_config["loras"])

    monkeypatch.setattr(lora_config_module, "_available_loras", lambda: ["hi.safetensors", "low.safetensors"])
    monkeypatch.setattr(wan_runtime, "apply_lora_config_model_only", fake_apply_model_lora)
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=2),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core"),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )
    plan["model_specific"]["wan"]["lora_resolution"]["single_generation_loras"] = {
        MODEL_LORA_TARGET_HIGH_NOISE: _lora_stack("hi.safetensors", 0.9, 0.2),
        MODEL_LORA_TARGET_LOW_NOISE: _lora_stack("low.safetensors", 0.4, 0.1),
    }
    clip = FakeClip()

    high_model, low_model, _positive, _negative, _video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel("high"),
        low_noise_model=FakeModel("low"),
        clip=clip,
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert calls == [("high", ["hi.safetensors"]), ("low", ["low.safetensors"])]
    assert high_model.label == "high+hi.safetensors"
    assert low_model.label == "low+low.safetensors"
    assert not hasattr(clip, "lora_applied")
    assert runtime_debug["loras"]["source_scope"] == "single_generation_loras"
    assert runtime_debug["loras"]["targets"][MODEL_LORA_TARGET_HIGH_NOISE]["applied"][0]["name"] == "hi.safetensors"
    assert runtime_debug["loras"]["targets"][MODEL_LORA_TARGET_LOW_NOISE]["applied"][0]["name"] == "low.safetensors"
    assert runtime_debug["loras"]["take_snapshot"]["targets"][MODEL_LORA_TARGET_HIGH_NOISE][0]["name"] == "hi.safetensors"
    assert runtime_debug["summary"]["lora_applied_count"] == 2


def test_wan_runtime_reports_missing_model_for_resolved_lora_target(tmp_path, monkeypatch):
    monkeypatch.setattr(lora_config_module, "_available_loras", lambda: ["low.safetensors"])
    monkeypatch.setattr(
        wan_runtime,
        "apply_lora_config_model_only",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("low model is missing")),
    )
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=1),
        create_wan_timeline_config(runtime_backend_profile="ComfyUI Core"),
    )
    plan["model_specific"]["wan"]["lora_resolution"]["single_generation_loras"] = {
        MODEL_LORA_TARGET_HIGH_NOISE: {"version": 1, "loras": [], "ui": {"show_strengths": "single", "match": ""}},
        MODEL_LORA_TARGET_LOW_NOISE: _lora_stack("low.safetensors"),
    }

    _high, low_model, _positive, _negative, _video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel("high"),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert low_model is None
    low_report = runtime_debug["loras"]["targets"][MODEL_LORA_TARGET_LOW_NOISE]
    assert low_report["resolved_count"] == 1
    assert low_report["applied_count"] == 0
    assert low_report["model_connected"] is False
    assert any("low_noise_model is not connected" in entry for entry in runtime_debug["diagnostics"])
    assert any("low_noise_model is not connected" in entry for entry in low_report["warnings"])


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
        return module, asyncio.run(extension.get_node_list())
    finally:
        sys.path = previous_path
        if previous is None:
            sys.modules.pop(sys_module_name, None)
        else:
            sys.modules[sys_module_name] = previous


def _item_names(items):
    return [getattr(item, "name", getattr(item, "id", None)) for item in items]


def _lora_stack(name: str, strength_model: float = 0.8, strength_clip: float = 0.8) -> dict:
    return {
        "version": 1,
        "loras": [
            {
                "enabled": True,
                "name": name,
                "strength_model": strength_model,
                "strength_clip": strength_clip,
            }
        ],
        "ui": {"show_strengths": "single", "match": ""},
    }


def _validation_codes(runtime_debug: dict, bucket: str) -> list[str]:
    return [entry["code"] for entry in runtime_debug["validation"].get(bucket, [])]


def _text_timeline():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["ui_state"]["selected_item_id"] = "shot_section_text"
    timeline["director_track"]["sections"].append({
        "item_id": "section_text",
        "type": SECTION_TYPE_TEXT,
        "start_time": 0.0,
        "end_time": 1.0,
        "prompt": "simple prompt",
    })
    return timeline


def _two_shot_boundary_timeline(
    video_path: Path,
    *,
    mode: str = BOUNDARY_MODE_CONTINUOUS_SHOT,
    tail_frames: int = 6,
    transition_prompt: str = "",
):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["project"]["frame_rate"] = 8.0
    timeline["ui_state"]["selected_item_id"] = "shot_next"
    timeline["assets"].append({
        "asset_id": "asset_previous_take",
        "type": ASSET_TYPE_VIDEO,
        "source_kind": ASSET_SOURCE_GENERATED,
        "path": str(video_path),
        "name": video_path.name,
    })
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "section_previous",
                "type": SECTION_TYPE_TEXT,
                "start_time": 0.0,
                "end_time": 1.0,
                "prompt": "previous shot",
            },
            {
                "item_id": "section_next",
                "type": SECTION_TYPE_TEXT,
                "start_time": 1.0,
                "end_time": 2.0,
                "prompt": "next shot",
            },
        ]
    )
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_previous",
            "start_time": 0.0,
            "end_time": 1.0,
            "section_ids": ["section_previous"],
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
            "shot_id": "shot_next",
            "start_time": 1.0,
            "end_time": 2.0,
            "section_ids": ["section_next"],
        },
    ]
    timeline["sequence"]["boundaries"] = [
        {
            "boundary_id": "boundary_previous_next",
            "left_shot_id": "shot_previous",
            "right_shot_id": "shot_next",
            "mode": mode,
            "tail_frames": tail_frames,
            "transition_prompt": transition_prompt,
        }
    ]
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


def _write_test_video(path: Path, frame_count=12, fps=8, width=24, height=16):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for index in range(frame_count):
            value = int(round(index / max(1, frame_count - 1) * 255))
            array = np.zeros((height, width, 3), dtype=np.uint8)
            array[:, :, 0] = value
            array[:, :, 1] = 64
            array[:, :, 2] = 255 - value
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


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
