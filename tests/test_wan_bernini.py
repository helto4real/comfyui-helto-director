from __future__ import annotations

import math
from pathlib import Path

import pytest
import torch
from PIL import Image

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
)
from shared.timeline import GENERATION_MODE_FORCE_FULL_TIMELINE, create_default_video_timeline
from shared.wan import build_wan_runtime_outputs, build_wan_timeline_plan, create_wan_timeline_config
from shared.wan.bernini import BERNINI_SYSTEM_PROMPTS


def test_bernini_config_normalizes_model_mode_and_prompt_policy():
    config = create_wan_timeline_config(model_mode="Bernini-A14B", bernini_task_prompt="i2v")

    assert config["model_mode"] == "Bernini-A14B"
    assert config["bernini_task_prompt"] == "i2v"

    fallback = create_wan_timeline_config(model_mode="Bernini-A14B", bernini_task_prompt="ads2v")

    assert fallback["bernini_task_prompt"] == "Auto"
    assert create_wan_timeline_config(model_mode="Bernini-A14B", bernini_task_prompt="r2v")["bernini_task_prompt"] == "r2v"
    assert create_wan_timeline_config(model_mode="Bernini-A14B", bernini_task_prompt="rv2v")["bernini_task_prompt"] == "rv2v"


def test_bernini_planner_auto_selects_t2v_for_text_only():
    plan, validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
    )

    bernini = plan["model_specific"]["wan"]["bernini"]
    prompt_relay = plan["model_specific"]["wan"]["prompt_relay"]

    assert validation["is_valid"] is True
    assert bernini["task_type"] == "t2v"
    assert bernini["system_prompt"] == BERNINI_SYSTEM_PROMPTS["t2v"]
    assert prompt_relay["global_prompt"].startswith(BERNINI_SYSTEM_PROMPTS["t2v"])
    assert "r2v" not in bernini["deferred_task_types"]
    assert "rv2v" not in bernini["deferred_task_types"]


def test_bernini_planner_auto_selects_i2v_for_multiple_images(tmp_path):
    plan, validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=3),
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )

    bernini = plan["model_specific"]["wan"]["bernini"]

    assert validation["is_valid"] is True
    assert bernini["task_type"] == "i2v"
    assert bernini["system_prompt"] == BERNINI_SYSTEM_PROMPTS["i2v"]
    assert bernini["media_used"]["item_id"] == "section_image_0"
    assert bernini["media_used"]["bernini_role"] == "source_video_single_frame"
    assert [entry["item_id"] for entry in bernini["ignored_timeline_media"]] == ["section_image_1", "section_image_2"]
    assert "BERNINI_TIMELINE_MEDIA_DEFERRED" in [entry["code"] for entry in validation["warnings"]]


def test_bernini_planner_auto_selects_v2v_and_defers_images_with_video(tmp_path):
    plan, validation, _debug = build_wan_timeline_plan(
        _video_timeline(tmp_path, include_image=True),
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )

    bernini = plan["model_specific"]["wan"]["bernini"]

    assert validation["is_valid"] is True
    assert bernini["task_type"] == "v2v"
    assert bernini["system_prompt"] == BERNINI_SYSTEM_PROMPTS["v2v"]
    assert bernini["media_used"]["item_id"] == "section_video"
    assert bernini["ignored_timeline_media"][0]["item_id"] == "section_image"
    assert "WAN_VIDEO_SECTION_PROMPT_ONLY" not in [entry["code"] for entry in validation["warnings"]]
    assert "BERNINI_SOURCE_VIDEO_PLANNED" in [entry["code"] for entry in validation["info"]]


