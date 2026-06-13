from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..contracts.video_timeline import (
    DEFAULT_AUDIO_FADE_IN_SECONDS,
    DEFAULT_AUDIO_FADE_OUT_SECONDS,
    DEFAULT_AUDIO_VOLUME,
    DEFAULT_IMAGE_GUIDE_STRENGTH,
    DEFAULT_VIDEO_GUIDE_STRENGTH,
    DEFAULT_VIDEO_TIMING_MODE,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
    CROP_MODE_PROJECT_DEFAULT,
)
from .defaults import create_default_video_timeline
from .migration import migrate_video_timeline


def normalize_video_timeline(timeline: Any) -> dict:
    migrated = migrate_video_timeline(timeline)
    normalized = _fill_missing(migrated, create_default_video_timeline())
    normalized["director_track"] = _normalize_director_track(
        normalized.get("director_track")
    )
    normalized["audio_tracks"] = _normalize_audio_tracks(
        normalized.get("audio_tracks")
    )
    return normalized


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
