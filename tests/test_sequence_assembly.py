import json
import math
import struct
import wave
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
import torch

from shared.contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_SOURCE_GENERATED,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_VIDEO,
    BOUNDARY_MODE_BLEND_SEAM,
    BOUNDARY_MODE_HARD_CUT,
    BOUNDARY_MODE_TRANSITION,
    SHOT_TYPE_GENERATED,
    SHOT_TYPE_IMPORTED,
    TAKE_STATUS_ACCEPTED,
)
from shared.timeline import (
    SequenceAssemblyError,
    assemble_timeline_sequence,
    create_default_video_timeline,
)


def test_assemble_two_accepted_generated_takes_with_hard_cut(tmp_path):
    first = _write_test_video(tmp_path / "first.mp4", color=(255, 0, 0))
    second = _write_test_video(tmp_path / "second.mp4", color=(0, 0, 255))
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_first", first, ASSET_SOURCE_GENERATED),
            _video_asset("asset_second", second, ASSET_SOURCE_GENERATED),
        ],
        [
            _generated_shot("shot_first", "asset_first", 0.0, 1.0),
            _generated_shot("shot_second", "asset_second", 1.0, 2.0),
        ],
        boundaries=[
            _boundary("boundary_first_second", "shot_first", "shot_second", BOUNDARY_MODE_HARD_CUT)
        ],
    )

    frames, audio, frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (8, 16, 16, 3)
    assert frame_rate == 4.0
    assert audio["waveform"].shape[-1] > 0
    assert debug["summary"]["included_clip_count"] == 2
    assert debug["boundaries"][0]["mode"] == BOUNDARY_MODE_HARD_CUT
    assert debug["boundaries"][0]["status"] == "concatenated"
    assert [clip["asset_id"] for clip in debug["clips"]] == ["asset_first", "asset_second"]
    assert str(first) not in json.dumps(debug)


def test_assemble_imported_clip_plus_generated_take(tmp_path):
    imported = _write_test_video(tmp_path / "imported.mp4", color=(0, 255, 0), frame_count=8, fps=8)
    generated = _write_test_video(tmp_path / "generated.mp4", color=(255, 255, 0))
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_imported", imported, ASSET_SOURCE_FILE_PATH),
            _video_asset("asset_generated", generated, ASSET_SOURCE_GENERATED),
        ],
        [
            _imported_shot(
                "shot_imported",
                "asset_imported",
                0.0,
                1.0,
                source_in=0.25,
                source_out=0.75,
            ),
            _generated_shot("shot_generated", "asset_generated", 1.0, 2.0),
        ],
    )

    frames, _audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (8, 16, 16, 3)
    assert debug["clips"][0]["source_kind"] == "imported_clip"
    assert debug["clips"][0]["source_range"]["start"] == 0.25
    assert debug["clips"][0]["source_range"]["end"] == 0.75
    assert debug["clips"][1]["source_kind"] == "accepted_take"


def test_missing_accepted_take_warns_and_skips_when_policy_allows(tmp_path):
    generated = _write_test_video(tmp_path / "generated.mp4", color=(128, 128, 255))
    timeline = _timeline_with_assets(
        [_video_asset("asset_generated", generated, ASSET_SOURCE_GENERATED)],
        [
            _generated_shot("shot_generated", "asset_generated", 0.0, 1.0),
            {
                "shot_id": "shot_missing",
                "type": SHOT_TYPE_GENERATED,
                "start_time": 1.0,
                "end_time": 2.0,
            },
        ],
    )

    frames, _audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (4, 16, 16, 3)
    assert debug["summary"]["missing_accepted_take_count"] == 1
    assert "SEQUENCE_ASSEMBLY_ACCEPTED_TAKE_MISSING" in _warning_codes(debug)


def test_missing_accepted_take_media_warns_and_skips_when_policy_allows(tmp_path):
    valid = _write_test_video(tmp_path / "valid.mp4", color=(128, 128, 255))
    missing = tmp_path / "deleted.mp4"
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_missing", missing, ASSET_SOURCE_GENERATED),
            _video_asset("asset_valid", valid, ASSET_SOURCE_GENERATED),
        ],
        [
            _generated_shot("shot_missing", "asset_missing", 0.0, 1.0),
            _generated_shot("shot_valid", "asset_valid", 1.0, 2.0),
        ],
    )

    frames, _audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (4, 16, 16, 3)
    assert debug["summary"]["included_clip_count"] == 1
    assert debug["summary"]["missing_source_media_count"] == 1
    assert "SEQUENCE_ASSEMBLY_SOURCE_MEDIA_MISSING" in _warning_codes(debug)
    assert debug["shots"][0]["status"] == "skipped"
    assert debug["shots"][1]["status"] == "included"
    assert str(missing) not in json.dumps(debug)


