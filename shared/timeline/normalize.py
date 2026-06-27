from __future__ import annotations

from copy import deepcopy
from math import ceil
import re
from typing import Any

from ..contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_SOURCE_KINDS,
    ASSET_TYPES,
    BOUNDARY_MODE_HARD_CUT,
    BOUNDARY_MODES,
    CROP_MODE_PROJECT_DEFAULT,
    DEFAULT_AUDIO_FADE_IN_SECONDS,
    DEFAULT_AUDIO_FADE_OUT_SECONDS,
    DEFAULT_AUDIO_VOLUME,
    DEFAULT_IMAGE_GUIDE_STRENGTH,
    DEFAULT_VIDEO_GUIDE_STRENGTH,
    DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT,
    DEFAULT_VIDEO_GUIDANCE_RANGE,
    DEFAULT_VIDEO_TIMING_MODE,
    LORA_MERGE_MODE_INHERIT_GLOBAL,
    LORA_MERGE_MODES,
    MODEL_LORA_MODEL_LTX_2_3,
    MODEL_LORA_MODEL_WAN_2_2,
    MODEL_LORA_SCHEMA_VERSION,
    MODEL_LORA_TARGET_HIGH_NOISE,
    MODEL_LORA_TARGET_LOW_NOISE,
    MODEL_LORA_TARGET_MAIN,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
    SEQUENCE_ID_MAIN,
    SEQUENCE_NAME_MAIN,
    SHOT_TYPES,
    TAKE_STATUSES,
)
from .defaults import (
    create_default_boundary,
    create_default_clip_instance,
    create_default_lora_stack,
    create_default_sequence,
    create_default_shot,
    create_default_take,
    create_default_video_timeline,
)
from .migration import migrate_video_timeline
from .project_storage import normalize_project_identity_and_storage
from .references import normalize_character_references
from ..lora.config import normalize_lora_config

SECTION_SHOT_TOUCH_TOLERANCE_SECONDS = 1e-6


def normalize_video_timeline(timeline: Any) -> dict:
    migrated = migrate_video_timeline(timeline)
    normalized = _fill_missing(migrated, create_default_video_timeline())
    normalized["assets"] = _normalize_assets(normalized.get("assets"))
    normalized["director_track"] = _normalize_director_track(
        normalized.get("director_track")
    )
    normalized["audio_tracks"] = _normalize_audio_tracks(
        normalized.get("audio_tracks")
    )
    normalized["sequence"] = _normalize_sequence(
        normalized.get("sequence"),
        normalized["director_track"]["sections"],
    )
    _normalize_project_identity_storage(normalized)
    _normalize_project_metadata(normalized)
    _strip_global_project_settings(normalized)
    _normalize_project_model_loras(normalized)
    _normalize_ui_state_view_range(normalized)
    return normalized


def _normalize_assets(assets: Any) -> list[dict]:
    if not isinstance(assets, list):
        return []
    normalized_assets = []
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue
        normalized = deepcopy(asset)
        normalized.setdefault("asset_id", f"asset_{index + 1:03d}")
        if normalized.get("type") not in ASSET_TYPES:
            normalized["type"] = "Image"
        if normalized.get("source_kind") not in ASSET_SOURCE_KINDS:
            normalized["source_kind"] = ASSET_SOURCE_FILE_PATH
        normalized.setdefault("path", normalized.get("file_path"))
        normalized.setdefault("name", _basename(normalized.get("path")))
        normalized.setdefault("mime_type", "")
        normalized.setdefault("size_bytes", None)
        if not isinstance(normalized.get("metadata"), dict):
            normalized["metadata"] = {}
        normalized_assets.append(normalized)
    return normalized_assets


def _normalize_project_metadata(timeline: dict) -> None:
    project = timeline.setdefault("project", {})
    metadata = project.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["character_references_enabled"] = (
        metadata.get("character_references_enabled") is not False
    )
    metadata["character_references"] = normalize_character_references(
        metadata.get("character_references")
    )
    project["metadata"] = metadata


