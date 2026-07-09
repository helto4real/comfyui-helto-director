from __future__ import annotations

import json
from pathlib import Path

from shared.contracts.video_timeline import MODEL_LORA_TARGET_DESCRIPTORS
from shared.timeline.defaults import create_default_project_model_loras
from shared.timeline.normalize import normalize_video_timeline
from shared.timeline.validate import validate_video_timeline


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "shared"
    / "contracts"
    / "model_lora_targets.fixture.json"
)


def _descriptor_contract() -> dict[str, dict[str, list[str]]]:
    return {
        model_key: {
            "family_aliases": list(descriptor["family_aliases"]),
            "targets": list(descriptor["targets"]),
        }
        for model_key, descriptor in MODEL_LORA_TARGET_DESCRIPTORS.items()
    }


def test_python_model_lora_descriptors_match_canonical_fixture():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert _descriptor_contract() == fixture


def test_model_lora_defaults_and_normalization_follow_descriptors():
    expected_targets = {
        model_key: list(descriptor["targets"])
        for model_key, descriptor in MODEL_LORA_TARGET_DESCRIPTORS.items()
    }

    defaults = create_default_project_model_loras()
    assert {
        model_key: list(targets)
        for model_key, targets in defaults["global"].items()
    } == expected_targets

    normalized = normalize_video_timeline({
        "project": {
            "model_loras": {
                "global": {
                    "ltx_2_3": {"unsupported": {"loras": [{"name": "bad"}]}},
                    "unsupported": {"main": {"loras": [{"name": "bad"}]}},
                },
            },
        },
    })
    assert {
        model_key: list(targets)
        for model_key, targets in normalized["project"]["model_loras"]["global"].items()
    } == expected_targets


def test_model_lora_validation_accepts_only_descriptor_targets():
    valid = normalize_video_timeline(None)
    assert not {
        entry["code"]
        for entry in validate_video_timeline(valid, global_settings={})["errors"]
        if entry["code"].startswith("MODEL_LORA_")
    }

    invalid = normalize_video_timeline(None)
    invalid["project"]["model_loras"]["global"]["ltx_2_3"]["unsupported"] = {
        "version": 1,
        "loras": [],
        "ui": {},
    }
    codes = {
        entry["code"]
        for entry in validate_video_timeline(invalid, global_settings={})["errors"]
    }
    assert "MODEL_LORA_TARGET_INVALID" in codes
