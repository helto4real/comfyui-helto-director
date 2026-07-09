from __future__ import annotations

import math
from typing import Any

import av
import torch

from .media_cache import resolve_media_path


TARGET_SAMPLE_RATE = 44100
AUDIO_NORMALIZE_TARGET_RMS = 10 ** (-18.0 / 20.0)
AUDIO_NORMALIZE_PEAK_CEILING = 10 ** (-1.0 / 20.0)
AUDIO_NORMALIZE_MAX_GAIN = 2.0
AUDIO_NORMALIZE_EPSILON = 1e-8


def empty_audio(
    duration_seconds: float,
    sample_rate: int = TARGET_SAMPLE_RATE,
    channels: int = 2,
) -> dict[str, Any]:
    """Return a silent ComfyUI AUDIO value with an explicit duration."""
    total_samples = max(
        1,
        int(math.ceil(max(0.0, float(duration_seconds or 0.0)) * sample_rate)),
    )
    return {
        "waveform": torch.zeros((1, channels, total_samples), dtype=torch.float32),
        "sample_rate": sample_rate,
    }


def mix_audio_clips(
    audio_plan: list[dict[str, Any]],
    duration_seconds: float,
    *,
    normalize: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """Decode and mix timeline-positioned clips into one stereo AUDIO value.

    The caller owns model and product policy. This function only performs the
    operational decode, trim, gain, fade, placement, and normalization work.
    """
    duration = max(0.0, float(duration_seconds or 0.0))
    total_samples = max(1, int(math.ceil(duration * TARGET_SAMPLE_RATE)))
    output = torch.zeros((2, total_samples), dtype=torch.float32)
    diagnostics: list[str] = []

    for entry in audio_plan:
        if entry.get("enabled") is False:
            continue
        start_time = _safe_float(entry.get("start_time"), 0.0)
        end_time = _safe_float(entry.get("end_time"), start_time)
        clip_waveform = entry.get("waveform")
        if not torch.is_tensor(clip_waveform):
            path = entry.get("path")
            if not path:
                raise ValueError(f"Audio clip {entry.get('item_id')} is missing a path.")
            clip_waveform = decode_audio_file(str(path))
        clip_waveform = trim_audio(
            clip_waveform,
            _safe_float(entry.get("source_in"), 0.0),
            entry.get("source_out"),
            end_time - start_time,
        )
        if clip_waveform.shape[-1] <= 0:
            continue
        clip_waveform = apply_gain_and_fades(
            clip_waveform,
            volume_to_gain(entry.get("volume")),
            _safe_float(entry.get("fade_in"), 0.0),
            _safe_float(entry.get("fade_out"), 0.0),
        )
        start_sample = max(0, int(round(start_time * TARGET_SAMPLE_RATE)))
        if start_sample >= output.shape[-1]:
            continue
        end_sample = min(output.shape[-1], start_sample + clip_waveform.shape[-1])
        length = end_sample - start_sample
        if length <= 0:
            continue
        output[:, start_sample:end_sample] += clip_waveform[:, :length]

    output = normalize_audio_waveform(output, enabled=normalize)
    return {"waveform": output.unsqueeze(0), "sample_rate": TARGET_SAMPLE_RATE}, diagnostics


def decode_audio_file(path_value: str) -> torch.Tensor:
    """Decode any supported audio stream to stereo float32 at 44.1 kHz."""
    path = resolve_media_path(path_value)
    frames: list[torch.Tensor] = []
    with av.open(str(path)) as container:
        stream = next((stream for stream in container.streams if stream.type == "audio"), None)
        if stream is None:
            raise ValueError(f"Audio media has no audio stream: {path}")
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=TARGET_SAMPLE_RATE)
        for frame in container.decode(stream):
            frames.extend(_resampled_tensors(resampler.resample(frame)))
        frames.extend(_resampled_tensors(resampler.resample(None)))
    if not frames:
        raise ValueError(f"Could not decode audio media: {path}")
    waveform = torch.cat(frames, dim=1).to(torch.float32)
    if waveform.shape[0] == 1:
        waveform = waveform.repeat(2, 1)
    elif waveform.shape[0] > 2:
        waveform = waveform[:2]
    return waveform.contiguous()


