import json
from pathlib import Path

import torch

import shared.privacy as privacy
from shared.contracts.video_timeline import ASSET_SOURCE_FILE_PATH, ASSET_TYPE_IMAGE, MODEL_LORA_TARGET_HIGH_NOISE, SECTION_TYPE_IMAGE, SECTION_TYPE_TEXT
from shared.privacy import BYTE_CHUNKED_ENVELOPE_SCHEMA, CRYPTO_AVAILABLE
from shared.segmented_executor import (
    SegmentSpillStore,
    blend_segment_seam,
    build_segment_plan,
    external_sigmas_step_count,
    post_decode_memory_cleanup,
    sample_latent,
    segment_seam_blend_frames,
    segment_seed,
    stitch_segment_images,
    stitch_spilled_segment_images,
    trim_visible_segment_images,
)
from shared.timeline import apply_take_registration, create_default_video_timeline, validate_video_timeline
from shared.timeline_status import TimelineStatusReporter
import shared.wan.runtime.segmented as wan_segmented
import shared.wan.runtime.runtime as wan_runtime
import shared.wan.runtime.visual as wan_visual
from shared.timeline import GENERATION_MODE_FORCE_FULL_TIMELINE
from shared.wan import build_wan_timeline_plan, create_wan_timeline_config
import shared.audio as shared_audio
import shared.ltx.runtime.segmented as ltx_segmented
from shared.ltx.references import LTX_HIDDEN_REFERENCE_GUARD_LATENT_FRAMES
from shared.timeline.segmentation import build_generation_segments


def test_segment_seed_modes_increment_or_reuse():
    assert segment_seed(10, 0, "Increment Per Segment") == 10
    assert segment_seed(10, 3, "Increment Per Segment") == 13
    assert segment_seed(10, 3, "Reuse Seed") == 10


def test_segment_seam_blend_frame_options_normalize():
    assert segment_seam_blend_frames("0") == 0
    assert segment_seam_blend_frames("3") == 3
    assert segment_seam_blend_frames("5") == 5
    assert segment_seam_blend_frames("4") == 3
    assert segment_seam_blend_frames("bad") == 3


def test_sample_latent_passes_connected_sigmas_to_comfy_sampler(monkeypatch):
    import sys
    import types

    calls = {}
    callback_steps = []
    sigmas = torch.tensor([1.0, 0.6, 0.0], dtype=torch.float32)
    latent = {"samples": torch.zeros((1, 16, 3, 1, 1), dtype=torch.float32)}

    comfy_module = types.ModuleType("comfy")
    comfy_sample_module = types.ModuleType("comfy.sample")
    comfy_utils_module = types.ModuleType("comfy.utils")
    latent_preview_module = types.ModuleType("latent_preview")
    comfy_module.sample = comfy_sample_module
    comfy_module.utils = comfy_utils_module
    comfy_sample_module.fix_empty_latent_channels = lambda _model, samples, _spatial, _temporal: samples
    comfy_sample_module.prepare_noise = lambda latent_image, _seed, _batch_inds: torch.zeros_like(latent_image)
    comfy_utils_module.PROGRESS_BAR_ENABLED = True
    latent_preview_module.prepare_callback = lambda _model, steps: callback_steps.append(steps) or "callback"
    monkeypatch.setitem(sys.modules, "comfy", comfy_module)
    monkeypatch.setitem(sys.modules, "comfy.sample", comfy_sample_module)
    monkeypatch.setitem(sys.modules, "comfy.utils", comfy_utils_module)
    monkeypatch.setitem(sys.modules, "latent_preview", latent_preview_module)

    def fake_sample(
        model,
        noise,
        steps,
        cfg,
        sampler_name,
        scheduler,
        positive,
        negative,
        latent_image,
        **kwargs,
    ):
        calls.update(
            {
                "model": model,
                "noise": noise,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "positive": positive,
                "negative": negative,
                "latent_image": latent_image,
                **kwargs,
            }
        )
        return latent_image + 1

    comfy_sample_module.sample = fake_sample

    output = sample_latent(
        model=object(),
        positive=[["positive"]],
        negative=[["negative"]],
        latent=latent,
        seed=123,
        steps=20,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        sigmas=sigmas,
    )

    assert callback_steps == [2]
    assert calls["steps"] == 2
    assert calls["sigmas"] is sigmas
    assert calls["scheduler"] == "normal"
    assert torch.equal(output["samples"], torch.ones_like(latent["samples"]))


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


