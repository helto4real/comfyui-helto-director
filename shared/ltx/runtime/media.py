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
    DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT,
    DEFAULT_VIDEO_GUIDANCE_RANGE,
    VIDEO_GUIDANCE_RANGE_FULL_SOURCE,
    VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
    VIDEO_TIMING_FIT_TO_SECTION,
    VIDEO_TIMING_FREEZE_LAST_FRAME,
    VIDEO_TIMING_LOOP,
    VIDEO_TIMING_USE_SOURCE_TIMING,
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
    tensor, fps, count = decode_video_frames(path_value)
    if max_frames and tail:
        tensor = tensor[-max_frames:]
    elif max_frames:
        tensor = tensor[:max_frames]
    return tensor, fps, int(tensor.shape[0] if max_frames else count)


def decode_video_frames(path_value: str) -> tuple[torch.Tensor, float, int]:
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
            reference_metadata = {}
        elif section_type == SECTION_TYPE_VIDEO:
            decoded, source_fps, decoded_count = decode_video_frames(path)
            trimmed, trim_metadata = trim_video_source_frames(decoded, source_fps, media)
            guidance_frames, guidance_metadata = select_video_guidance_range(trimmed, media, trim_metadata)
            selected = select_video_guide_frames(guidance_frames, source_fps, media, section)
            tensor = ltx_compatible_video_guide_frames(selected)
            reference_metadata = {
                "source_fps": float(source_fps),
                "decoded_frame_count": int(decoded_count),
                "trimmed_frame_count": int(trim_metadata["trimmed_frame_count"]),
                "selected_frame_count": int(tensor.shape[0]),
                "requested_frame_count": int(selected.shape[0]),
                "source_range": trim_metadata["source_range"],
                "timing_mode": media.get("timing_mode") or VIDEO_TIMING_FIT_TO_SECTION,
                **guidance_metadata,
            }
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
            "image": tensor,
            "insert_frame": int(section.get("start_frame") or 0),
            "strength": float(media.get("guide_strength") if media.get("guide_strength") is not None else 1.0),
            "hidden_tail": False,
            **reference_metadata,
        })

    _append_character_reference_guides(plan, guide_data, target_width, target_height)
    _append_segment_continuity_guides(plan, guide_data, target_width, target_height)
    character_references = plan.get("model_specific", {}).get("ltx", {}).get("character_references", {})
    if isinstance(character_references, dict):
        diagnostics.extend(str(item) for item in character_references.get("diagnostics", []) if item)

    if not guide_data["images"]:
        guide_data["images"].append(empty_image(target_width, target_height))
        guide_data["insert_frames"].append(0)
        guide_data["strengths"].append(0.0)
        diagnostics.append("No image or video guides were available; inserted a zero-strength dummy guide image.")

    return guide_data, diagnostics


def _append_segment_continuity_guides(plan: dict[str, Any], guide_data: dict[str, Any], target_width: int, target_height: int) -> None:
    continuity = plan.get("model_specific", {}).get("ltx", {}).get("segment_continuity", {})
    images = continuity.get("previous_tail_images") if isinstance(continuity, dict) else None
    if images is None or not torch.is_tensor(images) or images.shape[0] <= 0:
        return
    tensor = resize_image_frames(
        images,
        target_width,
        target_height,
        CROP_MODE_CROP,
        int(plan["resolved_output"].get("divisible_by") or 32),
    )
    guide_data["images"].append(tensor)
    guide_data["insert_frames"].append(0)
    guide_data["strengths"].append(float(continuity.get("strength") or 1.0))
    guide_data["reference_images"].append({
        "id": "segment_previous_tail",
        "label": "previous_tail",
        "kind": "segment_continuity",
        "section_type": "Transient",
        "image": tensor,
        "insert_frame": 0,
        "strength": float(continuity.get("strength") or 1.0),
        "hidden_tail": False,
        "transient": True,
    })


