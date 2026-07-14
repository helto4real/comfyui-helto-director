from __future__ import annotations

import json

from ...shared.contracts.validation import (
    SEVERITY_ERROR,
    create_validation_entry,
    create_validation_result,
)
from ...shared.timeline import create_default_video_timeline
from ...shared.timeline.execution import (
    apply_visible_project_fields,
    build_timeline_outputs,
)
from ...shared.timeline.managed_execution import (
    consume_director_subject_mode,
    director_subject_requires_private_execution,
    dispatch_director_execution,
)


def build_director_outputs(
    video_timeline_json: str | dict | None,
    duration_seconds: float,
    frame_rate: float,
    aspect_ratio: str,
    orientation: str,
    quality_preset: str,
    privacy_mode_reference: object = "",
    private_execution: object = "",
    subject_id: object = None,
    _subject_mode_lease: object = None,
) -> tuple[dict, dict]:
    if _subject_mode_lease is None and privacy_mode_reference:
        with consume_director_subject_mode(privacy_mode_reference, subject_id) as lease:
            return build_director_outputs(
                video_timeline_json,
                duration_seconds,
                frame_rate,
                aspect_ratio,
                orientation,
                quality_preset,
                private_execution=private_execution,
                subject_id=subject_id,
                _subject_mode_lease=lease,
            )
    if _subject_mode_lease is None and (private_execution or subject_id is not None):
        raise ValueError("Director execution requires a managed subject-mode reference.")
    if (
        _subject_mode_lease is not None
        and director_subject_requires_private_execution(_subject_mode_lease)
        and not private_execution
    ):
        raise ValueError("Private Director execution requires a managed execution reference.")
    if private_execution:
        result = dispatch_director_execution(
            private_execution,
            {
                "duration_seconds": duration_seconds,
                "frame_rate": frame_rate,
                "aspect_ratio": aspect_ratio,
                "orientation": orientation,
                "quality_preset": quality_preset,
            },
            subject_id=subject_id,
        )
        if not isinstance(result, tuple) or len(result) != 2:
            raise ValueError("Director managed execution result is invalid.")
        return result
    timeline, parse_validation = _parse_timeline_json(video_timeline_json)
    return build_timeline_outputs(
        timeline,
        duration_seconds=duration_seconds,
        frame_rate=frame_rate,
        aspect_ratio=aspect_ratio,
        orientation=orientation,
        quality_preset=quality_preset,
        initial_validation=parse_validation,
    )


def serialize_video_timeline(timeline: dict[str, Any]) -> str:
    return json.dumps(timeline, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _parse_timeline_json(video_timeline_json: str | dict | None) -> tuple[dict, dict]:
    if isinstance(video_timeline_json, dict):
        if video_timeline_json.get("encrypted") is True:
            raise ValueError("Protected Director timeline requires managed execution.")
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

    if isinstance(parsed, dict) and parsed.get("encrypted") is True:
        raise ValueError("Protected Director timeline requires managed execution.")

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