def test_timeline_status_reporter_emits_helto_progress_with_safe_details():
    progress = _CaptureHeltoProgress()
    reporter = TimelineStatusReporter(
        model="wan",
        node_id="123",
        total=2,
        emit_ui=False,
        helto_progress_sender=progress,
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
    reporter.done("WAN Executor: done")

    assert [call["event"] for call in progress.calls] == ["start", "done"]
    first = progress.calls[0]
    assert first["message"] == "WAN Executor: segment 1/2 - saving encrypted segment frames"
    assert first["kwargs"]["phase"] == "timeline.spill"
    assert first["kwargs"]["value"] == 1
    assert first["kwargs"]["total"] == 2
    assert first["kwargs"]["node_id"] == "123"
    assert first["kwargs"]["detail"]["model"] == "wan"
    assert first["kwargs"]["detail"]["segment_index"] == 1
    assert first["kwargs"]["detail"]["encrypted_spill"] is True
    assert "path" not in first["kwargs"]["detail"]
    assert "prompt" not in first["kwargs"]["detail"]
    assert progress.calls[-1]["kwargs"]["phase"] == "timeline.done"
    assert progress.calls[-1]["kwargs"]["value"] == 2


def test_timeline_status_reporter_noops_when_helto_progress_is_unavailable():
    reporter = TimelineStatusReporter(model="wan", node_id="123", total=1, emit_ui=False)

    reporter.report("timeline.prepare", "WAN Runtime: resolving backend")
    reporter.done("WAN Runtime: ready")

    assert [event["stage"] for event in reporter.snapshot()] == ["timeline.prepare", "timeline.done"]


def test_timeline_status_reporter_emits_error_without_swallowing_original_exception():
    progress = _CaptureHeltoProgress()
    reporter = TimelineStatusReporter(
        model="wan",
        node_id="123",
        total=2,
        emit_ui=False,
        helto_progress_sender=progress,
    )

    caught = None
    try:
        try:
            raise RuntimeError("original failure")
        except RuntimeError:
            reporter.error("WAN Runtime: failed")
            raise
    except RuntimeError as exc:
        caught = exc
    assert str(caught) == "original failure"
    assert progress.calls[0]["event"] == "error"
    assert progress.calls[0]["message"] == "WAN Runtime: failed"
    assert progress.calls[0]["kwargs"]["phase"] == "timeline.error"


class _CaptureHeltoProgress:
    def __init__(self):
        self.calls = []

    def start(self, message, **kwargs):
        self.calls.append({"event": "start", "message": message, "kwargs": kwargs})

    def update(self, message, **kwargs):
        self.calls.append({"event": "update", "message": message, "kwargs": kwargs})

    def done(self, message, **kwargs):
        self.calls.append({"event": "done", "message": message, "kwargs": kwargs})

    def error(self, message, **kwargs):
        self.calls.append({"event": "error", "message": message, "kwargs": kwargs})


def _wan_frame_rule(requested):
    requested = max(1, int(requested))
    return ((requested - 1 + 3) // 4) * 4 + 1


def test_wan_latent_slot_for_frame_uses_first_frame_then_four_frame_cells():
    assert wan_segmented._latent_slot_for_frame(0) == 0
    assert [wan_segmented._latent_slot_for_frame(frame) for frame in range(1, 5)] == [1, 1, 1, 1]
    assert [wan_segmented._latent_slot_for_frame(frame) for frame in range(117, 121)] == [30, 30, 30, 30]


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


def test_segment_plan_preserves_transient_boundary_media_in_active_segment():
    plan = _two_segment_executor_plan("ltx")
    plan["media_plan"] = [
        {
            "item_id": "section_001",
            "section_type": "Image",
            "path": "/tmp/section.png",
        },
        {
            "item_id": "boundary_tail_boundary_001",
            "section_type": "Video",
            "path": "/tmp/previous.mp4",
            "transient": True,
            "insert_frame": 0,
            "boundary_id": "boundary_001",
        },
    ]
    first_segment = plan["model_specific"]["ltx"]["segmented_generation"]["segments"][0]

    segment_plan = build_segment_plan(plan, first_segment, model_key="ltx")

    assert [entry["item_id"] for entry in segment_plan["media_plan"]] == [
        "section_001",
        "boundary_tail_boundary_001",
    ]
    boundary = segment_plan["media_plan"][1]
    assert boundary["transient"] is True
    assert boundary["insert_frame"] == 0
    assert boundary["boundary_id"] == "boundary_001"


def test_segment_plan_omits_transient_boundary_media_outside_segment():
    plan = _two_segment_executor_plan("ltx")
    plan["media_plan"] = [
        {
            "item_id": "boundary_tail_boundary_001",
            "section_type": "Video",
            "path": "/tmp/previous.mp4",
            "transient": True,
            "insert_frame": 0,
            "boundary_id": "boundary_001",
        },
        {
            "item_id": "section_002",
            "section_type": "Image",
            "path": "/tmp/section.png",
        },
    ]
    second_segment = plan["model_specific"]["ltx"]["segmented_generation"]["segments"][1]

    segment_plan = build_segment_plan(plan, second_segment, model_key="ltx")

    assert [entry["item_id"] for entry in segment_plan["media_plan"]] == ["section_002"]


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
        config={"painter_motion_boost": "Auto", "painter_motion_amplitude": 1.4},
    )

    assert calls[0][7] is tail
    assert calls[0][8] is None
    assert debug["applied_keyframes"] == [{"role": "Start", "section_id": "segment_previous_tail", "transient": True}]
    assert debug["media_decisions"][0]["section_id"] == "segment_previous_tail"
    assert debug["painter_motion_boost"]["input_frame_count"] == 5
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


def test_segment_spill_store_encrypted_round_trips_without_plaintext(tmp_path, unlocked_privacy_keystore):
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


def test_segment_spill_store_encrypted_chunked_round_trips(
    monkeypatch,
    tmp_path,
    unlocked_privacy_keystore,
):
    if not CRYPTO_AVAILABLE:
        return
    monkeypatch.setattr(privacy, "BYTE_CHUNK_SIZE", 64)
    store = SegmentSpillStore(privacy_mode=True, root=tmp_path)
    tensor = torch.arange(240, dtype=torch.float32).reshape(5, 4, 4, 3)

    record = store.write_segment({"id": "secret_chunked_segment"}, tensor)
    path = Path(record["path"])
    encrypted = json.loads(path.read_text(encoding="utf-8"))
    loaded = store.read_segment(record)
    summary = store.cleanup()

    assert encrypted["schema"] == BYTE_CHUNKED_ENVELOPE_SCHEMA
    assert len(encrypted["chunks"]) > 1
    assert torch.equal(loaded, tensor)
    assert "secret_chunked_segment" not in json.dumps(encrypted)
    assert summary["encrypted"] is True


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


def test_blend_segment_seam_preserves_shape_and_softens_first_frames():
    previous = torch.zeros((5, 1, 1, 1), dtype=torch.float32)
    current = torch.ones((6, 1, 1, 1), dtype=torch.float32)

    blended, debug = blend_segment_seam(current, previous, 3)

    assert blended.shape == current.shape
    assert debug["status"] == "applied"
    assert debug["configured_frame_count"] == 3
    assert debug["actual_frame_count"] == 3
    assert torch.allclose(blended[:3, 0, 0, 0], torch.tensor([0.25, 0.5, 0.75]))
    assert torch.equal(blended[3:], current[3:])
    assert blended[0].sub(previous[-1]).abs().item() < blended[2].sub(previous[-1]).abs().item()


def test_blend_segment_seam_clamps_and_supports_disabled_mode():
    previous = torch.zeros((2, 1, 1, 1), dtype=torch.float32)
    current = torch.ones((4, 1, 1, 1), dtype=torch.float32)

    disabled, disabled_debug = blend_segment_seam(current, previous, 0)
    clamped, clamped_debug = blend_segment_seam(current, previous, 5)
    missing, missing_debug = blend_segment_seam(current, None, 3)

    assert torch.equal(disabled, current)
    assert disabled_debug["reason"] == "disabled"
    assert clamped_debug["actual_frame_count"] == 2
    assert torch.allclose(clamped[:2, 0, 0, 0], torch.tensor([1 / 3, 2 / 3], dtype=torch.float32))
    assert torch.equal(missing, current)
    assert missing_debug["reason"] == "missing_previous_tail"


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


def test_wan_segmented_executor_cleans_spill_on_base_exception(monkeypatch, tmp_path):
    abort = _SegmentAbort("interrupt after spill")

    def fake_build_runtime_outputs(**_kwargs):
        runtime_context = {
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
        return object(), object(), [], [], {"samples": torch.zeros((1, 16, 3, 1, 1))}, runtime_context

    monkeypatch.setattr(wan_segmented, "build_wan_runtime_outputs", fake_build_runtime_outputs)
    monkeypatch.setattr(wan_segmented, "sample_wan_segment_latent", lambda **kwargs: (kwargs["latent"], {"sampling_policy": "two_phase", "unload_events": []}))
    monkeypatch.setattr(wan_segmented, "decode_latent_images", lambda _vae, _latent: torch.zeros((5, 1, 1, 1), dtype=torch.float32))
    monkeypatch.setattr(wan_segmented, "post_decode_memory_cleanup", lambda _stage: (_ for _ in ()).throw(abort))
    monkeypatch.setattr(
        wan_segmented,
        "SegmentSpillStore",
        lambda privacy_mode: SegmentSpillStore(privacy_mode=privacy_mode, root=tmp_path),
    )

    try:
        wan_segmented.build_wan_segmented_executor_outputs(
            high_noise_model=object(),
            low_noise_model=object(),
            clip=object(),
            vae=object(),
            wan_timeline_plan=_two_segment_executor_plan("wan"),
            seed=1,
            steps=4,
            cfg=5.0,
            sampler_name="euler",
            scheduler="normal",
            denoise=1.0,
            seed_mode="Increment Per Segment",
        )
    except _SegmentAbort as exc:
        assert exc is abort
    else:
        raise AssertionError("Expected WAN segmented executor to propagate the interruption.")

    assert list(tmp_path.iterdir()) == []


def test_ltx_segmented_executor_cleans_spill_on_base_exception(monkeypatch, tmp_path):
    abort = _SegmentAbort("interrupt after spill")

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, lambda **_kwargs: _fake_ltx_runtime_result(hidden_reference_count=0))
    monkeypatch.setattr(ltx_segmented, "post_decode_memory_cleanup", lambda _stage: (_ for _ in ()).throw(abort))

    try:
        ltx_segmented.build_ltx_segmented_executor_outputs(
            model=object(),
            clip=object(),
            vae=object(),
            ltx_timeline_plan=_two_segment_executor_plan("ltx"),
            seed=1,
            steps=4,
            cfg=5.0,
            sampler_name="euler",
            scheduler="normal",
            denoise=1.0,
            seed_mode="Increment Per Segment",
        )
    except _SegmentAbort as exc:
        assert exc is abort
    else:
        raise AssertionError("Expected LTX segmented executor to propagate the interruption.")

    assert list(tmp_path.iterdir()) == []


def test_wan_segmented_executor_applies_seam_blend_after_trim(monkeypatch, tmp_path):
    decode_calls = {"count": 0}

    def fake_build_runtime_outputs(**_kwargs):
        runtime_context = {
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
        return object(), object(), [], [], {"samples": torch.zeros((1, 16, 3, 1, 1))}, runtime_context

    def fake_decode(_vae, _latent):
        decode_calls["count"] += 1
        if decode_calls["count"] == 1:
            return torch.zeros((5, 1, 1, 1), dtype=torch.float32)
        return torch.ones((5, 1, 1, 1), dtype=torch.float32)

    monkeypatch.setattr(wan_segmented, "build_wan_runtime_outputs", fake_build_runtime_outputs)
    monkeypatch.setattr(wan_segmented, "sample_wan_segment_latent", lambda **kwargs: (kwargs["latent"], {"sampling_policy": "two_phase", "unload_events": []}))
    monkeypatch.setattr(wan_segmented, "decode_latent_images", fake_decode)
    monkeypatch.setattr(wan_segmented, "post_decode_memory_cleanup", lambda stage: {"stage": stage, "attempted": True, "success": True, "warnings": []})
    monkeypatch.setattr(shared_audio, "mix_audio_clips", lambda *_args, **_kwargs: ({"waveform": torch.zeros((1, 2, 1)), "sample_rate": 44100}, []))
    monkeypatch.setattr(
        wan_segmented,
        "SegmentSpillStore",
        lambda privacy_mode: SegmentSpillStore(privacy_mode=privacy_mode, root=tmp_path),
    )

    plan = _two_segment_executor_plan("wan")

    images, _audio, _fps, debug = wan_segmented.build_wan_segmented_executor_outputs(
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

    assert images.shape[0] == 8
    assert torch.allclose(images[5:, 0, 0, 0], torch.tensor([0.25, 0.5, 0.75]))
    assert debug["segments"][1]["spilled_frame_count"] == 3
    assert debug["segments"][1]["seam_blend"]["actual_frame_count"] == 3
    assert debug["stitching"]["output_frame_count"] == 8


def test_ltx_segmented_executor_applies_seam_blend_after_trim(monkeypatch, tmp_path):
    decode_calls = {"count": 0}
    runtime_tail_counts = []

    def fake_build_runtime_outputs(**kwargs):
        tail = kwargs.get("ltx_timeline_plan", {}).get("model_specific", {}).get("ltx", {}).get("segment_continuity", {}).get("previous_tail_images")
        runtime_tail_counts.append(float(tail[-1, 0, 0, 0].item()) if torch.is_tensor(tail) else None)
        runtime_context = {"summary": {}}
        return (
            object(),
            [],
            [],
            {"samples": torch.zeros((1, 16, 3, 1, 1))},
            None,
            None,
            {
                "clean_latent_frames": 3,
                "hidden_reference_count": 1,
                "hidden_reference_guard_latent_frames": LTX_HIDDEN_REFERENCE_GUARD_LATENT_FRAMES,
                "clean_pixel_frames": 5,
            },
            None,
            runtime_context,
        )

    def fake_decode(_vae, _latent):
        decode_calls["count"] += 1
        if decode_calls["count"] == 1:
            return torch.tensor([0.0, 0.0, 0.0, 0.0, 0.5, 9.0], dtype=torch.float32).reshape(6, 1, 1, 1)
        return torch.tensor([1.0, 1.0, 1.0, 1.0, 1.0, 9.0], dtype=torch.float32).reshape(6, 1, 1, 1)

    monkeypatch.setattr(ltx_segmented, "build_ltx_runtime_outputs", fake_build_runtime_outputs)
    monkeypatch.setattr(ltx_segmented, "sample_latent", lambda **kwargs: kwargs["latent"])
    monkeypatch.setattr(ltx_segmented, "crop_latent_to_frame_count", lambda latent, _clean, _hidden, _guard=0: latent)
    monkeypatch.setattr(ltx_segmented, "decode_latent_images", fake_decode)
    monkeypatch.setattr(ltx_segmented, "post_decode_memory_cleanup", lambda stage: {"stage": stage, "attempted": True, "success": True, "warnings": []})
    monkeypatch.setattr(ltx_segmented, "mix_timeline_audio", lambda _plan: ({"waveform": torch.zeros((1, 2, 1)), "sample_rate": 44100}, []))
    monkeypatch.setattr(
        ltx_segmented,
        "SegmentSpillStore",
        lambda privacy_mode: SegmentSpillStore(privacy_mode=privacy_mode, root=tmp_path),
    )

    plan = _two_segment_executor_plan("ltx")

    images, _audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Increment Per Segment",
    )

    assert images.shape[0] == 8
    assert torch.allclose(images[5:, 0, 0, 0], torch.tensor([0.625, 0.75, 0.875]))
    assert runtime_tail_counts == [None, 0.5]
    assert images[:, 0, 0, 0].max().item() < 9.0
    assert debug["segments"][0]["clean_latent_frames"] == 3
    assert debug["segments"][0]["hidden_reference_count"] == 1
    assert debug["segments"][0]["hidden_reference_guard_latent_frames"] == LTX_HIDDEN_REFERENCE_GUARD_LATENT_FRAMES
    assert debug["segments"][0]["sampled_latent_frame_count_before_crop"] == 3
    assert debug["segments"][0]["sampled_latent_frame_count_after_crop"] == 3
    assert debug["segments"][0]["decoded_frame_count_before_frame_crop"] == 6
    assert debug["segments"][0]["decoded_frame_count_after_frame_crop"] == 5
    assert debug["segments"][0]["frame_crop_applied"] is True
    assert debug["segments"][1]["spilled_frame_count"] == 3
    assert debug["segments"][1]["seam_blend"]["actual_frame_count"] == 3
    assert debug["stitching"]["output_frame_count"] == 8


def test_ltx_segmented_executor_uses_connected_sigmas_as_sampling_schedule(monkeypatch, tmp_path):
    sample_calls = []
    sigmas = torch.tensor([1.0, 0.7, 0.2, 0.0], dtype=torch.float32)

    def fake_sample_latent(**kwargs):
        sample_calls.append(kwargs)
        return kwargs["latent"]

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, lambda **_kwargs: _fake_ltx_runtime_result(hidden_reference_count=0))
    monkeypatch.setattr(ltx_segmented, "sample_latent", fake_sample_latent)

    _images, _audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=_ltx_one_segment_native_generated_audio_executor_plan(),
        seed=1,
        steps=20,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
        sigmas=sigmas,
    )

    assert sample_calls[0]["sigmas"] is sigmas
    assert sample_calls[0]["steps"] == 20
    assert sample_calls[0]["scheduler"] == "normal"
    assert debug["sampling"]["external_sigmas_used"] is True
    assert debug["sampling"]["sigma_count"] == 4
    assert debug["sampling"]["effective_steps"] == 3
    assert debug["segments"][0]["sampling"]["effective_steps"] == 3
    assert any("Connected sigmas input is controlling" in entry for entry in debug["diagnostics"])


