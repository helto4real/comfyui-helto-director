from __future__ import annotations

from copy import deepcopy
from math import ceil
from typing import Any

from ..contracts.video_timeline import (
    ASSET_SOURCE_FILE_PATH,
    ASSET_SOURCE_KINDS,
    ASSET_TYPES,
    CROP_MODE_PROJECT_DEFAULT,
    DEFAULT_AUDIO_FADE_IN_SECONDS,
    DEFAULT_AUDIO_FADE_OUT_SECONDS,
    DEFAULT_AUDIO_VOLUME,
    DEFAULT_IMAGE_GUIDE_STRENGTH,
    DEFAULT_VIDEO_GUIDE_STRENGTH,
    DEFAULT_VIDEO_GUIDANCE_FRAME_COUNT,
    DEFAULT_VIDEO_GUIDANCE_RANGE,
    DEFAULT_VIDEO_TIMING_MODE,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
)
from .defaults import create_default_video_timeline
from .migration import migrate_video_timeline
from .references import normalize_character_references


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
    _normalize_project_metadata(normalized)
    _normalize_privacy(normalized)
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
    metadata["character_references"] = normalize_character_references(
        metadata.get("character_references")
    )
    project["metadata"] = metadata


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


def _normalize_section(section: dict, index: int) -> dict:
    normalized = deepcopy(section)
    normalized.setdefault("item_id", f"section_{index + 1:03d}")
    normalized.setdefault("start_time", 0.0)
    normalized.setdefault("end_time", normalized["start_time"])

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


def _normalize_privacy(timeline: dict) -> None:
    project = timeline.setdefault("project", {})
    privacy = project.get("privacy")
    if not isinstance(privacy, dict):
        privacy = {}
    mode = any(
        bool(privacy.get(key))
        for key in (
            "mode",
            "hide_media_previews",
            "hide_text_prompts",
            "encrypt_previews",
        )
    )
    project["privacy"] = {"mode": mode}


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


def _basename(path: Any) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").split("/")[-1]