def test_bernini_planner_uses_tagged_references_for_r2v(tmp_path):
    timeline = _text_timeline()
    timeline["project"]["global_prompt"]["enabled"] = True
    timeline["project"]["global_prompt"]["prompt"] = "cinematic"
    _add_character_reference(timeline, tmp_path, 1, description="red coat and silver hair")
    _add_character_reference(timeline, tmp_path, 2, description="unused blue cloak")
    timeline["director_track"]["sections"][0]["prompt"] = "hero @image1:character[0.6] walks through fog"

    plan, validation, debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
    )

    bernini = plan["model_specific"]["wan"]["bernini"]
    references = bernini["character_references"]
    prompt_relay = plan["model_specific"]["wan"]["prompt_relay"]

    assert validation["is_valid"] is True
    assert bernini["task_type"] == "r2v"
    assert bernini["system_prompt"] == BERNINI_SYSTEM_PROMPTS["r2v"]
    assert bernini["reference_image_count"] == 1
    assert references["reference_specs"][0]["label"] == "image1"
    assert references["reference_specs"][0]["strength"] == 0.6
    assert plan["prompt_plan"][0]["raw_prompt"] == "hero @image1:character[0.6] walks through fog"
    assert plan["prompt_plan"][0]["runtime_prompt"] == "hero red coat and silver hair walks through fog"
    assert plan["prompt_plan"][0]["runtime_effective_prompt"] == "cinematic, hero red coat and silver hair walks through fog"
    assert prompt_relay["global_prompt"].startswith(BERNINI_SYSTEM_PROMPTS["r2v"])
    assert prompt_relay["global_prompt"].endswith("cinematic")
    assert prompt_relay["local_prompts"][0]["prompt"] == "hero red coat and silver hair walks through fog"
    assert prompt_relay["local_prompts"][0]["effective_prompt"] == "cinematic, hero red coat and silver hair walks through fog"
    assert "@image1:character" not in prompt_relay["local_prompts"][0]["prompt"]
    assert "image2" not in [entry["label"] for entry in references["reference_specs"]]
    assert debug["details"]["bernini"]["character_references"]["substitutions"][0]["status"] == "description"


def test_bernini_planner_uses_timeline_image_as_background_and_reference_as_subject(tmp_path):
    timeline = _image_timeline(tmp_path, count=1)
    _add_character_reference(timeline, tmp_path, 1, description="masked dancer")
    timeline["director_track"]["sections"][0]["prompt"] = "place @image1:character in the courtyard"

    plan, validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
    )

    bernini = plan["model_specific"]["wan"]["bernini"]

    assert validation["is_valid"] is True
    assert bernini["task_type"] == "rv2v"
    assert bernini["media_used"]["item_id"] == "section_image_0"
    assert bernini["media_used"]["bernini_role"] == "source_video_single_frame"
    assert bernini["reference_image_count"] == 1
    assert bernini["character_references"]["reference_specs"][0]["label"] == "image1"


def test_bernini_reference_tags_validate_unknown_disabled_and_global_off(tmp_path):
    unknown = _text_timeline()
    unknown["director_track"]["sections"][0]["prompt"] = "@image9:character enters"
    _add_character_reference(unknown, tmp_path, 1)
    _plan, validation, _debug = build_wan_timeline_plan(
        unknown,
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
    )
    assert "BERNINI_CHARACTER_REFERENCE_UNKNOWN" in [entry["code"] for entry in validation["errors"]]

    disabled = _text_timeline()
    disabled["director_track"]["sections"][0]["prompt"] = "@image1:character enters"
    _add_character_reference(disabled, tmp_path, 1, enabled=False)
    _plan, validation, _debug = build_wan_timeline_plan(
        disabled,
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
    )
    assert "BERNINI_CHARACTER_REFERENCE_DISABLED" in [entry["code"] for entry in validation["warnings"]]

    empty = _text_timeline()
    empty["director_track"]["sections"][0]["prompt"] = "@image1:character enters"
    _add_character_reference(empty, tmp_path, 1, description="")
    plan, validation, _debug = build_wan_timeline_plan(
        empty,
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
    )
    assert plan["prompt_plan"][0]["runtime_prompt"] == "enters"
    assert "BERNINI_CHARACTER_REFERENCE_EMPTY_DESCRIPTION" in [entry["code"] for entry in validation["warnings"]]
    assert plan["model_specific"]["wan"]["bernini"]["character_references"]["empty_description_tags"] == ["@image1:character"]

    global_off = _text_timeline()
    global_off["director_track"]["sections"][0]["prompt"] = "@image1:character enters"
    global_off["project"]["metadata"]["character_references_enabled"] = False
    _add_character_reference(global_off, tmp_path, 1)
    _plan, validation, _debug = build_wan_timeline_plan(
        global_off,
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
    )
    assert "BERNINI_CHARACTER_REFERENCE_DISABLED" in [entry["code"] for entry in validation["warnings"]]