def test_ltx_segmented_executor_uses_widget_schedule_without_sigmas(monkeypatch, tmp_path):
    sample_calls = []

    def fake_sample_latent(**kwargs):
        sample_calls.append(kwargs)
        return kwargs["latent"]

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, lambda **_kwargs: _fake_ltx_runtime_result(hidden_reference_count=0))
    monkeypatch.setattr(ltx_segmented, "sample_latent", fake_sample_latent)

    _images, _audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=_ltx_one_segment_native_generated_audio_executor_plan(),
        seed=1,
        steps=12,
        cfg=5.0,
        sampler_name="euler",
        scheduler="sgm_uniform",
        denoise=1.0,
        seed_mode="Reuse Seed",
    )

    assert sample_calls[0]["sigmas"] is None
    assert sample_calls[0]["steps"] == 12
    assert sample_calls[0]["scheduler"] == "sgm_uniform"
    assert debug["sampling"] == {
        "external_sigmas_used": False,
        "configured_steps": 12,
        "configured_scheduler": "sgm_uniform",
        "effective_steps": 12,
        "diagnostics": [],
    }


def test_ltx_segmented_executor_exposes_runtime_prompt_relay_debug(monkeypatch, tmp_path):
    def fake_build_runtime_outputs(**kwargs):
        segment_plan = kwargs["ltx_timeline_plan"]
        local_prompts = [
            str(prompt.get("runtime_prompt") or prompt.get("raw_prompt") or "").strip()
            for prompt in segment_plan.get("prompt_plan", [])
        ]
        runtime_context = {
            "summary": {"section_count": len(segment_plan.get("section_plan", []))},
            "prompt_relay": {
                "full_prompt": " ".join(local_prompts),
                "local_prompts": local_prompts,
                "prompt_sections": [
                    {"item_id": section.get("item_id"), "type": section.get("type")}
                    for section in segment_plan.get("section_plan", [])
                ],
                "latent_ranges": [
                    {"start": index, "end": index + 1, "length": 1}
                    for index, _prompt in enumerate(local_prompts)
                ],
            },
        }
        return (
            object(),
            [],
            [],
            {"samples": torch.zeros((1, 16, 3, 1, 1))},
            None,
            None,
            {
                "clean_latent_frames": 3,
                "hidden_reference_count": 0,
                "hidden_reference_guard_latent_frames": 0,
                "clean_pixel_frames": 5,
            },
            None,
            runtime_context,
        )

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, fake_build_runtime_outputs)
    plan = _two_segment_executor_plan("ltx")
    plan["project"]["privacy"]["mode"] = True

    _images, _audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=12,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
    )

    first, second = debug["segments"]
    assert first["runtime_prompt_relay"]["local_prompts"] == ["first"]
    assert first["runtime_prompt_relay"]["prompt_sections"] == [
        {"item_id": "section_001", "type": "Text"}
    ]
    assert second["runtime_prompt_relay"]["local_prompts"] == [
        "Continuing from the previous segment, same subject, setting, style, and motion. second"
    ]
    assert "second" in second["runtime_prompt_relay"]["full_prompt"]
    assert second["runtime_prompt_relay"]["latent_ranges"] == [
        {"start": 0, "end": 1, "length": 1}
    ]


