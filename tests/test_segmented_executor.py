from pathlib import Path

import torch

from shared.privacy import CRYPTO_AVAILABLE
from shared.segmented_executor import (
    SegmentSpillStore,
    post_decode_memory_cleanup,
    segment_seed,
    stitch_segment_images,
    stitch_spilled_segment_images,
    trim_visible_segment_images,
)
from shared.timeline_status import TimelineStatusReporter
import shared.wan.runtime.segmented as wan_segmented
import shared.wan.runtime.runtime as wan_runtime
import shared.wan.runtime.visual as wan_visual
from shared.timeline.segmentation import build_generation_segments


def test_segment_seed_modes_increment_or_reuse():
    assert segment_seed(10, 0, "Increment Per Segment") == 10
    assert segment_seed(10, 3, "Increment Per Segment") == 13
    assert segment_seed(10, 3, "Reuse Seed") == 10


def test_timeline_status_reporter_records_progress_text_and_safe_events():
    progress_calls = []
    text_calls = []
    event_calls = []

    class FakeProgressBar:
        def __init__(self, total, node_id=None):
            progress_calls.append(("init", total, node_id))

        def update_absolute(self, value, total=None):
            progress_calls.append(("update", value, total))

    reporter = TimelineStatusReporter(
        model="wan",
        node_id="123",
        total=2,
        progress_bar_factory=FakeProgressBar,
        text_sender=lambda label, node_id: text_calls.append((label, node_id)),
        event_sender=lambda payload: event_calls.append(payload),
    )

    reporter.report(
        "timeline.spill",
        "WAN Executor: segment 1/2 - saving encrypted segment frames",
        segment_index=1,
        segment_count=2,
        frame_count=79,
        encrypted_spill=True,
        path="/tmp/private-frame-cache.pt",
        prompt="secret prompt",
    )
    reporter.done()

    events = reporter.snapshot()
    assert progress_calls == [
        ("init", 2, "123"),
        ("update", 1, 2),
        ("update", 2, 2),
    ]
    assert text_calls[0] == ("WAN Executor: segment 1/2 - saving encrypted segment frames", "123")
    assert event_calls[0]["node_id"] == "123"
    assert event_calls[0]["stage"] == "timeline.spill"
    assert event_calls[0]["label"] == "WAN Executor: segment 1/2 - saving encrypted segment frames"
    assert event_calls[0]["encrypted_spill"] is True
    assert "path" not in event_calls[0]
    assert "prompt" not in event_calls[0]
    assert events[0]["stage"] == "timeline.spill"
    assert events[0]["encrypted_spill"] is True
    assert "path" not in events[0]
    assert "prompt" not in events[0]


