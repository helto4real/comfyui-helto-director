from __future__ import annotations

from typing import Any

from ..contracts.validation import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    create_validation_entry,
    create_validation_result,
)
from ..contracts.video_timeline import (
    ASSET_SOURCE_KINDS,
    ASSET_TYPES,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
)
from .gaps import detect_director_gaps
from .normalize import normalize_video_timeline
from .references import (
    REFERENCE_KIND_CHARACTER,
    get_character_references,
    parse_reference_tags,
)


def validate_video_timeline(timeline: Any) -> dict:
    normalized = normalize_video_timeline(timeline)
    entries: list[dict[str, Any]] = []
    duration = _as_float(normalized["project"].get("duration_seconds"))
    assets = normalized.get("assets", [])
    assets_by_id = {asset.get("asset_id"): asset for asset in assets}

    entries.extend(_validate_assets(assets))
    references = get_character_references(normalized)
    entries.extend(_validate_character_references(references))
    sections = normalized["director_track"]["sections"]
    entries.extend(_validate_director_sections(sections, duration, assets_by_id))
    entries.extend(_validate_prompt_reference_tags(sections, references))
    entries.extend(_gap_entries(normalized))
    entries.extend(_validate_audio_tracks(normalized.get("audio_tracks", []), duration, assets_by_id))

    return create_validation_result(entries)


def _validate_assets(assets: list[dict]) -> list[dict]:
    entries = []
    seen = set()
    for asset in assets:
        asset_id = asset.get("asset_id")
        if asset_id in seen:
            entries.append(
                create_validation_entry(
                    "ASSET_DUPLICATE_ID",
                    SEVERITY_ERROR,
                    "Director",
                    "Asset",
                    asset_id,
                    "Asset IDs must be unique.",
                    "Replace or remove the duplicate asset record.",
                )
            )
        seen.add(asset_id)
        if asset.get("type") not in ASSET_TYPES:
            entries.append(
                create_validation_entry(
                    "ASSET_UNSUPPORTED_TYPE",
                    SEVERITY_ERROR,
                    "Director",
                    "Asset",
                    asset_id,
                    "Asset type is not supported.",
                    "Use Image, Video, or Audio.",
                    {"type": asset.get("type")},
                )
            )
        if asset.get("source_kind") not in ASSET_SOURCE_KINDS:
            entries.append(
                create_validation_entry(
                    "ASSET_UNSUPPORTED_SOURCE_KIND",
                    SEVERITY_ERROR,
                    "Director",
                    "Asset",
                    asset_id,
                    "Asset source kind is not supported.",
                    "Use FilePath, UploadedFile, Generated, or ComfyUIInput.",
                    {"source_kind": asset.get("source_kind")},
                )
            )
        if _contains_embedded_media(asset):
            entries.append(
                create_validation_entry(
                    "ASSET_EMBEDDED_MEDIA_NOT_ALLOWED",
                    SEVERITY_ERROR,
                    "Director",
                    "Asset",
                    asset_id,
                    "Assets must not embed media, thumbnails, or waveform data in workflow JSON.",
                    "Store only a file/source reference and regenerate previews from cache.",
                )
            )
    return entries


def _validate_character_references(references: list[dict]) -> list[dict]:
    entries = []
    seen_labels = set()
    for reference in references:
        item_id = reference.get("id") or reference.get("label") or "reference"
        label = reference.get("label")
        if label in seen_labels:
            entries.append(
                create_validation_entry(
                    "CHARACTER_REFERENCE_DUPLICATE_LABEL",
                    SEVERITY_ERROR,
                    "Director",
                    "CharacterReference",
                    item_id,
                    "Character reference labels must be unique.",
                    "Rename or remove the duplicate reference so prompt tags are unambiguous.",
                    {"label": label},
                )
            )
        seen_labels.add(label)
        if _contains_embedded_media(reference) or _contains_embedded_media(reference.get("image")):
            entries.append(
                create_validation_entry(
                    "CHARACTER_REFERENCE_EMBEDDED_MEDIA_NOT_ALLOWED",
                    SEVERITY_ERROR,
                    "Director",
                    "CharacterReference",
                    item_id,
                    "Character references must not embed media, thumbnails, or waveform data in workflow JSON.",
                    "Store only a file/source reference and regenerate previews from cache.",
                )
            )
        if reference.get("enabled") is not False and not _has_media_reference(reference.get("image")):
            entries.append(
                create_validation_entry(
                    "CHARACTER_REFERENCE_MISSING_IMAGE",
                    SEVERITY_ERROR,
                    "Director",
                    "CharacterReference",
                    item_id,
                    "Enabled character reference requires an image.",
                    "Choose an image, disable the reference, or remove it.",
                    {"label": label},
                )
            )
    return entries