def test_ltx_segmented_executor_rejects_connected_sigmas_without_sampling_steps():
    for sigmas in (torch.tensor([], dtype=torch.float32), torch.tensor([1.0], dtype=torch.float32)):
        try:
            ltx_segmented.build_ltx_segmented_executor_outputs(
                model=object(),
                clip=object(),
                vae=object(),
                ltx_timeline_plan=_ltx_one_segment_native_generated_audio_executor_plan(),
                seed=1,
                steps=12,
                cfg=5.0,
                sampler_name="euler",
                scheduler="normal",
                denoise=1.0,
                seed_mode="Reuse Seed",
                sigmas=sigmas,
            )
        except ValueError as exc:
            assert "at least two values" in str(exc)
        else:
            raise AssertionError("Expected too-short sigmas to raise ValueError.")

    assert external_sigmas_step_count(torch.tensor([1.0, 0.0], dtype=torch.float32)) == 1


def test_ltx_segmented_executor_uses_text_only_for_repeated_character_reference(monkeypatch, tmp_path):
    captured_guides = []
    captured_runtime_prompts = []

    def fake_build_runtime_outputs(**kwargs):
        ltx = kwargs["ltx_timeline_plan"]["model_specific"]["ltx"]
        captured_guides.append([
            spec.get("label")
            for spec in ltx.get("character_references", {}).get("guide_specs", [])
        ])
        captured_runtime_prompts.append([
            prompt.get("runtime_prompt")
            for prompt in kwargs["ltx_timeline_plan"].get("prompt_plan", [])
        ])
        return _fake_ltx_runtime_result(
            hidden_reference_count=len(ltx.get("character_references", {}).get("guide_specs", []))
        )

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, fake_build_runtime_outputs)

    _images, _audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=_ltx_character_reference_executor_plan("follow @image1:character"),
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
    )

    assert captured_guides == [["image1"], []]
    assert captured_runtime_prompts[1] == ["Continuing from the previous segment, same subject, setting, style, and motion. follow red jacket hero"]
    assert debug["segments"][0]["character_reference_guidance"]["character_reference_labels_guided"] == ["image1"]
    assert debug["segments"][1]["character_reference_guidance"]["character_reference_labels_guided"] == []
    assert debug["segments"][1]["character_reference_guidance"]["character_reference_labels_text_only"] == ["image1"]


def test_ltx_segmented_executor_guides_new_character_in_continuation(monkeypatch, tmp_path):
    captured_guides = []

    def fake_build_runtime_outputs(**kwargs):
        ltx = kwargs["ltx_timeline_plan"]["model_specific"]["ltx"]
        captured_guides.append([
            spec.get("label")
            for spec in ltx.get("character_references", {}).get("guide_specs", [])
        ])
        return _fake_ltx_runtime_result(
            hidden_reference_count=len(ltx.get("character_references", {}).get("guide_specs", []))
        )

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, fake_build_runtime_outputs)

    _images, _audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=_ltx_character_reference_executor_plan("meet @image2:character", include_image2=True),
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
    )

    assert captured_guides == [["image1"], ["image2"]]
    assert debug["segments"][1]["character_reference_guidance"]["character_reference_labels_requested"] == ["image2"]
    assert debug["segments"][1]["character_reference_guidance"]["character_reference_labels_guided"] == ["image2"]
    assert debug["segments"][1]["character_reference_guidance"]["character_reference_labels_text_only"] == []


def test_ltx_segmented_executor_uses_native_source_video_audio_fallback(monkeypatch, tmp_path):
    _patch_ltx_executor_runtime(monkeypatch, tmp_path, lambda **_kwargs: _fake_ltx_runtime_result(hidden_reference_count=0))
    monkeypatch.setattr(
        shared_audio,
        "decode_audio_file",
        lambda _path: torch.full((2, 44100), 0.25, dtype=torch.float32),
    )
    plan = _ltx_native_video_audio_executor_plan()

    _images, audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
    )

    assert audio["waveform"].shape == (1, 2, 44100)
    assert torch.isclose(audio["waveform"].abs().max(), torch.tensor(0.25))
    assert any("fallback applied" in entry for entry in debug["diagnostics"])


def test_ltx_segmented_executor_keeps_timeline_audio_over_native_fallback(monkeypatch, tmp_path):
    _patch_ltx_executor_runtime(monkeypatch, tmp_path, lambda **_kwargs: _fake_ltx_runtime_result(hidden_reference_count=0))
    timeline_audio = {"waveform": torch.full((1, 2, 1), 0.75, dtype=torch.float32), "sample_rate": 44100}
    monkeypatch.setattr(ltx_segmented, "mix_timeline_audio", lambda _plan: (timeline_audio, ["timeline audio mix"]))
    monkeypatch.setattr(
        shared_audio,
        "decode_audio_file",
        lambda _path: torch.full((2, 44100), 0.25, dtype=torch.float32),
    )
    plan = _ltx_native_video_audio_executor_plan()
    plan["audio_plan"] = [{"item_id": "audio_clip_001", "enabled": True, "path": "/tmp/music.wav"}]

    _images, audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
    )

    assert audio is timeline_audio
    assert torch.equal(audio["waveform"], torch.full((1, 2, 1), 0.75, dtype=torch.float32))
    assert any("timeline audio clips are present" in entry for entry in debug["diagnostics"])


def test_ltx_segmented_executor_keeps_timeline_mix_when_native_source_audio_unavailable(monkeypatch, tmp_path):
    _patch_ltx_executor_runtime(monkeypatch, tmp_path, lambda **_kwargs: _fake_ltx_runtime_result(hidden_reference_count=0))

    def raise_no_audio(_path):
        raise ValueError("no audio stream")

    monkeypatch.setattr(shared_audio, "decode_audio_file", raise_no_audio)
    plan = _ltx_native_video_audio_executor_plan()

    _images, audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
    )

    assert torch.equal(audio["waveform"], torch.zeros((1, 2, 1)))
    assert any("no decodable audio stream" in entry for entry in debug["diagnostics"])
    assert any("returning timeline audio mix" in entry for entry in debug["diagnostics"])


def test_ltx_segmented_executor_samples_av_latent_and_returns_native_generated_audio(monkeypatch, tmp_path):
    sampled_latents = []

    def fake_build_runtime_outputs(**kwargs):
        frame_count = int(kwargs["ltx_timeline_plan"]["resolved_output"]["frame_count"])
        return _fake_ltx_runtime_result(
            hidden_reference_count=0,
            video_latent={"samples": torch.zeros((1, 16, frame_count, 1, 1), dtype=torch.float32)},
            audio_latent={"samples": torch.ones((1, 4, frame_count, 2), dtype=torch.float32)},
        )

    def fake_sample_latent(**kwargs):
        sampled_latents.append(kwargs["latent"])
        return kwargs["latent"]

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, fake_build_runtime_outputs)
    monkeypatch.setattr(ltx_segmented, "sample_latent", fake_sample_latent)
    plan = _ltx_one_segment_native_generated_audio_executor_plan()
    audio_vae = _FakeNativeAudioVAE(
        outputs=[torch.full((1, 44100, 2), 0.5, dtype=torch.float32)],
        sample_rate=44100,
    )

    _images, audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
        audio_vae=audio_vae,
    )

    assert getattr(sampled_latents[0]["samples"], "is_nested", False) is True
    assert audio["waveform"].shape == (1, 2, 44100)
    assert torch.isclose(audio["waveform"].abs().max(), torch.tensor(0.5))
    assert debug["native_audio"]["policy"] == "native_generated"
    assert any("Native generated audio decoded" in entry for entry in debug["diagnostics"])


