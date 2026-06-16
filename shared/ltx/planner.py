from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from ..contracts.validation import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    create_validation_entry,
    create_validation_result,
    flatten_validation_result,
)
from ..contracts.video_timeline import (
    QUALITY_PRESET_DRAFT,
    QUALITY_PRESET_HIGH,
    QUALITY_PRESET_NATIVE_RESOLUTION,
    QUALITY_PRESET_QUICK_DRAFT,
    QUALITY_PRESET_STANDARD,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
)
from ..timeline import (
    build_generation_segments,
    detect_director_gaps,
    merge_prompts,
    normalize_video_timeline,
    time_range_to_frames,
    validate_video_timeline,
)
from .config import (
    LTX_MODEL_FAMILY,
    LTX_MODEL_VERSION,
    RESOLUTION_PROFILE_AUTO,
    normalize_ltx_timeline_config,
)
from .references import build_ltx_character_reference_plan


LTX_PLAN_SCHEMA_VERSION = "1.0"
LTX_PLAN_TYPE = "LTX_TIMELINE_PLAN"

QUALITY_SHORT_EDGE = {
    QUALITY_PRESET_QUICK_DRAFT: 384,
    QUALITY_PRESET_DRAFT: 512,
    QUALITY_PRESET_STANDARD: 768,
    QUALITY_PRESET_HIGH: 1024,
    QUALITY_PRESET_NATIVE_RESOLUTION: 1280,
}


