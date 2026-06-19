from __future__ import annotations

import math
from typing import Any

import av
import torch

from ...contracts.video_timeline import SECTION_TYPE_VIDEO
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


def apply_native_source_video_audio_fallback(plan: dict[str, Any], timeline_audio: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not bool(plan.get("project", {}).get("audio", {}).get("use_native_audio")):
        return timeline_audio, []
    if _has_enabled_timeline_audio(plan):
        return timeline_audio, ["Native source-video audio fallback skipped because timeline audio clips are present."]

    source_audio, diagnostics = build_native_source_video_audio_fallback(plan)
    if source_audio is None:
        return timeline_audio, [
            *diagnostics,
            "Native source-video audio fallback unavailable; returning timeline audio mix.",
        ]
    return source_audio, [
        *diagnostics,
        "Native source-video audio fallback applied to executor audio output.",
    ]


def build_native_source_video_audio_fallback(plan: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    duration = _plan_duration_seconds(plan)
    total_samples = max(1, int(math.ceil(duration * TARGET_SAMPLE_RATE)))
    output = torch.zeros((2, total_samples), dtype=torch.float32)
    frame_rate = float(plan.get("resolved_output", {}).get("frame_rate") or 24.0)
    sections_by_id = {entry.get("item_id"): entry for entry in plan.get("section_plan", [])}
    video_media = [
        entry
        for entry in plan.get("media_plan", [])
        if entry.get("section_type") == SECTION_TYPE_VIDEO and entry.get("path")
    ]
    if not video_media:
        return None, ["Native source-video audio fallback unavailable; no video media with paths was found."]

    applied_count = 0
    diagnostics: list[str] = []
    for media in video_media:
        section = sections_by_id.get(media.get("item_id"), {})
        start_frame = int(section.get("start_frame") or 0)
        end_frame = int(section.get("end_frame_exclusive") or start_frame)
        if end_frame <= start_frame or frame_rate <= 0:
            continue
        section_duration = (end_frame - start_frame) / frame_rate
        try:
            waveform = decode_audio_file(str(media.get("path")))
        except Exception:
            diagnostics.append(
                f"Native source-video audio fallback skipped {media.get('item_id') or 'video media'}; no decodable audio stream was found."
            )
            continue

        clip_waveform = trim_audio(
            waveform,
            float(media.get("source_in") or 0.0),
            media.get("source_out"),
            section_duration,
        )
        max_clip_samples = max(1, int(math.ceil(section_duration * TARGET_SAMPLE_RATE)))
        clip_waveform = clip_waveform[:, :max_clip_samples]
        if clip_waveform.shape[-1] <= 0:
            continue
        start_sample = max(0, int(round((start_frame / frame_rate) * TARGET_SAMPLE_RATE)))
        if start_sample >= output.shape[-1]:
            continue
        end_sample = min(output.shape[-1], start_sample + clip_waveform.shape[-1])
        length = end_sample - start_sample
        if length <= 0:
            continue
        output[:, start_sample:end_sample] += clip_waveform[:, :length]
        applied_count += 1

    if applied_count <= 0:
        if not diagnostics:
            diagnostics.append("Native source-video audio fallback unavailable; no decodable source-video audio was found.")
        return None, diagnostics

    output = normalize_audio_waveform(output, enabled=bool(plan.get("project", {}).get("audio", {}).get("always_normalize")))
    return {"waveform": output.unsqueeze(0), "sample_rate": TARGET_SAMPLE_RATE}, diagnostics


def build_native_av_sampling_latent(video_latent: dict[str, Any], audio_latent: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    debug = {
        "av_latent_sampling": False,
        "video_latent_shape": _tensor_shape(video_latent.get("samples") if isinstance(video_latent, dict) else None),
        "audio_latent_shape": _tensor_shape(audio_latent.get("samples") if isinstance(audio_latent, dict) else None),
        "diagnostics": [],
    }
    if not _has_non_empty_audio_latent(audio_latent):
        debug["diagnostics"].append("Native generated audio unavailable; runtime returned an empty audio latent.")
        return video_latent, debug

    try:
        import comfy.nested_tensor
    except Exception as exc:
        debug["diagnostics"].append(f"Native generated audio unavailable; could not import ComfyUI nested tensor support: {exc}.")
        return video_latent, debug

    output = {}
    output.update(video_latent)
    output.update(audio_latent or {})
    video_noise_mask = video_latent.get("noise_mask") if isinstance(video_latent, dict) else None
    audio_noise_mask = audio_latent.get("noise_mask") if isinstance(audio_latent, dict) else None
    if video_noise_mask is not None or audio_noise_mask is not None:
        video_samples = video_latent.get("samples") if isinstance(video_latent, dict) else None
        audio_samples = audio_latent.get("samples") if isinstance(audio_latent, dict) else None
        if video_noise_mask is None and torch.is_tensor(video_samples):
            video_noise_mask = torch.ones_like(video_samples)
        if audio_noise_mask is None and torch.is_tensor(audio_samples):
            audio_noise_mask = torch.ones_like(audio_samples)
        output["noise_mask"] = comfy.nested_tensor.NestedTensor((video_noise_mask, audio_noise_mask))
    output["samples"] = comfy.nested_tensor.NestedTensor((video_latent["samples"], audio_latent["samples"]))
    debug["av_latent_sampling"] = True
    debug["nested_latent_shape"] = _nested_shape(output["samples"])
    return output, debug


def decode_native_generated_audio(sampled_latent: dict[str, Any], audio_vae) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    debug = {
        "decoded": False,
        "sampled_audio_latent_shape": None,
        "decoded_audio_shape": None,
        "sample_rate": None,
        "diagnostics": [],
    }
    if audio_vae is None:
        debug["diagnostics"].append("Native generated audio unavailable; no audio_vae is connected.")
        return None, debug
    audio_samples = _audio_samples_from_latent(sampled_latent)
    debug["sampled_audio_latent_shape"] = _tensor_shape(audio_samples)
    if not torch.is_tensor(audio_samples) or audio_samples.numel() <= 0:
        debug["diagnostics"].append("Native generated audio unavailable; sampled AV latent did not contain audio samples.")
        return None, debug
    try:
        decoded = audio_vae.decode(audio_samples)
        if decoded.ndim == 2:
            decoded = decoded.unsqueeze(0)
        waveform = decoded.movedim(-1, 1).to(audio_samples.device)
        sample_rate = int(getattr(getattr(audio_vae, "first_stage_model", audio_vae), "output_sample_rate"))
    except Exception as exc:
        debug["diagnostics"].append(f"Native generated audio decode failed; returning fallback audio: {exc}.")
        return None, debug
    debug["decoded"] = True
    debug["decoded_audio_shape"] = _tensor_shape(waveform)
    debug["sample_rate"] = sample_rate
    return {"waveform": waveform, "sample_rate": sample_rate}, debug


def stitch_native_generated_audio(
    segment_records: list[dict[str, Any]],
    *,
    final_frame_count: int,
    frame_rate: float,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    debug = {
        "decoded_segment_count": len(segment_records),
        "final_audio_shape": None,
        "sample_rate": None,
        "diagnostics": [],
    }
    if not segment_records:
        debug["diagnostics"].append("Native generated audio unavailable; no segment audio decoded.")
        return None, debug
    frame_rate = float(frame_rate or 24.0)
    if frame_rate <= 0:
        debug["diagnostics"].append("Native generated audio unavailable; invalid frame rate for audio stitching.")
        return None, debug

    sample_rate = int(segment_records[0]["audio"].get("sample_rate") or TARGET_SAMPLE_RATE)
    pieces = []
    for record in segment_records:
        audio = record.get("audio") or {}
        if int(audio.get("sample_rate") or sample_rate) != sample_rate:
            debug["diagnostics"].append("Native generated audio segment skipped because its sample rate did not match the first segment.")
            continue
        waveform = audio.get("waveform")
        if not torch.is_tensor(waveform):
            continue
        if waveform.ndim == 2:
            waveform = waveform.unsqueeze(0)
        segment = record.get("segment") or {}
        trim_leading = max(0, int(segment.get("trim_leading_frames") or 0))
        visible_frames = max(0, int(segment.get("visible_frame_count") or segment.get("frame_count") or 0))
        start_sample = max(0, int(round((trim_leading / frame_rate) * sample_rate)))
        visible_samples = max(0, int(round((visible_frames / frame_rate) * sample_rate)))
        if visible_samples <= 0:
            continue
        end_sample = min(waveform.shape[-1], start_sample + visible_samples)
        piece = waveform[..., start_sample:end_sample]
        if piece.shape[-1] < visible_samples:
            pad = torch.zeros(
                (*piece.shape[:-1], visible_samples - piece.shape[-1]),
                dtype=piece.dtype,
                device=piece.device,
            )
            piece = torch.cat((piece, pad), dim=-1)
        pieces.append(piece)

    if not pieces:
        debug["diagnostics"].append("Native generated audio unavailable; decoded segment audio was empty after trimming.")
        return None, debug

    output = torch.cat(pieces, dim=-1)
    final_samples = max(1, int(round((max(1, int(final_frame_count or 1)) / frame_rate) * sample_rate)))
    if output.shape[-1] > final_samples:
        output = output[..., :final_samples]
    elif output.shape[-1] < final_samples:
        pad = torch.zeros((*output.shape[:-1], final_samples - output.shape[-1]), dtype=output.dtype, device=output.device)
        output = torch.cat((output, pad), dim=-1)
    debug["final_audio_shape"] = _tensor_shape(output)
    debug["sample_rate"] = sample_rate
    return {"waveform": output, "sample_rate": sample_rate}, debug


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


def _has_enabled_timeline_audio(plan: dict[str, Any]) -> bool:
    return any(entry.get("enabled") is not False for entry in plan.get("audio_plan", []))


def _has_non_empty_audio_latent(audio_latent: dict[str, Any] | None) -> bool:
    samples = audio_latent.get("samples") if isinstance(audio_latent, dict) else None
    return torch.is_tensor(samples) and samples.numel() > 0


def _audio_samples_from_latent(latent: dict[str, Any]):
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if getattr(samples, "is_nested", False):
        try:
            streams = list(samples.unbind())
        except Exception:
            return None
        return streams[1] if len(streams) > 1 else None
    if isinstance(latent, dict) and latent.get("type") == "audio":
        return samples
    return None


def _tensor_shape(value) -> list[int] | None:
    shape = getattr(value, "shape", None)
    if shape is None:
        return None
    try:
        return [int(dim) for dim in shape]
    except Exception:
        return None


def _nested_shape(value) -> list[list[int] | None] | list[int] | None:
    if getattr(value, "is_nested", False):
        try:
            return [_tensor_shape(stream) for stream in value.unbind()]
        except Exception:
            return None
    return _tensor_shape(value)


def _plan_duration_seconds(plan: dict[str, Any]) -> float:
    resolved = plan.get("resolved_output", {})
    duration = float(resolved.get("duration_seconds") or 0.0)
    frame_count = int(resolved.get("frame_count") or 1)
    frame_rate = float(resolved.get("frame_rate") or 24.0)
    return max(duration, frame_count / frame_rate if frame_rate > 0 else duration)


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