def test_ltx_segmented_executor_native_generated_audio_wins_over_timeline_audio(monkeypatch, tmp_path):
    def fake_build_runtime_outputs(**kwargs):
        frame_count = int(kwargs["ltx_timeline_plan"]["resolved_output"]["frame_count"])
        return _fake_ltx_runtime_result(
            hidden_reference_count=0,
            video_latent={"samples": torch.zeros((1, 16, frame_count, 1, 1), dtype=torch.float32)},
            audio_latent={"samples": torch.ones((1, 4, frame_count, 2), dtype=torch.float32)},
        )

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, fake_build_runtime_outputs)
    timeline_audio = {"waveform": torch.full((1, 2, 44100), 0.75, dtype=torch.float32), "sample_rate": 44100}
    monkeypatch.setattr(ltx_segmented, "mix_timeline_audio", lambda _plan: (timeline_audio, ["timeline audio mix"]))
    plan = _ltx_one_segment_native_generated_audio_executor_plan()
    plan["audio_plan"] = [{"item_id": "audio_clip_001", "enabled": True, "path": "/tmp/music.wav"}]
    audio_vae = _FakeNativeAudioVAE(
        outputs=[torch.full((1, 44100, 2), 0.25, dtype=torch.float32)],
        sample_rate=44100,
    )

    _images, audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
        audio_vae=audio_vae,
    )

    assert torch.isclose(audio["waveform"].abs().max(), torch.tensor(0.25))
    assert debug["native_audio"]["policy"] == "native_generated"


def test_ltx_segmented_executor_falls_back_when_native_audio_latent_is_empty(monkeypatch, tmp_path):
    _patch_ltx_executor_runtime(monkeypatch, tmp_path, lambda **_kwargs: _fake_ltx_runtime_result(hidden_reference_count=0))
    plan = _ltx_one_segment_native_generated_audio_executor_plan()

    _images, audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
    )

    assert torch.equal(audio["waveform"], torch.zeros((1, 2, 1)))
    assert debug["native_audio"]["policy"] == "timeline_mix_fallback"
    assert any("empty audio latent" in entry for entry in debug["diagnostics"])


def test_ltx_segmented_executor_falls_back_when_native_audio_decode_fails(monkeypatch, tmp_path):
    def fake_build_runtime_outputs(**kwargs):
        frame_count = int(kwargs["ltx_timeline_plan"]["resolved_output"]["frame_count"])
        return _fake_ltx_runtime_result(
            hidden_reference_count=0,
            video_latent={"samples": torch.zeros((1, 16, frame_count, 1, 1), dtype=torch.float32)},
            audio_latent={"samples": torch.ones((1, 4, frame_count, 2), dtype=torch.float32)},
        )

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, fake_build_runtime_outputs)
    plan = _ltx_one_segment_native_generated_audio_executor_plan()

    _images, audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
        audio_vae=_FakeNativeAudioVAE(fail=True),
    )

    assert torch.equal(audio["waveform"], torch.zeros((1, 2, 1)))
    assert debug["native_audio"]["policy"] == "timeline_mix_fallback"
    assert any("decode failed" in entry for entry in debug["diagnostics"])


def test_ltx_segmented_executor_trims_and_stitches_native_generated_audio_segments(monkeypatch, tmp_path):
    def fake_build_runtime_outputs(**kwargs):
        frame_count = int(kwargs["ltx_timeline_plan"]["resolved_output"]["frame_count"])
        return _fake_ltx_runtime_result(
            hidden_reference_count=0,
            video_latent={"samples": torch.zeros((1, 16, frame_count, 1, 1), dtype=torch.float32)},
            audio_latent={"samples": torch.ones((1, 4, frame_count, 2), dtype=torch.float32)},
        )

    _patch_ltx_executor_runtime(monkeypatch, tmp_path, fake_build_runtime_outputs)
    plan = _ltx_native_generated_audio_executor_plan()
    audio_vae = _FakeNativeAudioVAE(
        outputs=[
            torch.tensor([[[1.0, 1.0]] * 5], dtype=torch.float32),
            torch.tensor([[[9.0, 9.0], [9.0, 9.0], [2.0, 2.0], [2.0, 2.0], [2.0, 2.0]]], dtype=torch.float32),
        ],
        sample_rate=8,
    )

    _images, audio, _fps, debug = ltx_segmented.build_ltx_segmented_executor_outputs(
        model=object(),
        clip=object(),
        vae=object(),
        ltx_timeline_plan=plan,
        seed=1,
        steps=4,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        seed_mode="Reuse Seed",
        audio_vae=audio_vae,
    )

    assert audio["sample_rate"] == 8
    assert torch.equal(audio["waveform"][0, 0], torch.tensor([1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0]))
    assert debug["native_audio"]["policy"] == "native_generated"
    assert debug["native_audio"]["stitch"]["final_audio_shape"] == [1, 2, 8]


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


def test_wan_two_phase_sampling_uses_split_conditionings(monkeypatch):
    calls = []

    def fake_sample_latent(**kwargs):
        calls.append(kwargs)
        return kwargs["latent"]

    monkeypatch.setattr(wan_segmented, "sample_latent", fake_sample_latent)
    positive = {
        "high": [["positive_high"]],
        "low": [["positive_low"]],
        "default": [["positive_default"]],
        "_helto_wan_conditioning_split": True,
    }

    _sampled, debug = wan_segmented.sample_wan_segment_latent(
        high_noise_model=object(),
        low_noise_model=object(),
        positive=positive,
        negative=[["negative"]],
        latent={"samples": torch.zeros((1, 16, 3, 2, 2))},
        model_mode="I2V-A14B",
        seed=1,
        steps=12,
        cfg=5.0,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        phase_split_step=6,
    )

    assert calls[0]["positive"] == [["positive_high"]]
    assert calls[1]["positive"] == [["positive_low"]]
    assert debug["phases"][0]["conditioning"] == "positive_high"
    assert debug["phases"][1]["conditioning"] == "positive_low"


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