def test_bernini_reference_limit_keeps_first_eight(tmp_path):
    timeline = _text_timeline()
    tags = []
    for index in range(1, 10):
        _add_character_reference(timeline, tmp_path, index, description=f"subject {index}")
        tags.append(f"@image{index}:character")
    timeline["director_track"]["sections"][0]["prompt"] = " ".join(tags)

    plan, validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
    )

    references = plan["model_specific"]["wan"]["bernini"]["character_references"]

    assert len(references["reference_specs"]) == 8
    assert references["reference_specs"][-1]["label"] == "image8"
    assert references["overflow_reference_specs"][0]["label"] == "image9"
    assert "BERNINI_CHARACTER_REFERENCE_LIMIT_EXCEEDED" in [entry["code"] for entry in validation["warnings"]]


def test_bernini_plan_only_debug_needs_no_backend_inputs(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=2),
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
    )

    *_outputs, runtime_debug = build_wan_runtime_outputs(wan_timeline_plan=plan)

    assert runtime_debug["bernini"]["task_type"] == "i2v"
    assert runtime_debug["summary"]["bernini_task_type"] == "i2v"
    assert runtime_debug["status"]["plan_only"] is True


def test_bernini_plan_reports_empty_user_conditioning():
    plan, validation, debug = build_wan_timeline_plan(
        create_default_video_timeline(),
        create_wan_timeline_config(model_mode="Bernini-A14B", debug_mode="Full"),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )

    bernini = plan["model_specific"]["wan"]["bernini"]

    assert validation["is_valid"] is True
    assert "BERNINI_NO_USER_CONDITIONING" in [entry["code"] for entry in validation["warnings"]]
    assert bernini["task_type"] == "t2v"
    assert bernini["timeline_image_count"] == 0
    assert bernini["timeline_video_count"] == 0
    assert bernini["timeline_prompt_count"] == 0
    assert bernini["has_user_prompt_text"] is False
    assert bernini["has_media_conditioning"] is False
    assert bernini["has_user_conditioning"] is False
    assert debug["details"]["bernini"]["has_user_conditioning"] is False


def test_bernini_comfyui_core_rejects_empty_user_conditioning():
    plan, _validation, _debug = build_wan_timeline_plan(
        create_default_video_timeline(),
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            resolution_profile="Quick Draft",
            debug_mode="Full",
        ),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )

    with pytest.raises(ValueError, match="BERNINI_NO_USER_CONDITIONING"):
        build_wan_runtime_outputs(
            high_noise_model=FakeModel(),
            clip=FakeClip(),
            vae=FakeVAE(),
            wan_timeline_plan=plan,
        )


def test_bernini_comfyui_core_allows_text_only_t2v_prompt():
    plan, _validation, _debug = build_wan_timeline_plan(
        _text_timeline(),
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            resolution_profile="Quick Draft",
            prompt_routing="Off",
            debug_mode="Full",
        ),
    )

    _high, _low, positive, _negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert runtime_debug["bernini"]["task_type"] == "t2v"
    assert runtime_debug["bernini"]["has_user_conditioning"] is True
    assert runtime_debug["bernini"]["timeline_prompt_count"] == 1
    assert _helper_decision(runtime_debug) == "BerniniConditioning"
    assert "BERNINI_PROMPT_RELAY_DISABLED" in [entry["code"] for entry in runtime_debug["validation"]["warnings"]]
    assert positive[0][1]["prompt"].startswith(BERNINI_SYSTEM_PROMPTS["t2v"])
    assert video_latent["samples"].shape[1] == 16


