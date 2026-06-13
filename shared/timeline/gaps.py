from __future__ import annotations

from typing import Any

from .normalize import normalize_video_timeline


def detect_director_gaps(timeline: Any) -> list[dict]:
    normalized = normalize_video_timeline(timeline)
    duration = float(normalized["project"]["duration_seconds"])
    sections = [
        section
        for section in normalized["director_track"]["sections"]
        if _has_number(section.get("start_time")) and _has_number(section.get("end_time"))
    ]
    sections.sort(key=lambda section: (float(section["start_time"]), float(section["end_time"])))

    gaps = []
    cursor = 0.0
    for section in sections:
        start = max(0.0, float(section["start_time"]))
        end = max(start, float(section["end_time"]))
        if start > cursor:
            gaps.append(_gap(cursor, min(start, duration)))
        cursor = max(cursor, min(end, duration))
    if cursor < duration:
        gaps.append(_gap(cursor, duration))
    return [gap for gap in gaps if gap["duration_seconds"] > 0]


def _gap(start_time: float, end_time: float) -> dict:
    return {
        "type": "No Guidance",
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": end_time - start_time,
    }


def _has_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
