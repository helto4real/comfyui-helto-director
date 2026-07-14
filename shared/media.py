from __future__ import annotations

from typing import Any, NamedTuple

import av
import numpy as np
import torch
from PIL import Image

from .contracts.video_timeline import (
    CROP_MODE_CROP,
    CROP_MODE_PAD,
    CROP_MODE_PROJECT_DEFAULT,
    CROP_MODE_STRETCH_TO_FIT,
    DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT,
    DEFAULT_VIDEO_GUIDANCE_RANGE,
    VIDEO_GUIDANCE_RANGE_FULL_SOURCE,
    VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
    VIDEO_TIMING_FIT_TO_SECTION,
    VIDEO_TIMING_FREEZE_LAST_FRAME,
    VIDEO_TIMING_LOOP,
    VIDEO_TIMING_USE_SOURCE_TIMING,
)
from .media_domain import resolve_media_path


class DecodedVideo(NamedTuple):
    frames: torch.Tensor
    frame_rate: float
    frame_count: int


class SelectedVideoFrames(NamedTuple):
    frames: torch.Tensor
    metadata: dict[str, Any]


def empty_image(width: int, height: int) -> torch.Tensor:
    return torch.zeros(
        (1, max(1, int(height)), max(1, int(width)), 3),
        dtype=torch.float32,
    )


def load_image_tensor(path_value: str) -> torch.Tensor:
    path = resolve_media_path(path_value)
    with Image.open(path) as image:
        image = image.convert("RGB")
        array = np.array(image, dtype=np.float32) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def load_video_frames(
    path_value: str,
    max_frames: int | None = None,
    tail: bool = False,
) -> DecodedVideo:
    tensor, fps, count = decode_video_frames(path_value)
    if max_frames and tail:
        tensor = tensor[-max_frames:]
    elif max_frames:
        tensor = tensor[:max_frames]
    return DecodedVideo(tensor, fps, int(tensor.shape[0] if max_frames else count))


def decode_video_frames(path_value: str) -> DecodedVideo:
    path = resolve_media_path(path_value)
    frames: list[torch.Tensor] = []
    fps = 24.0
    with av.open(str(path)) as container:
        stream = next((stream for stream in container.streams if stream.type == "video"), None)
        if stream is None:
            raise ValueError(f"Video media has no video stream: {path}")
        stream.thread_type = "AUTO"
        fps = _stream_fps(stream, 24.0)
        for frame in container.decode(stream):
            array = frame.to_ndarray(format="rgb24").astype(np.float32) / 255.0
            frames.append(torch.from_numpy(array))
    if not frames:
        raise ValueError(f"Could not decode video frames: {path}")
    tensor = torch.stack(frames, dim=0)
    return DecodedVideo(tensor, fps, int(tensor.shape[0]))


def trim_video_source_frames(
    frames: torch.Tensor,
    fps: float,
    media: dict[str, Any],
) -> SelectedVideoFrames:
    frame_count = int(frames.shape[0])
    source_fps = max(0.001, float(fps or 24.0))
    source_duration = frame_count / source_fps
    source_in = _safe_float(media.get("source_in"), 0.0)
    source_out = media.get("source_out")
    start_seconds = min(max(0.0, source_in), source_duration)
    if source_out is None or source_out == "":
        end_seconds = source_duration
    else:
        end_seconds = min(
            max(0.0, _safe_float(source_out, source_duration)),
            source_duration,
        )
    start_index = min(
        frame_count,
        max(0, int(np.floor(start_seconds * source_fps))),
    )
    end_index = min(
        frame_count,
        max(start_index, int(np.ceil(end_seconds * source_fps))),
    )
    if end_index <= start_index:
        raise ValueError(
            f"Video media item {media.get('item_id')} has an empty decoded source range "
            f"({start_seconds:.3f}s to {end_seconds:.3f}s)."
        )
    trimmed = frames[start_index:end_index]
    return SelectedVideoFrames(
        trimmed,
        {
            "trimmed_frame_count": int(trimmed.shape[0]),
            "source_range": {
                "start": float(start_seconds),
                "end": float(end_seconds),
                "start_frame": int(start_index),
                "end_frame_exclusive": int(end_index),
            },
        },
    )


def select_video_guide_frames(
    frames: torch.Tensor,
    fps: float,
    media: dict[str, Any],
    section: dict[str, Any],
) -> torch.Tensor:
    del fps
    target_count = max(1, int(section.get("frame_count") or frames.shape[0]))
    timing_mode = media.get("timing_mode") or VIDEO_TIMING_FIT_TO_SECTION
    if timing_mode == VIDEO_TIMING_FIT_TO_SECTION:
        return _sample_evenly(frames, target_count)
    if timing_mode == VIDEO_TIMING_USE_SOURCE_TIMING:
        return frames[:target_count]
    if timing_mode == VIDEO_TIMING_LOOP:
        repeats = int(np.ceil(target_count / max(1, int(frames.shape[0]))))
        return frames.repeat((repeats, 1, 1, 1))[:target_count]
    if timing_mode == VIDEO_TIMING_FREEZE_LAST_FRAME:
        if frames.shape[0] >= target_count:
            return frames[:target_count]
        padding = frames[-1:].repeat((target_count - frames.shape[0], 1, 1, 1))
        return torch.cat((frames, padding), dim=0)
    return _sample_evenly(frames, target_count)