def test_bernini_comfyui_core_uses_context_latents_for_i2v_single_frame_source(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=1),
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            resolution_profile="Quick Draft",
            debug_mode="Full",
        ),
    )

    _high, _low, positive, negative, video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert runtime_debug["output_payload_type"] == "COMFYUI_CORE_BERNINI_CONDITIONING_LATENT"
    assert runtime_debug["bernini"]["task_type"] == "i2v"
    assert runtime_debug["bernini"]["system_prompt"] == BERNINI_SYSTEM_PROMPTS["i2v"]
    assert runtime_debug["bernini"]["runtime_media_decisions"][0]["bernini_role"] == "source_video_single_frame"
    assert "reference_images" not in runtime_debug["bernini"]["runtime_media_decisions"][0]
    assert runtime_debug["bernini"]["runtime_media_decisions"][0]["tensor_shape"] == [1, 8, 12, 3]
    assert runtime_debug["bernini"]["runtime_media_decisions"][0]["tensor_stats"]["max"] > 0.0
    assert runtime_debug["bernini"]["runtime_media_decisions"][0]["source_aspect_ratio"] == 1.5
    assert runtime_debug["bernini"]["runtime_media_decisions"][0]["aspect_mismatch"] is True
    assert "BERNINI_SOURCE_ASPECT_MISMATCH" in [entry["code"] for entry in runtime_debug["validation"]["warnings"]]
    conditioning_debug = _bernini_decision(runtime_debug, "bernini_conditioning_debug")
    assert conditioning_debug["source_video"]["frame_count"] == 1
    assert conditioning_debug["source_video"]["tensor_shape"] == [1, 8, 12, 3]
    assert conditioning_debug["context_latents"]["count"] == 1
    assert conditioning_debug["context_latents"]["shapes"][0][1] == 16
    assert conditioning_debug["positive_prompt"]["starts_with_system_prompt"] is True
    assert "single-frame source_video" in " ".join(runtime_debug["bernini"]["runtime_diagnostics"])
    assert _helper_decision(runtime_debug) == "BerniniConditioning"
    assert _helper_decision(runtime_debug) not in {"WanImageToVideo", "Wan22ImageToVideoLatent"}
    assert positive[0][1]["prompt"].startswith(BERNINI_SYSTEM_PROMPTS["i2v"])
    assert positive[0][1]["context_latents"][0].shape[1] == 16
    assert negative[0][1]["context_latents"][0].shape[1] == 16
    assert "concat_latent_image" not in positive[0][1]
    assert "concat_mask" not in positive[0][1]
    assert video_latent["samples"].shape[1] == 16


def test_bernini_comfyui_core_passes_reference_images_for_r2v(tmp_path):
    timeline = _text_timeline()
    _add_character_reference(timeline, tmp_path, 1, description="green jacket")
    timeline["director_track"]["sections"][0]["prompt"] = "@image1:character waves"
    plan, _validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            resolution_profile="Quick Draft",
            debug_mode="Full",
        ),
    )

    _high, _low, positive, negative, _video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    assert runtime_debug["bernini"]["task_type"] == "r2v"
    assert runtime_debug["bernini"]["reference_image_count"] == 1
    assert "green jacket waves" in runtime_debug["prompt_relay"]["full_prompt"]
    assert "@image1:character" not in runtime_debug["prompt_relay"]["full_prompt"]
    reference_decision = next(
        decision for decision in runtime_debug["bernini"]["runtime_media_decisions"]
        if decision.get("bernini_role") == "subject_reference_image"
    )
    assert reference_decision["label"] == "image1"
    assert reference_decision["tensor_shape"] == [1, 9, 7, 3]
    conditioning_debug = _bernini_decision(runtime_debug, "bernini_conditioning_debug")
    assert conditioning_debug["source_video"]["present"] is False
    assert conditioning_debug["reference_images"]["count"] == 1
    assert conditioning_debug["context_latents"]["count"] == 1
    assert "green jacket waves" in conditioning_debug["positive_prompt"]["preview"]
    assert "@image1:character" not in conditioning_debug["positive_prompt"]["preview"]
    assert positive[0][1]["context_latents"][0].shape[1] == 16
    assert negative[0][1]["context_latents"][0].shape[1] == 16


def test_bernini_comfyui_core_uses_source_context_plus_reference_context(tmp_path):
    timeline = _image_timeline(tmp_path, count=1)
    _add_character_reference(timeline, tmp_path, 1, description="gold helmet")
    timeline["director_track"]["sections"][0]["prompt"] = "@image1:character stands in the background scene"
    plan, _validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            resolution_profile="Quick Draft",
            debug_mode="Full",
        ),
    )

    _high, _low, _positive, _negative, _video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    conditioning_debug = _bernini_decision(runtime_debug, "bernini_conditioning_debug")
    assert runtime_debug["bernini"]["task_type"] == "rv2v"
    assert conditioning_debug["source_video"]["present"] is True
    assert conditioning_debug["reference_images"]["count"] == 1
    assert conditioning_debug["context_latents"]["count"] == 2


