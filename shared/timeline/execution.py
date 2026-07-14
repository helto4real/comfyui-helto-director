"""Shared Director timeline normalization and validation execution boundary."""

from __future__ import annotations

from typing import Any

from ..contracts.validation import create_validation_result, merge_validation_results
from .normalize import normalize_video_timeline
from .validate import validate_video_timeline


def build_timeline_outputs(
    timeline: dict[str, Any],
    *,
    duration_seconds: float,
    frame_rate: float,
    aspect_ratio: str,
    orientation: str,
    quality_preset: str,
    initial_validation: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply visible fields, normalize, and validate one plaintext timeline."""

    apply_visible_project_fields(
        timeline,
        duration_seconds=duration_seconds,
        frame_rate=frame_rate,
        aspect_ratio=aspect_ratio,
        orientation=orientation,
        quality_preset=quality_preset,
    )
    timeline = normalize_video_timeline(timeline)
    validation = merge_validation_results(
        initial_validation or create_validation_result(),
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
) -> dict[str, Any]:
    project = timeline.setdefault("project", {})
    project["duration_seconds"] = float(duration_seconds)
    project["frame_rate"] = float(frame_rate)
    project["aspect_ratio"] = aspect_ratio
    project["orientation"] = orientation
    project["quality_preset"] = quality_preset
    return timeline
