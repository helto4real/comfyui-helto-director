from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..contracts.video_timeline import (
    BOUNDARY_MODE_BLEND_SEAM,
    BOUNDARY_MODE_CONTINUOUS_SHOT,
    BOUNDARY_MODE_HARD_CUT,
    BOUNDARY_MODE_TRANSITION,
)
from .normalize import normalize_video_timeline

CONTINUITY_POLICY_NONE = "none"
CONTINUITY_POLICY_CONTINUOUS = "continuous"
CONTINUITY_POLICY_BLEND = "blend"
CONTINUITY_POLICY_TRANSITION = "transition"
CONTINUITY_STATUS_NOT_REQUESTED = "not_requested"
CONTINUITY_STATUS_AVAILABLE = "available"
CONTINUITY_STATUS_UNAVAILABLE = "unavailable"


class ShotExtractionError(ValueError):
    """Raised when a shot-local timeline cannot be extracted."""


def extract_shot_timeline(timeline: Any, shot_id: str) -> dict[str, Any]:
    """Return a shot-local VIDEO_TIMELINE plus generic extraction context."""

    normalized = normalize_video_timeline(timeline)
    requested_shot_id = str(shot_id or "")
    sequence = normalized.get("sequence") if isinstance(normalized.get("sequence"), dict) else {}
    shots = [shot for shot in sequence.get("shots", []) if isinstance(shot, dict)]
    selected_index, selected_shot = _find_shot(shots, requested_shot_id)
    if selected_shot is None:
        raise ShotExtractionError(
            f"Shot '{requested_shot_id}' was not found in the timeline sequence."
        )

    source_start = _as_float(selected_shot.get("start_time"), 0.0)
    source_end = _as_float(selected_shot.get("end_time"), source_start)
    duration = max(0.0, source_end - source_start)
    section_ids = [str(section_id) for section_id in selected_shot.get("section_ids", [])]
    boundary_context = build_shot_boundary_context(normalized, requested_shot_id)
    shot_context = {
        "schema_version": 1,
        "source_sequence_id": sequence.get("sequence_id"),
        "shot_id": selected_shot.get("shot_id"),
        "shot_type": selected_shot.get("type"),
        "original_start_time": source_start,
        "original_end_time": source_end,
        "duration_seconds": duration,
        "local_start_time": 0.0,
        "local_end_time": duration,
        "time_offset_seconds": source_start,
        "section_ids": section_ids,
        "lora_overrides": deepcopy(selected_shot.get("lora_overrides") or {}),
        "boundary_context": boundary_context,
    }

    local_timeline = deepcopy(normalized)
    local_timeline["project"]["duration_seconds"] = duration
    local_timeline["director_track"]["sections"] = _local_sections(
        normalized.get("director_track", {}).get("sections", []),
        section_ids,
        source_start,
        duration,
    )
    local_timeline["sequence"] = _local_sequence(
        sequence,
        selected_shot,
        duration,
        shot_context,
    )
    local_timeline["ui_state"]["view_start_seconds"] = 0
    local_timeline["ui_state"]["view_end_seconds"] = max(1, int(duration + 0.999999))

    return {
        "timeline": normalize_video_timeline(local_timeline),
        "shot_context": shot_context,
    }


