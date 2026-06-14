from __future__ import annotations

from typing import Any

import av
import numpy as np
import torch
from PIL import Image

from ...contracts.video_timeline import (
    CROP_MODE_CROP,
    CROP_MODE_PAD,
    CROP_MODE_PROJECT_DEFAULT,
    CROP_MODE_STRETCH_TO_FIT,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_VIDEO,
)
from ...media_cache import resolve_media_path


def empty_image(width: int, height: int) -> torch.Tensor:
    return torch.zeros((1, max(1, int(height)), max(1, int(width)), 3), dtype=torch.float32)


def load_image_tensor(path_value: str) -> torch.Tensor:
    path = resolve_media_path(path_value)
    with Image.open(path) as image:
        image = image.convert("RGB")
        array = np.array(image, dtype=np.float32) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def load_video_frames(path_value: str, max_frames: int | None = None, tail: bool = False) -> tuple[torch.Tensor, float, int]:
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
            if max_frames and not tail and len(frames) >= max_frames:
                break
    if not frames:
        raise ValueError(f"Could not decode video frames: {path}")
    if max_frames and tail:
        frames = frames[-max_frames:]
    tensor = torch.stack(frames, dim=0)
    return tensor, fps, int(tensor.shape[0])


def build_guide_data(plan: dict[str, Any], target_width: int, target_height: int) -> tuple[dict[str, Any], list[str]]:
    frame_rate = float(plan["resolved_output"].get("frame_rate") or 24.0)
    frame_count = int(plan["resolved_output"].get("frame_count") or 1)
    clean_latent_frames = ((frame_count - 1) // 8) + 1
    guide_data: dict[str, Any] = {
        "images": [],
        "insert_frames": [],
        "strengths": [],
        "frame_rate": frame_rate,
        "reference_images": [],
        "reference_mode": "timeline_guides",
        "clean_pixel_frames": frame_count,
        "clean_latent_frames": clean_latent_frames,
        "hidden_reference_count": 0,
    }
    diagnostics: list[str] = []
    sections_by_id = {entry.get("item_id"): entry for entry in plan.get("section_plan", [])}

    for media in plan.get("media_plan", []):
        ltx_role = media.get("ltx_role")
        if ltx_role in {"Disabled", "None", None}:
            continue
        section_type = media.get("section_type")
        path = media.get("path")
        if not path:
            raise ValueError(f"LTX runtime media item {media.get('item_id')} is missing a path.")
        section = sections_by_id.get(media.get("item_id"), {})
        if section_type == SECTION_TYPE_IMAGE:
            tensor = load_image_tensor(path)
        elif section_type == SECTION_TYPE_VIDEO:
            tensor, _, _ = load_video_frames(path, max_frames=max(1, int(section.get("frame_count") or 9)), tail=True)
        else:
            continue

        tensor = resize_image_frames(
            tensor,
            target_width,
            target_height,
            _resize_mode(media.get("crop_mode")),
            int(plan["resolved_output"].get("divisible_by") or 32),
        )
        guide_data["images"].append(tensor)
        guide_data["insert_frames"].append(int(section.get("start_frame") or 0))
        guide_data["strengths"].append(float(media.get("guide_strength") if media.get("guide_strength") is not None else 1.0))
        guide_data["reference_images"].append({
            "id": media.get("item_id"),
            "label": media.get("asset_id"),
            "kind": "timeline_media",
            "section_type": section_type,
            "insert_frame": int(section.get("start_frame") or 0),
            "strength": float(media.get("guide_strength") if media.get("guide_strength") is not None else 1.0),
            "hidden_tail": False,
        })

    if not guide_data["images"]:
        guide_data["images"].append(empty_image(target_width, target_height))
        guide_data["insert_frames"].append(0)
        guide_data["strengths"].append(0.0)
        diagnostics.append("No image or video guides were available; inserted a zero-strength dummy guide image.")

    return guide_data, diagnostics


def source_video_outputs(plan: dict[str, Any], target_width: int, target_height: int):
    video_media = next(
        (media for media in plan.get("media_plan", []) if media.get("section_type") == SECTION_TYPE_VIDEO and media.get("path")),
        None,
    )
    frame_rate = float(plan["resolved_output"].get("frame_rate") or 24.0)
    if not video_media:
        from .audio import empty_audio

        return empty_image(target_width, target_height), empty_audio(1.0), frame_rate, 0
    images, fps, count = load_video_frames(video_media["path"])
    images = resize_image_frames(
        images,
        target_width,
        target_height,
        _resize_mode(video_media.get("crop_mode")),
        int(plan["resolved_output"].get("divisible_by") or 32),
    )
    from .audio import decode_source_video_audio, empty_audio

    duration = count / fps if fps > 0 else 1.0
    try:
        audio = decode_source_video_audio(video_media["path"], duration)
    except Exception:
        audio = empty_audio(duration)
    return images, audio, float(fps), int(count)


def resize_image_frames(tensor: torch.Tensor, target_width: int, target_height: int, mode: str, divisible_by: int = 32) -> torch.Tensor:
    if tensor.shape[0] <= 1:
        return _resize_image(tensor, target_width, target_height, mode, divisible_by)
    return torch.cat([
        _resize_image(tensor[index:index + 1], target_width, target_height, mode, divisible_by)
        for index in range(tensor.shape[0])
    ], dim=0)


def _resize_image(tensor: torch.Tensor, target_width: int, target_height: int, mode: str, divisible_by: int) -> torch.Tensor:
    width = _snap(target_width, divisible_by)
    height = _snap(target_height, divisible_by)
    image_np = (tensor[0].detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
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


def _resize_mode(value: Any) -> str:
    if value == CROP_MODE_CROP:
        return CROP_MODE_CROP
    if value == CROP_MODE_STRETCH_TO_FIT:
        return CROP_MODE_STRETCH_TO_FIT
    if value in {CROP_MODE_PAD, CROP_MODE_PROJECT_DEFAULT, None, ""}:
        return CROP_MODE_PAD
    return CROP_MODE_PAD


def _stream_fps(stream, fallback: float) -> float:
    rate = stream.average_rate or stream.base_rate
    if rate:
        return float(rate)
    return fallback


def _snap(value: int, divisible_by: int) -> int:
    divisor = max(1, int(divisible_by or 1))
    return max(divisor, (int(value) // divisor) * divisor)
