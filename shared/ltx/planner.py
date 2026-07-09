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
    BOUNDARY_MODE_BLEND_SEAM,
    BOUNDARY_MODE_CONTINUOUS_SHOT,
    BOUNDARY_MODE_TRANSITION,
    CROP_MODE_CROP,
    MODEL_LORA_MODEL_LTX_2_3,
    MODEL_LORA_TARGET_MAIN,
    QUALITY_PRESET_DRAFT,
    QUALITY_PRESET_HIGH,
    QUALITY_PRESET_NATIVE_RESOLUTION,
    QUALITY_PRESET_QUICK_DRAFT,
    QUALITY_PRESET_STANDARD,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
    VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
    VIDEO_TIMING_USE_SOURCE_TIMING,
)
from ..timeline import (
    GENERATION_MODE_MISSING_ONLY,
    GENERATION_STATUS_TARGETED,
    build_generation_segments,
    detect_director_gaps,
    extract_shot_timeline,
    generation_policy_debug_summary,
    generation_policy_requires_generation,
    generation_policy_validation_entries,
    merge_prompts,
    resolve_generation_policy,
    time_range_to_frames,
    validate_video_timeline,
)
from ..timeline.planner_context import (
    build_model_lora_resolution,
    build_section_shot_map,
    build_sequence_plan_metadata,
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
DEFAULT_BOUNDARY_TAIL_FRAMES = 5

QUALITY_SHORT_EDGE = {
    QUALITY_PRESET_QUICK_DRAFT: 384,
    QUALITY_PRESET_DRAFT: 512,
    QUALITY_PRESET_STANDARD: 768,
    QUALITY_PRESET_HIGH: 1024,
    QUALITY_PRESET_NATIVE_RESOLUTION: 1280,
}


def build_ltx_timeline_plan(
    video_timeline: Any,
    ltx_config: Any,
    generation_mode: str = GENERATION_MODE_MISSING_ONLY,
    shot_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_timeline, generation_policy = resolve_generation_policy(
        video_timeline,
        generation_mode,
        legacy_shot_id=shot_id,
    )
    shot_context = None
    if generation_policy.get("status") == GENERATION_STATUS_TARGETED:
        extracted = extract_shot_timeline(source_timeline, str(generation_policy.get("target_shot_id") or ""))
        timeline = extracted["timeline"]
        shot_context = extracted["shot_context"]
    else:
        timeline = source_timeline
    should_plan_generation = generation_policy_requires_generation(generation_policy)
    config = normalize_ltx_timeline_config(ltx_config)
    director_validation = validate_video_timeline(timeline)
    frame_rate = float(timeline["project"].get("frame_rate") or 24.0)
    requested_frames = _requested_frame_count(float(timeline["project"].get("duration_seconds") or 0.0), frame_rate)
    total_frames = _ltx_frame_count_from_requested(requested_frames, int(config["rules"]["temporal_stride"]))
    resolved_output = _resolve_output(timeline["project"], config)
    section_entries = _build_section_plan(timeline, frame_rate, total_frames) if should_plan_generation else []
    sequence_metadata = build_sequence_plan_metadata(timeline)
    lora_resolution = build_model_lora_resolution(
        timeline,
        section_entries,
        model_key=MODEL_LORA_MODEL_LTX_2_3,
        target_keys=[MODEL_LORA_TARGET_MAIN],
    )
    segmented_generation = (
        build_generation_segments(
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
        if should_plan_generation
        else _empty_segmented_generation("ltx")
    )
    character_references, character_validation_entries = build_ltx_character_reference_plan(timeline, config, section_entries)
    boundary_conditioning = _build_ltx_boundary_conditioning(
        source_timeline,
        shot_context,
        config,
        enabled=should_plan_generation,
    )
    continuity_context = _model_continuity_context(shot_context, boundary_conditioning)
    ltx_validation = _validate_ltx_inputs(
        timeline,
        config,
        director_validation,
        character_validation_entries,
        lora_resolution,
    )
    validation = create_validation_result([
        *flatten_validation_result(director_validation),
        *generation_policy_validation_entries(generation_policy, "LTX Planner"),
        *_shot_continuity_validation_entries(shot_context, "LTX Planner", boundary_conditioning),
        *flatten_validation_result(ltx_validation),
    ])

    prompt_entries = _build_prompt_plan(timeline, section_entries, character_references) if should_plan_generation else []
    media_entries = _build_media_plan(timeline, section_entries, config) if should_plan_generation else []
    if should_plan_generation:
        prompt_entries = _apply_ltx_transition_prompt(prompt_entries, boundary_conditioning)
        media_entries = _append_ltx_boundary_media(media_entries, boundary_conditioning)
    audio_entries = _build_audio_plan(timeline, frame_rate) if should_plan_generation else []

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
                "timeline_structure": sequence_metadata,
                "lora_resolution": lora_resolution,
                "generation_policy": deepcopy(generation_policy),
                "boundary_conditioning": boundary_conditioning,
                "rules": deepcopy(config["rules"]),
            },
        },
        "validation": validation,
    }
    if shot_context is not None:
        plan["model_specific"]["ltx"]["shot_context"] = deepcopy(shot_context)
        plan["model_specific"]["ltx"]["continuity_context"] = continuity_context
    debug = _build_debug(
        timeline,
        config,
        plan,
        validation,
        shot_context=shot_context,
        generation_policy=generation_policy,
    )
    return plan, validation, debug


