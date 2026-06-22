import json

import pytest

from shared.contracts.video_timeline import (
    ASSET_SOURCE_GENERATED,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    MODEL_LORA_TARGET_MAIN,
    SECTION_TYPE_TEXT,
    SHOT_TYPE_EXTENDED,
    SHOT_TYPE_GENERATED,
    TAKE_STATUS_ACCEPTED,
    TAKE_STATUS_CANDIDATE,
    TAKE_STATUS_REJECTED,
)
from shared.timeline import (
    GENERATED_TAKE_CAPTURE_TYPE,
    GeneratedCaptureError,
    TakeRegistrationError,
    accept_take,
    build_generated_take_capture_sidecar,
    build_take_capture_metadata,
    create_default_video_timeline,
    generated_take_capture_to_registration,
    register_generated_take,
    register_take_for_asset,
    reject_take,
    set_take_status,
    validate_video_timeline,
)


def _timeline_with_shot(*, shot_type: str = SHOT_TYPE_GENERATED) -> dict:
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["director_track"]["sections"] = [
        {
            "item_id": "section_001",
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 2.0,
            "prompt": "generate a quiet establishing shot",
        }
    ]
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_001",
            "type": shot_type,
            "start_time": 0.0,
            "end_time": 2.0,
            "section_ids": ["section_001"],
        }
    ]
    return timeline


def _shot(timeline: dict) -> dict:
    return timeline["sequence"]["shots"][0]


def test_register_generated_asset_and_nested_take_for_shot():
    timeline = _timeline_with_shot()
    resolved_loras = {
        "model_family": "LTX",
        "model_version": "2.3",
        "targets": {
            MODEL_LORA_TARGET_MAIN: [
                {"name": "style.safetensors", "strength_model": 0.75}
            ]
        },
    }

    result = register_generated_take(
        timeline,
        {
            "shot_id": "shot_001",
            "asset": {
                "asset_id": "asset_output",
                "type": ASSET_TYPE_VIDEO,
                "path": "/tmp/output/shot_001.mp4",
                "name": "shot_001.mp4",
                "thumbnail": "data:image/png;base64,AAAA",
                "metadata": {
                    "frame_count": 49,
                    "thumbnail": "data:image/png;base64,BBBB",
                },
            },
            "take": {
                "take_id": "take_output",
                "shot_id": "should_not_be_saved",
                "seed": 1234,
                "model_family": "LTX",
                "model_version": "2.3",
                "plan_hash": "plan_hash",
                "prompt_hash": "prompt_hash",
                "resolved_loras": resolved_loras,
                "metadata": {
                    "sampler": "euler",
                    "waveform": {"data": [0, 1, 0]},
                },
            },
        },
    )

    updated = result["timeline"]
    asset = updated["assets"][0]
    take = _shot(updated)["takes"][0]

    assert result["shot_id"] == "shot_001"
    assert result["asset_id"] == "asset_output"
    assert result["take_id"] == "take_output"
    assert asset["source_kind"] == ASSET_SOURCE_GENERATED
    assert asset["type"] == ASSET_TYPE_VIDEO
    assert asset["metadata"] == {"frame_count": 49}
    assert take == {
        "take_id": "take_output",
        "asset_id": "asset_output",
        "status": TAKE_STATUS_CANDIDATE,
        "seed": 1234,
        "model_family": "LTX",
        "model_version": "2.3",
        "plan_hash": "plan_hash",
        "prompt_hash": "prompt_hash",
        "resolved_loras": resolved_loras,
        "metadata": {"sampler": "euler"},
    }
    assert "shot_id" not in take
    assert "thumbnail" not in json.dumps(updated["assets"])
    assert "waveform" not in json.dumps(take)
    assert "data:image" not in json.dumps(updated["assets"])
    assert validate_video_timeline(updated)["is_valid"] is True