def test_missing_accepted_take_media_errors_when_policy_requires_it(tmp_path):
    missing = tmp_path / "deleted.mp4"
    timeline = _timeline_with_assets(
        [_video_asset("asset_missing", missing, ASSET_SOURCE_GENERATED)],
        [_generated_shot("shot_missing", "asset_missing", 0.0, 1.0)],
    )

    with pytest.raises(SequenceAssemblyError, match="SEQUENCE_ASSEMBLY_SOURCE_MEDIA_MISSING"):
        assemble_timeline_sequence(timeline, missing_take_policy="error")


def test_sequence_assembly_without_ready_clips_returns_placeholder():
    timeline = _timeline_with_assets(
        [],
        [
            {
                "shot_id": "shot_missing",
                "type": SHOT_TYPE_GENERATED,
                "start_time": 0.0,
                "end_time": 1.0,
            },
        ],
    )

    frames, audio, frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (1, 16, 16, 3)
    assert float(frames.sum()) == 0.0
    assert frame_rate == 4.0
    assert torch.is_tensor(audio["waveform"])
    assert debug["summary"]["status"] == "not_built"
    assert debug["summary"]["included_clip_count"] == 0
    assert debug["summary"]["output_frame_count"] == 0
    assert debug["summary"]["placeholder_output_frame_count"] == 1
    assert debug["errors"] == []
    assert "SEQUENCE_ASSEMBLY_NO_CLIPS" in _warning_codes(debug)


def test_missing_accepted_take_errors_when_policy_requires_it(tmp_path):
    timeline = _timeline_with_assets(
        [],
        [
            {
                "shot_id": "shot_missing",
                "type": SHOT_TYPE_GENERATED,
                "start_time": 0.0,
                "end_time": 1.0,
            },
        ],
    )

    with pytest.raises(SequenceAssemblyError, match="SEQUENCE_ASSEMBLY_ACCEPTED_TAKE_MISSING"):
        assemble_timeline_sequence(timeline, missing_take_policy="error")


def test_missing_asset_reference_raises_clear_error(tmp_path):
    timeline = _timeline_with_assets(
        [],
        [_generated_shot("shot_generated", "asset_missing", 0.0, 1.0)],
    )

    with pytest.raises(SequenceAssemblyError, match="SEQUENCE_ASSEMBLY_ASSET_NOT_FOUND"):
        assemble_timeline_sequence(timeline)


def test_blend_seam_applies_when_frames_are_compatible(tmp_path):
    first = _write_test_video(tmp_path / "first.mp4", color=(0, 0, 0))
    second = _write_test_video(tmp_path / "second.mp4", color=(255, 255, 255))
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_first", first, ASSET_SOURCE_GENERATED),
            _video_asset("asset_second", second, ASSET_SOURCE_GENERATED),
        ],
        [
            _generated_shot("shot_first", "asset_first", 0.0, 1.0),
            _generated_shot("shot_second", "asset_second", 1.0, 2.0),
        ],
        boundaries=[
            _boundary(
                "boundary_blend",
                "shot_first",
                "shot_second",
                BOUNDARY_MODE_BLEND_SEAM,
                blend_frames=2,
            )
        ],
    )

    frames, _audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (6, 16, 16, 3)
    assert debug["boundaries"][0]["status"] == "blend_applied"
    blended_mean = float(frames[2].mean())
    assert 0.05 < blended_mean < 0.95


def test_blend_seam_falls_back_when_frames_are_too_short(tmp_path):
    first = _write_test_video(tmp_path / "first.mp4", color=(0, 0, 0))
    second = _write_test_video(tmp_path / "second.mp4", color=(255, 255, 255))
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_first", first, ASSET_SOURCE_GENERATED),
            _video_asset("asset_second", second, ASSET_SOURCE_GENERATED),
        ],
        [
            _generated_shot("shot_first", "asset_first", 0.0, 1.0),
            _generated_shot("shot_second", "asset_second", 1.0, 2.0),
        ],
        boundaries=[
            _boundary(
                "boundary_blend",
                "shot_first",
                "shot_second",
                BOUNDARY_MODE_BLEND_SEAM,
                blend_frames=8,
            )
        ],
    )

    frames, _audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (8, 16, 16, 3)
    assert debug["boundaries"][0]["status"] == "blend_fallback_concatenate"
    assert "SEQUENCE_ASSEMBLY_BLEND_FALLBACK" in _warning_codes(debug)