def _strip_global_project_settings(timeline: dict) -> None:
    project = timeline.setdefault("project", {})
    project.pop("settings", None)
    project.pop("privacy", None)
    project.pop("display", None)
    global_prompt = project.get("global_prompt")
    if isinstance(global_prompt, dict):
        global_prompt.pop("show_effective_prompt", None)
    audio = project.get("audio")
    if isinstance(audio, dict):
        audio.pop("always_normalize", None)


def _normalize_project_identity_storage(timeline: dict) -> None:
    project = timeline.setdefault("project", {})
    normalize_project_identity_and_storage(project)


def _normalize_project_model_loras(timeline: dict) -> None:
    project = timeline.setdefault("project", {})
    model_loras = project.get("model_loras")
    if not isinstance(model_loras, dict):
        model_loras = {}
    global_loras = model_loras.get("global")
    if not isinstance(global_loras, dict):
        global_loras = {}
    project["model_loras"] = {
        "schema_version": MODEL_LORA_SCHEMA_VERSION,
        "global": _normalize_project_lora_targets(global_loras),
    }


def _normalize_project_lora_targets(targets: dict[str, Any]) -> dict[str, Any]:
    ltx = targets.get(MODEL_LORA_MODEL_LTX_2_3)
    if not isinstance(ltx, dict):
        ltx = {}
    wan = targets.get(MODEL_LORA_MODEL_WAN_2_2)
    if not isinstance(wan, dict):
        wan = {}
    return {
        MODEL_LORA_MODEL_LTX_2_3: {
            MODEL_LORA_TARGET_MAIN: _normalize_lora_stack(
                ltx.get(MODEL_LORA_TARGET_MAIN)
            ),
        },
        MODEL_LORA_MODEL_WAN_2_2: {
            MODEL_LORA_TARGET_HIGH_NOISE: _normalize_lora_stack(
                wan.get(MODEL_LORA_TARGET_HIGH_NOISE)
            ),
            MODEL_LORA_TARGET_LOW_NOISE: _normalize_lora_stack(
                wan.get(MODEL_LORA_TARGET_LOW_NOISE)
            ),
        },
    }


def _normalize_lora_stack(stack: Any) -> dict[str, Any]:
    if not isinstance(stack, dict):
        stack = create_default_lora_stack()
    return normalize_lora_config(stack)


def _fill_missing(value: Any, defaults: Any) -> Any:
    if isinstance(defaults, dict):
        result = deepcopy(value) if isinstance(value, dict) else {}
        for key, default_value in defaults.items():
            if key not in result:
                result[key] = deepcopy(default_value)
            else:
                result[key] = _fill_missing(result[key], default_value)
        return result
    return deepcopy(value)


def _normalize_director_track(track: Any) -> dict:
    if not isinstance(track, dict):
        track = {}
    normalized = deepcopy(track)
    normalized.setdefault("track_id", "director")
    sections = normalized.get("sections")
    normalized["sections"] = [
        _normalize_section(section, index)
        for index, section in enumerate(sections if isinstance(sections, list) else [])
        if isinstance(section, dict)
    ]
    return normalized


def _normalize_sequence(sequence: Any, sections: list[dict] | None = None) -> dict:
    if not isinstance(sequence, dict):
        sequence = {}
    normalized = _fill_missing(sequence, create_default_sequence())
    normalized["sequence_id"] = str(normalized.get("sequence_id") or SEQUENCE_ID_MAIN)
    normalized["name"] = str(normalized.get("name") or SEQUENCE_NAME_MAIN)
    shots = normalized.get("shots")
    shot_items = [
        shot
        for shot in (shots if isinstance(shots, list) else [])
        if isinstance(shot, dict)
    ]
    should_derive_from_sections = not shot_items and bool(sections)
    if should_derive_from_sections:
        normalized["shots"] = _derive_shots_from_sections(sections or [])
        normalized["boundaries"] = _derive_boundaries_from_sections(
            sections or [],
            normalized["shots"],
        )
    else:
        normalized["shots"] = [
            _normalize_shot(shot, index)
            for index, shot in enumerate(shot_items)
        ]
        boundaries = normalized.get("boundaries")
        normalized["boundaries"] = [
            _normalize_boundary(boundary, index)
            for index, boundary in enumerate(
                boundaries if isinstance(boundaries, list) else []
            )
            if isinstance(boundary, dict)
        ]
    return normalized