def test_bernini_reference_image_load_failure_is_clear(tmp_path):
    timeline = _text_timeline()
    _add_character_reference(timeline, tmp_path, 1, description="missing", path=tmp_path / "missing.png")
    timeline["director_track"]["sections"][0]["prompt"] = "@image1:character enters"
    plan, _validation, _debug = build_wan_timeline_plan(
        timeline,
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            resolution_profile="Quick Draft",
            debug_mode="Full",
        ),
    )

    with pytest.raises(ValueError, match="BERNINI_CHARACTER_REFERENCE_LOAD_FAILED"):
        build_wan_runtime_outputs(
            high_noise_model=FakeModel(),
            clip=FakeClip(),
            vae=FakeVAE(),
            wan_timeline_plan=plan,
        )


def test_bernini_source_image_loader_applies_exif_transpose(tmp_path):
    image_path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (3, 5), (80, 120, 160))
    exif = image.getexif()
    exif[274] = 6
    image.save(image_path, exif=exif)
    plan, _validation, _debug = build_wan_timeline_plan(
        _single_image_timeline(image_path),
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            resolution_profile="Quick Draft",
            debug_mode="Full",
        ),
    )

    _high, _low, _positive, _negative, _video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    media_debug = runtime_debug["bernini"]["runtime_media_decisions"][0]
    conditioning_debug = _bernini_decision(runtime_debug, "bernini_conditioning_debug")

    assert media_debug["bernini_role"] == "source_video_single_frame"
    assert media_debug["original_size"] == [3, 5]
    assert media_debug["exif_orientation"] == 6
    assert media_debug["exif_transposed_size"] == [5, 3]
    assert media_debug["tensor_shape"] == [1, 3, 5, 3]
    assert conditioning_debug["source_video"]["tensor_shape"] == [1, 3, 5, 3]


def test_bernini_prompt_debug_falls_back_to_prompt_relay_payload(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=1),
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            resolution_profile="Quick Draft",
            debug_mode="Full",
        ),
    )

    _high, _low, _positive, _negative, _video_latent, runtime_debug = build_wan_runtime_outputs(
        high_noise_model=FakeModel(),
        clip=FakeClipWithoutPromptMetadata(),
        vae=FakeVAE(),
        wan_timeline_plan=plan,
    )

    conditioning_debug = _bernini_decision(runtime_debug, "bernini_conditioning_debug")

    assert conditioning_debug["positive_prompt"]["present"] is True
    assert conditioning_debug["positive_prompt"]["source"] == "runtime_prompt_debug"
    assert conditioning_debug["positive_prompt"]["prompt_relay_status"] == "patched"
    assert conditioning_debug["positive_prompt"]["starts_with_system_prompt"] is True


def test_bernini_comfyui_core_rejects_48_channel_wan22_i2v_wiring(tmp_path):
    plan, _validation, _debug = build_wan_timeline_plan(
        _image_timeline(tmp_path, count=1),
        create_wan_timeline_config(
            model_mode="Bernini-A14B",
            runtime_backend_profile="ComfyUI Core",
            resolution_profile="Quick Draft",
            debug_mode="Full",
        ),
    )

    with pytest.raises(ValueError, match="BERNINI_RUNTIME_LATENT_FORMAT_MISMATCH"):
        build_wan_runtime_outputs(
            high_noise_model=FakeModel(latent_channels=48, spatial_scale=16),
            clip=FakeClip(),
            vae=FakeVAE(latent_channels=48, spatial_scale=16),
            wan_timeline_plan=plan,
        )


def _helper_decision(runtime_debug: dict) -> str | None:
    for decision in runtime_debug.get("media_decisions", []):
        if decision.get("type") == "comfy_core_helper":
            return decision.get("helper")
    return None


def _bernini_decision(runtime_debug: dict, decision_type: str) -> dict:
    for decision in runtime_debug["bernini"]["runtime_media_decisions"]:
        if decision.get("type") == decision_type:
            return decision
    raise AssertionError(f"Missing Bernini runtime decision {decision_type}")


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


def _single_image_timeline(path: Path):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 0.25
    timeline["project"]["frame_rate"] = 8.0
    timeline["assets"].append({
        "asset_id": "image_0",
        "type": ASSET_TYPE_IMAGE,
        "source_kind": ASSET_SOURCE_FILE_PATH,
        "path": str(path),
        "name": path.name,
    })
    timeline["director_track"]["sections"].append({
        "item_id": "section_image_0",
        "type": SECTION_TYPE_IMAGE,
        "start_time": 0.0,
        "end_time": 0.25,
        "image": {"asset_id": "image_0"},
        "prompt": "animate this image",
        "guide_strength": 1.0,
    })
    return timeline