def test_accept_reject_and_recover_take_without_deleting_assets_or_changing_shot_type():
    timeline = _timeline_with_shot(shot_type=SHOT_TYPE_EXTENDED)
    first = register_generated_take(
        timeline,
        {
            "shot_id": "shot_001",
            "accept": True,
            "asset": {
                "asset_id": "asset_output",
                "type": ASSET_TYPE_VIDEO,
                "path": "/tmp/output/shot_001_a.mp4",
            },
            "take": {"take_id": "take_output"},
        },
    )
    timeline = first["timeline"]
    first_take_id = first["take_id"]
    first_asset_id = first["asset_id"]

    assert _shot(timeline)["type"] == SHOT_TYPE_EXTENDED
    assert _shot(timeline)["accepted_take_id"] == first_take_id
    assert _shot(timeline)["takes"][0]["status"] == TAKE_STATUS_ACCEPTED
    assert _shot(timeline)["clip_instance"]["asset_id"] == first_asset_id

    candidate = set_take_status(
        timeline,
        "shot_001",
        first_take_id,
        TAKE_STATUS_CANDIDATE,
    )
    timeline = candidate["timeline"]

    assert _shot(timeline)["accepted_take_id"] is None
    assert _shot(timeline)["takes"][0]["status"] == TAKE_STATUS_CANDIDATE
    assert _shot(timeline)["clip_instance"] is None

    promoted = accept_take(timeline, "shot_001", first_take_id)
    timeline = promoted["timeline"]

    assert _shot(timeline)["accepted_take_id"] == first_take_id
    assert _shot(timeline)["clip_instance"]["asset_id"] == first_asset_id

    reroll = register_generated_take(
        timeline,
        {
            "shot_id": "shot_001",
            "asset": {
                "asset_id": "asset_output",
                "type": ASSET_TYPE_VIDEO,
                "path": "/tmp/output/shot_001_b.mp4",
            },
            "take": {"take_id": "take_output"},
        },
    )
    timeline = reroll["timeline"]

    assert reroll["asset_id"] == "asset_output_2"
    assert reroll["take_id"] == "take_output_2"
    assert [take["take_id"] for take in _shot(timeline)["takes"]] == [
        "take_output",
        "take_output_2",
    ]
    assert _shot(timeline)["accepted_take_id"] == first_take_id

    accepted_reroll = accept_take(timeline, "shot_001", reroll["take_id"])
    timeline = accepted_reroll["timeline"]
    statuses = {
        take["take_id"]: take["status"]
        for take in _shot(timeline)["takes"]
    }

    assert _shot(timeline)["accepted_take_id"] == reroll["take_id"]
    assert statuses == {
        "take_output": TAKE_STATUS_CANDIDATE,
        "take_output_2": TAKE_STATUS_ACCEPTED,
    }
    assert _shot(timeline)["clip_instance"]["asset_id"] == reroll["asset_id"]

    rejected = reject_take(timeline, "shot_001", reroll["take_id"])
    timeline = rejected["timeline"]

    assert _shot(timeline)["accepted_take_id"] is None
    assert _shot(timeline)["clip_instance"] is None
    assert _shot(timeline)["takes"][1]["status"] == TAKE_STATUS_REJECTED
    assert [asset["asset_id"] for asset in timeline["assets"]] == [
        "asset_output",
        "asset_output_2",
    ]

    recovered = accept_take(timeline, "shot_001", reroll["take_id"])
    timeline = recovered["timeline"]

    assert _shot(timeline)["accepted_take_id"] == reroll["take_id"]
    assert _shot(timeline)["takes"][1]["status"] == TAKE_STATUS_ACCEPTED
    assert _shot(timeline)["clip_instance"]["asset_id"] == reroll["asset_id"]
    assert _shot(timeline)["type"] == SHOT_TYPE_EXTENDED
    assert validate_video_timeline(timeline)["is_valid"] is True


