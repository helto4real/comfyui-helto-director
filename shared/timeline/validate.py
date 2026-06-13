from __future__ import annotations

from typing import Any

from ..contracts.validation import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    create_validation_entry,
    create_validation_result,
)
from ..contracts.video_timeline import (
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
)
from .gaps import detect_director_gaps
from .normalize import normalize_video_timeline


def validate_video_timeline(timeline: Any) -> dict:
    normalized = normalize_video_timeline(timeline)
    entries: list[dict[str, Any]] = []
    duration = _as_float(normalized["project"].get("duration_seconds"))

    sections = normalized["director_track"]["sections"]
    entries.extend(_validate_director_sections(sections, duration))
    entries.extend(_gap_entries(normalized))
    entries.extend(_validate_audio_tracks(normalized.get("audio_tracks", []), duration))

    return create_validation_result(entries)


def _validate_director_sections(sections: list[dict], duration: float | None) -> list[dict]:
    entries = []
    sorted_sections = sorted(
        sections,
        key=lambda section: (
            _as_float(section.get("start_time")) or 0.0,
            _as_float(section.get("end_time")) or 0.0,
        ),
    )
    previous_end: float | None = None
    previous_id: str | None = None

    for section in sorted_sections:
        item_id = section.get("item_id")
        section_type = section.get("type")
        start = _as_float(section.get("start_time"))
        end = _as_float(section.get("end_time"))

        if start is None or end is None or end <= start:
            entries.append(
                create_validation_entry(
                    "SECTION_INVALID_TIME_RANGE",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Section requires a valid start_time and end_time.",
                    "Set end_time greater than start_time.",
                    {"start_time": section.get("start_time"), "end_time": section.get("end_time")},
                )
            )
        elif duration is not None and (start < 0 or end > duration):
            entries.append(
                create_validation_entry(
                    "SECTION_OUTSIDE_PROJECT_DURATION",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Section must stay within Project Duration.",
                    "Move or trim the section inside the project boundary.",
                    {"duration_seconds": duration, "start_time": start, "end_time": end},
                )
            )

        if previous_end is not None and start is not None and start < previous_end:
            entries.append(
                create_validation_entry(
                    "DIRECTOR_SECTION_OVERLAP",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Director Track sections cannot overlap.",
                    "Move or trim one section so the Director Track is sequential.",
                    {"previous_item_id": previous_id},
                )
            )
        if end is not None and (previous_end is None or end > previous_end):
            previous_end = end
            previous_id = item_id

        if section_type == SECTION_TYPE_TEXT and not str(section.get("prompt", "")).strip():
            entries.append(
                create_validation_entry(
                    "TEXT_SECTION_EMPTY_PROMPT",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Text Section requires a non-empty prompt.",
                    "Add a prompt or remove the Text Section.",
                )
            )
        elif section_type == SECTION_TYPE_IMAGE and not _has_media_reference(section.get("image")):
            entries.append(
                create_validation_entry(
                    "IMAGE_SECTION_MISSING_IMAGE",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Image Section requires an image.",
                    "Choose an image or remove the Image Section.",
                )
            )
        elif section_type == SECTION_TYPE_VIDEO and not _has_media_reference(section.get("video")):
            entries.append(
                create_validation_entry(
                    "VIDEO_SECTION_MISSING_VIDEO",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Video Section requires a video.",
                    "Choose a video or remove the Video Section.",
                )
            )

    return entries


def _gap_entries(timeline: dict) -> list[dict]:
    entries = []
    for index, gap in enumerate(detect_director_gaps(timeline)):
        entries.append(
            create_validation_entry(
                "DIRECTOR_GAP",
                SEVERITY_INFO,
                "Director",
                "Gap",
                f"gap_{index + 1:03d}",
                "Director Track gap means No Guidance.",
                "This is allowed. Planner nodes may apply model-specific policy later.",
                gap,
            )
        )
    return entries


def _validate_audio_tracks(audio_tracks: list[dict], duration: float | None) -> list[dict]:
    entries = []
    for track in audio_tracks:
        lanes: dict[int, list[dict]] = {}
        for clip in track.get("clips", []):
            item_id = clip.get("item_id")
            start = _as_float(clip.get("start_time"))
            end = _as_float(clip.get("end_time"))
            if start is None or end is None or end <= start:
                entries.append(
                    create_validation_entry(
                        "AUDIO_CLIP_INVALID_TIME_RANGE",
                        SEVERITY_ERROR,
                        "Director",
                        "AudioClip",
                        item_id,
                        "Audio Clip requires a valid start_time and end_time.",
                        "Set end_time greater than start_time.",
                    )
                )
            elif duration is not None and (start < 0 or end > duration):
                entries.append(
                    create_validation_entry(
                        "AUDIO_CLIP_OUTSIDE_PROJECT_DURATION",
                        SEVERITY_ERROR,
                        "Director",
                        "AudioClip",
                        item_id,
                        "Audio Clip must stay within Project Duration.",
                        "Move or trim the clip inside the project boundary.",
                    )
                )
            if not _has_media_reference(clip.get("audio")):
                entries.append(
                    create_validation_entry(
                        "AUDIO_CLIP_MISSING_AUDIO",
                        SEVERITY_ERROR,
                        "Director",
                        "AudioClip",
                        item_id,
                        "Audio Clip requires audio.",
                        "Choose audio or remove the clip.",
                    )
                )
            lane = int(clip.get("lane", 0))
            lanes.setdefault(lane, []).append(clip)

        for lane, clips in lanes.items():
            entries.extend(_validate_audio_lane(track.get("track_id"), lane, clips))
    return entries


def _validate_audio_lane(track_id: str | None, lane: int, clips: list[dict]) -> list[dict]:
    entries = []
    sorted_clips = sorted(clips, key=lambda clip: _as_float(clip.get("start_time")) or 0.0)
    previous_end: float | None = None
    previous_id: str | None = None
    for clip in sorted_clips:
        start = _as_float(clip.get("start_time"))
        end = _as_float(clip.get("end_time"))
        if previous_end is not None and start is not None and start < previous_end:
            entries.append(
                create_validation_entry(
                    "AUDIO_CLIP_LANE_OVERLAP",
                    SEVERITY_ERROR,
                    "Director",
                    "AudioClip",
                    clip.get("item_id"),
                    "Audio Clips cannot overlap within the same lane.",
                    "Move one clip to another lane or trim the overlap.",
                    {
                        "track_id": track_id,
                        "lane": lane,
                        "previous_item_id": previous_id,
                    },
                )
            )
        if end is not None and (previous_end is None or end > previous_end):
            previous_end = end
            previous_id = clip.get("item_id")
    return entries


def _has_media_reference(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value.get("asset_id") or value.get("path") or value.get("file_path"))
    return value is not None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