def decode_source_video_audio(
    path_value: str,
    duration_seconds: float,
    source_in: float = 0.0,
    source_out: Any = None,
) -> dict[str, Any]:
    """Decode a video's audio stream, returning duration-matched silence if absent."""
    try:
        waveform = decode_audio_file(path_value)
    except Exception:
        return empty_audio(duration_seconds)
    waveform = trim_audio(waveform, source_in, source_out, duration_seconds)
    expected_samples = max(
        1,
        int(math.ceil(max(0.0, duration_seconds) * TARGET_SAMPLE_RATE)),
    )
    if waveform.shape[-1] < expected_samples:
        pad = torch.zeros(
            (waveform.shape[0], expected_samples - waveform.shape[-1]),
            dtype=waveform.dtype,
        )
        waveform = torch.cat((waveform, pad), dim=1)
    elif waveform.shape[-1] > expected_samples:
        waveform = waveform[:, :expected_samples]
    return {"waveform": waveform.unsqueeze(0), "sample_rate": TARGET_SAMPLE_RATE}


def trim_audio(
    waveform: torch.Tensor,
    source_in: float,
    source_out: Any,
    clip_duration: float,
) -> torch.Tensor:
    start = max(0, int(round(max(0.0, source_in) * TARGET_SAMPLE_RATE)))
    if source_out is None or source_out == "":
        end = start + int(round(max(0.0, clip_duration) * TARGET_SAMPLE_RATE))
    else:
        end = int(round(max(0.0, float(source_out)) * TARGET_SAMPLE_RATE))
    end = min(max(start, end), waveform.shape[-1])
    return waveform[:, start:end]


def apply_gain_and_fades(
    waveform: torch.Tensor,
    gain: float,
    fade_in: float,
    fade_out: float,
) -> torch.Tensor:
    result = waveform * float(gain)
    sample_count = result.shape[-1]
    fade_in_samples = min(
        sample_count,
        max(0, int(round(fade_in * TARGET_SAMPLE_RATE))),
    )
    fade_out_samples = min(
        sample_count,
        max(0, int(round(fade_out * TARGET_SAMPLE_RATE))),
    )
    if fade_in_samples > 0:
        ramp = torch.linspace(
            0.0,
            1.0,
            fade_in_samples,
            dtype=result.dtype,
            device=result.device,
        )
        result[:, :fade_in_samples] *= ramp
    if fade_out_samples > 0:
        ramp = torch.linspace(
            1.0,
            0.0,
            fade_out_samples,
            dtype=result.dtype,
            device=result.device,
        )
        result[:, sample_count - fade_out_samples:] *= ramp
    return result


def normalize_audio_waveform(
    waveform: torch.Tensor,
    enabled: bool = True,
) -> torch.Tensor:
    if not enabled or not torch.is_tensor(waveform) or waveform.numel() == 0:
        return waveform
    values = torch.nan_to_num(
        waveform.detach().float(),
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )
    peak = float(values.abs().max())
    if peak <= AUDIO_NORMALIZE_EPSILON:
        return waveform
    rms = float(torch.sqrt(torch.mean(values * values)))
    if rms <= AUDIO_NORMALIZE_EPSILON:
        return waveform
    rms_gain = AUDIO_NORMALIZE_TARGET_RMS / rms
    peak_gain = AUDIO_NORMALIZE_PEAK_CEILING / peak
    gain = min(rms_gain, peak_gain, AUDIO_NORMALIZE_MAX_GAIN)
    normalized = waveform * gain
    final_peak = float(normalized.detach().float().abs().max())
    if final_peak > AUDIO_NORMALIZE_PEAK_CEILING:
        normalized = normalized * (AUDIO_NORMALIZE_PEAK_CEILING / final_peak)
    return normalized


def audio_stream_is_decodable(path_value: str) -> bool:
    """Return whether a media file contains at least one decodable audio frame."""
    try:
        path = resolve_media_path(path_value)
        with av.open(str(path)) as container:
            stream = next(
                (stream for stream in container.streams if stream.type == "audio"),
                None,
            )
            if stream is None:
                return False
            for _frame in container.decode(stream):
                return True
    except Exception:
        return False
    return False


def volume_to_gain(value: Any) -> float:
    number = _safe_float(value, 100.0)
    return max(0.0, number / 100.0)


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _resampled_tensors(value) -> list[torch.Tensor]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        value = [value]
    tensors = []
    for frame in value:
        if frame is None:
            continue
        tensors.append(torch.from_numpy(frame.to_ndarray()).to(torch.float32))
    return tensors
