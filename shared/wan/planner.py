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
    detect_director_gaps,
    merge_prompts,
    normalize_video_timeline,
    time_range_to_frames,
    validate_video_timeline,
)
from .config import (
    RESOLUTION_PROFILE_AUTO,
    WAN_MODEL_FAMILY,
    WAN_MODEL_VERSION,
    normalize_wan_timeline_config,
)


WAN_PLAN_SCHEMA_VERSION = "1.0"
WAN_PLAN_TYPE = "WAN_TIMELINE_PLAN"

QUALITY_SHORT_EDGE = {
    QUALITY_PRESET_QUICK_DRAFT: 384,
    QUALITY_PRESET_DRAFT: 512,
    QUALITY_PRESET_STANDARD: 768,
    QUALITY_PRESET_HIGH: 1024,
    QUALITY_PRESET_NATIVE_RESOLUTION: 1280,
}


def build_wan_timeline_plan(video_timeline: Any, wan_config: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    timeline = normalize_video_timeline(video_timeline)
    config = normalize_wan_timeline_config(wan_config)
    director_validation = validate_video_timeline(timeline)
    wan_validation = _validate_wan_inputs(timeline, director_validation)
    validation = create_validation_result([
        *flatten_validation_result(director_validation),
        *flatten_validation_result(wan_validation),
    ])

    frame_rate = float(timeline["project"].get("frame_rate") or 24.0)
    total_frames = _wan_frame_count(float(timeline["project"].get("duration_seconds") or 0.0), frame_rate)
    resolved_output = _resolve_output(timeline["project"], config)
    section_entries = _build_section_plan(timeline, frame_rate, total_frames)
    prompt_entries = _build_prompt_plan(timeline, section_entries)
    media_entries = _build_media_plan(timeline, section_entries)
    audio_entries = _build_audio_plan(timeline, frame_rate)

    plan = {
        "schema_version": WAN_PLAN_SCHEMA_VERSION,
        "type": WAN_PLAN_TYPE,
        "model_family": WAN_MODEL_FAMILY,
        "model_version": WAN_MODEL_VERSION,
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
            "wan": {
                "config": config,
                "rules": deepcopy(config["rules"]),
                "runtime_status": "Planner skeleton only",
            },
        },
        "validation": validation,
    }
    debug = _build_debug(timeline, config, plan, validation)
    return plan, validation, debug


def _validate_wan_inputs(timeline: dict[str, Any], director_validation: dict[str, Any]) -> dict[str, Any]:
    entries = []
    if not director_validation.get("is_valid", False):
        entries.append(
            create_validation_entry(
                "WAN_DIRECTOR_TIMELINE_INVALID",
                SEVERITY_ERROR,
                "WAN Planner",
                "Timeline",
                None,
                "WAN planning requires a valid Director timeline.",
                "Fix Director validation errors before using the WAN planner output.",
            )
        )
    for gap in detect_director_gaps(timeline):
        entries.append(
            create_validation_entry(
                "WAN_DIRECTOR_GAP_NO_GUIDANCE",
                SEVERITY_INFO,
                "WAN Planner",
                "Gap",
                None,
                "Director gap will be planned as No Guidance for WAN.",
                "This is allowed; no prompt guidance is created for this range.",
                gap,
            )
        )
    for section in timeline["director_track"]["sections"]:
        section_type = section.get("type")
        if section_type == SECTION_TYPE_IMAGE:
            entries.append(
                create_validation_entry(
                    "WAN_IMAGE_SECTION_UNSUPPORTED",
                    SEVERITY_WARNING,
                    "WAN Planner",
                    "Section",
                    section.get("item_id"),
                    "Image Sections are preserved in the WAN plan but are not supported by the WAN skeleton.",
                    "Use Text Sections for the first WAN skeleton, or wait for WAN image guidance runtime support.",
                )
            )
        elif section_type == SECTION_TYPE_VIDEO:
            entries.append(
                create_validation_entry(
                    "WAN_VIDEO_SECTION_UNSUPPORTED",
                    SEVERITY_WARNING,
                    "WAN Planner",
                    "Section",
                    section.get("item_id"),
                    "Video Sections are preserved in the WAN plan but are not supported by the WAN skeleton.",
                    "Use Text Sections for the first WAN skeleton, or wait for WAN video guidance runtime support.",
                )
            )
    for track in timeline.get("audio_tracks", []):
        for clip in track.get("clips", []):
            entries.append(
                create_validation_entry(
                    "WAN_AUDIO_CLIP_UNSUPPORTED",
                    SEVERITY_WARNING,
                    "WAN Planner",
                    "Audio Clip",
                    clip.get("item_id"),
                    "Audio clips are preserved in the WAN plan but are not supported by the WAN skeleton.",
                    "WAN audio handling will be designed in a later runtime phase.",
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


def _build_prompt_plan(timeline: dict[str, Any], section_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    global_prompt = timeline["project"].get("global_prompt", {})
    sections_by_id = {
        section.get("item_id"): section
        for section in timeline["director_track"]["sections"]
    }
    prompts = []
    for entry in section_entries:
        section = sections_by_id.get(entry["item_id"])
        if not section:
            prompts.append({
                "item_id": entry["item_id"],
                "type": entry["type"],
                "raw_prompt": "",
                "effective_prompt": "",
            })
            continue
        raw_prompt = section.get("prompt", "") if section else ""
        prompts.append({
            "item_id": entry["item_id"],
            "type": entry["type"],
            "raw_prompt": raw_prompt,
            "effective_prompt": merge_prompts(
                raw_prompt,
                global_prompt.get("prompt", ""),
                bool(global_prompt.get("enabled")),
                global_prompt.get("position", "Prefix"),
            ),
        })
    return prompts


def _build_media_plan(timeline: dict[str, Any], section_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            "wan_role": "Unsupported Guidance",
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
        "source": "WAN Planner",
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


def _wan_frame_count(duration_seconds: float, frame_rate: float) -> int:
    return max(1, math.ceil(duration_seconds * frame_rate))


def _section_role(section_type: str | None) -> str:
    if section_type == SECTION_TYPE_TEXT:
        return "Prompt Only"
    if section_type == SECTION_TYPE_IMAGE:
        return "Unsupported Image Guidance"
    if section_type == SECTION_TYPE_VIDEO:
        return "Unsupported Video Guidance"
    return "No Guidance"


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