def _wan_frame_rule(requested):
    requested = max(1, int(requested))
    return ((requested - 1 + 3) // 4) * 4 + 1


def test_generation_segments_ignore_model_padding_for_segment_count():
    segmented = build_generation_segments(
        section_entries=[
            {"item_id": "section_001", "start_frame": 0, "end_frame_exclusive": 161},
        ],
        frame_rate=16.0,
        total_frames=161,
        requested_frame_count=160,
        max_generation_duration=5.0,
        segment_continuity_tail_frames=5,
        temporal_stride=4,
        model="wan",
        frame_rule=_wan_frame_rule,
    )

    assert segmented["enabled"] is True
    assert [segment["visible_frame_count"] for segment in segmented["segments"]] == [80, 81]


def test_generation_segments_absorb_padding_in_last_segment():
    segmented = build_generation_segments(
        section_entries=[
            {"item_id": "section_001", "start_frame": 0, "end_frame_exclusive": 25},
        ],
        frame_rate=8.0,
        total_frames=25,
        requested_frame_count=24,
        max_generation_duration=1.0,
        segment_continuity_tail_frames=5,
        temporal_stride=4,
        model="wan",
        frame_rule=_wan_frame_rule,
    )

    assert segmented["enabled"] is True
    assert [segment["visible_frame_count"] for segment in segmented["segments"]] == [8, 8, 9]


def test_generation_segments_prefers_nearby_natural_boundary_without_extra_segment():
    segmented = build_generation_segments(
        section_entries=[
            {"item_id": "image_001", "start_frame": 0, "end_frame_exclusive": 79},
            {"item_id": "text_001", "start_frame": 79, "end_frame_exclusive": 120},
            {"item_id": "text_002", "start_frame": 120, "end_frame_exclusive": 161},
        ],
        frame_rate=16.0,
        total_frames=161,
        requested_frame_count=160,
        max_generation_duration=5.0,
        segment_continuity_tail_frames=5,
        temporal_stride=4,
        model="wan",
        frame_rule=_wan_frame_rule,
    )

    assert [segment["visible_frame_count"] for segment in segmented["segments"]] == [79, 82]
    assert segmented["segments"][0]["split_reason"] == "natural_boundary"
    assert segmented["segments"][1]["continuity"]["continuity_frame_count"] == 5
    assert segmented["segments"][1]["trim_leading_frames"] == 5


def test_generation_segments_support_configurable_short_tail():
    segmented = build_generation_segments(
        section_entries=[
            {"item_id": "section_001", "start_frame": 0, "end_frame_exclusive": 25},
        ],
        frame_rate=8.0,
        total_frames=25,
        requested_frame_count=24,
        max_generation_duration=1.0,
        segment_continuity_tail_frames=1,
        temporal_stride=4,
        model="wan",
        frame_rule=_wan_frame_rule,
    )

    assert segmented["segments"][1]["continuity"]["continuity_frame_count"] == 1
    assert segmented["segments"][1]["trim_leading_frames"] == 1


def test_generation_segments_clamp_tail_to_previous_visible_segment():
    segmented = build_generation_segments(
        section_entries=[
            {"item_id": "section_001", "start_frame": 0, "end_frame_exclusive": 2},
            {"item_id": "section_002", "start_frame": 2, "end_frame_exclusive": 25},
        ],
        frame_rate=8.0,
        total_frames=25,
        requested_frame_count=24,
        max_generation_duration=0.25,
        segment_continuity_tail_frames=5,
        temporal_stride=4,
        model="wan",
        frame_rule=_wan_frame_rule,
    )

    second = segmented["segments"][1]
    assert segmented["segments"][0]["visible_frame_count"] == 2
    assert second["continuity"]["continuity_frame_count"] == 2
    assert second["trim_leading_frames"] == 2
    assert second["generation_frame_count"] == _wan_frame_rule(second["visible_frame_count"] + 2)


def test_stitch_segment_images_trims_seam_frames_and_clamps_to_target():
    first = torch.zeros((5, 2, 2, 3), dtype=torch.float32)
    second = torch.ones((6, 2, 2, 3), dtype=torch.float32)
    stitched = stitch_segment_images(
        [
            {
                "segment": {"visible_frame_count": 5, "trim_leading_frames": 0, "trim_trailing_frames": 0},
                "images": first,
            },
            {
                "segment": {"visible_frame_count": 4, "trim_leading_frames": 1, "trim_trailing_frames": 1},
                "images": second,
            },
        ],
        final_frame_count=9,
    )

    assert stitched.shape == (9, 2, 2, 3)
    assert torch.equal(stitched[:5], first)
    assert torch.equal(stitched[5:], second[1:5])


def test_stitch_segment_images_trims_configured_preroll_frames():
    first = torch.zeros((5, 2, 2, 3), dtype=torch.float32)
    second = torch.arange(9 * 2 * 2 * 3, dtype=torch.float32).reshape(9, 2, 2, 3)
    stitched = stitch_segment_images(
        [
            {
                "segment": {"visible_frame_count": 5, "trim_leading_frames": 0, "trim_trailing_frames": 0},
                "images": first,
            },
            {
                "segment": {"visible_frame_count": 4, "trim_leading_frames": 5, "trim_trailing_frames": 0},
                "images": second,
            },
        ],
        final_frame_count=9,
    )

    assert torch.equal(stitched[5:], second[5:9])


def test_wan_visual_prefers_continuation_tail_over_stale_start_keyframe(monkeypatch):
    calls = []

    def fake_execute_helper(*args):
        calls.append(args)
        return "positive", "negative", {"samples": torch.zeros((1, 48, 2, 2, 2))}, "FakeHelper"

    monkeypatch.setattr(wan_visual, "execute_comfy_core_visual_helper", fake_execute_helper)
    tail = torch.ones((5, 8, 8, 3), dtype=torch.float32)

    _positive, _negative, _latent, debug = wan_visual.apply_comfy_core_visual_keyframes(
        "positive",
        "negative",
        vae=object(),
        visual={
            "continuation_source": "previous_tail",
            "transient_start_image": tail,
            "applied_keyframes": [
                {
                    "role": "Start",
                    "section_id": "original_image",
                    "asset_id": "image_001",
                    "path": "/mnt/media/woman.png",
                }
            ],
            "unsupported_keyframes": [],
        },
        width=8,
        height=8,
        frame_count=13,
        batch_size=1,
        latent_spec={},
        model_mode="I2V-A14B",
    )

    assert calls[0][7] is tail
    assert calls[0][8] is None
    assert debug["applied_keyframes"] == [{"role": "Start", "section_id": "segment_previous_tail", "transient": True}]
    assert debug["media_decisions"][0]["section_id"] == "segment_previous_tail"
    assert any("overrode copied visual keyframes" in item for item in debug["diagnostics"])


def test_wan_i2v_validation_accepts_previous_tail_start_conditioning():
    validation_entries = []
    tail = torch.ones((5, 8, 8, 3), dtype=torch.float32)

    wan_runtime._validate_comfy_core_visual_requirements(
        {"model_mode": "I2V-A14B"},
        {
            "continuation_source": "previous_tail",
            "transient_start_image": tail,
            "applied_keyframes": [],
        },
        validation_entries,
    )

    assert [entry["code"] for entry in validation_entries] == ["WAN_CONTINUATION_TAIL_IMAGE_CONDITIONING"]


def test_wan_i2v_validation_still_rejects_missing_image_conditioning():
    validation_entries = []

    try:
        wan_runtime._validate_comfy_core_visual_requirements(
            {"model_mode": "I2V-A14B"},
            {"applied_keyframes": []},
            validation_entries,
        )
    except ValueError as exc:
        assert "WAN_REQUIRED_IMAGE_CONDITIONING_MISSING" in str(exc)
    else:
        raise AssertionError("Expected WAN I2V validation to reject missing image conditioning.")

    assert [entry["code"] for entry in validation_entries] == ["WAN_REQUIRED_IMAGE_CONDITIONING_MISSING"]


def test_segment_spill_store_plain_round_trips_and_cleans_up(tmp_path):
    store = SegmentSpillStore(privacy_mode=False, root=tmp_path)
    tensor = torch.arange(36, dtype=torch.float32).reshape(3, 2, 2, 3)

    record = store.write_segment({"id": "gen_001"}, tensor)
    path = Path(record["path"])
    loaded = store.read_segment(record)
    summary = store.cleanup()

    assert torch.equal(loaded, tensor)
    assert path.exists() is False
    assert store.root.exists() is False
    assert summary["encrypted"] is False
    assert summary["files_written"] == 1
    assert summary["files_read"] == 1
    assert summary["files_deleted"] == 1


def test_segment_spill_store_encrypted_round_trips_without_plaintext(tmp_path):
    if not CRYPTO_AVAILABLE:
        return
    store = SegmentSpillStore(privacy_mode=True, root=tmp_path)
    tensor = torch.arange(36, dtype=torch.float32).reshape(3, 2, 2, 3)

    record = store.write_segment({"id": "secret_segment"}, tensor)
    path = Path(record["path"])
    encrypted_text = path.read_text(encoding="utf-8")
    loaded = store.read_segment(record)
    summary = store.cleanup()

    assert torch.equal(loaded, tensor)
    assert "secret_segment" not in encrypted_text
    assert "tensor" not in encrypted_text
    assert encrypted_text.startswith("{")
    assert summary["encrypted"] is True
    assert "path" not in summary["records"][0]


def test_stitch_spilled_segments_loads_sequentially_and_clamps(tmp_path):
    store = SegmentSpillStore(privacy_mode=False, root=tmp_path)
    first = torch.zeros((5, 2, 2, 3), dtype=torch.float32)
    second = torch.ones((6, 2, 2, 3), dtype=torch.float32)
    records = [
        store.write_segment({"id": "gen_001"}, first),
        store.write_segment({"id": "gen_002"}, second),
    ]

    stitched = stitch_spilled_segment_images(records, store, final_frame_count=9)
    summary = store.cleanup()

    assert stitched.shape == (9, 2, 2, 3)
    assert torch.equal(stitched[:5], first)
    assert torch.equal(stitched[5:], second[:4])
    assert summary["files_read"] == 2
    assert summary["files_deleted"] == 2


def test_trim_visible_segment_images_matches_legacy_stitch_trimming():
    images = torch.arange(6 * 2 * 2 * 3, dtype=torch.float32).reshape(6, 2, 2, 3)
    segment = {"visible_frame_count": 4, "trim_leading_frames": 1, "trim_trailing_frames": 1}

    assert torch.equal(trim_visible_segment_images(images, segment), images[1:5])


def test_segment_spill_cleanup_after_stitch_failure(tmp_path):
    store = SegmentSpillStore(privacy_mode=False, root=tmp_path)
    first = torch.zeros((2, 2, 2, 3), dtype=torch.float32)
    second = torch.ones((2, 3, 2, 3), dtype=torch.float32)
    records = [
        store.write_segment({"id": "gen_001"}, first),
        store.write_segment({"id": "gen_bad"}, second),
    ]

    try:
        stitch_spilled_segment_images(records, store, final_frame_count=4)
    except ValueError as exc:
        assert "gen_bad" in str(exc)
    else:
        raise AssertionError("Expected stitch to fail for mismatched frame shapes.")
    summary = store.cleanup()

    assert summary["files_deleted"] == 2
    assert store.root.exists() is False


def test_post_decode_memory_cleanup_reports_event():
    event = post_decode_memory_cleanup("post_decode_test")

    assert event["stage"] == "post_decode_test"
    assert event["attempted"] is True
    assert "warnings" in event


def test_wan_two_phase_sampling_requires_high_and_low_models():
    latent = {"samples": torch.zeros((1, 16, 3, 2, 2))}

    try:
        wan_segmented.sample_wan_segment_latent(
            high_noise_model=object(),
            low_noise_model=None,
            positive=[],
            negative=[],
            latent=latent,
            model_mode="I2V-A14B",
            seed=1,
            steps=20,
            cfg=5.0,
            sampler_name="euler",
            scheduler="normal",
            denoise=1.0,
            phase_split_step=10,
        )
    except ValueError as exc:
        assert "requires both high_noise_model and low_noise_model" in str(exc)
    else:
        raise AssertionError("Expected WAN two-phase sampling to require both models.")


def test_wan_two_phase_sampling_runs_high_then_low(monkeypatch):
    calls = []

    def fake_sample_latent(**kwargs):
        calls.append(kwargs)
        output = dict(kwargs["latent"])
        output["samples"] = kwargs["latent"]["samples"] + len(calls)
        return output

    monkeypatch.setattr(wan_segmented, "sample_latent", fake_sample_latent)
    latent = {"samples": torch.zeros((1, 16, 3, 2, 2))}
    high = object()
    low = object()
    reporter = TimelineStatusReporter(model="wan", total=4, emit_ui=False)

    sampled, debug = wan_segmented.sample_wan_segment_latent(
        high_noise_model=high,
        low_noise_model=low,
        positive=[["positive"]],
        negative=[["negative"]],
        latent=latent,
        model_mode="Bernini-A14B",
        seed=42,
        steps=20,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        phase_split_step=10,
        status_reporter=reporter,
        segment_index=1,
        segment_count=2,
    )

    assert sampled["samples"].mean().item() == 3.0
    assert [event["stage"] for event in reporter.snapshot()] == ["wan.sample.high_noise", "wan.sample.low_noise"]
    assert debug["sampling_policy"] == "two_phase"
    assert debug["phase_split_step"] == 10
    assert debug["split_step"] == 10
    assert calls[0]["model"] is high
    assert calls[0]["start_step"] == 0
    assert calls[0]["last_step"] == 10
    assert calls[0]["force_full_denoise"] is False
    assert calls[1]["model"] is low
    assert calls[1]["latent"]["samples"].mean().item() == 1.0
    assert calls[1]["disable_noise"] is True
    assert calls[1]["start_step"] == 10
    assert calls[1]["last_step"] == 20
    assert calls[1]["force_full_denoise"] is True


def test_wan_two_phase_split_step_fallback_uses_half_rounded_down(monkeypatch):
    calls = []

    def fake_sample_latent(**kwargs):
        calls.append(kwargs)
        return kwargs["latent"]

    monkeypatch.setattr(wan_segmented, "sample_latent", fake_sample_latent)

    _sampled, debug = wan_segmented.sample_wan_segment_latent(
        high_noise_model=object(),
        low_noise_model=object(),
        positive=[],
        negative=[],
        latent={"samples": torch.zeros((1, 16, 3, 2, 2))},
        model_mode="I2V-A14B",
        seed=1,
        steps=21,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        phase_split_step=0.5,
    )

    assert debug["phase_split_step"] == 10
    assert debug["split_step"] == 10
    assert calls[0]["last_step"] == 10
    assert calls[1]["start_step"] == 10


def test_wan_two_phase_split_step_clamps_to_valid_range(monkeypatch):
    calls = []

    def fake_sample_latent(**kwargs):
        calls.append(kwargs)
        return kwargs["latent"]

    monkeypatch.setattr(wan_segmented, "sample_latent", fake_sample_latent)

    _sampled, low_debug = wan_segmented.sample_wan_segment_latent(
        high_noise_model=object(),
        low_noise_model=object(),
        positive=[],
        negative=[],
        latent={"samples": torch.zeros((1, 16, 3, 2, 2))},
        model_mode="I2V-A14B",
        seed=1,
        steps=12,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        phase_split_step=0,
    )
    _sampled, high_debug = wan_segmented.sample_wan_segment_latent(
        high_noise_model=object(),
        low_noise_model=object(),
        positive=[],
        negative=[],
        latent={"samples": torch.zeros((1, 16, 3, 2, 2))},
        model_mode="I2V-A14B",
        seed=1,
        steps=12,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        phase_split_step=99,
    )

    assert low_debug["split_step"] == 1
    assert high_debug["split_step"] == 11


def test_wan_ti2v_5b_uses_single_phase_sampling(monkeypatch):
    calls = []

    def fake_sample_latent(**kwargs):
        calls.append(kwargs)
        return kwargs["latent"]

    monkeypatch.setattr(wan_segmented, "sample_latent", fake_sample_latent)
    latent = {"samples": torch.zeros((1, 16, 3, 2, 2))}
    high = object()
    reporter = TimelineStatusReporter(model="wan", total=2, emit_ui=False)

    _sampled, debug = wan_segmented.sample_wan_segment_latent(
        high_noise_model=high,
        low_noise_model=None,
        positive=[],
        negative=[],
        latent=latent,
        model_mode="TI2V-5B",
        seed=7,
        steps=12,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        phase_split_step=6,
        status_reporter=reporter,
        segment_index=1,
        segment_count=1,
    )

    assert debug["sampling_policy"] == "single_phase"
    assert reporter.snapshot()[0]["stage"] == "timeline.sample"
    assert len(calls) == 1
    assert calls[0]["model"] is high
    assert calls[0]["force_full_denoise"] is True
    assert debug["phase_split_step"] is None
    assert debug["split_step"] is None


def test_wan_two_phase_vram_unload_policy_records_events(monkeypatch):
    unload_calls = []

    def fake_sample_latent(**kwargs):
        return kwargs["latent"]

    def fake_unload(stage, role, model):
        unload_calls.append((stage, role, model))
        return {"stage": stage, "role": role, "attempted": True, "success": True}

    monkeypatch.setattr(wan_segmented, "sample_latent", fake_sample_latent)
    monkeypatch.setattr(wan_segmented, "_unload_model", fake_unload)
    latent = {"samples": torch.zeros((1, 16, 3, 2, 2))}
    high = object()
    low = object()
    reporter = TimelineStatusReporter(model="wan", total=6, emit_ui=False)

    _sampled, debug = wan_segmented.sample_wan_segment_latent(
        high_noise_model=high,
        low_noise_model=low,
        positive=[],
        negative=[],
        latent=latent,
        model_mode="T2V-A14B",
        seed=7,
        steps=12,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        phase_split_step=6,
        vram_unload_policy="Between High Low And Decode",
        status_reporter=reporter,
        segment_index=1,
        segment_count=1,
    )

    assert unload_calls == [
        ("between_high_low", "high_noise_model", high),
        ("before_decode", "low_noise_model", low),
    ]
    assert [event["stage"] for event in debug["unload_events"]] == ["between_high_low", "before_decode"]
    assert [event["stage"] for event in reporter.snapshot()] == [
        "wan.sample.high_noise",
        "wan.vram.unload",
        "wan.sample.low_noise",
        "wan.vram.unload",
    ]


def test_wan_segmented_executor_debug_includes_status_events(monkeypatch, tmp_path):
    def fake_build_runtime_outputs(**_kwargs):
        runtime_debug = {
            "wan": {
                "visual_conditioning": {
                    "requested_keyframes": [],
                    "applied_keyframes": [],
                    "media_decisions": [],
                },
                "bernini": None,
            },
            "summary": {},
        }
        return object(), object(), [], [], {"samples": torch.zeros((1, 16, 3, 2, 2))}, runtime_debug

    def fake_sample_wan_segment_latent(**kwargs):
        if kwargs.get("status_reporter") is not None:
            kwargs["status_reporter"].report(
                "wan.sample.high_noise",
                "WAN Executor: segment 1/1 - high-noise sampling",
                segment_index=kwargs.get("segment_index"),
                segment_count=kwargs.get("segment_count"),
            )
            kwargs["status_reporter"].report(
                "wan.sample.low_noise",
                "WAN Executor: segment 1/1 - low-noise sampling",
                segment_index=kwargs.get("segment_index"),
                segment_count=kwargs.get("segment_count"),
            )
        return kwargs["latent"], {"sampling_policy": "two_phase", "unload_events": []}

    monkeypatch.setattr(wan_segmented, "build_wan_runtime_outputs", fake_build_runtime_outputs)
    monkeypatch.setattr(wan_segmented, "sample_wan_segment_latent", fake_sample_wan_segment_latent)
    monkeypatch.setattr(wan_segmented, "decode_latent_images", lambda _vae, _latent: torch.ones((5, 2, 2, 3)))
    monkeypatch.setattr(wan_segmented, "post_decode_memory_cleanup", lambda stage: {"stage": stage, "attempted": True, "success": True, "warnings": []})
    monkeypatch.setattr(wan_segmented, "mix_timeline_audio", lambda _plan: ({"waveform": torch.zeros((1, 2, 1)), "sample_rate": 44100}, []))
    monkeypatch.setattr(
        wan_segmented,
        "SegmentSpillStore",
        lambda privacy_mode: SegmentSpillStore(privacy_mode=privacy_mode, root=tmp_path),
    )

    plan = {
        "resolved_output": {"frame_count": 5, "frame_rate": 8.0},
        "project": {"privacy": {"mode": False}},
        "section_plan": [{"item_id": "section_001", "type": "Text", "start_frame": 0, "end_frame_exclusive": 5, "frame_count": 5}],
        "prompt_plan": [{"item_id": "section_001", "type": "Text", "raw_prompt": "prompt"}],
        "media_plan": [],
        "audio_plan": [],
        "model_specific": {
            "wan": {
                "config": {
                    "model_mode": "I2V-A14B",
                    "prompt_routing": "Prompt Relay",
                    "prompt_relay_epsilon": 0.15,
                    "vram_unload_policy": "Off",
                },
                "segmented_generation": {
                    "enabled": True,
                    "segments": [
                        {
                            "id": "gen_001",
                            "index": 0,
                            "start_frame": 0,
                            "end_frame_exclusive": 5,
                            "visible_frame_count": 5,
                            "generation_frame_count": 5,
                            "trim_leading_frames": 0,
                            "trim_trailing_frames": 0,
                            "continuity": {"mode": "initial"},
                        }
                    ],
                },
            }
        },
    }

    _images, _audio, _fps, debug = wan_segmented.build_wan_segmented_executor_outputs(
        high_noise_model=object(),
        low_noise_model=object(),
        clip=object(),
        vae=object(),
        wan_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Increment Per Segment",
    )

    stages = [event["stage"] for event in debug["status_events"]]
    assert "timeline.conditioning" in stages
    assert "wan.sample.high_noise" in stages
    assert "wan.sample.low_noise" in stages
    assert "timeline.decode" in stages
    assert "timeline.spill" in stages
    assert "timeline.cleanup" in stages
    assert stages[-3:] == ["timeline.stitch", "timeline.audio", "timeline.done"]
