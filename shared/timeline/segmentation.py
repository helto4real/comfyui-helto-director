from __future__ import annotations

import math
from typing import Any, Callable


def build_generation_segments(
    *,
    section_entries: list[dict[str, Any]],
    frame_rate: float,
    total_frames: int,
    requested_frame_count: int | None = None,
    max_generation_duration: float,
    segment_continuity_tail_frames: int = 5,
    temporal_stride: int,
    model: str,
    frame_rule: Callable[[int], int],
) -> dict[str, Any]:
    total_frames = max(1, int(total_frames or 1))
    requested_frames = max(1, int(requested_frame_count or total_frames))
    frame_rate = max(0.001, float(frame_rate or 24.0))
    max_generation_duration = max(0.0, float(max_generation_duration or 0.0))
    segment_continuity_tail_frames = max(1, int(segment_continuity_tail_frames or 1))
    temporal_stride = max(1, int(temporal_stride or 1))
    max_visible_frames = int(math.floor(max_generation_duration * frame_rate)) if max_generation_duration > 0 else 0
    max_visible_frames = max(1, max_visible_frames) if max_visible_frames else 0
    boundaries = _natural_boundaries(section_entries, total_frames)
    requested_segment_count = (
        int(math.ceil(requested_frames / max_visible_frames))
        if max_visible_frames
        else 1
    )

    if not max_visible_frames or requested_segment_count <= 1:
        visible = total_frames
        return {
            "enabled": False,
            "model": model,
            "max_generation_duration": max_generation_duration,
            "max_visible_frames": max_visible_frames,
            "continuity_strategy": "single_segment",
            "segments": [
                _segment(
                    1,
                    0,
                    visible,
                    frame_rate,
                    section_entries,
                    visible,
                    frame_rule(visible),
                    0,
                    None,
                    "single_segment",
                    segment_continuity_tail_frames,
                    0,
                )
            ],
            "diagnostics": ["Segmented generation is disabled or the timeline fits in one generation."],
        }

    segments = []
    split_points = _choose_split_points(
        boundaries,
        total_frames,
        requested_frames,
        max_visible_frames,
        requested_segment_count,
        temporal_stride,
    )
    ranges = [0, *split_points, total_frames]
    for index, (start, end) in enumerate(zip(ranges, ranges[1:]), start=1):
        reason = "natural_boundary" if end in boundaries and end != total_frames else "duration_cap"
        visible = max(1, end - start)
        previous_visible = int(segments[-1]["visible_frame_count"]) if segments else 0
        trim_leading = (
            min(max(1, int(segment_continuity_tail_frames or 1)), visible, max(1, previous_visible))
            if segments
            else 0
        )
        requested_generation = visible + trim_leading
        generation = frame_rule(requested_generation)
        segments.append(
            _segment(
                index,
                start,
                end,
                frame_rate,
                section_entries,
                visible,
                generation,
                trim_leading,
                segments[-1]["id"] if segments else None,
                reason,
                segment_continuity_tail_frames,
                previous_visible,
            )
        )

    return {
        "enabled": True,
        "model": model,
        "max_generation_duration": max_generation_duration,
        "max_visible_frames": max_visible_frames,
        "continuity_strategy": "model_auto",
        "segments": segments,
        "diagnostics": [
            f"Planned {len(segments)} hidden generation segment(s) for {model}.",
            f"Continuation segments generate {segment_continuity_tail_frames} leading continuity frame(s) that are trimmed during stitching.",
        ],
    }


def _choose_split_points(
    boundaries: list[int],
    total_frames: int,
    requested_frames: int,
    max_visible_frames: int,
    segment_count: int,
    temporal_stride: int,
) -> list[int]:
    splits: list[int] = []
    previous = 0
    tolerance = max(1, int(temporal_stride or 1) // 2)
    for split_index in range(1, segment_count):
        remaining_segments = segment_count - split_index
        lower = previous + 1
        upper = max(lower, total_frames - remaining_segments)
        target = min(requested_frames, split_index * max_visible_frames)
        target = min(max(int(round(target)), lower), upper)
        candidates = [
            boundary
            for boundary in boundaries
            if lower <= boundary <= upper and abs(boundary - target) <= tolerance
        ]
        if candidates:
            split = min(candidates, key=lambda boundary: (abs(boundary - target), boundary))
        else:
            split = target
        split = min(max(split, lower), upper)
        splits.append(split)
        previous = split
    return splits


def _natural_boundaries(section_entries: list[dict[str, Any]], total_frames: int) -> list[int]:
    boundaries = {0, int(total_frames)}
    for entry in section_entries:
        start = int(entry.get("start_frame") or 0)
        end = int(entry.get("end_frame_exclusive") or start)
        if 0 <= start <= total_frames:
            boundaries.add(start)
        if 0 <= end <= total_frames:
            boundaries.add(end)
    return sorted(boundaries)


def _segment(
    index: int,
    start: int,
    end: int,
    frame_rate: float,
    section_entries: list[dict[str, Any]],
    visible_frame_count: int,
    generation_frame_count: int,
    trim_leading_frames: int,
    previous_segment_id: str | None,
    split_reason: str,
    segment_continuity_tail_frames: int,
    previous_visible_frame_count: int = 0,
) -> dict[str, Any]:
    continuity_frame_count = (
        0
        if previous_segment_id is None
        else min(
            max(1, int(segment_continuity_tail_frames or 1)),
            max(1, visible_frame_count),
            max(1, int(previous_visible_frame_count or 1)),
        )
    )
    return {
        "id": f"gen_{index:03d}",
        "index": index - 1,
        "start_frame": int(start),
        "end_frame_exclusive": int(end),
        "frame_count": int(visible_frame_count),
        "visible_frame_count": int(visible_frame_count),
        "generation_frame_count": int(generation_frame_count),
        "trim_leading_frames": int(trim_leading_frames),
        "trim_trailing_frames": max(0, int(generation_frame_count) - int(visible_frame_count) - int(trim_leading_frames)),
        "start_time": int(start) / frame_rate,
        "end_time": int(end) / frame_rate,
        "split_reason": split_reason,
        "source_section_ids": _source_sections(section_entries, start, end),
        "continuity": {
            "mode": "initial" if previous_segment_id is None else "model_auto",
            "source": None if previous_segment_id is None else "previous_tail",
            "source_segment": previous_segment_id,
            "continuity_frame_count": int(continuity_frame_count),
            "prompt_hint": previous_segment_id is not None,
        },
    }


def _source_sections(section_entries: list[dict[str, Any]], start: int, end: int) -> list[str]:
    ids: list[str] = []
    for entry in section_entries:
        entry_start = int(entry.get("start_frame") or 0)
        entry_end = int(entry.get("end_frame_exclusive") or entry_start)
        if entry_end <= start or entry_start >= end:
            continue
        item_id = entry.get("item_id")
        if item_id is not None:
            ids.append(str(item_id))
    return ids