def test_wan_segmented_executor_context_includes_status_events(monkeypatch, tmp_path):
    def fake_build_runtime_outputs(**_kwargs):
        runtime_context = {
            "wan": {
                "visual_conditioning": {
                    "requested_keyframes": [],
                    "applied_keyframes": [],
                    "media_decisions": [],
                },
                "bernini": None,
                "loras": {
                    "take_snapshot": {
                        "model_family": "WAN",
                        "model_version": "2.2",
                        "targets": {
                            MODEL_LORA_TARGET_HIGH_NOISE: [
                                {"name": "segment_style.safetensors"}
                            ],
                        },
                    },
                },
            },
            "summary": {},
        }
        return object(), object(), [], [], {"samples": torch.zeros((1, 16, 3, 2, 2))}, runtime_context

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
    monkeypatch.setattr(shared_audio, "mix_audio_clips", lambda *_args, **_kwargs: ({"waveform": torch.zeros((1, 2, 1)), "sample_rate": 44100}, []))
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
                "timeline_structure": {
                    "shots": [
                        {
                            "shot_id": "shot_section_001",
                            "type": "Generated",
                            "start_time": 0.0,
                            "end_time": 5 / 8.0,
                            "section_ids": ["section_001"],
                        }
                    ],
                    "section_to_shot": {"section_001": "shot_section_001"},
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
                            "source_section_ids": ["section_001"],
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
    take_registration = debug["segments"][0]["take_registration"]
    assert take_registration["shot_id"] == "shot_section_001"
    assert take_registration["shot_ids"] == ["shot_section_001"]
    assert take_registration["registration_ready"] is True
    assert take_registration["capture_blockers"] == []
    assert take_registration["segment_context"]["id"] == "gen_001"
    assert take_registration["take"]["take_id"] == "take_wan_shot_section_001_gen_001_generated"
    assert take_registration["take"]["seed"] == 1
    assert take_registration["take"]["metadata"]["settings"]["steps"] == 4
    assert take_registration["asset_suggestion"]["name"] == take_registration["suggested_asset_name"]
    assert take_registration["take"]["resolved_loras"]["targets"][MODEL_LORA_TARGET_HIGH_NOISE][0]["name"] == "segment_style.safetensors"

    registered = apply_take_registration(
        _timeline_with_text_section("section_001"),
        take_registration,
        generated_asset_path="/tmp/output/wan_segment.mp4",
    )
    assert registered["take_id"] == "take_wan_shot_section_001_gen_001_generated"
    assert validate_video_timeline(registered["timeline"])["is_valid"] is True


def test_wan_segmented_executor_passes_previous_latent_to_fmlf_svi(monkeypatch, tmp_path):
    runtime_calls = []
    sample_calls = []

    def fake_build_runtime_outputs(**kwargs):
        runtime_calls.append(kwargs)
        runtime_context = {
            "visual_conditioning": {
                "requested_keyframes": [],
                "applied_keyframes": [],
                "media_decisions": [],
            },
            "bernini": None,
            "fmlf_advanced_i2v": {
                "helper": "FMLF Advanced I2V",
                "continuation_mode": "SVI",
                "used_prev_latent": kwargs.get("fmlf_prev_latent") is not None,
            },
            "summary": {},
        }
        positive = {
            "high": [["positive_high"]],
            "low": [["positive_low"]],
            "default": [["positive_high"]],
            "_helto_wan_conditioning_split": True,
        }
        latent = {"samples": torch.zeros((1, 16, 3, 2, 2))}
        return object(), object(), positive, [], latent, runtime_context

    def fake_sample_wan_segment_latent(**kwargs):
        sample_calls.append(kwargs)
        if len(sample_calls) == 1:
            values = torch.tensor([10.0, 20.0, 99.0], dtype=torch.float32)
        else:
            values = torch.tensor([30.0, 40.0, 50.0], dtype=torch.float32)
        samples = values.view(1, 1, 3, 1, 1).repeat(1, 16, 1, 2, 2)
        return {"samples": samples}, {"sampling_policy": "two_phase", "unload_events": []}

    def fake_decode(_vae, latent):
        value = float(latent["samples"].mean().item())
        frame_count = int(runtime_calls[len(sample_calls) - 1]["wan_timeline_plan"]["resolved_output"]["frame_count"])
        return torch.ones((frame_count, 2, 2, 3)) * value

    monkeypatch.setattr(wan_segmented, "build_wan_runtime_outputs", fake_build_runtime_outputs)
    monkeypatch.setattr(wan_segmented, "sample_wan_segment_latent", fake_sample_wan_segment_latent)
    monkeypatch.setattr(wan_segmented, "decode_latent_images", fake_decode)
    monkeypatch.setattr(wan_segmented, "post_decode_memory_cleanup", lambda stage: {"stage": stage, "attempted": True, "success": True, "warnings": []})
    monkeypatch.setattr(shared_audio, "mix_audio_clips", lambda *_args, **_kwargs: ({"waveform": torch.zeros((1, 2, 1)), "sample_rate": 44100}, []))
    monkeypatch.setattr(
        wan_segmented,
        "SegmentSpillStore",
        lambda privacy_mode: SegmentSpillStore(privacy_mode=privacy_mode, root=tmp_path),
    )

    plan = _two_segment_executor_plan("wan")
    wan_config = plan["model_specific"]["wan"]["config"]
    wan_config["runtime_backend_profile"] = "FMLF Advanced I2V"
    wan_config["fmlf_continuation_mode"] = "SVI"
    wan_config["model_mode"] = "I2V-A14B"
    plan["model_specific"]["wan"]["segmented_generation"]["segments"][0]["generation_frame_count"] = 9
    plan["model_specific"]["wan"]["segmented_generation"]["segments"][0]["trim_trailing_frames"] = 4
    plan["model_specific"]["wan"]["segmented_generation"]["segments"][1]["continuity"]["continuity_frame_count"] = 5
    plan["model_specific"]["wan"]["segmented_generation"]["segments"][1]["trim_leading_frames"] = 1

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

    assert runtime_calls[0]["split_conditioning"] is True
    assert runtime_calls[0]["fmlf_prev_latent"] is None
    assert runtime_calls[1]["fmlf_prev_latent"]["samples"].shape == (1, 16, 2, 2, 2)
    assert float(runtime_calls[1]["fmlf_prev_latent"]["samples"].mean().item()) == 15.0
    assert runtime_calls[1]["fmlf_motion_frames"].shape[0] == 5
    handoff = debug["segments"][0]["previous_latent_handoff"]
    assert handoff["dropped_trailing_latent_slots"] == 1
    assert handoff["dropped_fully_nonvisible_trailing_latent_slots"] == 1
    assert handoff["visible_frame_start_index"] == 0
    assert handoff["visible_frame_end_index"] == 4
    assert handoff["last_visible_latent_slot"] == 1
    assert handoff["svi_last_slot_guard_applied"] is False
    assert handoff["svi_last_slot_guard_skip_reason"] == "final_slot_overlaps_visible_frames"
    assert handoff["previous_latent_shape"] == [1, 16, 2, 2, 2]
    assert debug["segments"][1]["fmlf_advanced_i2v"]["used_prev_latent"] is True
    assert len(sample_calls) == 2


def test_wan_fmlf_svi_three_segment_plan_and_handoff_exclude_padded_tail(monkeypatch, tmp_path):
    runtime_calls = []
    sample_calls = []

    def fake_build_runtime_outputs(**kwargs):
        runtime_calls.append(kwargs)
        frame_count = int(kwargs["wan_timeline_plan"]["resolved_output"]["frame_count"])
        latent_slots = ((frame_count - 1) // 4) + 1
        runtime_context = {
            "visual_conditioning": {
                "requested_keyframes": [],
                "applied_keyframes": [],
                "media_decisions": [],
            },
            "bernini": None,
            "fmlf_advanced_i2v": {
                "helper": "FMLF Advanced I2V",
                "continuation_mode": "SVI",
                "used_prev_latent": kwargs.get("fmlf_prev_latent") is not None,
            },
            "summary": {},
        }
        positive = {
            "high": [["positive_high"]],
            "low": [["positive_low"]],
            "default": [["positive_high"]],
            "_helto_wan_conditioning_split": True,
        }
        latent = {"samples": torch.zeros((1, 16, latent_slots, 2, 2))}
        return object(), object(), positive, [], latent, runtime_context

    def fake_sample_wan_segment_latent(**kwargs):
        sample_calls.append(kwargs)
        latent_slots = int(kwargs["latent"]["samples"].shape[2])
        values = torch.arange(latent_slots, dtype=torch.float32) + (100.0 * len(sample_calls))
        samples = values.view(1, 1, latent_slots, 1, 1).repeat(1, 16, 1, 2, 2)
        return {"samples": samples}, {"sampling_policy": "two_phase", "unload_events": []}

    def fake_decode(_vae, _latent):
        frame_count = int(runtime_calls[len(sample_calls) - 1]["wan_timeline_plan"]["resolved_output"]["frame_count"])
        return torch.ones((frame_count, 2, 2, 3), dtype=torch.float32) * len(sample_calls)

    monkeypatch.setattr(wan_segmented, "build_wan_runtime_outputs", fake_build_runtime_outputs)
    monkeypatch.setattr(wan_segmented, "sample_wan_segment_latent", fake_sample_wan_segment_latent)
    monkeypatch.setattr(wan_segmented, "decode_latent_images", fake_decode)
    monkeypatch.setattr(wan_segmented, "post_decode_memory_cleanup", lambda stage: {"stage": stage, "attempted": True, "success": True, "warnings": []})
    monkeypatch.setattr(shared_audio, "mix_audio_clips", lambda *_args, **_kwargs: ({"waveform": torch.zeros((1, 2, 1)), "sample_rate": 44100}, []))
    monkeypatch.setattr(
        wan_segmented,
        "SegmentSpillStore",
        lambda privacy_mode: SegmentSpillStore(privacy_mode=privacy_mode, root=tmp_path),
    )

    plan, validation, _debug = build_wan_timeline_plan(
        _wan_15s_image_then_two_text_timeline(),
        create_wan_timeline_config(
            runtime_backend_profile="FMLF Advanced I2V",
            model_mode="I2V-A14B",
            max_generation_duration=5.0,
            segment_continuity_tail_frames=1,
            segment_seam_blend_frames=0,
            fmlf_continuation_mode="SVI",
        ),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )
    segments = plan["model_specific"]["wan"]["segmented_generation"]["segments"]

    assert validation["is_valid"] is True
    assert [segment["generation_frame_count"] for segment in segments] == [121, 121, 125]
    assert [segment["visible_frame_count"] for segment in segments] == [120, 120, 121]
    assert [segment["trim_trailing_frames"] for segment in segments] == [1, 0, 3]

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

    assert runtime_calls[0]["fmlf_prev_latent"] is None
    assert runtime_calls[1]["fmlf_prev_latent"]["samples"].shape == (1, 16, 1, 2, 2)
    assert float(runtime_calls[1]["fmlf_prev_latent"]["samples"].mean().item()) == 130.0
    first_handoff = debug["segments"][0]["previous_latent_handoff"]
    assert first_handoff["original_sampled_latent_shape"] == [1, 16, 31, 2, 2]
    assert first_handoff["visible_frame_start_index"] == 0
    assert first_handoff["visible_frame_end_index"] == 119
    assert first_handoff["first_visible_latent_slot"] == 0
    assert first_handoff["last_visible_latent_slot"] == 30
    assert first_handoff["visible_continuation_latent_shape"] == [1, 16, 31, 2, 2]
    assert first_handoff["dropped_trailing_latent_slots"] == 0
    assert first_handoff["dropped_fully_nonvisible_trailing_latent_slots"] == 0
    assert first_handoff["svi_last_slot_guard_applied"] is False
    assert first_handoff["svi_last_slot_guard_skip_reason"] == "final_slot_overlaps_visible_frames"
    assert first_handoff["previous_latent_shape"] == [1, 16, 1, 2, 2]
    assert len(sample_calls) == 3


def test_wan_fmlf_svi_10s_debug_shows_two_text_sections_in_second_generation(monkeypatch, tmp_path):
    runtime_calls = []
    sample_calls = []

    def fake_build_runtime_outputs(**kwargs):
        runtime_calls.append(kwargs)
        frame_count = int(kwargs["wan_timeline_plan"]["resolved_output"]["frame_count"])
        latent_slots = ((frame_count - 1) // 4) + 1
        runtime_context = {
            "visual_conditioning": {
                "requested_keyframes": [],
                "applied_keyframes": [],
                "media_decisions": [],
            },
            "bernini": None,
            "fmlf_advanced_i2v": {
                "helper": "FMLF Advanced I2V",
                "continuation_mode": "SVI",
                "used_prev_latent": kwargs.get("fmlf_prev_latent") is not None,
            },
            "summary": {},
        }
        positive = {
            "high": [["positive_high"]],
            "low": [["positive_low"]],
            "default": [["positive_high"]],
            "_helto_wan_conditioning_split": True,
        }
        latent = {"samples": torch.zeros((1, 16, latent_slots, 2, 2))}
        return object(), object(), positive, [], latent, runtime_context

    def fake_sample_wan_segment_latent(**kwargs):
        sample_calls.append(kwargs)
        latent_slots = int(kwargs["latent"]["samples"].shape[2])
        samples = torch.zeros((1, 16, latent_slots, 2, 2), dtype=torch.float32)
        return {"samples": samples}, {"sampling_policy": "two_phase", "unload_events": []}

    def fake_decode(_vae, _latent):
        frame_count = int(runtime_calls[len(sample_calls) - 1]["wan_timeline_plan"]["resolved_output"]["frame_count"])
        return torch.ones((frame_count, 2, 2, 3), dtype=torch.float32) * len(sample_calls)

    monkeypatch.setattr(wan_segmented, "build_wan_runtime_outputs", fake_build_runtime_outputs)
    monkeypatch.setattr(wan_segmented, "sample_wan_segment_latent", fake_sample_wan_segment_latent)
    monkeypatch.setattr(wan_segmented, "decode_latent_images", fake_decode)
    monkeypatch.setattr(wan_segmented, "post_decode_memory_cleanup", lambda stage: {"stage": stage, "attempted": True, "success": True, "warnings": []})
    monkeypatch.setattr(shared_audio, "mix_audio_clips", lambda *_args, **_kwargs: ({"waveform": torch.zeros((1, 2, 1)), "sample_rate": 44100}, []))
    monkeypatch.setattr(
        wan_segmented,
        "SegmentSpillStore",
        lambda privacy_mode: SegmentSpillStore(privacy_mode=privacy_mode, root=tmp_path),
    )

    plan, validation, _debug = build_wan_timeline_plan(
        _wan_10s_image_then_two_text_timeline(),
        create_wan_timeline_config(
            runtime_backend_profile="FMLF Advanced I2V",
            model_mode="I2V-A14B",
            max_generation_duration=5.0,
            segment_continuity_tail_frames=1,
            segment_seam_blend_frames=0,
            fmlf_continuation_mode="SVI",
        ),
        generation_mode=GENERATION_MODE_FORCE_FULL_TIMELINE,
    )
    segments = plan["model_specific"]["wan"]["segmented_generation"]["segments"]

    assert validation["is_valid"] is True
    assert len(segments) == 2
    assert segments[0]["source_section_ids"] == ["image_section"]
    assert segments[1]["source_section_ids"] == ["text_001", "text_002"]

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

    second = debug["segments"][1]
    assert second["source_section_ids"] == ["text_001", "text_002"]
    assert [entry["item_id"] for entry in second["local_sections"]] == ["text_001", "text_002"]
    assert [entry["item_id"] for entry in second["prompt_relay"]["local_prompts"]] == ["text_001", "text_002"]
    assert second["prompt_relay"]["segment_lengths"] == [16, 16]
    assert "he looks around" in second["prompt_relay"]["local_prompts"][0]["prompt_preview"]
    assert "he walks away" in second["prompt_relay"]["local_prompts"][1]["prompt_preview"]
    assert any("3 planned section(s) but 2 hidden generation segment(s)" in entry for entry in debug["diagnostics"])
    assert len(sample_calls) == 2


class _SegmentAbort(BaseException):
    pass


def _wan_10s_image_then_two_text_timeline():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 10.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["assets"].append(
        {
            "asset_id": "image_001",
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/woman.png",
            "name": "woman.png",
        }
    )
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "image_section",
                "type": SECTION_TYPE_IMAGE,
                "start_time": 0.0,
                "end_time": 5.0,
                "image": {"asset_id": "image_001"},
                "prompt": "woman in frame",
            },
            {
                "item_id": "text_001",
                "type": SECTION_TYPE_TEXT,
                "start_time": 5.0,
                "end_time": 7.5,
                "prompt": "he looks around",
            },
            {
                "item_id": "text_002",
                "type": SECTION_TYPE_TEXT,
                "start_time": 7.5,
                "end_time": 10.0,
                "prompt": "he walks away",
            },
        ]
    )
    return timeline


