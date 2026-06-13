from __future__ import annotations

import json
from typing import Any

from shared.contracts.validation import (
    SEVERITY_ERROR,
    create_validation_entry,
    create_validation_result,
    merge_validation_results,
)
from shared.timeline import create_default_video_timeline, normalize_video_timeline
from shared.timeline.validate import validate_video_timeline


def build_director_outputs(
    video_timeline_json: str | dict | None,
    duration_seconds: float,
    frame_rate: float,
    aspect_ratio: str,
    orientation: str,
    quality_preset: str,
    zoom_level: float,
) -> tuple[dict, dict]:
    timeline, parse_validation = _parse_timeline_json(video_timeline_json)
    timeline = normalize_video_timeline(timeline)
    apply_visible_project_fields(
        timeline,
        duration_seconds=duration_seconds,
        frame_rate=frame_rate,
        aspect_ratio=aspect_ratio,
        orientation=orientation,
        quality_preset=quality_preset,
        zoom_level=zoom_level,
    )
    validation = merge_validation_results(
        parse_validation,
        validate_video_timeline(timeline),
    )
    timeline["validation"] = validation
    return timeline, validation


def apply_visible_project_fields(
    timeline: dict[str, Any],
    duration_seconds: float,
    frame_rate: float,
    aspect_ratio: str,
    orientation: str,
    quality_preset: str,
    zoom_level: float,
) -> dict[str, Any]:
    project = timeline.setdefault("project", {})
    project["duration_seconds"] = float(duration_seconds)
    project["frame_rate"] = float(frame_rate)
    project["aspect_ratio"] = aspect_ratio
    project["orientation"] = orientation
    project["quality_preset"] = quality_preset

    ui_state = timeline.setdefault("ui_state", {})
    ui_state["zoom_level"] = float(zoom_level)
    return timeline


def serialize_video_timeline(timeline: dict[str, Any]) -> str:
    return json.dumps(timeline, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _parse_timeline_json(video_timeline_json: str | dict | None) -> tuple[dict, dict]:
    if isinstance(video_timeline_json, dict):
        return video_timeline_json, create_validation_result()

    if video_timeline_json is None or not str(video_timeline_json).strip():
        return create_default_video_timeline(), create_validation_result()

    try:
        parsed = json.loads(video_timeline_json)
    except json.JSONDecodeError as error:
        return (
            create_default_video_timeline(),
            create_validation_result(
                [
                    create_validation_entry(
                        "TIMELINE_JSON_INVALID",
                        SEVERITY_ERROR,
                        "Director",
                        "Timeline",
                        None,
                        "video_timeline_json is not valid JSON.",
                        "Reset the timeline state or fix the serialized timeline JSON.",
                        {"error": str(error)},
                    )
                ]
            ),
        )

    if not isinstance(parsed, dict):
        return (
            create_default_video_timeline(),
            create_validation_result(
                [
                    create_validation_entry(
                        "TIMELINE_JSON_NOT_OBJECT",
                        SEVERITY_ERROR,
                        "Director",
                        "Timeline",
                        None,
                        "video_timeline_json must contain a JSON object.",
                        "Reset the timeline state or provide a serialized VIDEO_TIMELINE object.",
                    )
                ]
            ),
        )
    return parsed, create_validation_result()