def _validate_prompt_reference_tags(sections: list[dict], references: list[dict]) -> list[dict]:
    entries = []
    references_by_label = {
        reference.get("label"): reference
        for reference in references
    }
    seen_warnings = set()
    for section in sections:
        for tag in parse_reference_tags(section.get("prompt")):
            if tag.get("kind") != REFERENCE_KIND_CHARACTER:
                continue
            reference = references_by_label.get(tag["label"])
            key = (section.get("item_id"), tag["token"])
            if key in seen_warnings:
                continue
            seen_warnings.add(key)
            if reference is None:
                entries.append(
                    create_validation_entry(
                        "PROMPT_REFERENCE_UNKNOWN",
                        SEVERITY_WARNING,
                        "Director",
                        "Section",
                        section.get("item_id"),
                        "Prompt references a missing character reference.",
                        "Add the referenced character image or remove the tag.",
                        {"token": tag["token"], "label": tag["label"]},
                    )
                )
            elif reference.get("enabled") is False:
                entries.append(
                    create_validation_entry(
                        "PROMPT_REFERENCE_DISABLED",
                        SEVERITY_WARNING,
                        "Director",
                        "Section",
                        section.get("item_id"),
                        "Prompt references a disabled character reference.",
                        "Enable the reference or remove the tag.",
                        {"token": tag["token"], "label": tag["label"]},
                    )
                )
    return entries


def _validate_director_sections(sections: list[dict], duration: float | None, assets_by_id: dict) -> list[dict]:
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
        elif section_type == SECTION_TYPE_IMAGE:
            entries.extend(
                _validate_media_reference(
                    section.get("image"),
                    assets_by_id,
                    "Section",
                    item_id,
                    "IMAGE_SECTION_MEDIA",
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
        elif section_type == SECTION_TYPE_VIDEO:
            entries.extend(
                _validate_media_reference(
                    section.get("video"),
                    assets_by_id,
                    "Section",
                    item_id,
                    "VIDEO_SECTION_MEDIA",
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


def _validate_audio_tracks(audio_tracks: list[dict], duration: float | None, assets_by_id: dict) -> list[dict]:
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
            else:
                entries.extend(
                    _validate_media_reference(
                        clip.get("audio"),
                        assets_by_id,
                        "AudioClip",
                        item_id,
                        "AUDIO_CLIP_MEDIA",
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


def _validate_media_reference(
    reference: Any,
    assets_by_id: dict,
    scope: str,
    item_id: str | None,
    code_prefix: str,
) -> list[dict]:
    if _contains_embedded_media(reference):
        return [
            create_validation_entry(
                f"{code_prefix}_EMBEDDED_MEDIA_NOT_ALLOWED",
                SEVERITY_ERROR,
                "Director",
                scope,
                item_id,
                "Media references must not embed media, thumbnails, or waveform data in workflow JSON.",
                "Reference an asset_id or file path instead.",
            )
        ]
    if isinstance(reference, dict) and reference.get("asset_id") and reference.get("asset_id") not in assets_by_id:
        return [
            create_validation_entry(
                f"{code_prefix}_ASSET_NOT_FOUND",
                SEVERITY_ERROR,
                "Director",
                scope,
                item_id,
                "Media reference points to a missing asset record.",
                "Choose the media again or remove the stale reference.",
                {"asset_id": reference.get("asset_id")},
            )
        ]
    return []


def _contains_embedded_media(value: Any) -> bool:
    if not isinstance(value, dict):
        return isinstance(value, str) and value.startswith(("data:", "blob:"))
    stack = [value]
    blocked_keys = {
        "data",
        "blob",
        "bytes",
        "thumbnail",
        "thumbnail_data",
        "waveform",
        "waveform_data",
    }
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        for key, child in current.items():
            if key in blocked_keys:
                return True
            if isinstance(child, str) and child.startswith(("data:", "blob:")):
                return True
            if isinstance(child, dict):
                stack.append(child)
            elif isinstance(child, list):
                stack.extend(item for item in child if isinstance(item, dict))
    return False


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
