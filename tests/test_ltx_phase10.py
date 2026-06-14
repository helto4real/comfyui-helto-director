from __future__ import annotations

import asyncio
import copy
import importlib.util
import sys
from pathlib import Path

import torch
from PIL import Image

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_TYPE_IMAGE,
    QUALITY_PRESET_QUICK_DRAFT,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
)
from shared.ltx import build_ltx_runtime_outputs, build_ltx_timeline_plan, create_ltx_timeline_config
from shared.ltx import identity as identity_module
from shared.timeline import create_default_video_timeline


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


def _registered_classes_by_id():
    _module, node_classes = _load_nodepack()
    return {node_class.define_schema().node_id: node_class for node_class in node_classes}


class FakeClip:
    def tokenize(self, text):
        return {"text": text}

    def encode_from_tokens_scheduled(self, tokens):
        return [[
            torch.ones((1, 2, 3), dtype=torch.float32),
            {
                "text": tokens["text"],
                "pooled_output": torch.ones((1, 3), dtype=torch.float32),
                "conditioning_lyrics": torch.ones((1, 4), dtype=torch.float32),
            },
        ]]


class FakeModel:
    def __init__(self, label="model"):
        self.label = label
        self.model = type("ModelWrapper", (), {"diffusion_model": object()})()

    def clone(self):
        return FakeModel(f"{self.label}:clone")

    def get_model_object(self, name):
        assert name == "diffusion_model"
        return self.model.diffusion_model

    def add_object_patch(self, _key, _value):
        pass


class FakeVAE:
    downscale_index_formula = (8, 32, 32)

    def encode(self, pixels):
        frames = ((pixels.shape[0] - 1) // 8) + 1
        height = max(1, pixels.shape[1] // 32)
        width = max(1, pixels.shape[2] // 32)
        return torch.ones((1, 128, frames, height, width), dtype=torch.float32)


def _text_plan():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "wide shot",
        }
    )
    config = create_ltx_timeline_config(reference_mode="Disabled", debug_mode=True)
    plan, validation, _debug = build_ltx_timeline_plan(timeline, config)
    assert validation["is_valid"] is True
    return plan


def _image_plan(path: Path):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["project"]["quality_preset"] = QUALITY_PRESET_QUICK_DRAFT
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
            "prompt": "subject",
            "guide_strength": 0.5,
        }
    )
    config = create_ltx_timeline_config(reference_mode="Disabled", debug_mode=True)
    plan, validation, _debug = build_ltx_timeline_plan(timeline, config)
    assert validation["is_valid"] is True
    return plan


def test_phase10_nodes_register_with_timeline_names_and_mappings():
    module, node_classes = _load_nodepack()
    node_ids = [node_class.define_schema().node_id for node_class in node_classes]

    expected_helpers = [
        "HeltoLTX23TimelineCropReferenceTail",
        "HeltoLTX23TimelineReferenceImageSelector",
        "HeltoLTX23TimelineIdentityAnchorLatentAware",
        "HeltoLTX23TimelineIdentityAnchorFace",
        "HeltoLTX23TimelineIdentityAnchorCombine",
        "HeltoLTX23TimelineApplyIdentityAnchor",
    ]
    for node_id in expected_helpers:
        assert node_id in node_ids
        assert node_id in module.NODE_CLASS_MAPPINGS
        assert "Timeline" in module.NODE_DISPLAY_NAME_MAPPINGS[node_id]