def _derive_shots_from_sections(sections: list[dict]) -> list[dict]:
    used_shot_ids: set[str] = set()
    shots = []
    for index, section in enumerate(sections):
        section_id = section.get("item_id")
        if section_id is None or section_id == "":
            section_id = f"section_{index + 1:03d}"
        section_id_text = str(section_id)
        shot_id = _unique_timeline_id(
            f"shot_{_sanitize_timeline_id(section_id_text, f'section_{index + 1:03d}')}",
            used_shot_ids,
        )
        shot = create_default_shot(index + 1)
        shot.update(
            {
                "shot_id": shot_id,
                "start_time": section.get("start_time"),
                "end_time": section.get("end_time"),
                "section_ids": [section_id_text],
            }
        )
        shots.append(_normalize_shot(shot, index))
    return shots


def _derive_boundaries_from_sections(
    sections: list[dict],
    shots: list[dict],
) -> list[dict]:
    used_boundary_ids: set[str] = set()
    boundaries = []
    for index in range(max(0, min(len(sections), len(shots)) - 1)):
        left_section = sections[index]
        right_section = sections[index + 1]
        left_end = _as_float(left_section.get("end_time"), None)
        right_start = _as_float(right_section.get("start_time"), None)
        if left_end is None or right_start is None:
            continue
        if abs(left_end - right_start) > SECTION_SHOT_TOUCH_TOLERANCE_SECONDS:
            continue
        left_shot_id = shots[index]["shot_id"]
        right_shot_id = shots[index + 1]["shot_id"]
        boundary_id = _unique_timeline_id(
            f"boundary_{left_shot_id}_to_{right_shot_id}",
            used_boundary_ids,
        )
        boundary = create_default_boundary(len(boundaries) + 1)
        boundary.update(
            {
                "boundary_id": boundary_id,
                "left_shot_id": left_shot_id,
                "right_shot_id": right_shot_id,
                "mode": BOUNDARY_MODE_HARD_CUT,
            }
        )
        boundaries.append(_normalize_boundary(boundary, len(boundaries)))
    return boundaries