def select_video_guidance_range(
    frames: torch.Tensor,
    media: dict[str, Any],
    trim_metadata: dict[str, Any],
) -> SelectedVideoFrames:
    guidance_range = media.get("video_guidance_range") or DEFAULT_VIDEO_GUIDANCE_RANGE
    if guidance_range != VIDEO_GUIDANCE_RANGE_FULL_SOURCE:
        guidance_range = VIDEO_GUIDANCE_RANGE_LAST_FRAMES
    requested_count = max(
        1,
        _safe_int(
            media.get("video_guidance_frame_count"),
            DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT,
        ),
    )
    if guidance_range == VIDEO_GUIDANCE_RANGE_LAST_FRAMES:
        selected_count = min(int(frames.shape[0]), requested_count)
        offset = int(frames.shape[0]) - selected_count
        selected = frames[offset:]
    else:
        selected_count = int(frames.shape[0])
        offset = 0
        selected = frames
    source_range = trim_metadata["source_range"]
    start_frame = int(source_range["start_frame"]) + offset
    return SelectedVideoFrames(
        selected,
        {
            "guidance_range": guidance_range,
            "guidance_frame_count": requested_count,
            "guidance_source_range": {
                "start_frame": start_frame,
                "end_frame_exclusive": start_frame + selected_count,
            },
        },
    )


def resize_image_frames(
    tensor: torch.Tensor,
    target_width: int,
    target_height: int,
    mode: str,
    divisible_by: int = 32,
) -> torch.Tensor:
    if tensor.shape[0] <= 1:
        return _resize_image(tensor, target_width, target_height, mode, divisible_by)
    return torch.cat(
        [
            _resize_image(
                tensor[index:index + 1],
                target_width,
                target_height,
                mode,
                divisible_by,
            )
            for index in range(tensor.shape[0])
        ],
        dim=0,
    )


def normalize_resize_mode(value: Any) -> str:
    if value == CROP_MODE_CROP:
        return CROP_MODE_CROP
    if value == CROP_MODE_STRETCH_TO_FIT:
        return CROP_MODE_STRETCH_TO_FIT
    if value in {CROP_MODE_PAD, CROP_MODE_PROJECT_DEFAULT, None, ""}:
        return CROP_MODE_PAD
    return CROP_MODE_PAD


def _resize_image(
    tensor: torch.Tensor,
    target_width: int,
    target_height: int,
    mode: str,
    divisible_by: int,
) -> torch.Tensor:
    width = _snap(target_width, divisible_by)
    height = _snap(target_height, divisible_by)
    image_np = (
        tensor[0].detach().cpu().numpy() * 255.0
    ).clip(0, 255).astype(np.uint8)
    image = Image.fromarray(image_np)
    source_width, source_height = image.size

    if mode == CROP_MODE_STRETCH_TO_FIT:
        resized = image.resize((width, height), Image.Resampling.LANCZOS)
    elif mode == CROP_MODE_CROP:
        ratio = max(width / source_width, height / source_height)
        inner_width = max(1, int(round(source_width * ratio)))
        inner_height = max(1, int(round(source_height * ratio)))
        inner = image.resize((inner_width, inner_height), Image.Resampling.LANCZOS)
        left = (inner_width - width) // 2
        top = (inner_height - height) // 2
        resized = inner.crop((left, top, left + width, top + height))
    else:
        ratio = min(width / source_width, height / source_height)
        inner_width = max(1, int(round(source_width * ratio)))
        inner_height = max(1, int(round(source_height * ratio)))
        inner = image.resize((inner_width, inner_height), Image.Resampling.LANCZOS)
        resized = Image.new("RGB", (width, height), (0, 0, 0))
        resized.paste(inner, ((width - inner_width) // 2, (height - inner_height) // 2))

    array = np.array(resized, dtype=np.float32) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def _stream_fps(stream, fallback: float) -> float:
    rate = stream.average_rate or stream.base_rate
    if rate:
        return float(rate)
    return fallback


def _sample_evenly(frames: torch.Tensor, target_count: int) -> torch.Tensor:
    count = max(1, int(target_count))
    if count == 1:
        return frames[:1]
    indices = torch.linspace(0, frames.shape[0] - 1, count).round().to(torch.long)
    return frames[indices]


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def _snap(value: int, divisible_by: int) -> int:
    divisor = max(1, int(divisible_by or 1))
    return max(divisor, (int(value) // divisor) * divisor)