def build_ltx_timeline_plan(video_timeline: Any, ltx_config: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    timeline = normalize_video_timeline(video_timeline)
    config = normalize_ltx_timeline_config(ltx_config)
    director_validation = validate_video_timeline(timeline)
    frame_rate = float(timeline["project"].get("frame_rate") or 24.0)
    requested_frames = _requested_frame_count(float(timeline["project"].get("duration_seconds") or 0.0), frame_rate)
    total_frames = _ltx_frame_count_from_requested(requested_frames, int(config["rules"]["temporal_stride"]))
    resolved_output = _resolve_output(timeline["project"], config)
    section_entries = _build_section_plan(timeline, frame_rate, total_frames)
    segmented_generation = build_generation_segments(
        section_entries=section_entries,
        frame_rate=frame_rate,
        total_frames=total_frames,
        requested_frame_count=requested_frames,
        max_generation_duration=float(config.get("max_generation_duration") or 0.0),
        segment_continuity_tail_frames=int(config.get("segment_continuity_tail_frames") or 5),
        temporal_stride=int(config["rules"]["temporal_stride"]),
        model="ltx",
        frame_rule=lambda requested: _ltx_frame_count_from_requested(requested, int(config["rules"]["temporal_stride"])),
    )
    character_references, character_validation_entries = build_ltx_character_reference_plan(timeline, config, section_entries)
    ltx_validation = _validate_ltx_inputs(
        timeline,
        config,
        director_validation,
        character_validation_entries,
    )
    validation = create_validation_result([
        *flatten_validation_result(director_validation),
        *flatten_validation_result(ltx_validation),
    ])

    prompt_entries = _build_prompt_plan(timeline, section_entries, character_references)
    media_entries = _build_media_plan(timeline, section_entries, config)
    audio_entries = _build_audio_plan(timeline, frame_rate)

    plan = {
        "schema_version": LTX_PLAN_SCHEMA_VERSION,
        "type": LTX_PLAN_TYPE,
        "model_family": LTX_MODEL_FAMILY,
        "model_version": LTX_MODEL_VERSION,
        "source_timeline_schema_version": timeline.get("schema_version"),
        "project": deepcopy(timeline["project"]),
        "resolved_output": {
            **resolved_output,
            "frame_rate": frame_rate,
            "frame_count": total_frames,
            "duration_seconds": timeline["project"].get("duration_seconds"),
        },
        "section_plan": section_entries,
        "prompt_plan": prompt_entries,
        "media_plan": media_entries,
        "audio_plan": audio_entries,
        "model_specific": {
            "ltx": {
                "config": config,
                "prompt_relay": {
                    "enabled": config["reference_mode"] == "Prompt Relay",
                    "epsilon": config["prompt_relay_epsilon"],
                },
                "character_references": character_references,
                "segmented_generation": segmented_generation,
                "rules": deepcopy(config["rules"]),
            },
        },
        "validation": validation,
    }
    debug = _build_debug(timeline, config, plan, validation)
    return plan, validation, debug


def _validate_ltx_inputs(
    timeline: dict[str, Any],
    config: dict[str, Any],
    director_validation: dict[str, Any],
    character_validation_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entries = [*(character_validation_entries or [])]
    if not director_validation.get("is_valid", False):
        entries.append(
            create_validation_entry(
                "LTX_DIRECTOR_TIMELINE_INVALID",
                SEVERITY_ERROR,
                "LTX Planner",
                "Timeline",
                None,
                "LTX planning requires a valid Director timeline.",
                "Fix Director validation errors before running the LTX runtime.",
            )
        )
    for gap in detect_director_gaps(timeline):
        entries.append(
            create_validation_entry(
                "LTX_DIRECTOR_GAP_NO_GUIDANCE",
                SEVERITY_INFO,
                "LTX Planner",
                "Gap",
                None,
                "Director gap will be planned as No Guidance for LTX.",
                "This is allowed; runtime will not create guidance for this range.",
                gap,
            )
        )
    if config["image_guidance_mode"] == "Disabled":
        for section in timeline["director_track"]["sections"]:
            if section.get("type") == SECTION_TYPE_IMAGE:
                entries.append(
                    create_validation_entry(
                        "LTX_IMAGE_GUIDANCE_DISABLED",
                        SEVERITY_WARNING,
                        "LTX Planner",
                        "Section",
                        section.get("item_id"),
                        "Image Section media is present but image guidance is disabled.",
                        "Enable image guidance or use a Text Section.",
                    )
                )
    return create_validation_result(entries)


def _resolve_output(project: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    profile = config["resolution_profile"]
    if profile == RESOLUTION_PROFILE_AUTO:
        profile = project.get("quality_preset", QUALITY_PRESET_STANDARD)
    short_edge = QUALITY_SHORT_EDGE.get(profile, QUALITY_SHORT_EDGE[QUALITY_PRESET_STANDARD])
    ratio_w, ratio_h = _aspect_ratio(project.get("aspect_ratio", "16:9"), project.get("orientation", "Landscape"))
    if ratio_w >= ratio_h:
        height = short_edge
        width = round(short_edge * ratio_w / ratio_h)
    else:
        width = short_edge
        height = round(short_edge * ratio_h / ratio_w)
    divisible_by = int(config["rules"]["divisible_by"])
    return {
        "width": _round_to_multiple(width, divisible_by),
        "height": _round_to_multiple(height, divisible_by),
        "resolution_profile": profile,
        "divisible_by": divisible_by,
    }


def _build_section_plan(timeline: dict[str, Any], frame_rate: float, total_frames: int) -> list[dict[str, Any]]:
    sections = []
    for section in sorted(timeline["director_track"]["sections"], key=lambda item: item.get("start_time", 0.0)):
        frame_range = time_range_to_frames(section["start_time"], section["end_time"], frame_rate)
        sections.append({
            "item_id": section.get("item_id"),
            "type": section.get("type"),
            "role": _section_role(section.get("type")),
            "start_time": section.get("start_time"),
            "end_time": section.get("end_time"),
            "start_frame": frame_range["start_frame"],
            "end_frame_exclusive": min(frame_range["end_frame_exclusive"], total_frames),
            "frame_count": max(0, min(frame_range["end_frame_exclusive"], total_frames) - frame_range["start_frame"]),
        })
    for index, gap in enumerate(detect_director_gaps(timeline)):
        frame_range = time_range_to_frames(gap["start_time"], gap["end_time"], frame_rate)
        sections.append({
            "item_id": f"gap_{index + 1:03d}",
            "type": "Gap",
            "role": "No Guidance",
            "start_time": gap["start_time"],
            "end_time": gap["end_time"],
            "start_frame": frame_range["start_frame"],
            "end_frame_exclusive": min(frame_range["end_frame_exclusive"], total_frames),
            "frame_count": max(0, min(frame_range["end_frame_exclusive"], total_frames) - frame_range["start_frame"]),
        })
    return sorted(sections, key=lambda item: (item["start_frame"], item["end_frame_exclusive"], item["item_id"]))


def _build_prompt_plan(
    timeline: dict[str, Any],
    section_entries: list[dict[str, Any]],
    character_references: dict[str, Any],
) -> list[dict[str, Any]]:
    global_prompt = timeline["project"].get("global_prompt", {})
    runtime_global_prompt = character_references.get("runtime_global_prompt", global_prompt.get("prompt", ""))
    runtime_prompts_by_id = {
        entry.get("item_id"): entry.get("runtime_prompt", "")
        for entry in character_references.get("section_usage", [])
    }
    sections_by_id = {
        section.get("item_id"): section
        for section in timeline["director_track"]["sections"]
    }
    prompts = []
    for entry in section_entries:
        section = sections_by_id.get(entry["item_id"])
        raw_prompt = section.get("prompt", "") if section else ""
        runtime_prompt = runtime_prompts_by_id.get(entry["item_id"], raw_prompt)
        prompts.append({
            "item_id": entry["item_id"],
            "type": entry["type"],
            "raw_prompt": raw_prompt,
            "runtime_prompt": runtime_prompt,
            "original_effective_prompt": merge_prompts(
                raw_prompt,
                global_prompt.get("prompt", ""),
                bool(global_prompt.get("enabled")),
                global_prompt.get("position", "Prefix"),
            ),
            "effective_prompt": merge_prompts(
                runtime_prompt,
                runtime_global_prompt,
                bool(global_prompt.get("enabled")),
                global_prompt.get("position", "Prefix"),
            ),
        })
    return prompts


def _build_media_plan(timeline: dict[str, Any], section_entries: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    assets_by_id = {asset.get("asset_id"): asset for asset in timeline.get("assets", [])}
    sections_by_id = {
        section.get("item_id"): section
        for section in timeline["director_track"]["sections"]
    }
    media = []
    for entry in section_entries:
        section = sections_by_id.get(entry["item_id"])
        if not section:
            continue
        reference = section.get("image") if section.get("type") == SECTION_TYPE_IMAGE else section.get("video")
        asset = _resolve_asset(reference, assets_by_id)
        if not asset:
            continue
        media.append({
            "item_id": entry["item_id"],
            "section_type": section.get("type"),
            "asset_id": asset.get("asset_id"),
            "asset_type": asset.get("type"),
            "source_kind": asset.get("source_kind"),
            "path": asset.get("path"),
            "ltx_role": _ltx_media_role(section, config),
            "guide_strength": section.get("guide_strength"),
            "crop_mode": section.get("crop_mode"),
            "source_in": section.get("source_in"),
            "source_out": section.get("source_out"),
            "timing_mode": section.get("timing_mode"),
            "video_guidance_range": section.get("video_guidance_range"),
            "video_guidance_frame_count": section.get("video_guidance_frame_count"),
        })
    return media


def _build_audio_plan(timeline: dict[str, Any], frame_rate: float) -> list[dict[str, Any]]:
    assets_by_id = {asset.get("asset_id"): asset for asset in timeline.get("assets", [])}
    audio_entries = []
    for track in timeline.get("audio_tracks", []):
        for clip in track.get("clips", []):
            frame_range = time_range_to_frames(clip.get("start_time", 0.0), clip.get("end_time", 0.0), frame_rate)
            asset = _resolve_asset(clip.get("audio"), assets_by_id)
            audio_entries.append({
                "track_id": track.get("track_id"),
                "item_id": clip.get("item_id"),
                "asset_id": asset.get("asset_id") if asset else None,
                "path": asset.get("path") if asset else None,
                "start_frame": frame_range["start_frame"],
                "end_frame_exclusive": frame_range["end_frame_exclusive"],
                "start_time": clip.get("start_time"),
                "end_time": clip.get("end_time"),
                "source_in": clip.get("source_in"),
                "source_out": clip.get("source_out"),
                "volume": clip.get("volume"),
                "fade_in": clip.get("fade_in"),
                "fade_out": clip.get("fade_out"),
                "enabled": clip.get("enabled"),
                "lane": clip.get("lane"),
            })
    return audio_entries


def _build_debug(timeline: dict[str, Any], config: dict[str, Any], plan: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "DEBUG_INFO",
        "source": "LTX Planner",
        "enabled": bool(config.get("debug_mode")),
        "summary": {
            "section_count": len(timeline["director_track"]["sections"]),
            "planned_ranges": len(plan["section_plan"]),
            "media_items": len(plan["media_plan"]),
            "audio_items": len(plan["audio_plan"]),
            "error_count": len(validation["errors"]),
            "warning_count": len(validation["warnings"]),
            "info_count": len(validation["info"]),
        },
    }


def _ltx_frame_count(duration_seconds: float, frame_rate: float, temporal_stride: int) -> int:
    return _ltx_frame_count_from_requested(_requested_frame_count(duration_seconds, frame_rate), temporal_stride)


def _requested_frame_count(duration_seconds: float, frame_rate: float) -> int:
    return max(1, math.ceil(duration_seconds * frame_rate))


def _ltx_frame_count_from_requested(raw_frames: int, temporal_stride: int) -> int:
    raw_frames = max(1, int(raw_frames))
    if raw_frames <= 1:
        return 1
    return ((raw_frames - 1 + temporal_stride - 1) // temporal_stride) * temporal_stride + 1


def _section_role(section_type: str | None) -> str:
    if section_type == SECTION_TYPE_TEXT:
        return "Prompt Only"
    if section_type == SECTION_TYPE_IMAGE:
        return "Image Guidance"
    if section_type == SECTION_TYPE_VIDEO:
        return "Video Guidance"
    return "No Guidance"


def _ltx_media_role(section: dict[str, Any], config: dict[str, Any]) -> str:
    if section.get("type") == SECTION_TYPE_IMAGE:
        return config["image_guidance_mode"]
    if section.get("type") == SECTION_TYPE_VIDEO:
        return config["video_section_mode"]
    return "None"


def _resolve_asset(reference: Any, assets_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if isinstance(reference, dict) and reference.get("asset_id"):
        return assets_by_id.get(reference["asset_id"])
    if isinstance(reference, dict) and (reference.get("path") or reference.get("file_path")):
        return {"path": reference.get("path") or reference.get("file_path")}
    if isinstance(reference, str) and reference.strip():
        return {"path": reference.strip()}
    return None


def _aspect_ratio(value: str, orientation: str) -> tuple[int, int]:
    try:
        width, height = [int(part) for part in str(value).split(":", 1)]
    except Exception:
        width, height = 16, 9
    width = max(1, width)
    height = max(1, height)
    if orientation == "Portrait" and width > height:
        return height, width
    if orientation == "Landscape" and height > width:
        return height, width
    if orientation == "Square":
        return 1, 1
    return width, height


def _round_to_multiple(value: int, multiple: int) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)