def _append_character_reference_guides(plan: dict[str, Any], guide_data: dict[str, Any], target_width: int, target_height: int) -> None:
    character_references = plan.get("model_specific", {}).get("ltx", {}).get("character_references", {})
    specs = character_references.get("guide_specs") if isinstance(character_references, dict) else []
    if not isinstance(specs, list) or not specs:
        return
    clean_latent_frames = int(guide_data.get("clean_latent_frames") or 1)
    divisible_by = int(plan["resolved_output"].get("divisible_by") or 32)
    guide_data["reference_mode"] = "timeline_guides+character_references"
    guide_data["hidden_reference_count"] = len(specs)

    for index, spec in enumerate(specs):
        image = spec.get("image")
        path = image.get("path") or image.get("file_path") if isinstance(image, dict) else None
        if not path:
            raise ValueError(
                f"LTX character reference '{spec.get('label') or spec.get('id') or index}' is missing an image path."
            )
        try:
            raw_tensor = load_image_tensor(path)
        except Exception as exc:
            raise ValueError(
                f"LTX character reference '{spec.get('label') or spec.get('id') or index}' could not load image '{path}': {exc}"
            ) from exc
        tensor = resize_image_frames(
            raw_tensor,
            target_width,
            target_height,
            CROP_MODE_PAD,
            divisible_by,
        )
        insert_frame = (clean_latent_frames + index) * 8
        strength = _safe_float(spec.get("strength"), 1.0)
        guide_data["images"].append(tensor)
        guide_data["insert_frames"].append(insert_frame)
        guide_data["strengths"].append(strength)
        guide_data["reference_images"].append({
            "id": spec.get("id"),
            "label": spec.get("label"),
            "kind": "character",
            "description": spec.get("description") or "",
            "section_id": spec.get("section_id"),
            "insert_frame": insert_frame,
            "strength": strength,
            "image": tensor,
            "hidden_tail": True,
            "clean_latent_frames": clean_latent_frames,
            "clean_pixel_frames": guide_data.get("clean_pixel_frames"),
        })


def source_video_outputs(plan: dict[str, Any], target_width: int, target_height: int):
    video_media = next(
        (media for media in plan.get("media_plan", []) if media.get("section_type") == SECTION_TYPE_VIDEO and media.get("path")),
        None,
    )
    frame_rate = float(plan["resolved_output"].get("frame_rate") or 24.0)
    if not video_media:
        from .audio import empty_audio

        return empty_image(target_width, target_height), empty_audio(1.0), frame_rate, 0
    decoded, fps, _ = decode_video_frames(video_media["path"])
    images, trim_metadata = trim_video_source_frames(decoded, fps, video_media)
    images = resize_image_frames(
        images,
        target_width,
        target_height,
        _resize_mode(video_media.get("crop_mode")),
        int(plan["resolved_output"].get("divisible_by") or 32),
    )
    from .audio import decode_source_video_audio, empty_audio

    count = int(images.shape[0])
    duration = count / fps if fps > 0 else 1.0
    try:
        audio = decode_source_video_audio(
            video_media["path"],
            duration,
            trim_metadata["source_range"]["start"],
            trim_metadata["source_range"]["end"],
        )
    except Exception:
        audio = empty_audio(duration)
    return images, audio, float(fps), int(count)


def trim_video_source_frames(frames: torch.Tensor, fps: float, media: dict[str, Any]) -> tuple[torch.Tensor, dict[str, Any]]:
    frame_count = int(frames.shape[0])
    source_fps = max(0.001, float(fps or 24.0))
    source_duration = frame_count / source_fps
    source_in = _safe_float(media.get("source_in"), 0.0)
    source_out = media.get("source_out")
    start_seconds = min(max(0.0, source_in), source_duration)
    if source_out is None or source_out == "":
        end_seconds = source_duration
    else:
        end_seconds = min(max(0.0, _safe_float(source_out, source_duration)), source_duration)
    start_index = min(frame_count, max(0, int(np.floor(start_seconds * source_fps))))
    end_index = min(frame_count, max(start_index, int(np.ceil(end_seconds * source_fps))))
    if end_index <= start_index:
        raise ValueError(
            f"LTX runtime video media item {media.get('item_id')} has an empty decoded source range "
            f"({start_seconds:.3f}s to {end_seconds:.3f}s)."
        )
    trimmed = frames[start_index:end_index]
    return trimmed, {
        "trimmed_frame_count": int(trimmed.shape[0]),
        "source_range": {
            "start": float(start_seconds),
            "end": float(end_seconds),
            "start_frame": int(start_index),
            "end_frame_exclusive": int(end_index),
        },
    }


def select_video_guide_frames(frames: torch.Tensor, fps: float, media: dict[str, Any], section: dict[str, Any]) -> torch.Tensor:
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


def select_video_guidance_range(frames: torch.Tensor, media: dict[str, Any], trim_metadata: dict[str, Any]) -> tuple[torch.Tensor, dict[str, Any]]:
    guidance_range = media.get("video_guidance_range") or DEFAULT_VIDEO_GUIDANCE_RANGE
    if guidance_range != VIDEO_GUIDANCE_RANGE_FULL_SOURCE:
        guidance_range = VIDEO_GUIDANCE_RANGE_LAST_FRAMES
    requested_count = _safe_int(media.get("video_guidance_frame_count"), DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT)
    requested_count = max(1, requested_count)
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
    return selected, {
        "guidance_range": guidance_range,
        "guidance_frame_count": requested_count,
        "guidance_source_range": {
            "start_frame": start_frame,
            "end_frame_exclusive": start_frame + selected_count,
        },
    }


def ltx_compatible_video_guide_frames(frames: torch.Tensor) -> torch.Tensor:
    keep = ((max(1, int(frames.shape[0])) - 1) // 8) * 8 + 1
    return frames[:keep]


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