def test_accept_generated_image_take_does_not_create_clip_instance():
    result = register_generated_take(
        _timeline_with_shot(),
        {
            "shot_id": "shot_001",
            "accept": True,
            "asset": {
                "asset_id": "asset_image",
                "type": ASSET_TYPE_IMAGE,
                "path": "/tmp/output/shot_001.png",
            },
            "take": {"take_id": "take_image"},
        },
    )
    timeline = result["timeline"]

    assert _shot(timeline)["accepted_take_id"] == "take_image"
    assert _shot(timeline)["clip_instance"] is None
    assert _shot(timeline)["type"] == SHOT_TYPE_GENERATED
    assert validate_video_timeline(timeline)["is_valid"] is True


def test_register_take_for_stale_asset_reference_is_rejected():
    with pytest.raises(TakeRegistrationError, match="Asset 'missing_asset'"):
        register_take_for_asset(
            _timeline_with_shot(),
            "shot_001",
            {"take_id": "take_missing", "asset_id": "missing_asset"},
        )


def test_generated_take_capture_sidecar_pairs_asset_metadata_with_registration():
    registration = {
        "shot_id": "shot_001",
        "asset": {
            "name": "shot_001_take_001.mp4",
            "metadata": {"sampler": "euler"},
        },
        "take": {
            "take_id": "take_001",
            "seed": 1234,
            "model_family": "LTX",
            "model_version": "2.3",
            "plan_hash": "plan_hash",
            "prompt_hash": "prompt_hash",
        },
    }

    sidecar = build_generated_take_capture_sidecar(
        registration,
        media={
            "type": ASSET_TYPE_VIDEO,
            "path": "/tmp/outputs/shot_001_take_001.mp4",
            "filename": "shot_001_take_001.mp4",
            "mime_type": "video/mp4",
            "frame_rate": 24.0,
            "frame_count": 49,
            "duration_seconds": 2.0,
            "width": 1024,
            "height": 576,
        },
    )

    assert sidecar["type"] == GENERATED_TAKE_CAPTURE_TYPE
    assert sidecar["media"]["frame_rate"] == 24.0
    assert sidecar["registration"]["asset"]["source_kind"] == ASSET_SOURCE_GENERATED
    assert sidecar["registration"]["asset"]["metadata"]["shot_id"] == "shot_001"
    assert sidecar["registration"]["asset"]["metadata"]["take_id"] == "take_001"
    assert "/tmp/outputs" not in json.dumps(sidecar)

    registration_for_timeline = generated_take_capture_to_registration(
        sidecar,
        path="/tmp/outputs/shot_001_take_001.mp4",
        accept=True,
    )
    result = register_generated_take(_timeline_with_shot(), registration_for_timeline)
    timeline = result["timeline"]
    asset = timeline["assets"][0]
    take = _shot(timeline)["takes"][0]

    assert result["accepted"] is True
    assert asset["path"] == "/tmp/outputs/shot_001_take_001.mp4"
    assert asset["metadata"]["frame_count"] == 49
    assert take["take_id"] == "take_001"
    assert take["status"] == TAKE_STATUS_ACCEPTED
    assert validate_video_timeline(timeline)["is_valid"] is True


def test_generated_take_capture_privacy_redacts_clear_names_paths_and_loras():
    sidecar = build_generated_take_capture_sidecar(
        {
            "shot_id": "shot_001",
            "privacy": {"privacy_mode": True},
            "asset": {
                "name": "private_character_output.mp4",
                "metadata": {
                    "private_path": "/private/character/output.mp4",
                },
            },
            "take": {
                "take_id": "take_private",
                "model_family": "LTX",
                "model_version": "2.3",
                "resolved_loras": {
                    "model_family": "LTX",
                    "model_version": "2.3",
                    "targets": {
                        MODEL_LORA_TARGET_MAIN: [
                            {"name": "secret_identity.safetensors", "strength_model": 0.9}
                        ]
                    },
                },
                "metadata": {"source_path": "/private/source.mov"},
            },
        },
        media={
            "type": ASSET_TYPE_VIDEO,
            "filename": "private_character_output.mp4",
            "name": "private character output",
            "duration_seconds": 2.0,
        },
    )

    rendered = json.dumps(sidecar)
    assert "private_character" not in rendered
    assert "secret_identity" not in rendered
    assert "/private" not in rendered
    assert sidecar["media"]["filename"] == "generated_private.mp4"
    assert sidecar["registration"]["asset"]["name"] == "Private Generated Video"
    row = sidecar["registration"]["take"]["resolved_loras"]["targets"][MODEL_LORA_TARGET_MAIN][0]
    assert row["name"] == "lora_001"
    assert row["name_hash"]
    assert sidecar["privacy"]["privacy_mode"] is True
    assert "registration.take.resolved_loras.targets.*.name" in sidecar["privacy"]["redacted_fields"]


