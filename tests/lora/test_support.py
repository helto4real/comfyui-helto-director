import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nodes.timeline_lora_configuration.node import HeltoTimelineLoraConfiguration
from routes import lora_info
from shared.lora.application import apply_lora_config, apply_lora_config_model_only
from shared.lora import config as lora_config_module
from shared.lora.config import normalize_lora_config


@pytest.fixture(autouse=True)
def fake_available_loras(monkeypatch):
    monkeypatch.setattr(
        lora_config_module,
        "_available_loras",
        lambda: ["style.safetensors", "detail.safetensors", "keep.safetensors", "a.safetensors", "b.safetensors"],
    )


def test_lora_config_node_returns_director_custom_type():
    assert HeltoTimelineLoraConfiguration.RETURN_TYPES == ("HELTO_LORA_CONFIG",)
    assert HeltoTimelineLoraConfiguration.CATEGORY == "timeline/director"


def test_lora_config_node_normalizes_rgthree_style_rows():
    config = HeltoTimelineLoraConfiguration().configure(
        show_strengths="separate",
        match="style",
        lora_2={
            "on": True,
            "lora": "detail",
            "strength": 0.4,
            "strengthTwo": 0.2,
        },
        lora_1={
            "on": True,
            "lora": "style",
            "strength": 1.25,
        },
    )[0]

    assert config["version"] == 1
    assert config["ui"] == {"show_strengths": "separate", "match": "style"}
    assert config["loras"] == [
        {
            "enabled": True,
            "name": "style.safetensors",
            "strength_model": 1.25,
            "strength_clip": 1.25,
        },
        {
            "enabled": True,
            "name": "detail.safetensors",
            "strength_model": 0.4,
            "strength_clip": 0.2,
        },
    ]


def test_lora_config_skips_disabled_and_zero_strength_rows():
    config = normalize_lora_config(
        {
            "lora_1": {"on": False, "lora": "off", "strength": 1.0},
            "lora_2": {"on": True, "lora": "zero", "strength": 0, "strengthTwo": 0},
            "lora_3": {"on": True, "lora": "keep", "strength": 0, "strengthTwo": 0.6},
        }
    )

    assert [lora["name"] for lora in config["loras"]] == ["keep.safetensors"]


def test_lora_config_rejects_unknown_lora_when_lora_list_available():
    with pytest.raises(ValueError, match="was selected, but it was not found"):
        normalize_lora_config(
            {"lora_1": {"on": True, "lora": "missing", "strength": 1}},
            available_loras=["known.safetensors"],
        )


def test_lora_config_matches_basename_without_extension():
    config = normalize_lora_config(
        {"lora_1": {"on": True, "lora": "style", "strength": 1}},
        available_loras=["subdir/style.safetensors"],
    )

    assert config["loras"][0]["name"] == "subdir/style.safetensors"


def test_lora_application_uses_comfy_lora_loader_in_order(monkeypatch):
    calls = []

    class FakeLoraLoader:
        def load_lora(self, model, clip, lora_name, strength_model, strength_clip):
            calls.append((model, clip, lora_name, strength_model, strength_clip))
            return f"{model}+{lora_name}", f"{clip}+{lora_name}"

    monkeypatch.setitem(sys.modules, "nodes", SimpleNamespace(LoraLoader=FakeLoraLoader))

    model, clip, applied = apply_lora_config(
        model="model",
        clip="clip",
        lora_config={
            "loras": [
                {
                    "enabled": True,
                    "name": "a.safetensors",
                    "strength_model": 1.0,
                    "strength_clip": 0.5,
                },
                {
                    "enabled": True,
                    "name": "b.safetensors",
                    "strength_model": 0.7,
                    "strength_clip": 0.7,
                },
            ]
        },
    )

    assert model == "model+a.safetensors+b.safetensors"
    assert clip == "clip+a.safetensors+b.safetensors"
    assert [row["name"] for row in applied] == ["a.safetensors", "b.safetensors"]
    assert calls == [
        ("model", "clip", "a.safetensors", 1.0, 0.5),
        ("model+a.safetensors", "clip+a.safetensors", "b.safetensors", 0.7, 0.7),
    ]


def test_lora_application_model_only_uses_comfy_model_only_loader(monkeypatch):
    calls = []

    class FakeLoraLoaderModelOnly:
        def load_lora_model_only(self, model, lora_name, strength_model):
            calls.append((model, lora_name, strength_model))
            return (f"{model}+{lora_name}",)

    monkeypatch.setitem(sys.modules, "nodes", SimpleNamespace(LoraLoaderModelOnly=FakeLoraLoaderModelOnly))

    model, applied = apply_lora_config_model_only(
        model="model",
        lora_config={"loras": [{"enabled": True, "name": "a.safetensors", "strength_model": 0.8}]},
    )

    assert model == "model+a.safetensors"
    assert applied == [{"enabled": True, "name": "a.safetensors", "strength_model": 0.8, "strength_clip": 0.8}]
    assert calls == [("model", "a.safetensors", 0.8)]


def test_lora_info_merges_civitai_data():
    info = {"images": [], "raw": {}}
    lora_info._merge_civitai(
        info,
        {
            "_sha256": "abc",
            "_civitai_api": "https://civitai.example/api",
            "id": 456,
            "modelId": 123,
            "name": "Version",
            "baseModel": "Flux.1",
            "model": {"name": "Model", "type": "LORA"},
            "trainedWords": ["word_a, word_b"],
            "images": [{"url": "https://image.example/999.jpeg", "meta": {"seed": 7}}],
        },
    )

    assert info["name"] == "Model - Version"
    assert "https://civitai.com/models/123?modelVersionId=456" in info["links"]
    assert info["trainedWords"] == [
        {"word": "word_a", "civitai": True},
        {"word": "word_b", "civitai": True},
    ]
    assert info["images"][0]["seed"] == 7


def test_lora_info_reads_safetensors_metadata(tmp_path):
    metadata = b'{"__metadata__":{"ss_output_name":"Demo","ss_clip_skip":"2"}}'
    path = tmp_path / "demo.safetensors"
    path.write_bytes(len(metadata).to_bytes(8, "little") + metadata)

    parsed = lora_info._read_safetensors_metadata(Path(path))

    assert parsed["ss_output_name"] == "Demo"
    assert parsed["ss_clip_skip"] == "2"