def select_shot_timeline_for_planning(
    timeline: Any,
    shot_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    normalized = normalize_video_timeline(timeline)
    requested_shot_id = str(shot_id or "").strip()
    if not requested_shot_id:
        return normalized, None, None
    try:
        extracted = extract_shot_timeline(normalized, requested_shot_id)
    except ShotExtractionError as error:
        return normalized, None, {
            "shot_id": requested_shot_id,
            "error": str(error),
        }
    return extracted["timeline"], extracted["shot_context"], None


def build_shot_boundary_context(timeline: Any, shot_id: str) -> dict[str, Any]:
    normalized = normalize_video_timeline(timeline)
    requested_shot_id = str(shot_id or "")
    sequence = normalized.get("sequence") if isinstance(normalized.get("sequence"), dict) else {}
    shots = [shot for shot in sequence.get("shots", []) if isinstance(shot, dict)]
    selected_index, selected_shot = _find_shot(shots, requested_shot_id)
    if selected_shot is None:
        raise ShotExtractionError(
            f"Shot '{requested_shot_id}' was not found in the timeline sequence."
        )

    previous_shot = shots[selected_index - 1] if selected_index > 0 else None
    next_shot = shots[selected_index + 1] if selected_index < len(shots) - 1 else None
    boundaries = [
        boundary
        for boundary in sequence.get("boundaries", [])
        if isinstance(boundary, dict)
    ]
    incoming_boundary = _find_boundary(
        boundaries,
        left_shot_id=previous_shot.get("shot_id") if previous_shot else None,
        right_shot_id=selected_shot.get("shot_id"),
    )
    outgoing_boundary = _find_boundary(
        boundaries,
        left_shot_id=selected_shot.get("shot_id"),
        right_shot_id=next_shot.get("shot_id") if next_shot else None,
    )
    incoming_policy = _boundary_continuity_policy(incoming_boundary)
    outgoing_policy = _boundary_continuity_policy(outgoing_boundary)
    incoming_tail_frames = _effective_tail_frames(incoming_boundary)
    outgoing_tail_frames = _effective_tail_frames(outgoing_boundary)
    incoming_blend_frames = _effective_blend_frames(incoming_boundary)
    outgoing_blend_frames = _effective_blend_frames(outgoing_boundary)

    return {
        "previous_shot_id": previous_shot.get("shot_id") if previous_shot else None,
        "next_shot_id": next_shot.get("shot_id") if next_shot else None,
        "incoming_boundary": _boundary_summary(incoming_boundary),
        "outgoing_boundary": _boundary_summary(outgoing_boundary),
        "previous_accepted_take_id": _accepted_take_id(previous_shot),
        "previous_clip_asset_id": _shot_clip_asset_id(previous_shot),
        "next_accepted_take_id": _accepted_take_id(next_shot),
        "next_clip_asset_id": _shot_clip_asset_id(next_shot),
        "incoming_continuity_policy": incoming_policy,
        "outgoing_continuity_policy": outgoing_policy,
        "continuity_policy": _combined_continuity_policy(incoming_policy, outgoing_policy),
        "tail_frames": max(incoming_tail_frames, outgoing_tail_frames),
        "blend_frames": max(incoming_blend_frames, outgoing_blend_frames),
        "incoming_continuity": _continuity_source_context(
            selected_shot_id=selected_shot.get("shot_id"),
            source_shot=previous_shot,
            boundary=incoming_boundary,
            policy=incoming_policy,
            tail_frames=incoming_tail_frames,
            blend_frames=incoming_blend_frames,
            direction="incoming",
        ),
        "outgoing_continuity": _continuity_source_context(
            selected_shot_id=selected_shot.get("shot_id"),
            source_shot=selected_shot,
            boundary=outgoing_boundary,
            policy=outgoing_policy,
            tail_frames=outgoing_tail_frames,
            blend_frames=outgoing_blend_frames,
            direction="outgoing",
        ),
    }


def _find_shot(shots: list[dict[str, Any]], shot_id: str) -> tuple[int, dict[str, Any] | None]:
    for index, shot in enumerate(shots):
        if str(shot.get("shot_id") or "") == shot_id:
            return index, shot
    return -1, None


def _local_sections(
    sections: list[dict[str, Any]],
    section_ids: list[str],
    source_start: float,
    duration: float,
) -> list[dict[str, Any]]:
    selected_ids = set(section_ids)
    local_sections = []
    for section in sections:
        item_id = section.get("item_id")
        if item_id is None or str(item_id) not in selected_ids:
            continue
        local_section = deepcopy(section)
        start_time = _as_float(local_section.get("start_time"), source_start) - source_start
        end_time = _as_float(local_section.get("end_time"), source_start) - source_start
        local_section["start_time"] = _clamp(start_time, 0.0, duration)
        local_section["end_time"] = max(
            local_section["start_time"],
            _clamp(end_time, 0.0, duration),
        )
        local_sections.append(local_section)
    return local_sections


def _local_sequence(
    sequence: dict[str, Any],
    selected_shot: dict[str, Any],
    duration: float,
    shot_context: dict[str, Any],
) -> dict[str, Any]:
    local_sequence = deepcopy(sequence)
    local_shot = deepcopy(selected_shot)
    local_shot["start_time"] = 0.0
    local_shot["end_time"] = duration
    local_sequence["shots"] = [local_shot]
    local_sequence["boundaries"] = []
    metadata = local_sequence.get("metadata") if isinstance(local_sequence.get("metadata"), dict) else {}
    metadata = deepcopy(metadata)
    metadata["shot_extraction"] = deepcopy(shot_context)
    local_sequence["metadata"] = metadata
    return local_sequence


def _find_boundary(
    boundaries: list[dict[str, Any]],
    *,
    left_shot_id: Any,
    right_shot_id: Any,
) -> dict[str, Any] | None:
    if left_shot_id is None or right_shot_id is None:
        return None
    left_text = str(left_shot_id)
    right_text = str(right_shot_id)
    for boundary in boundaries:
        if (
            str(boundary.get("left_shot_id") or "") == left_text
            and str(boundary.get("right_shot_id") or "") == right_text
        ):
            return boundary
    return None


def _boundary_summary(boundary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(boundary, dict):
        return None
    return {
        "boundary_id": boundary.get("boundary_id"),
        "left_shot_id": boundary.get("left_shot_id"),
        "right_shot_id": boundary.get("right_shot_id"),
        "mode": boundary.get("mode"),
        "tail_frames": boundary.get("tail_frames"),
        "blend_frames": boundary.get("blend_frames"),
        "transition_prompt": boundary.get("transition_prompt"),
        "reuse_character_refs": boundary.get("reuse_character_refs"),
        "reuse_style": boundary.get("reuse_style"),
        "metadata": deepcopy(boundary.get("metadata") or {}),
    }


def _boundary_continuity_policy(boundary: dict[str, Any] | None) -> str:
    if not isinstance(boundary, dict):
        return CONTINUITY_POLICY_NONE
    mode = boundary.get("mode")
    if mode == BOUNDARY_MODE_CONTINUOUS_SHOT:
        return CONTINUITY_POLICY_CONTINUOUS
    if mode == BOUNDARY_MODE_BLEND_SEAM:
        return CONTINUITY_POLICY_BLEND
    if mode == BOUNDARY_MODE_TRANSITION:
        return CONTINUITY_POLICY_TRANSITION
    return CONTINUITY_POLICY_NONE


def _combined_continuity_policy(incoming_policy: str, outgoing_policy: str) -> str:
    priority = {
        CONTINUITY_POLICY_NONE: 0,
        CONTINUITY_POLICY_CONTINUOUS: 1,
        CONTINUITY_POLICY_BLEND: 2,
        CONTINUITY_POLICY_TRANSITION: 3,
    }
    return max(
        (incoming_policy, outgoing_policy),
        key=lambda policy: priority.get(policy, 0),
    )


def _effective_tail_frames(boundary: dict[str, Any] | None) -> int:
    if _boundary_continuity_policy(boundary) == CONTINUITY_POLICY_NONE:
        return 0
    return max(0, _as_int(boundary.get("tail_frames") if boundary else None, 0))


def _effective_blend_frames(boundary: dict[str, Any] | None) -> int:
    policy = _boundary_continuity_policy(boundary)
    if policy not in {CONTINUITY_POLICY_BLEND, CONTINUITY_POLICY_TRANSITION}:
        return 0
    return max(0, _as_int(boundary.get("blend_frames") if boundary else None, 0))


def _accepted_take_id(shot: dict[str, Any] | None) -> str | None:
    if not isinstance(shot, dict):
        return None
    take_id = shot.get("accepted_take_id")
    return str(take_id) if take_id is not None else None


def _shot_clip_asset_id(shot: dict[str, Any] | None) -> str | None:
    if not isinstance(shot, dict):
        return None
    clip_instance = shot.get("clip_instance")
    if isinstance(clip_instance, dict) and clip_instance.get("enabled") is not False:
        asset_id = clip_instance.get("asset_id")
        if asset_id is not None:
            return str(asset_id)
    accepted_take_id = _accepted_take_id(shot)
    if accepted_take_id is None:
        return None
    for take in shot.get("takes", []):
        if not isinstance(take, dict):
            continue
        if str(take.get("take_id") or "") != accepted_take_id:
            continue
        asset_id = take.get("asset_id")
        return str(asset_id) if asset_id is not None else None
    return None


def _continuity_source_context(
    *,
    selected_shot_id: Any,
    source_shot: dict[str, Any] | None,
    boundary: dict[str, Any] | None,
    policy: str,
    tail_frames: int,
    blend_frames: int,
    direction: str,
) -> dict[str, Any]:
    context = {
        "direction": direction,
        "policy": policy,
        "status": CONTINUITY_STATUS_NOT_REQUESTED,
        "boundary_id": boundary.get("boundary_id") if isinstance(boundary, dict) else None,
        "source_shot_id": source_shot.get("shot_id") if isinstance(source_shot, dict) else None,
        "target_shot_id": selected_shot_id,
        "tail_frames": int(tail_frames),
        "blend_frames": int(blend_frames),
        "clip_reference": None,
        "warning_code": None,
        "message": "Boundary does not request continuity context.",
    }
    if policy == CONTINUITY_POLICY_NONE:
        return context
    if not isinstance(source_shot, dict):
        context.update(
            {
                "status": CONTINUITY_STATUS_UNAVAILABLE,
                "warning_code": "SHOT_CONTINUITY_SOURCE_MISSING",
                "message": "Boundary requests continuity context, but the source shot is missing.",
            }
        )
        return context
    clip_reference = _shot_clip_reference(source_shot)
    if clip_reference is None:
        context.update(
            {
                "status": CONTINUITY_STATUS_UNAVAILABLE,
                "warning_code": "SHOT_CONTINUITY_PREVIOUS_CLIP_MISSING",
                "message": "Boundary requests continuity context, but the source shot has no accepted take or enabled clip instance.",
            }
        )
        return context
    context.update(
        {
            "status": CONTINUITY_STATUS_AVAILABLE,
            "clip_reference": clip_reference,
            "message": "Continuity clip reference is available for model-specific helpers that support it.",
        }
    )
    return context


def _shot_clip_reference(shot: dict[str, Any]) -> dict[str, Any] | None:
    accepted_take_id = _accepted_take_id(shot)
    if accepted_take_id is not None:
        for take in shot.get("takes", []):
            if not isinstance(take, dict):
                continue
            if str(take.get("take_id") or "") != accepted_take_id:
                continue
            asset_id = take.get("asset_id")
            if asset_id is None:
                return None
            return {
                "source_kind": "accepted_take",
                "shot_id": shot.get("shot_id"),
                "take_id": accepted_take_id,
                "asset_id": str(asset_id),
            }
    clip_instance = shot.get("clip_instance")
    if isinstance(clip_instance, dict) and clip_instance.get("enabled") is not False:
        asset_id = clip_instance.get("asset_id")
        if asset_id is None:
            return None
        return {
            "source_kind": "clip_instance",
            "shot_id": shot.get("shot_id"),
            "take_id": None,
            "asset_id": str(asset_id),
            "source_in": clip_instance.get("source_in"),
            "source_out": clip_instance.get("source_out"),
        }
    return None


def _as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