def test_transition_boundary_falls_back_with_warning(tmp_path):
    first = _write_test_video(tmp_path / "first.mp4", color=(64, 64, 64))
    second = _write_test_video(tmp_path / "second.mp4", color=(192, 192, 192))
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_first", first, ASSET_SOURCE_GENERATED),
            _video_asset("asset_second", second, ASSET_SOURCE_GENERATED),
        ],
        [
            _generated_shot("shot_first", "asset_first", 0.0, 1.0),
            _generated_shot("shot_second", "asset_second", 1.0, 2.0),
        ],
        boundaries=[
            _boundary("boundary_transition", "shot_first", "shot_second", BOUNDARY_MODE_TRANSITION)
        ],
    )

    frames, _audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (8, 16, 16, 3)
    assert debug["boundaries"][0]["status"] == "transition_fallback_concatenate"
    assert "SEQUENCE_ASSEMBLY_TRANSITION_FALLBACK" in _warning_codes(debug)


def test_transition_boundary_uses_generated_ltx_metadata_without_warning(tmp_path):
    first = _write_test_video(tmp_path / "first.mp4", color=(64, 64, 64))
    second = _write_test_video(tmp_path / "second.mp4", color=(192, 192, 192))
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_first", first, ASSET_SOURCE_GENERATED),
            _video_asset("asset_second", second, ASSET_SOURCE_GENERATED),
        ],
        [
            _generated_shot("shot_first", "asset_first", 0.0, 1.0),
            _generated_shot("shot_second", "asset_second", 1.0, 2.0),
        ],
        boundaries=[
            _boundary("boundary_transition", "shot_first", "shot_second", BOUNDARY_MODE_TRANSITION)
        ],
    )
    timeline["sequence"]["shots"][1]["takes"][0]["metadata"] = {
        "model_specific": {
            "ltx": {
                "boundary_conditioning": {
                    "mode": BOUNDARY_MODE_TRANSITION,
                    "policy": "transition",
                    "model_status": "applied",
                    "boundary_id": "boundary_transition",
                    "source_shot_id": "shot_first",
                    "target_shot_id": "shot_second",
                    "asset_id": "asset_first",
                    "effective_tail_frames": 9,
                }
            }
        }
    }

    frames, _audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (8, 16, 16, 3)
    assert debug["boundaries"][0]["status"] == "transition_generated_bridge_concatenate"
    assert debug["boundaries"][0]["boundary_conditioning"]["model_status"] == "applied"
    assert "SEQUENCE_ASSEMBLY_TRANSITION_FALLBACK" not in _warning_codes(debug)


def test_transition_boundary_uses_generated_wan_metadata_without_warning(tmp_path):
    first = _write_test_video(tmp_path / "first.mp4", color=(64, 64, 64))
    second = _write_test_video(tmp_path / "second.mp4", color=(192, 192, 192))
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_first", first, ASSET_SOURCE_GENERATED),
            _video_asset("asset_second", second, ASSET_SOURCE_GENERATED),
        ],
        [
            _generated_shot("shot_first", "asset_first", 0.0, 1.0),
            _generated_shot("shot_second", "asset_second", 1.0, 2.0),
        ],
        boundaries=[
            _boundary("boundary_transition", "shot_first", "shot_second", BOUNDARY_MODE_TRANSITION)
        ],
    )
    timeline["sequence"]["shots"][1]["takes"][0]["metadata"] = {
        "model_specific": {
            "wan": {
                "boundary_conditioning": {
                    "mode": BOUNDARY_MODE_TRANSITION,
                    "policy": "transition",
                    "model_status": "applied",
                    "runtime_status": "applied",
                    "boundary_id": "boundary_transition",
                    "source_shot_id": "shot_first",
                    "target_shot_id": "shot_second",
                    "asset_id": "asset_first",
                    "effective_tail_frames": 9,
                }
            }
        }
    }

    frames, _audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert tuple(frames.shape) == (8, 16, 16, 3)
    assert debug["boundaries"][0]["status"] == "transition_generated_bridge_concatenate"
    assert debug["boundaries"][0]["boundary_conditioning"]["runtime_status"] == "applied"
    assert "SEQUENCE_ASSEMBLY_TRANSITION_FALLBACK" not in _warning_codes(debug)


