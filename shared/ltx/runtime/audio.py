from __future__ import annotations

import math
from typing import Any

import av
import torch

from ...media_cache import resolve_media_path


TARGET_SAMPLE_RATE = 44100
AUDIO_NORMALIZE_TARGET_RMS = 10 ** (-18.0 / 20.0)
AUDIO_NORMALIZE_PEAK_CEILING = 10 ** (-1.0 / 20.0)
AUDIO_NORMALIZE_MAX_GAIN = 2.0
AUDIO_NORMALIZE_EPSILON = 1e-8


def empty_audio(duration_seconds: float, sample_rate: int = TARGET_SAMPLE_RATE, channels: int = 2) -> dict[str, Any]:
    total_samples = max(1, int(math.ceil(max(0.0, float(duration_seconds or 0.0)) * sample_rate)))
    return {
        "waveform": torch.zeros((1, channels, total_samples), dtype=torch.float32),
        "sample_rate": sample_rate,
    }


def mix_timeline_audio(plan: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    config = plan.get("model_specific", {}).get("ltx", {}).get("config", {})
    duration = float(plan["resolved_output"].get("duration_seconds") or 0.0)
    frame_count = int(plan["resolved_output"].get("frame_count") or 1)
    frame_rate = float(plan["resolved_output"].get("frame_rate") or 24.0)
    duration = max(duration, frame_count / frame_rate if frame_rate > 0 else duration)
    if config.get("audio_mode") == "Ignore Timeline Audio":
        return empty_audio(duration), ["Timeline audio was ignored by LTX audio mode."]

    total_samples = max(1, int(math.ceil(duration * TARGET_SAMPLE_RATE)))
    output = torch.zeros((2, total_samples), dtype=torch.float32)
    diagnostics: list[str] = []

    for entry in plan.get("audio_plan", []):
        if entry.get("enabled") is False:
            continue
        path = entry.get("path")
        if not path:
            raise ValueError(f"LTX runtime audio clip {entry.get('item_id')} is missing a path.")
        clip_waveform = decode_audio_file(path)
        clip_waveform = trim_audio(
            clip_waveform,
            float(entry.get("source_in") or 0.0),
            entry.get("source_out"),
            float(entry.get("end_time") or 0.0) - float(entry.get("start_time") or 0.0),
        )
        if clip_waveform.shape[-1] <= 0:
            continue
        clip_waveform = apply_gain_and_fades(
            clip_waveform,
            _volume_to_gain(entry.get("volume")),
            float(entry.get("fade_in") or 0.0),
            float(entry.get("fade_out") or 0.0),
        )
        start_sample = max(0, int(round(float(entry.get("start_time") or 0.0) * TARGET_SAMPLE_RATE)))
        if start_sample >= output.shape[-1]:
            continue
        end_sample = min(output.shape[-1], start_sample + clip_waveform.shape[-1])
        length = end_sample - start_sample
        if length <= 0:
            continue
        output[:, start_sample:end_sample] += clip_waveform[:, :length]

    output = normalize_audio_waveform(output, enabled=bool(plan.get("project", {}).get("audio", {}).get("always_normalize")))
    return {"waveform": output.unsqueeze(0), "sample_rate": TARGET_SAMPLE_RATE}, diagnostics


def decode_audio_file(path_value: str) -> torch.Tensor:
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


def decode_source_video_audio(path_value: str, duration_seconds: float, source_in: float = 0.0, source_out: Any = None) -> dict[str, Any]:
    try:
        waveform = decode_audio_file(path_value)
    except Exception:
        return empty_audio(duration_seconds)
    waveform = trim_audio(waveform, source_in, source_out, duration_seconds)
    expected_samples = max(1, int(math.ceil(max(0.0, duration_seconds) * TARGET_SAMPLE_RATE)))
    if waveform.shape[-1] < expected_samples:
        pad = torch.zeros((waveform.shape[0], expected_samples - waveform.shape[-1]), dtype=waveform.dtype)
        waveform = torch.cat((waveform, pad), dim=1)
    elif waveform.shape[-1] > expected_samples:
        waveform = waveform[:, :expected_samples]
    return {"waveform": waveform.unsqueeze(0), "sample_rate": TARGET_SAMPLE_RATE}


def trim_audio(waveform: torch.Tensor, source_in: float, source_out: Any, clip_duration: float) -> torch.Tensor:
    start = max(0, int(round(max(0.0, source_in) * TARGET_SAMPLE_RATE)))
    if source_out is None or source_out == "":
        end = start + int(round(max(0.0, clip_duration) * TARGET_SAMPLE_RATE))
    else:
        end = int(round(max(0.0, float(source_out)) * TARGET_SAMPLE_RATE))
    end = min(max(start, end), waveform.shape[-1])
    return waveform[:, start:end]


def apply_gain_and_fades(waveform: torch.Tensor, gain: float, fade_in: float, fade_out: float) -> torch.Tensor:
    result = waveform * float(gain)
    sample_count = result.shape[-1]
    fade_in_samples = min(sample_count, max(0, int(round(fade_in * TARGET_SAMPLE_RATE))))
    fade_out_samples = min(sample_count, max(0, int(round(fade_out * TARGET_SAMPLE_RATE))))
    if fade_in_samples > 0:
        ramp = torch.linspace(0.0, 1.0, fade_in_samples, dtype=result.dtype, device=result.device)
        result[:, :fade_in_samples] *= ramp
    if fade_out_samples > 0:
        ramp = torch.linspace(1.0, 0.0, fade_out_samples, dtype=result.dtype, device=result.device)
        result[:, sample_count - fade_out_samples:] *= ramp
    return result


def normalize_audio_waveform(waveform: torch.Tensor, enabled: bool = True) -> torch.Tensor:
    if not enabled or not torch.is_tensor(waveform) or waveform.numel() == 0:
        return waveform
    values = torch.nan_to_num(waveform.detach().float(), nan=0.0, posinf=0.0, neginf=0.0)
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


def build_audio_latent(audio: dict[str, Any], audio_vae, frame_count: int, frame_rate: float) -> tuple[dict[str, Any], list[str]]:
    diagnostics: list[str] = []
    if audio_vae is None:
        diagnostics.append("No audio_vae connected; returned an empty audio latent placeholder.")
        return {"samples": torch.zeros((1, 0, 0, 0), dtype=torch.float32), "type": "audio"}, diagnostics

    try:
        waveform = audio["waveform"]
        vae_sample_rate = getattr(audio_vae, "audio_sample_rate", TARGET_SAMPLE_RATE)
        if int(vae_sample_rate) != int(audio.get("sample_rate", TARGET_SAMPLE_RATE)):
            import torchaudio

            waveform = torchaudio.functional.resample(waveform, int(audio["sample_rate"]), int(vae_sample_rate))
        latent_samples = audio_vae.encode(waveform.movedim(1, -1))
        if latent_samples.numel() == 0:
            raise ValueError("Encoded audio latent is empty.")
        mask = torch.zeros((1, latent_samples.shape[-2], latent_samples.shape[-1]), dtype=torch.float32, device=latent_samples.device)
        return {
            "samples": latent_samples,
            "type": "audio",
            "noise_mask": mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])),
        }, diagnostics
    except Exception:
        inner = getattr(audio_vae, "first_stage_model", audio_vae)
        z_channels = int(getattr(audio_vae, "latent_channels"))
        audio_freq = int(getattr(inner, "latent_frequency_bins"))
        num_latents = int(inner.num_of_latents_from_frames(int(frame_count), float(frame_rate)))
        diagnostics.append("Audio VAE encode failed or was unavailable; returned an empty LTX audio latent.")
        return {
            "samples": torch.zeros((1, z_channels, max(1, num_latents), audio_freq), dtype=torch.float32),
            "type": "audio",
        }, diagnostics