def _wan_15s_image_then_two_text_timeline():
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 15.0
    timeline["project"]["frame_rate"] = 24.0
    timeline["assets"].append(
        {
            "asset_id": "image_001",
            "type": ASSET_TYPE_IMAGE,
            "source_kind": ASSET_SOURCE_FILE_PATH,
            "path": "/mnt/media/woman.png",
            "name": "woman.png",
        }
    )
    timeline["director_track"]["sections"].extend(
        [
            {
                "item_id": "image_section",
                "type": SECTION_TYPE_IMAGE,
                "start_time": 0.0,
                "end_time": 5.0,
                "image": {"asset_id": "image_001"},
                "prompt": "woman in frame",
            },
            {
                "item_id": "text_001",
                "type": SECTION_TYPE_TEXT,
                "start_time": 5.0,
                "end_time": 10.0,
                "prompt": "she turns toward the window",
            },
            {
                "item_id": "text_002",
                "type": SECTION_TYPE_TEXT,
                "start_time": 10.0,
                "end_time": 15.0,
                "prompt": "she walks forward",
            },
        ]
    )
    return timeline


def _two_segment_executor_plan(model_key):
    return {
        "resolved_output": {"frame_count": 8, "frame_rate": 8.0},
        "project": {"privacy": {"mode": False}},
        "section_plan": [
            {"item_id": "section_001", "type": "Text", "start_frame": 0, "end_frame_exclusive": 5, "frame_count": 5},
            {"item_id": "section_002", "type": "Text", "start_frame": 5, "end_frame_exclusive": 8, "frame_count": 3},
        ],
        "prompt_plan": [
            {"item_id": "section_001", "type": "Text", "raw_prompt": "first"},
            {"item_id": "section_002", "type": "Text", "raw_prompt": "second"},
        ],
        "media_plan": [],
        "audio_plan": [],
        "model_specific": {
            model_key: {
                "config": {
                    "model_mode": "I2V-A14B",
                    "prompt_routing": "Prompt Relay",
                    "prompt_relay_epsilon": 0.15,
                    "vram_unload_policy": "Off",
                    "segment_seam_blend_frames": 3,
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
                            "continuity": {"mode": "initial", "continuity_frame_count": 0},
                        },
                        {
                            "id": "gen_002",
                            "index": 1,
                            "start_frame": 5,
                            "end_frame_exclusive": 8,
                            "visible_frame_count": 3,
                            "generation_frame_count": 5,
                            "trim_leading_frames": 2,
                            "trim_trailing_frames": 0,
                            "continuity": {
                                "mode": "model_auto",
                                "source": "previous_tail",
                                "source_segment": "gen_001",
                                "continuity_frame_count": 2,
                                "prompt_hint": True,
                            },
                        },
                    ],
                },
            },
        },
    }