def test_sequence_assembly_mixes_timeline_audio(tmp_path):
    video = _write_test_video(tmp_path / "video.mp4", color=(255, 128, 0))
    audio = _write_test_wav(tmp_path / "tone.wav")
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_video", video, ASSET_SOURCE_GENERATED),
            {
                "asset_id": "asset_audio",
                "type": ASSET_TYPE_AUDIO,
                "source_kind": ASSET_SOURCE_FILE_PATH,
                "path": str(audio),
                "name": audio.name,
            },
        ],
        [_generated_shot("shot_video", "asset_video", 0.0, 1.0)],
    )
    timeline["audio_tracks"] = [
        {
            "track_id": "music",
            "clips": [
                {
                    "item_id": "audio_001",
                    "audio": {"asset_id": "asset_audio"},
                    "start_time": 0.0,
                    "end_time": 1.0,
                    "volume": 100.0,
                }
            ],
        }
    ]

    _frames, mixed_audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert debug["audio"]["status"] == "mixed"
    assert debug["audio"]["clip_count"] == 1
    assert torch.is_tensor(mixed_audio["waveform"])
    assert float(mixed_audio["waveform"].abs().max()) > 0.0
    assert "data:" not in json.dumps(debug)
    assert "waveform" not in json.dumps(debug)


def test_sequence_assembly_preserves_embedded_audio_from_accepted_take(tmp_path):
    video = _write_test_video_with_audio(tmp_path / "captured_with_audio.mp4", color=(255, 64, 64))
    timeline = _timeline_with_assets(
        [_video_asset("asset_video", video, ASSET_SOURCE_GENERATED)],
        [_generated_shot("shot_video", "asset_video", 0.0, 1.0)],
    )

    _frames, mixed_audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert debug["audio"]["status"] == "mixed"
    assert debug["audio"]["timeline_clip_count"] == 0
    assert debug["audio"]["source_clip_count"] == 1
    assert debug["audio"]["source_mixed_clip_count"] == 1
    assert debug["audio"]["source_skipped_clip_count"] == 0
    assert debug["audio"]["clip_count"] == 1
    assert torch.is_tensor(mixed_audio["waveform"])
    assert float(mixed_audio["waveform"].abs().max()) > 0.0
    assert str(video) not in json.dumps(debug)


def test_sequence_assembly_overlays_embedded_video_audio_and_timeline_audio(tmp_path):
    video = _write_test_video_with_audio(tmp_path / "captured_with_audio.mp4", color=(255, 64, 64))
    music = _write_test_wav(tmp_path / "music.wav", duration=1.0, frequency=880.0)
    timeline = _timeline_with_assets(
        [
            _video_asset("asset_video", video, ASSET_SOURCE_GENERATED),
            {
                "asset_id": "asset_music",
                "type": ASSET_TYPE_AUDIO,
                "source_kind": ASSET_SOURCE_FILE_PATH,
                "path": str(music),
                "name": music.name,
            },
        ],
        [_generated_shot("shot_video", "asset_video", 0.0, 2.0)],
    )
    timeline["audio_tracks"] = [
        {
            "track_id": "music",
            "clips": [
                {
                    "item_id": "audio_001",
                    "audio": {"asset_id": "asset_music"},
                    "start_time": 1.0,
                    "end_time": 2.0,
                    "volume": 100.0,
                }
            ],
        }
    ]

    _frames, mixed_audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert debug["audio"]["status"] == "mixed"
    assert debug["audio"]["timeline_clip_count"] == 1
    assert debug["audio"]["source_clip_count"] == 1
    assert debug["audio"]["source_mixed_clip_count"] == 1
    assert debug["audio"]["clip_count"] == 2
    waveform = mixed_audio["waveform"]
    assert float(waveform[:, :, :44100].abs().max()) > 0.0
    assert float(waveform[:, :, 44100:].abs().max()) > 0.0


def test_sequence_assembly_skips_source_video_without_audio(tmp_path):
    video = _write_test_video(tmp_path / "silent_video.mp4", color=(128, 128, 128))
    timeline = _timeline_with_assets(
        [_video_asset("asset_video", video, ASSET_SOURCE_GENERATED)],
        [_generated_shot("shot_video", "asset_video", 0.0, 1.0)],
    )

    _frames, mixed_audio, _frame_rate, debug = assemble_timeline_sequence(timeline)

    assert debug["audio"]["status"] == "empty"
    assert debug["audio"]["source_clip_count"] == 1
    assert debug["audio"]["source_mixed_clip_count"] == 0
    assert debug["audio"]["source_skipped_clip_count"] == 1
    assert debug["audio"]["clip_count"] == 0
    assert any("no decodable audio stream" in entry for entry in debug["audio"]["diagnostics"])
    assert float(mixed_audio["waveform"].abs().max()) == 0.0