def _validate_ltx_inputs(
    timeline: dict[str, Any],
    config: dict[str, Any],
    director_validation: dict[str, Any],
    character_validation_entries: list[dict[str, Any]] | None = None,
    lora_resolution: dict[str, Any] | None = None,
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
    if lora_resolution and lora_resolution.get("requires_per_shot_execution"):
        entries.append(
            create_validation_entry(
                "LTX_SHOT_LORA_STACKS_DIFFER",
                SEVERITY_WARNING,
                "LTX Planner",
                "LoRA",
                None,
                "Different shots resolve to different LTX LoRA stacks.",
                "Current LTX runtime generation does not switch LoRAs inside one generation; generate by compatible shot/segment groups or keep one stack.",
                _lora_warning_details(lora_resolution),
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
    section_to_shot = build_section_shot_map(timeline)
    sections = []
    for section in sorted(timeline["director_track"]["sections"], key=lambda item: item.get("start_time", 0.0)):
        item_id = section.get("item_id")
        shot = section_to_shot.get(str(item_id)) if item_id is not None else None
        frame_range = time_range_to_frames(section["start_time"], section["end_time"], frame_rate)
        sections.append({
            "item_id": item_id,
            "shot_id": shot.get("shot_id") if shot else None,
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
            "shot_id": None,
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
            "shot_id": entry.get("shot_id"),
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
            "shot_id": entry.get("shot_id"),
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


def _shot_selection_validation_entries(
    shot_selection_error: dict[str, Any] | None,
    source: str,
) -> list[dict[str, Any]]:
    if not shot_selection_error:
        return []
    return [
        create_validation_entry(
            "SHOT_SELECTION_NOT_FOUND",
            SEVERITY_ERROR,
            source,
            "Shot",
            shot_selection_error.get("shot_id"),
            "Selected shot_id was not found in the timeline sequence.",
            "Use an existing sequence shot_id or leave Shot ID blank for full-timeline planning.",
            shot_selection_error,
        )
    ]


def _shot_continuity_validation_entries(
    shot_context: dict[str, Any] | None,
    source: str,
    boundary_conditioning: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    continuity = _incoming_continuity(shot_context)
    if not continuity or continuity.get("policy") == "none":
        return []
    conditioning = boundary_conditioning if isinstance(boundary_conditioning, dict) else {}
    if conditioning.get("model_status") == "unavailable" or continuity.get("status") == "unavailable":
        return [
            create_validation_entry(
                "LTX_SHOT_CONTINUITY_SOURCE_MISSING",
                SEVERITY_WARNING,
                source,
                "Boundary",
                continuity.get("boundary_id"),
                "Selected shot requests continuity, but the previous clip reference is unavailable.",
                "Accept a take or assign an imported clip on the previous shot, or change the boundary to Hard Cut.",
                {
                    **_continuity_warning_details(continuity),
                    "boundary_conditioning": _conditioning_warning_details(conditioning),
                },
            )
        ]
    return []


def _build_debug(
    timeline: dict[str, Any],
    config: dict[str, Any],
    plan: dict[str, Any],
    validation: dict[str, Any],
    *,
    shot_context: dict[str, Any] | None = None,
    generation_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ltx = plan["model_specific"]["ltx"]
    continuity_context = ltx.get("continuity_context") if isinstance(ltx.get("continuity_context"), dict) else _model_continuity_context(shot_context, ltx.get("boundary_conditioning"))
    boundary_conditioning = ltx.get("boundary_conditioning") if isinstance(ltx.get("boundary_conditioning"), dict) else {}
    timeline_structure = ltx.get("timeline_structure", {})
    lora_resolution = ltx.get("lora_resolution", {})
    summary = {
        "section_count": len(timeline["director_track"]["sections"]),
        "shot_count": len(timeline_structure.get("shots", [])),
        "boundary_count": len(timeline_structure.get("boundaries", [])),
        "planned_ranges": len(plan["section_plan"]),
        "media_items": len(plan["media_plan"]),
        "audio_items": len(plan["audio_plan"]),
        "lora_signature_count": int(lora_resolution.get("unique_signature_count") or 0),
        "error_count": len(validation["errors"]),
        "warning_count": len(validation["warnings"]),
        "info_count": len(validation["info"]),
    }
    summary.update(generation_policy_debug_summary(generation_policy))
    _add_shot_debug_summary(summary, shot_context, generation_policy, continuity_context=continuity_context)
    if boundary_conditioning:
        summary["boundary_conditioning_status"] = boundary_conditioning.get("model_status")
        summary["boundary_conditioning_mode"] = boundary_conditioning.get("mode")
        summary["boundary_conditioning_effective_tail_frames"] = boundary_conditioning.get("effective_tail_frames")
    debug = {
        "type": "DEBUG_INFO",
        "source": "LTX Planner",
        "enabled": bool(config.get("debug_mode")),
        "summary": summary,
    }
    if bool(config.get("debug_mode")) and shot_context is not None:
        debug["details"] = {
            "shot_context": deepcopy(shot_context),
            "continuity_context": deepcopy(continuity_context),
            "boundary_conditioning": deepcopy(boundary_conditioning),
            "generation_policy": deepcopy(generation_policy),
        }
    return debug


def _add_shot_debug_summary(
    summary: dict[str, Any],
    shot_context: dict[str, Any] | None,
    generation_policy: dict[str, Any] | None,
    *,
    continuity_context: dict[str, Any] | None = None,
) -> None:
    if shot_context is not None:
        summary["selected_shot_id"] = shot_context.get("shot_id")
        summary["shot_original_start_time"] = shot_context.get("original_start_time")
        summary["shot_original_end_time"] = shot_context.get("original_end_time")
        summary["shot_duration_seconds"] = shot_context.get("duration_seconds")
        continuity = continuity_context if isinstance(continuity_context, dict) else _model_continuity_context(shot_context)
        summary["shot_continuity_policy"] = continuity.get("policy")
        summary["shot_continuity_status"] = continuity.get("model_status")
        summary["shot_continuity_tail_frames"] = continuity.get("tail_frames")
    elif isinstance(generation_policy, dict) and generation_policy.get("block_reason"):
        summary["selected_shot_id"] = generation_policy.get("selected_shot_id")
        summary["shot_selection_error"] = generation_policy.get("block_reason")


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


def _empty_segmented_generation(model: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "model": model,
        "max_generation_duration": 0.0,
        "max_visible_frames": 0,
        "continuity_strategy": "generation_skipped",
        "segments": [],
        "diagnostics": ["Generation skipped by Director policy."],
    }


def _ltx_media_role(section: dict[str, Any], config: dict[str, Any]) -> str:
    if section.get("type") == SECTION_TYPE_IMAGE:
        return config["image_guidance_mode"]
    if section.get("type") == SECTION_TYPE_VIDEO:
        return config["video_section_mode"]
    return "None"


def _lora_warning_details(lora_resolution: dict[str, Any]) -> dict[str, Any]:
    shot_ids = [
        entry.get("shot_id")
        for entry in lora_resolution.get("shot_loras", [])
        if entry.get("shot_id")
    ]
    section_ids = [
        entry.get("item_id")
        for entry in lora_resolution.get("section_loras", [])
        if entry.get("shot_id")
    ]
    return {
        "model": lora_resolution.get("model"),
        "targets": list(lora_resolution.get("targets") or []),
        "shot_ids": shot_ids,
        "section_ids": section_ids,
        "unique_signature_count": lora_resolution.get("unique_signature_count"),
        "execution_strategy": lora_resolution.get("execution_strategy"),
    }


def _incoming_continuity(shot_context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(shot_context, dict):
        return None
    boundary_context = shot_context.get("boundary_context")
    if not isinstance(boundary_context, dict):
        return None
    continuity = boundary_context.get("incoming_continuity")
    return continuity if isinstance(continuity, dict) else None


def _incoming_boundary(shot_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(shot_context, dict):
        return {}
    boundary_context = shot_context.get("boundary_context")
    if not isinstance(boundary_context, dict):
        return {}
    boundary = boundary_context.get("incoming_boundary")
    return boundary if isinstance(boundary, dict) else {}


def _model_continuity_context(
    shot_context: dict[str, Any] | None,
    boundary_conditioning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    continuity = _incoming_continuity(shot_context) or {}
    policy = continuity.get("policy") or "none"
    status = continuity.get("status") or "not_requested"
    conditioning = boundary_conditioning if isinstance(boundary_conditioning, dict) else {}
    if policy == "none":
        model_status = "not_requested"
    elif conditioning.get("model_status"):
        model_status = str(conditioning.get("model_status"))
    else:
        model_status = status
    return {
        "policy": policy,
        "source_status": status,
        "model_status": model_status,
        "boundary_id": continuity.get("boundary_id"),
        "source_shot_id": continuity.get("source_shot_id"),
        "target_shot_id": continuity.get("target_shot_id"),
        "tail_frames": int(continuity.get("tail_frames") or 0),
        "effective_tail_frames": int(conditioning.get("effective_tail_frames") or continuity.get("tail_frames") or 0),
        "blend_frames": int(continuity.get("blend_frames") or 0),
        "clip_reference": deepcopy(continuity.get("clip_reference")),
        "asset_id": conditioning.get("asset_id"),
        "media_item_id": conditioning.get("media_item_id"),
        "transition_prompt_applied": bool(conditioning.get("transition_prompt_applied")),
        "warning_code": continuity.get("warning_code"),
        "message": (
            conditioning.get("message")
            or continuity.get("message")
        ),
    }


def _continuity_warning_details(continuity: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": continuity.get("policy"),
        "status": continuity.get("status"),
        "boundary_id": continuity.get("boundary_id"),
        "source_shot_id": continuity.get("source_shot_id"),
        "target_shot_id": continuity.get("target_shot_id"),
        "tail_frames": continuity.get("tail_frames"),
        "blend_frames": continuity.get("blend_frames"),
        "clip_reference": deepcopy(continuity.get("clip_reference")),
        "warning_code": continuity.get("warning_code"),
    }


def _conditioning_warning_details(conditioning: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(conditioning, dict):
        return {}
    return {
        "model_status": conditioning.get("model_status"),
        "asset_id": conditioning.get("asset_id"),
        "source_shot_id": conditioning.get("source_shot_id"),
        "target_shot_id": conditioning.get("target_shot_id"),
        "requested_tail_frames": conditioning.get("requested_tail_frames"),
        "effective_tail_frames": conditioning.get("effective_tail_frames"),
        "fallback_reason": conditioning.get("fallback_reason"),
    }


def _build_ltx_boundary_conditioning(
    source_timeline: dict[str, Any],
    shot_context: dict[str, Any] | None,
    config: dict[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    continuity = _incoming_continuity(shot_context) or {}
    boundary = _incoming_boundary(shot_context)
    policy = str(continuity.get("policy") or "none")
    mode = str(boundary.get("mode") or "")
    if not mode:
        mode = {
            "continuous": BOUNDARY_MODE_CONTINUOUS_SHOT,
            "blend": BOUNDARY_MODE_BLEND_SEAM,
            "transition": BOUNDARY_MODE_TRANSITION,
        }.get(policy, "Hard Cut")
    base = {
        "type": "ltx_previous_tail",
        "mode": mode,
        "policy": policy,
        "model_status": "not_requested",
        "status": "not_requested",
        "source_status": str(continuity.get("status") or "not_requested"),
        "boundary_id": continuity.get("boundary_id"),
        "source_shot_id": continuity.get("source_shot_id"),
        "target_shot_id": continuity.get("target_shot_id"),
        "requested_tail_frames": int(continuity.get("tail_frames") or 0),
        "effective_tail_frames": 0,
        "blend_frames": int(continuity.get("blend_frames") or 0),
        "transition_prompt": str(boundary.get("transition_prompt") or ""),
        "transition_prompt_applied": False,
        "reuse_character_refs": boundary.get("reuse_character_refs"),
        "reuse_style": boundary.get("reuse_style"),
        "clip_reference": deepcopy(continuity.get("clip_reference")),
        "diagnostics": [],
        "message": "Boundary does not request LTX continuity conditioning.",
    }
    if policy == "none":
        return base
    if not enabled:
        base["message"] = "Generation was skipped by Director policy; no LTX boundary conditioning was applied."
        return base

    clip_reference = continuity.get("clip_reference") if isinstance(continuity.get("clip_reference"), dict) else None
    if continuity.get("status") != "available" or not clip_reference:
        base.update(
            {
                "model_status": "unavailable",
                "status": "unavailable",
                "fallback_reason": continuity.get("warning_code") or "continuity_source_unavailable",
                "message": continuity.get("message") or "Previous clip reference is unavailable.",
            }
        )
        return base

    assets_by_id = {
        str(asset.get("asset_id")): asset
        for asset in source_timeline.get("assets", [])
        if isinstance(asset, dict) and asset.get("asset_id") is not None
    }
    asset_id = str(clip_reference.get("asset_id") or "")
    asset = assets_by_id.get(asset_id)
    path = asset.get("path") or asset.get("file_path") if isinstance(asset, dict) else None
    if not isinstance(asset, dict) or asset.get("type") != SECTION_TYPE_VIDEO or not path:
        fallback = "continuity_asset_missing"
        if isinstance(asset, dict) and asset.get("type") != SECTION_TYPE_VIDEO:
            fallback = "continuity_asset_not_video"
        elif isinstance(asset, dict):
            fallback = "continuity_asset_path_missing"
        base.update(
            {
                "model_status": "unavailable",
                "status": "unavailable",
                "asset_id": asset_id or None,
                "fallback_reason": fallback,
                "message": "Previous clip asset is unavailable for LTX boundary conditioning.",
            }
        )
        return base

    requested_tail = _requested_boundary_tail_frames(continuity.get("tail_frames"))
    effective_tail = _ltx_guide_frame_count_from_requested(requested_tail)
    media_item_id = f"boundary_tail_{str(base.get('boundary_id') or asset_id or 'incoming')}"
    source_in = clip_reference.get("source_in")
    source_out = clip_reference.get("source_out")
    base.update(
        {
            "model_status": "applied",
            "status": "applied",
            "asset_id": asset_id,
            "asset_type": asset.get("type"),
            "source_kind": clip_reference.get("source_kind"),
            "take_id": clip_reference.get("take_id"),
            "path": path,
            "source_in": source_in,
            "source_out": source_out,
            "requested_tail_frames": requested_tail,
            "effective_tail_frames": effective_tail,
            "media_item_id": media_item_id,
            "guide_strength": float(config.get("video_guide_strength") if config.get("video_guide_strength") is not None else 1.0),
            "transition_prompt_applied": policy == "transition" and bool(str(boundary.get("transition_prompt") or "").strip()),
            "message": "LTX will use the previous clip tail as transient start guidance.",
        }
    )
    if base["transition_prompt_applied"]:
        base["message"] = "LTX will use previous-tail guidance and merge the transition prompt into the first prompt region."
    return base


def _ltx_guide_frame_count_from_requested(value: Any) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = DEFAULT_BOUNDARY_TAIL_FRAMES
    requested = max(1, requested)
    if requested <= 1:
        return 1
    return ((requested - 1 + 7) // 8) * 8 + 1


def _requested_boundary_tail_frames(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_BOUNDARY_TAIL_FRAMES
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_BOUNDARY_TAIL_FRAMES


def _append_ltx_boundary_media(
    media_entries: list[dict[str, Any]],
    boundary_conditioning: dict[str, Any],
) -> list[dict[str, Any]]:
    if boundary_conditioning.get("model_status") != "applied":
        return media_entries
    path = boundary_conditioning.get("path")
    if not path:
        return media_entries
    media = [*media_entries]
    media.append(
        {
            "item_id": boundary_conditioning.get("media_item_id"),
            "shot_id": boundary_conditioning.get("target_shot_id"),
            "section_type": SECTION_TYPE_VIDEO,
            "asset_id": boundary_conditioning.get("asset_id"),
            "asset_type": boundary_conditioning.get("asset_type"),
            "source_kind": boundary_conditioning.get("source_kind"),
            "take_id": boundary_conditioning.get("take_id"),
            "path": path,
            "ltx_role": "Source Video Guides",
            "guide_strength": boundary_conditioning.get("guide_strength", 1.0),
            "crop_mode": CROP_MODE_CROP,
            "source_in": boundary_conditioning.get("source_in"),
            "source_out": boundary_conditioning.get("source_out"),
            "timing_mode": VIDEO_TIMING_USE_SOURCE_TIMING,
            "video_guidance_range": VIDEO_GUIDANCE_RANGE_LAST_FRAMES,
            "video_guidance_frame_count": boundary_conditioning.get("effective_tail_frames"),
            "insert_frame": 0,
            "transient": True,
            "boundary_id": boundary_conditioning.get("boundary_id"),
            "boundary_mode": boundary_conditioning.get("mode"),
            "boundary_policy": boundary_conditioning.get("policy"),
            "requested_tail_frames": boundary_conditioning.get("requested_tail_frames"),
            "effective_tail_frames": boundary_conditioning.get("effective_tail_frames"),
            "source_shot_id": boundary_conditioning.get("source_shot_id"),
            "target_shot_id": boundary_conditioning.get("target_shot_id"),
        }
    )
    return media


def _apply_ltx_transition_prompt(
    prompt_entries: list[dict[str, Any]],
    boundary_conditioning: dict[str, Any],
) -> list[dict[str, Any]]:
    if boundary_conditioning.get("model_status") != "applied":
        return prompt_entries
    if boundary_conditioning.get("policy") != "transition":
        return prompt_entries
    transition_prompt = str(boundary_conditioning.get("transition_prompt") or "").strip()
    if not transition_prompt or not prompt_entries:
        return prompt_entries
    prompts = [deepcopy(entry) for entry in prompt_entries]
    first = prompts[0]
    for key in ("raw_prompt", "runtime_prompt", "effective_prompt"):
        first[key] = _merge_transition_prompt(transition_prompt, first.get(key))
    first["boundary_transition_prompt"] = transition_prompt
    first["boundary_transition_prompt_applied"] = True
    first["boundary_id"] = boundary_conditioning.get("boundary_id")
    return prompts


def _merge_transition_prompt(transition_prompt: str, prompt: Any) -> str:
    text = str(prompt or "").strip()
    if not text:
        return transition_prompt
    if text.startswith(transition_prompt):
        return text
    return f"{transition_prompt}. {text}"


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