def _ltx_character_reference_executor_plan(second_prompt, *, include_image2=False):
    plan = _two_segment_executor_plan("ltx")
    plan["prompt_plan"] = [
        {
            "item_id": "section_001",
            "type": "Text",
            "raw_prompt": "follow @image1:character",
            "runtime_prompt": "follow red jacket hero",
        },
        {
            "item_id": "section_002",
            "type": "Text",
            "raw_prompt": second_prompt,
            "runtime_prompt": (
                "meet blue coat friend"
                if "image2" in second_prompt
                else "follow red jacket hero"
            ),
        },
    ]
    specs = [
        {
            "id": "ref_hero",
            "label": "image1",
            "kind": "character",
            "description": "red jacket hero",
            "strength": 1.0,
            "image": {"path": "/tmp/hero.png"},
            "section_id": "section_001,section_002",
        },
    ]
    if include_image2:
        specs.append(
            {
                "id": "ref_friend",
                "label": "image2",
                "kind": "character",
                "description": "blue coat friend",
                "strength": 1.0,
                "image": {"path": "/tmp/friend.png"},
                "section_id": "section_002",
            }
        )
    plan["model_specific"]["ltx"]["character_references"] = {
        "active": True,
        "mode": "Prompt Relay",
        "guide_specs": specs,
        "section_usage": [],
        "runtime_global_prompt": "",
        "substitutions": [],
        "diagnostics": [],
    }
    return plan


def _ltx_native_video_audio_executor_plan():
    plan = _two_segment_executor_plan("ltx")
    plan["resolved_output"]["duration_seconds"] = 1.0
    plan["project"]["audio"] = {
        "use_native_audio": True,
        "always_normalize": False,
    }
    plan["section_plan"] = [
        {
            "item_id": "section_video_001",
            "type": "Video",
            "start_frame": 0,
            "end_frame_exclusive": 8,
            "frame_count": 8,
        }
    ]
    plan["prompt_plan"] = [
        {
            "item_id": "section_video_001",
            "type": "Video",
            "raw_prompt": "continue the source video",
        }
    ]
    plan["media_plan"] = [
        {
            "item_id": "section_video_001",
            "section_type": "Video",
            "path": "/tmp/source-video.mp4",
            "source_in": 0.0,
            "source_out": None,
        }
    ]
    plan["audio_plan"] = []
    return plan


def _ltx_native_generated_audio_executor_plan():
    plan = _two_segment_executor_plan("ltx")
    plan["resolved_output"]["duration_seconds"] = 1.0
    plan["project"]["audio"] = {
        "use_native_audio": True,
        "always_normalize": False,
    }
    plan["audio_plan"] = []
    return plan


def _ltx_one_segment_native_generated_audio_executor_plan():
    plan = _ltx_native_generated_audio_executor_plan()
    plan["model_specific"]["ltx"]["segmented_generation"]["segments"] = [
        {
            "id": "gen_001",
            "index": 0,
            "start_frame": 0,
            "end_frame_exclusive": 8,
            "visible_frame_count": 8,
            "generation_frame_count": 8,
            "trim_leading_frames": 0,
            "trim_trailing_frames": 0,
            "continuity": {"mode": "initial", "continuity_frame_count": 0},
        }
    ]
    return plan


def _timeline_with_text_section(item_id: str):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 1.0
    timeline["director_track"]["sections"].append(
        {
            "item_id": item_id,
            "type": SECTION_TYPE_TEXT,
            "start_time": 0.0,
            "end_time": 1.0,
            "prompt": "segment prompt",
        }
    )
    return timeline


class _FakeNativeAudioVAE:
    def __init__(self, *, outputs=None, sample_rate=44100, fail=False):
        self.outputs = list(outputs or [])
        self.fail = bool(fail)
        self.first_stage_model = type("FakeAudioVAEModel", (), {"output_sample_rate": int(sample_rate)})()

    def decode(self, audio_latent):
        if self.fail:
            raise ValueError("fake native audio decode failure")
        if self.outputs:
            return self.outputs.pop(0)
        sample_count = max(1, int(audio_latent.shape[2]))
        return torch.full((1, sample_count, 2), 0.5, dtype=torch.float32, device=audio_latent.device)


def _fake_ltx_runtime_result(*, hidden_reference_count, video_latent=None, audio_latent=None):
    runtime_context = {"summary": {}}
    return (
        object(),
        [],
        [],
        video_latent or {"samples": torch.zeros((1, 16, 3, 1, 1))},
        audio_latent,
        None,
        {
            "clean_latent_frames": 3,
            "hidden_reference_count": hidden_reference_count,
            "hidden_reference_guard_latent_frames": (
                LTX_HIDDEN_REFERENCE_GUARD_LATENT_FRAMES if hidden_reference_count else 0
            ),
            "clean_pixel_frames": 5,
        },
        None,
        runtime_context,
    )


def _patch_ltx_executor_runtime(monkeypatch, tmp_path, build_runtime_outputs):
    monkeypatch.setattr(ltx_segmented, "build_ltx_runtime_outputs", build_runtime_outputs)
    monkeypatch.setattr(ltx_segmented, "sample_latent", lambda **kwargs: kwargs["latent"])
    monkeypatch.setattr(ltx_segmented, "crop_latent_to_frame_count", lambda latent, _clean, _hidden, _guard=0: latent)
    monkeypatch.setattr(
        ltx_segmented,
        "decode_latent_images",
        lambda _vae, _latent: torch.zeros((5, 1, 1, 1), dtype=torch.float32),
    )
    monkeypatch.setattr(
        ltx_segmented,
        "post_decode_memory_cleanup",
        lambda stage: {"stage": stage, "attempted": True, "success": True, "warnings": []},
    )
    monkeypatch.setattr(
        ltx_segmented,
        "mix_timeline_audio",
        lambda _plan: ({"waveform": torch.zeros((1, 2, 1)), "sample_rate": 44100}, []),
    )
    monkeypatch.setattr(
        ltx_segmented,
        "SegmentSpillStore",
        lambda privacy_mode: SegmentSpillStore(privacy_mode=privacy_mode, root=tmp_path),
    )
