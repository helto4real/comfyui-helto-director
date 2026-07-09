from __future__ import annotations

from typing import Any

import torch

from ... import audio as shared_audio
from ...contracts.video_timeline import (
    CROP_MODE_CROP,
    CROP_MODE_PAD,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_VIDEO,
    VIDEO_TIMING_FIT_TO_SECTION,
)
from ...media import (
    decode_video_frames,
    empty_image,
    load_image_tensor,
    load_video_frames,
    normalize_resize_mode as _resize_mode,
    resize_image_frames,
    select_video_guidance_range,
    select_video_guide_frames,
    trim_video_source_frames,
)
from ..references import planned_hidden_reference_guard_latent_frames


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
        "hidden_reference_guard_latent_frames": 0,
        "reserved_latent_frames": clean_latent_frames,
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

        insert_frame = int(media.get("insert_frame") if media.get("insert_frame") is not None else section.get("start_frame") or 0)
        tensor = resize_image_frames(
            tensor,
            target_width,
            target_height,
            _resize_mode(media.get("crop_mode")),
            int(plan["resolved_output"].get("divisible_by") or 32),
        )
        guide_data["images"].append(tensor)
        guide_data["insert_frames"].append(insert_frame)
        guide_data["strengths"].append(float(media.get("guide_strength") if media.get("guide_strength") is not None else 1.0))
        guide_data["reference_images"].append({
            "id": media.get("item_id"),
            "label": media.get("asset_id"),
            "kind": "boundary_conditioning" if media.get("transient") else "timeline_media",
            "section_type": section_type,
            "image": tensor,
            "insert_frame": insert_frame,
            "strength": float(media.get("guide_strength") if media.get("guide_strength") is not None else 1.0),
            "hidden_tail": False,
            "transient": bool(media.get("transient")),
            "boundary_id": media.get("boundary_id"),
            "boundary_mode": media.get("boundary_mode"),
            "boundary_policy": media.get("boundary_policy"),
            "source_shot_id": media.get("source_shot_id"),
            "target_shot_id": media.get("target_shot_id"),
            "requested_tail_frames": media.get("requested_tail_frames"),
            "effective_tail_frames": media.get("effective_tail_frames"),
            **reference_metadata,
        })
        if media.get("transient") and reference_metadata.get("selected_frame_count") != media.get("effective_tail_frames"):
            diagnostics.append(
                "Boundary conditioning guide used "
                f"{reference_metadata.get('selected_frame_count')} frame(s) from the previous clip tail; "
                f"requested {media.get('requested_tail_frames')} and planned {media.get('effective_tail_frames')}."
            )

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
    hidden_reference_guard_latent_frames = planned_hidden_reference_guard_latent_frames(plan)
    divisible_by = int(plan["resolved_output"].get("divisible_by") or 32)
    guide_data["reference_mode"] = "timeline_guides+character_references"
    guide_data["hidden_reference_count"] = len(specs)
    guide_data["hidden_reference_guard_latent_frames"] = hidden_reference_guard_latent_frames
    guide_data["reserved_latent_frames"] = clean_latent_frames + hidden_reference_guard_latent_frames + len(specs)

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
        insert_frame = (clean_latent_frames + hidden_reference_guard_latent_frames + index) * 8
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
            "hidden_reference_guard_latent_frames": hidden_reference_guard_latent_frames,
            "clean_pixel_frames": guide_data.get("clean_pixel_frames"),
        })


def source_video_outputs(plan: dict[str, Any], target_width: int, target_height: int):
    video_media = next(
        (
            media
            for media in plan.get("media_plan", [])
            if media.get("section_type") == SECTION_TYPE_VIDEO and media.get("path") and not media.get("transient")
        ),
        None,
    )
    frame_rate = float(plan["resolved_output"].get("frame_rate") or 24.0)
    if not video_media:
        return (
            empty_image(target_width, target_height),
            shared_audio.empty_audio(1.0),
            frame_rate,
            0,
        )
    decoded, fps, _ = decode_video_frames(video_media["path"])
    images, trim_metadata = trim_video_source_frames(decoded, fps, video_media)
    images = resize_image_frames(
        images,
        target_width,
        target_height,
        _resize_mode(video_media.get("crop_mode")),
        int(plan["resolved_output"].get("divisible_by") or 32),
    )
    count = int(images.shape[0])
    duration = count / fps if fps > 0 else 1.0
    try:
        audio = shared_audio.decode_source_video_audio(
            video_media["path"],
            duration,
            trim_metadata["source_range"]["start"],
            trim_metadata["source_range"]["end"],
        )
    except Exception:
        audio = shared_audio.empty_audio(duration)
    return images, audio, float(fps), int(count)


def ltx_compatible_video_guide_frames(frames: torch.Tensor) -> torch.Tensor:
    keep = ((max(1, int(frames.shape[0])) - 1) // 8) * 8 + 1
    return frames[:keep]


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)