def test_phase10_node_schemas_use_expected_socket_types():
    classes = _registered_classes_by_id()

    crop_schema = classes["HeltoLTX23TimelineCropReferenceTail"].define_schema()
    selector_schema = classes["HeltoLTX23TimelineReferenceImageSelector"].define_schema()
    aware_schema = classes["HeltoLTX23TimelineIdentityAnchorLatentAware"].define_schema()
    face_schema = classes["HeltoLTX23TimelineIdentityAnchorFace"].define_schema()
    combine_schema = classes["HeltoLTX23TimelineIdentityAnchorCombine"].define_schema()
    apply_schema = classes["HeltoLTX23TimelineApplyIdentityAnchor"].define_schema()

    assert [input_item.io_type for input_item in crop_schema.inputs] == ["LATENT", "GUIDE_DATA"]
    assert [output.io_type for output in crop_schema.outputs] == ["LATENT", "INT"]
    assert [input_item.io_type for input_item in selector_schema.inputs] == ["GUIDE_DATA", "STRING"]
    assert [output.io_type for output in selector_schema.outputs] == ["IMAGE"]
    assert aware_schema.outputs[0].io_type == "LTX_IDENTITY_ANCHOR"
    assert face_schema.outputs[0].io_type == "LTX_IDENTITY_ANCHOR"
    assert [input_item.io_type for input_item in combine_schema.inputs[:2]] == ["LTX_IDENTITY_ANCHOR", "LTX_IDENTITY_ANCHOR"]
    assert [input_item.io_type for input_item in apply_schema.inputs] == ["MODEL", "LTX_IDENTITY_ANCHOR", "GUIDE_DATA", "SIGMAS", "VAE"]
    assert apply_schema.outputs[0].io_type == "MODEL"


def test_identity_anchor_nodes_emit_old_compatible_dictionaries():
    classes = _registered_classes_by_id()

    latent_anchor = classes["HeltoLTX23TimelineIdentityAnchorLatentAware"].execute().result[0]
    face_anchor = classes["HeltoLTX23TimelineIdentityAnchorFace"].execute().result[0]
    combined = classes["HeltoLTX23TimelineIdentityAnchorCombine"].execute(latent_anchor, face_anchor).result[0]

    assert latent_anchor["kind"] == "latent_aware"
    assert latent_anchor["energy_source"] == "auto"
    assert latent_anchor["cache_at_step"] == 6
    assert face_anchor["kind"] == "face"
    assert face_anchor["inject_mode"] == "tracked"
    assert combined == {
        "kind": "combined",
        "anchors": [latent_anchor, face_anchor],
        "scale_strengths": True,
        "strength_scale": 0.75,
    }


def test_combined_anchor_orders_latent_aware_before_face_and_scales_strengths(monkeypatch):
    calls = []

    class FakeAware:
        def patch(self, model, **kwargs):
            calls.append(("aware", kwargs["strength"]))
            return (model + ["aware"],)

    class FakeFace:
        def patch(self, model, **kwargs):
            calls.append(("face", kwargs["strength"]))
            return (model + ["face"],)

    def fake_loader(filename, _class_name):
        if filename == "latent_anchor_aware.py":
            return FakeAware
        if filename == "face_anchor.py":
            return FakeFace
        raise AssertionError(filename)

    monkeypatch.setattr(identity_module, "_load_10s_class", fake_loader)
    identity_anchor = {
        "kind": "combined",
        "scale_strengths": True,
        "strength_scale": 0.5,
        "anchors": [
            {"kind": "face", "strength": 0.2},
            {"kind": "latent_aware", "strength": 0.1, "energy_source": "none"},
        ],
    }

    result = identity_module.apply_identity_anchor([], identity_anchor)

    assert result == ["aware", "face"]
    assert calls == [("aware", 0.05), ("face", 0.1)]


def test_reference_selector_matches_label_id_first_and_errors():
    image_one = object()
    image_two = object()
    guide_data = {
        "reference_images": [
            {"id": "ref-one", "label": "image1", "image": image_one},
            {"id": "custom-id", "label": "image2", "image": image_two},
        ]
    }

    assert identity_module.select_timeline_reference_image(guide_data, "image1") is image_one
    assert identity_module.select_timeline_reference_image(guide_data, "custom-id") is image_two
    assert identity_module.select_timeline_reference_image(guide_data, "") is image_one

    try:
        identity_module.select_timeline_reference_image(guide_data, "missing")
    except ValueError as exc:
        assert "missing" in str(exc)
        assert "image1" in str(exc)
        assert "image2" in str(exc)
    else:
        raise AssertionError("Expected missing reference selector to raise.")