def build_native_audio_latent(audio_vae, frame_count: int, frame_rate: float) -> tuple[dict[str, Any], list[str]]:
    diagnostics = ["Native audio is enabled; timeline audio was not encoded as provided audio."]
    if audio_vae is None:
        diagnostics.append("No audio_vae connected; returned an empty native audio latent placeholder.")
        return {"samples": torch.zeros((1, 0, 0, 0), dtype=torch.float32), "type": "audio"}, diagnostics

    inner = getattr(audio_vae, "first_stage_model", audio_vae)
    try:
        z_channels = int(getattr(audio_vae, "latent_channels"))
        audio_freq = int(getattr(inner, "latent_frequency_bins"))
        num_latents = int(inner.num_of_latents_from_frames(int(frame_count), float(frame_rate)))
    except Exception as exc:
        raise ValueError("Native audio requires an LTX audio VAE to create the empty audio latent.") from exc

    try:
        import comfy.model_management

        device = comfy.model_management.intermediate_device()
    except Exception:
        device = "cpu"
    return {
        "samples": torch.zeros((1, z_channels, max(1, num_latents), audio_freq), dtype=torch.float32, device=device),
        "type": "audio",
    }, diagnostics


def _volume_to_gain(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 100.0
    return max(0.0, number / 100.0)


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