def _timeline_with_assets(assets, shots, *, boundaries=None):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["project"]["frame_rate"] = 4.0
    timeline["assets"] = list(assets)
    timeline["sequence"]["shots"] = list(shots)
    timeline["sequence"]["boundaries"] = list(boundaries or [])
    return timeline


def _video_asset(asset_id: str, path: Path, source_kind: str) -> dict:
    return {
        "asset_id": asset_id,
        "type": ASSET_TYPE_VIDEO,
        "source_kind": source_kind,
        "path": str(path),
        "name": path.name,
    }


def _generated_shot(shot_id: str, asset_id: str, start: float, end: float) -> dict:
    take_id = f"take_{shot_id}"
    return {
        "shot_id": shot_id,
        "type": SHOT_TYPE_GENERATED,
        "start_time": start,
        "end_time": end,
        "takes": [
            {
                "take_id": take_id,
                "asset_id": asset_id,
                "status": TAKE_STATUS_ACCEPTED,
            }
        ],
        "accepted_take_id": take_id,
        "clip_instance": {"asset_id": asset_id},
    }


def _imported_shot(
    shot_id: str,
    asset_id: str,
    start: float,
    end: float,
    *,
    source_in: float = 0.0,
    source_out=None,
) -> dict:
    return {
        "shot_id": shot_id,
        "type": SHOT_TYPE_IMPORTED,
        "start_time": start,
        "end_time": end,
        "clip_instance": {
            "asset_id": asset_id,
            "source_in": source_in,
            "source_out": source_out,
            "enabled": True,
        },
    }


def _boundary(boundary_id: str, left: str, right: str, mode: str, *, blend_frames: int = 3) -> dict:
    return {
        "boundary_id": boundary_id,
        "left_shot_id": left,
        "right_shot_id": right,
        "mode": mode,
        "blend_frames": blend_frames,
    }


def _write_test_video(
    path: Path,
    *,
    color: tuple[int, int, int],
    frame_count: int = 4,
    fps: int = 4,
    width: int = 16,
    height: int = 16,
) -> Path:
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for _index in range(frame_count):
            array = np.zeros((height, width, 3), dtype=np.uint8)
            array[:, :, 0] = color[0]
            array[:, :, 1] = color[1]
            array[:, :, 2] = color[2]
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def _write_test_video_with_audio(
    path: Path,
    *,
    color: tuple[int, int, int],
    frame_count: int = 4,
    fps: int = 4,
    width: int = 16,
    height: int = 16,
    frequency: float = 440.0,
) -> Path:
    sample_rate = 44100
    samples_per_video_frame = sample_rate // fps
    audio_position = 0
    with av.open(str(path), "w") as container:
        video_stream = container.add_stream("mpeg4", rate=fps)
        video_stream.width = width
        video_stream.height = height
        video_stream.pix_fmt = "yuv420p"
        audio_stream = container.add_stream("aac", rate=sample_rate, layout="stereo")
        for frame_index in range(frame_count):
            array = np.zeros((height, width, 3), dtype=np.uint8)
            array[:, :, 0] = color[0]
            array[:, :, 1] = color[1]
            array[:, :, 2] = color[2]
            video_frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            video_frame.pts = frame_index
            video_frame.time_base = Fraction(1, fps)
            for packet in video_stream.encode(video_frame):
                container.mux(packet)

            sample_indices = np.arange(samples_per_video_frame, dtype=np.float32) + audio_position
            tone = (0.25 * np.sin(2.0 * math.pi * frequency * sample_indices / sample_rate)).astype(np.float32)
            audio_frame = av.AudioFrame.from_ndarray(np.stack([tone, tone]), format="fltp", layout="stereo")
            audio_frame.sample_rate = sample_rate
            audio_frame.pts = audio_position
            audio_frame.time_base = Fraction(1, sample_rate)
            audio_position += samples_per_video_frame
            for packet in audio_stream.encode(audio_frame):
                container.mux(packet)
        for packet in video_stream.encode():
            container.mux(packet)
        for packet in audio_stream.encode(None):
            container.mux(packet)
    return path


def _write_test_wav(path: Path, *, duration: float = 1.0, frequency: float = 440.0) -> Path:
    sample_rate = 44100
    total = int(sample_rate * duration)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        for index in range(total):
            value = int(math.sin(2.0 * math.pi * frequency * index / sample_rate) * 16000)
            output.writeframes(struct.pack("<h", value))
    return path


def _warning_codes(debug: dict) -> list[str]:
    return [entry["code"] for entry in debug.get("warnings", [])]