def _image_timeline(tmp_path: Path, *, count: int):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = float(count) * 0.25
    timeline["project"]["frame_rate"] = 8.0
    for index in range(count):
        asset_id = f"image_{index}"
        path = tmp_path / f"image_{index}.png"
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


def _video_timeline(tmp_path: Path, *, include_image: bool = False):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 0.5
    timeline["project"]["frame_rate"] = 8.0
    video_path = tmp_path / "source.mp4"
    timeline["assets"].append({
        "asset_id": "video_001",
        "type": ASSET_TYPE_VIDEO,
        "source_kind": ASSET_SOURCE_FILE_PATH,
        "path": str(video_path),
        "name": video_path.name,
    })
    timeline["director_track"]["sections"].append({
        "item_id": "section_video",
        "type": SECTION_TYPE_VIDEO,
        "start_time": 0.0,
        "end_time": 0.25,
        "video": {"asset_id": "video_001"},
        "prompt": "edit the source video",
    })
    if include_image:
        image_path = tmp_path / "image.png"
        Image.new("RGB", (12, 8), (120, 80, 40)).save(image_path)
        timeline["assets"].append({
            "asset_id": "image_001",
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": str(image_path),
            "name": image_path.name,
        })
        timeline["director_track"]["sections"].append({
            "item_id": "section_image",
            "type": SECTION_TYPE_IMAGE,
            "start_time": 0.25,
            "end_time": 0.5,
            "image": {"asset_id": "image_001"},
            "prompt": "ignored keyframe",
        })
    return timeline


def _add_character_reference(
    timeline: dict,
    tmp_path: Path,
    index: int,
    *,
    description: str = "character reference",
    enabled: bool = True,
    path: Path | None = None,
):
    if path is None:
        path = tmp_path / f"reference_{index}.png"
        Image.new("RGB", (7, 9), (20 + index * 20, 120, 90)).save(path)
    timeline["project"].setdefault("metadata", {})
    timeline["project"]["metadata"].setdefault("character_references_enabled", True)
    timeline["project"]["metadata"].setdefault("character_references", []).append({
        "id": f"reference_{index}",
        "label": f"image{index}",
        "kind": "character",
        "enabled": enabled,
        "description": description,
        "strength": 1.0,
        "image": {
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": str(path),
            "name": path.name,
            "metadata": {},
        },
    })
    return path


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


class FakeClipWithoutPromptMetadata(FakeClip):
    def encode_from_tokens_scheduled(self, tokens):
        return [[torch.ones(1, 2), {"pooled_output": torch.ones(1, 2)}]]


class FakeCrossAttention:
    pass


class FakeBlock:
    def __init__(self):
        self.cross_attn = FakeCrossAttention()


class FakeDiffusionModel:
    def __init__(self):
        self.blocks = [FakeBlock(), FakeBlock()]


class FakeLatentFormat:
    def __init__(self, latent_channels: int = 16, spatial_scale: int = 8):
        self.latent_channels = latent_channels
        self.spacial_downscale_ratio = spatial_scale


class FakeModel:
    def __init__(self, latent_channels: int = 16, spatial_scale: int = 8):
        self.diffusion_model = FakeDiffusionModel()
        self.latent_format = FakeLatentFormat(latent_channels, spatial_scale)
        self.object_patches = {}

    def clone(self):
        return FakeModel(
            latent_channels=self.latent_format.latent_channels,
            spatial_scale=self.latent_format.spacial_downscale_ratio,
        )

    def get_model_object(self, name):
        if name == "latent_format":
            return self.latent_format
        assert name == "diffusion_model"
        return self.diffusion_model

    def add_object_patch(self, key, patch):
        self.object_patches[key] = patch


class FakeVAE:
    def __init__(self, latent_channels: int = 16, spatial_scale: int = 8):
        self.latent_channels = latent_channels
        self._spatial_scale = spatial_scale

    def spacial_compression_encode(self):
        return self._spatial_scale

    def encode(self, image):
        frames = int(image.shape[0])
        height = math.ceil(int(image.shape[1]) / self._spatial_scale)
        width = math.ceil(int(image.shape[2]) / self._spatial_scale)
        latent_frames = ((frames - 1) // 4) + 1
        return torch.ones(1, self.latent_channels, latent_frames, height, width)