def test_generated_take_capture_rejects_embedded_media_payloads():
    with pytest.raises(GeneratedCaptureError, match="embedded media"):
        build_generated_take_capture_sidecar(
            {"shot_id": "shot_001", "take": {"take_id": "take_001"}},
            media={
                "type": ASSET_TYPE_VIDEO,
                "thumbnail": "data:image/png;base64,AAAA",
            },
        )

    with pytest.raises(GeneratedCaptureError, match="embedded media"):
        build_generated_take_capture_sidecar(
            {
                "shot_id": "shot_001",
                "asset": {"metadata": {"waveform": [0.0, 1.0]}},
                "take": {"take_id": "take_001"},
            },
            media={"type": ASSET_TYPE_VIDEO},
        )


def test_runtime_take_registration_metadata_pairs_with_generated_capture_sidecar():
    runtime_registration = build_take_capture_metadata(
        {
            "type": "LTX_TIMELINE_PLAN",
            "project": {
                "privacy": {"mode": False},
                "global_prompt": {"prompt": "wide establishing shot"},
            },
            "resolved_output": {
                "width": 768,
                "height": 432,
                "frame_rate": 24.0,
                "frame_count": 49,
                "duration_seconds": 2.0,
            },
            "prompt_plan": [
                {"item_id": "section_001", "type": "Text", "runtime_prompt": "wide establishing shot"}
            ],
            "section_plan": [
                {
                    "item_id": "section_001",
                    "type": "Text",
                    "start_time": 0.0,
                    "end_time": 2.0,
                    "start_frame": 0,
                    "end_frame_exclusive": 49,
                    "frame_count": 49,
                }
            ],
            "model_specific": {
                "ltx": {
                    "shot_context": {
                        "shot_id": "shot_001",
                        "duration_seconds": 2.0,
                        "section_ids": ["section_001"],
                    }
                }
            },
        },
        model_key="ltx",
        model_family="LTX",
        model_version="2.3",
        source="LTX Runtime",
        expected_asset_type=ASSET_TYPE_VIDEO,
        seed=42,
    )

    sidecar = build_generated_take_capture_sidecar(
        runtime_registration,
        media={
            "type": ASSET_TYPE_VIDEO,
            "filename": "shot_001_take.mp4",
            "mime_type": "video/mp4",
            "frame_rate": 24.0,
            "frame_count": 49,
            "duration_seconds": 2.0,
            "width": 768,
            "height": 432,
        },
    )
    registration = generated_take_capture_to_registration(
        sidecar,
        path="/tmp/outputs/shot_001_take.mp4",
    )
    result = register_generated_take(_timeline_with_shot(), registration)
    timeline = result["timeline"]
    asset = timeline["assets"][0]
    take = _shot(timeline)["takes"][0]

    assert sidecar["registration"]["shot_id"] == "shot_001"
    assert asset["metadata"]["width"] == 768
    assert asset["metadata"]["height"] == 432
    assert take["seed"] == 42
    assert take["model_family"] == "LTX"
    assert take["model_version"] == "2.3"
    assert validate_video_timeline(timeline)["is_valid"] is True
