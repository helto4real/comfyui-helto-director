from __future__ import annotations

import torch

import shared.audio as shared_audio
import shared.media as shared_media
from shared.contracts.video_timeline import (
    VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
    VIDEO_TIMING_FREEZE_LAST_FRAME,
)
from shared.ltx.runtime import audio as ltx_audio
from shared.timeline import sequence_assembly
from shared.wan.runtime import bernini, runtime as wan_runtime, segmented as wan_segmented


def test_mix_audio_clips_owns_decode_trim_gain_and_timeline_placement(monkeypatch):
    source = torch.ones((2, shared_audio.TARGET_SAMPLE_RATE), dtype=torch.float32)
    monkeypatch.setattr(shared_audio, "decode_audio_file", lambda _path: source)

    audio, diagnostics = shared_audio.mix_audio_clips(
        [
            {
                "item_id": "clip_001",
                "path": "/approved/music.wav",
                "start_time": 0.25,
                "end_time": 0.75,
                "source_in": 0.25,
                "source_out": 0.75,
                "volume": 50.0,
                "fade_in": 0.0,
                "fade_out": 0.0,
                "enabled": True,
            }
        ],
        1.0,
        normalize=False,
    )

    waveform = audio["waveform"]
    quarter = shared_audio.TARGET_SAMPLE_RATE // 4
    assert diagnostics == []
    assert waveform.shape == (1, 2, shared_audio.TARGET_SAMPLE_RATE)
    assert torch.count_nonzero(waveform[..., :quarter]) == 0
    assert torch.allclose(waveform[..., quarter:quarter * 3], torch.full((1, 2, quarter * 2), 0.5))
    assert torch.count_nonzero(waveform[..., quarter * 3:]) == 0


def test_ltx_audio_adapter_keeps_ignore_policy_and_delegates_shared_mix(monkeypatch):
    calls = []

    def fake_mix(audio_plan, duration_seconds, *, normalize):
        calls.append((audio_plan, duration_seconds, normalize))
        return shared_audio.empty_audio(duration_seconds), ["shared mixer"]

    monkeypatch.setattr(shared_audio, "mix_audio_clips", fake_mix)
    base_plan = {
        "resolved_output": {
            "duration_seconds": 1.0,
            "frame_count": 48,
            "frame_rate": 24.0,
        },
        "audio_plan": [{"item_id": "clip_001"}],
        "model_specific": {"ltx": {"config": {"audio_mode": "Mix Timeline Audio"}}},
    }

    mixed, diagnostics = ltx_audio.mix_timeline_audio(base_plan)

    assert mixed["waveform"].shape[-1] == shared_audio.TARGET_SAMPLE_RATE * 2
    assert diagnostics == ["shared mixer"]
    assert calls[0][0] is base_plan["audio_plan"]
    assert calls[0][1] == 2.0

    ignored_plan = {
        **base_plan,
        "model_specific": {"ltx": {"config": {"audio_mode": "Ignore Timeline Audio"}}},
    }
    ignored, ignored_diagnostics = ltx_audio.mix_timeline_audio(ignored_plan)

    assert ignored["waveform"].shape[-1] == shared_audio.TARGET_SAMPLE_RATE * 2
    assert ignored_diagnostics == ["Timeline audio was ignored by LTX audio mode."]
    assert len(calls) == 1


def test_ltx_native_source_audio_adapter_does_not_spill_past_its_section(monkeypatch):
    monkeypatch.setattr(
        shared_audio,
        "decode_audio_file",
        lambda _path: torch.ones(
            (2, shared_audio.TARGET_SAMPLE_RATE * 2),
            dtype=torch.float32,
        ),
    )
    monkeypatch.setattr(ltx_audio, "global_always_normalize_audio", lambda: False)
    plan = {
        "resolved_output": {
            "duration_seconds": 2.0,
            "frame_count": 48,
            "frame_rate": 24.0,
        },
        "section_plan": [
            {
                "item_id": "video_001",
                "start_frame": 0,
                "end_frame_exclusive": 24,
            }
        ],
        "media_plan": [
            {
                "item_id": "video_001",
                "section_type": "Video",
                "path": "/approved/video.mp4",
                "source_in": 0.5,
                "source_out": 2.0,
            }
        ],
    }

    audio, diagnostics = ltx_audio.build_native_source_video_audio_fallback(plan)

    assert diagnostics == []
    assert torch.count_nonzero(
        audio["waveform"][..., shared_audio.TARGET_SAMPLE_RATE:]
    ) == 0


def test_shared_video_trim_guidance_and_timing_return_structured_results():
    frames = torch.arange(10, dtype=torch.float32).reshape(10, 1, 1, 1).repeat(1, 2, 2, 3)
    trimmed = shared_media.trim_video_source_frames(
        frames,
        4.0,
        {"item_id": "video_001", "source_in": 0.5, "source_out": 2.0},
    )
    guidance = shared_media.select_video_guidance_range(
        trimmed.frames,
        {
            "video_guidance_range": VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
            "video_guidance_frame_count": 3,
        },
        trimmed.metadata,
    )
    selected = shared_media.select_video_guide_frames(
        guidance.frames,
        4.0,
        {"timing_mode": VIDEO_TIMING_FREEZE_LAST_FRAME},
        {"frame_count": 5},
    )

    assert isinstance(trimmed, shared_media.SelectedVideoFrames)
    assert trimmed.metadata["source_range"] == {
        "start": 0.5,
        "end": 2.0,
        "start_frame": 2,
        "end_frame_exclusive": 8,
    }
    assert guidance.metadata["guidance_source_range"] == {
        "start_frame": 5,
        "end_frame_exclusive": 8,
    }
    assert guidance.frames[:, 0, 0, 0].tolist() == [5.0, 6.0, 7.0]
    assert selected[:, 0, 0, 0].tolist() == [5.0, 6.0, 7.0, 7.0, 7.0]


def test_ltx_media_exports_delegate_to_shared_mechanics():
    from shared.ltx.runtime import media as ltx_media

    assert ltx_media.decode_video_frames is shared_media.decode_video_frames
    assert ltx_media.trim_video_source_frames is shared_media.trim_video_source_frames
    assert ltx_media.resize_image_frames is shared_media.resize_image_frames


def test_wan_bernini_and_sequence_assembly_use_shared_processing_seams():
    assert bernini.decode_video_frames is shared_media.decode_video_frames
    assert wan_runtime.decode_video_frames is shared_media.decode_video_frames
    assert sequence_assembly.shared_media is shared_media
    assert sequence_assembly.shared_audio is shared_audio
    assert wan_segmented.shared_audio is shared_audio