def _sanitize_timeline_id(value: Any, fallback: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return sanitized or fallback


def _unique_timeline_id(base_id: str, used_ids: set[str]) -> str:
    candidate = base_id
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base_id}_{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _normalize_shot(shot: dict, index: int) -> dict:
    normalized = _fill_missing(shot, create_default_shot(index + 1))
    normalized["shot_id"] = str(normalized.get("shot_id") or f"shot_{index + 1:03d}")
    normalized["name"] = str(normalized.get("name") or "")
    if normalized.get("type") not in SHOT_TYPES:
        normalized["type"] = create_default_shot(index + 1)["type"]
    normalized["start_time"] = _as_float(normalized.get("start_time"), 0.0)
    normalized["end_time"] = _as_float(normalized.get("end_time"), normalized["start_time"])
    section_ids = normalized.get("section_ids")
    normalized["section_ids"] = [
        str(section_id)
        for section_id in (section_ids if isinstance(section_ids, list) else [])
        if section_id is not None
    ]
    normalized["lora_overrides"] = _normalize_shot_lora_overrides(
        normalized.get("lora_overrides")
    )
    takes = normalized.get("takes")
    normalized["takes"] = [
        _normalize_take(take, take_index)
        for take_index, take in enumerate(takes if isinstance(takes, list) else [])
        if isinstance(take, dict)
    ]
    accepted_take_id = normalized.get("accepted_take_id")
    normalized["accepted_take_id"] = (
        str(accepted_take_id) if accepted_take_id is not None else None
    )
    normalized["clip_instance"] = _normalize_clip_instance(
        normalized.get("clip_instance")
    )
    if not isinstance(normalized.get("metadata"), dict):
        normalized["metadata"] = {}
    return normalized


def _normalize_boundary(boundary: dict, index: int) -> dict:
    normalized = _fill_missing(boundary, create_default_boundary(index + 1))
    normalized["boundary_id"] = str(
        normalized.get("boundary_id") or f"boundary_{index + 1:03d}"
    )
    for key in ("left_shot_id", "right_shot_id"):
        normalized[key] = str(normalized[key]) if normalized.get(key) is not None else None
    if normalized.get("mode") not in BOUNDARY_MODES:
        normalized["mode"] = create_default_boundary(index + 1)["mode"]
    normalized["tail_frames"] = _as_int(normalized.get("tail_frames"), 5)
    normalized["blend_frames"] = _as_int(normalized.get("blend_frames"), 3)
    normalized["transition_prompt"] = str(normalized.get("transition_prompt") or "")
    normalized["reuse_character_refs"] = normalized.get("reuse_character_refs") is not False
    normalized["reuse_style"] = normalized.get("reuse_style") is not False
    if not isinstance(normalized.get("metadata"), dict):
        normalized["metadata"] = {}
    return normalized


def _normalize_take(take: dict, index: int) -> dict:
    normalized = _fill_missing(take, create_default_take(index + 1))
    normalized["take_id"] = str(normalized.get("take_id") or f"take_{index + 1:03d}")
    if normalized.get("status") not in TAKE_STATUSES:
        normalized["status"] = create_default_take(index + 1)["status"]
    for key in ("asset_id", "seed", "resolved_loras"):
        normalized[key] = normalized.get(key)
    for key in ("model_family", "model_version", "plan_hash", "prompt_hash"):
        normalized[key] = str(normalized.get(key) or "")
    if not isinstance(normalized.get("metadata"), dict):
        normalized["metadata"] = {}
    return normalized


def _normalize_clip_instance(clip_instance: Any) -> dict | None:
    if clip_instance is None:
        return None
    if not isinstance(clip_instance, dict):
        clip_instance = {}
    normalized = _fill_missing(clip_instance, create_default_clip_instance())
    normalized["asset_id"] = (
        str(normalized["asset_id"]) if normalized.get("asset_id") is not None else None
    )
    normalized["source_in"] = _as_float(normalized.get("source_in"), 0.0)
    source_out = normalized.get("source_out")
    normalized["source_out"] = (
        _as_float(source_out, None) if source_out is not None else None
    )
    normalized["speed"] = _as_float(normalized.get("speed"), 1.0)
    normalized["enabled"] = normalized.get("enabled") is not False
    return normalized


def _normalize_shot_lora_overrides(overrides: Any) -> dict:
    if not isinstance(overrides, dict):
        overrides = {}
    normalized = deepcopy(overrides)
    normalized["enabled"] = bool(normalized.get("enabled", False))
    if normalized.get("merge_mode") not in LORA_MERGE_MODES:
        normalized["merge_mode"] = LORA_MERGE_MODE_INHERIT_GLOBAL
    normalized["targets"] = _normalize_optional_lora_targets(normalized.get("targets"))
    return normalized


def _normalize_optional_lora_targets(targets: Any) -> dict[str, Any]:
    if not isinstance(targets, dict):
        return {}
    normalized: dict[str, Any] = {}
    ltx = targets.get(MODEL_LORA_MODEL_LTX_2_3)
    if isinstance(ltx, dict) and MODEL_LORA_TARGET_MAIN in ltx:
        normalized[MODEL_LORA_MODEL_LTX_2_3] = {
            MODEL_LORA_TARGET_MAIN: _normalize_lora_stack(
                ltx.get(MODEL_LORA_TARGET_MAIN)
            )
        }
    wan = targets.get(MODEL_LORA_MODEL_WAN_2_2)
    wan_targets: dict[str, Any] = {}
    if isinstance(wan, dict):
        if MODEL_LORA_TARGET_HIGH_NOISE in wan:
            wan_targets[MODEL_LORA_TARGET_HIGH_NOISE] = _normalize_lora_stack(
                wan.get(MODEL_LORA_TARGET_HIGH_NOISE)
            )
        if MODEL_LORA_TARGET_LOW_NOISE in wan:
            wan_targets[MODEL_LORA_TARGET_LOW_NOISE] = _normalize_lora_stack(
                wan.get(MODEL_LORA_TARGET_LOW_NOISE)
            )
    if wan_targets:
        normalized[MODEL_LORA_MODEL_WAN_2_2] = wan_targets
    return normalized


def _normalize_section(section: dict, index: int) -> dict:
    normalized = deepcopy(section)
    if normalized.get("item_id") is None or normalized.get("item_id") == "":
        normalized["item_id"] = f"section_{index + 1:03d}"
    if normalized.get("start_time") is None:
        normalized["start_time"] = 0.0
    if normalized.get("end_time") is None:
        normalized["end_time"] = normalized["start_time"]

    section_type = normalized.get("type")
    if section_type == SECTION_TYPE_IMAGE:
        normalized.setdefault("image", None)
        normalized.setdefault("prompt", "")
        normalized.setdefault("guide_strength", DEFAULT_IMAGE_GUIDE_STRENGTH)
        normalized.setdefault("crop_mode", CROP_MODE_PROJECT_DEFAULT)
    elif section_type == SECTION_TYPE_TEXT:
        normalized.setdefault("prompt", "")
    elif section_type == SECTION_TYPE_VIDEO:
        normalized.setdefault("video", None)
        normalized.setdefault("prompt", "")
        normalized.setdefault("guide_strength", DEFAULT_VIDEO_GUIDE_STRENGTH)
        normalized.setdefault("crop_mode", CROP_MODE_PROJECT_DEFAULT)
        normalized.setdefault("source_in", 0.0)
        normalized.setdefault("source_out", None)
        normalized.setdefault("timing_mode", DEFAULT_VIDEO_TIMING_MODE)
        normalized.setdefault("video_guidance_range", DEFAULT_VIDEO_GUIDANCE_RANGE)
        normalized.setdefault("video_guidance_frame_count", DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT)
    return normalized


def _normalize_audio_tracks(audio_tracks: Any) -> list[dict]:
    if not isinstance(audio_tracks, list):
        return []
    normalized_tracks = []
    for track_index, track in enumerate(audio_tracks):
        if not isinstance(track, dict):
            continue
        normalized_track = deepcopy(track)
        normalized_track.setdefault("track_id", f"audio_track_{track_index + 1:03d}")
        clips = normalized_track.get("clips")
        normalized_track["clips"] = [
            _normalize_audio_clip(clip, clip_index)
            for clip_index, clip in enumerate(clips if isinstance(clips, list) else [])
            if isinstance(clip, dict)
        ]
        normalized_tracks.append(normalized_track)
    return normalized_tracks


def _normalize_audio_clip(clip: dict, index: int) -> dict:
    normalized = deepcopy(clip)
    normalized.setdefault("item_id", f"audio_clip_{index + 1:03d}")
    normalized.setdefault("audio", None)
    normalized.setdefault("start_time", 0.0)
    normalized.setdefault("end_time", normalized["start_time"])
    normalized.setdefault("source_in", 0.0)
    normalized.setdefault("source_out", None)
    normalized.setdefault("volume", DEFAULT_AUDIO_VOLUME)
    normalized.setdefault("normalization", {})
    normalized.setdefault("fade_in", DEFAULT_AUDIO_FADE_IN_SECONDS)
    normalized.setdefault("fade_out", DEFAULT_AUDIO_FADE_OUT_SECONDS)
    normalized.setdefault("enabled", True)
    normalized.setdefault("locked", False)
    normalized.setdefault("name", "")
    normalized.setdefault("lane", 0)
    return normalized


def _normalize_ui_state_view_range(timeline: dict) -> None:
    ui_state = timeline.setdefault("ui_state", {})
    project = timeline.get("project", {})
    try:
        duration = float(project.get("duration_seconds", 5.0))
    except (TypeError, ValueError):
        duration = 5.0
    project_seconds = max(1, ceil(max(0.25, duration)))
    try:
        start = round(float(ui_state.get("view_start_seconds", 0)))
    except (TypeError, ValueError):
        start = 0
    try:
        end = round(float(ui_state.get("view_end_seconds", project_seconds)))
    except (TypeError, ValueError):
        end = project_seconds
    start = max(0, min(start, max(0, project_seconds - 1)))
    end = max(start + 1, min(end, project_seconds))
    ui_state["view_start_seconds"] = start
    ui_state["view_end_seconds"] = end


def _as_float(value: Any, fallback: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _basename(path: Any) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").split("/")[-1]