def test_crop_reference_tail_crops_samples_and_noise_mask():
    classes = _registered_classes_by_id()
    latent = {
        "samples": torch.arange(1 * 128 * 5 * 2 * 2, dtype=torch.float32).reshape(1, 128, 5, 2, 2),
        "noise_mask": torch.ones((1, 1, 5, 1, 1), dtype=torch.float32),
    }
    guide_data = {"clean_latent_frames": 3, "clean_pixel_frames": 17}

    cropped, clean_pixel_frames = classes["HeltoLTX23TimelineCropReferenceTail"].execute(latent, guide_data).result

    assert clean_pixel_frames == 17
    assert tuple(cropped["samples"].shape) == (1, 128, 3, 2, 2)
    assert tuple(cropped["noise_mask"].shape) == (1, 1, 3, 1, 1)
    assert torch.equal(cropped["samples"], latent["samples"][:, :, :3])


def test_apply_identity_anchor_node_routes_to_shared_helper(monkeypatch):
    classes = _registered_classes_by_id()
    apply_node = classes["HeltoLTX23TimelineApplyIdentityAnchor"]
    calls = {}

    def fake_apply(model, **kwargs):
        calls.update(kwargs)
        return "patched-model"

    monkeypatch.setitem(apply_node.execute.__func__.__globals__, "apply_identity_anchor", fake_apply)
    result = apply_node.execute("model", identity_anchor={"kind": "face"}, guide_data={"images": []}, sigmas="sigmas", vae="vae").result[0]

    assert result == "patched-model"
    assert calls == {
        "identity_anchor": {"kind": "face"},
        "guide_data": {"images": []},
        "sigmas": "sigmas",
        "vae": "vae",
    }


def test_runtime_applies_identity_anchor_and_does_not_mutate_plan(monkeypatch):
    from shared.ltx.runtime import runtime as runtime_module

    plan = _text_plan()
    original_plan = copy.deepcopy(plan)
    calls = {}

    def fake_apply(model, **kwargs):
        calls["model"] = model
        calls.update(kwargs)
        return "identity-patched-model"

    monkeypatch.setattr(runtime_module, "apply_identity_anchor", fake_apply)
    outputs = build_ltx_runtime_outputs(
        model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        ltx_timeline_plan=plan,
        identity_anchor={"kind": "face"},
        sigmas="sigmas",
    )

    runtime_model, *_rest, runtime_debug = outputs
    assert runtime_model == "identity-patched-model"
    assert calls["identity_anchor"] == {"kind": "face"}
    assert calls["sigmas"] == "sigmas"
    assert isinstance(calls["guide_data"], dict)
    assert not any("accepted but Phase 9 runtime does not apply" in entry for entry in runtime_debug["diagnostics"])
    assert plan == original_plan


def test_runtime_reference_images_include_transient_image_tensor(tmp_path):
    image_path = tmp_path / "reference.png"
    Image.new("RGB", (64, 64), (200, 32, 16)).save(image_path)
    plan = _image_plan(image_path)

    _runtime_model, _positive, _negative, _video_latent, _audio_latent, _combined_audio, guide_data, *_rest = build_ltx_runtime_outputs(
        model=FakeModel(),
        clip=FakeClip(),
        vae=FakeVAE(),
        ltx_timeline_plan=plan,
    )

    reference = guide_data["reference_images"][0]
    assert reference["id"] == "section_001"
    assert reference["label"] == "image_001"
    assert reference["image"].shape == guide_data["images"][0].shape
    assert torch.equal(identity_module.select_timeline_reference_image(guide_data, "image_001"), reference["image"])
