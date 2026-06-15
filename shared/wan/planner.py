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
from .bernini import (
    apply_bernini_prompt_prefix,
    build_bernini_plan,
)


WAN_PLAN_SCHEMA_VERSION = "1.0"
WAN_PLAN_TYPE = "WAN_TIMELINE_PLAN"
WAN_TEMPORAL_STRIDE = 4

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

    frame_rate = float(timeline["project"].get("frame_rate") or 24.0)
    requested_frames = _requested_frame_count(float(timeline["project"].get("duration_seconds") or 0.0), frame_rate)
    total_frames = _wan_frame_count(requested_frames)
    latent_chunk_count = _latent_chunk_count(total_frames)
    frame_info = _wan_frame_info(requested_frames, total_frames, frame_rate)
    resolved_output = _resolve_output(timeline["project"], config)
    section_entries, gap_decisions = _build_section_plan(timeline, config, frame_rate, total_frames)
    prompt_entries = _build_prompt_plan(timeline, section_entries)
    media_entries = _build_media_plan(timeline, section_entries)
    audio_entries = _build_audio_plan(timeline, frame_rate)
    prompt_relay = _build_prompt_relay(timeline, config, section_entries, prompt_entries, total_frames, latent_chunk_count)
    visual_conditioning = _build_visual_conditioning(config, section_entries, prompt_entries, media_entries)
    bernini = build_bernini_plan(config, section_entries, media_entries, prompt_entries, prompt_relay.get("global_prompt"))
    prompt_relay = apply_bernini_prompt_prefix(prompt_relay, bernini)

    wan_validation = _validate_wan_inputs(
        timeline,
        config,
        director_validation,
        gap_decisions,
        visual_conditioning,
        media_entries,
        audio_entries,
        prompt_relay,
        frame_info,
        bernini,
    )
    validation = create_validation_result([
        *flatten_validation_result(director_validation),
        *flatten_validation_result(wan_validation),
    ])

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
            "requested_frame_count": requested_frames,
            "frame_count": total_frames,
            "duration_seconds": timeline["project"].get("duration_seconds"),
            "generation_duration_seconds": total_frames / frame_rate if frame_rate > 0 else None,
            "latent_chunk_count": latent_chunk_count,
            "frame_count_rule": frame_info["rule"],
        },
        "section_plan": section_entries,
        "prompt_plan": prompt_entries,
        "media_plan": media_entries,
        "audio_plan": audio_entries,
        "model_specific": {
            "wan": {
                "config": config,
                "rules": deepcopy(config["rules"]),
                "runtime_status": "Runtime backend selected by WAN Timeline Runtime",
                "prompt_relay": prompt_relay,
                "visual_conditioning": visual_conditioning,
                "bernini": bernini,
                "gap_decisions": gap_decisions,
            },
        },
        "validation": validation,
    }
    debug = _build_debug(timeline, config, plan, validation)
    return plan, validation, debug


def _build_section_plan(
    timeline: dict[str, Any],
    config: dict[str, Any],
    frame_rate: float,
    total_frames: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sections = []
    for section in sorted(timeline["director_track"]["sections"], key=lambda item: item.get("start_time", 0.0)):
        frame_range = time_range_to_frames(section["start_time"], section["end_time"], frame_rate)
        start_frame = min(max(0, frame_range["start_frame"]), total_frames)
        end_frame = min(max(start_frame, frame_range["end_frame_exclusive"]), total_frames)
        sections.append({
            "item_id": section.get("item_id"),
            "type": section.get("type"),
            "role": _section_role(section.get("type")),
            "start_time": section.get("start_time"),
            "end_time": section.get("end_time"),
            "start_frame": start_frame,
            "end_frame_exclusive": end_frame,
            "frame_count": max(0, end_frame - start_frame),
        })

    gap_decisions = []
    for index, gap in enumerate(detect_director_gaps(timeline)):
        frame_range = time_range_to_frames(gap["start_time"], gap["end_time"], frame_rate)
        start_frame = min(max(0, frame_range["start_frame"]), total_frames)
        end_frame = min(max(start_frame, frame_range["end_frame_exclusive"]), total_frames)
        decision = {
            "item_id": f"gap_{index + 1:03d}",
            "policy": config["gap_policy"],
            "start_time": gap["start_time"],
            "end_time": gap["end_time"],
            "start_frame": start_frame,
            "end_frame_exclusive": end_frame,
            "frame_count": max(0, end_frame - start_frame),
        }
        gap_decisions.append(decision)
        if config["gap_policy"] != "Merge With Previous Prompt":
            sections.append({
                "item_id": decision["item_id"],
                "type": "Gap",
                "role": "No Guidance",
                "start_time": gap["start_time"],
                "end_time": gap["end_time"],
                "start_frame": start_frame,
                "end_frame_exclusive": end_frame,
                "frame_count": decision["frame_count"],
            })

    return sorted(sections, key=lambda item: (item["start_frame"], item["end_frame_exclusive"], str(item["item_id"]))), gap_decisions


def _build_prompt_plan(timeline: dict[str, Any], section_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    global_prompt = timeline["project"].get("global_prompt", {})
    sections_by_id = {
        section.get("item_id"): section
        for section in timeline["director_track"]["sections"]
    }
    prompts = []
    for entry in section_entries:
        section = sections_by_id.get(entry["item_id"])
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
            ) if section else "",
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
        section_type = section.get("type")
        media.append({
            "item_id": entry["item_id"],
            "section_type": section_type,
            "asset_id": asset.get("asset_id"),
            "asset_type": asset.get("type"),
            "source_kind": asset.get("source_kind"),
            "path": asset.get("path"),
            "wan_role": "Visual Keyframe Candidate" if section_type == SECTION_TYPE_IMAGE else "Prompt Only Video Fallback",
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
                "wan_role": "Final Mix Only",
            })
    return audio_entries


def _build_prompt_relay(
    timeline: dict[str, Any],
    config: dict[str, Any],
    section_entries: list[dict[str, Any]],
    prompt_entries: list[dict[str, Any]],
    video_frame_count: int,
    latent_chunk_count: int,
) -> dict[str, Any]:
    prompts_by_id = {entry.get("item_id"): entry for entry in prompt_entries}
    project_global = timeline.get("project", {}).get("global_prompt", {})
    global_prompt = str(project_global.get("prompt") or "") if project_global.get("enabled") else ""
    segments = []
    for entry in section_entries:
        if int(entry.get("frame_count") or 0) <= 0:
            continue
        prompt_entry = prompts_by_id.get(entry.get("item_id"), {})
        effective_prompt = str(prompt_entry.get("effective_prompt") or "").strip()
        raw_prompt = str(prompt_entry.get("raw_prompt") or "").strip()
        if entry.get("type") == "Gap" or entry.get("role") == "No Guidance":
            prompt = ""
            guidance_type = "No Guidance"
        else:
            prompt = raw_prompt or effective_prompt
            guidance_type = "Prompt"
        segments.append({
            "item_id": entry.get("item_id"),
            "type": entry.get("type"),
            "guidance_type": guidance_type,
            "prompt": prompt,
            "effective_prompt": effective_prompt,
            "start_frame": int(entry.get("start_frame") or 0),
            "end_frame_exclusive": int(entry.get("end_frame_exclusive") or 0),
            "frame_count": int(entry.get("frame_count") or 0),
            "start_latent_chunk": _frame_to_latent(int(entry.get("start_frame") or 0), latent_chunk_count),
            "end_latent_chunk_exclusive": _frame_end_to_latent(
                int(entry.get("end_frame_exclusive") or 0),
                latent_chunk_count,
            ),
        })
    segment_lengths = _distribute_segment_lengths(segments, latent_chunk_count)
    local_prompts = []
    mapping = []
    for index, segment in enumerate(segments):
        start = sum(segment_lengths[:index])
        end = start + segment_lengths[index]
        segment = {**segment, "latent_segment_start": start, "latent_segment_end_exclusive": end}
        local_prompts.append(segment)
        mapping.append({
            "item_id": segment["item_id"],
            "start_frame": segment["start_frame"],
            "end_frame_exclusive": segment["end_frame_exclusive"],
            "start_latent_chunk": segment["start_latent_chunk"],
            "end_latent_chunk_exclusive": segment["end_latent_chunk_exclusive"],
            "segment_length": segment_lengths[index],
        })
    return {
        "enabled": config["prompt_routing"] == "Prompt Relay",
        "global_prompt": global_prompt,
        "local_prompts": local_prompts,
        "segment_lengths": segment_lengths,
        "epsilon": config["prompt_relay_epsilon"],
        "video_frame_count": video_frame_count,
        "latent_chunk_count": latent_chunk_count,
        "frame_to_latent_rule": "(frame - 1) // 4 + 1 latent chunk count; floor frame / 4 mapping",
        "section_to_latent_mapping": mapping,
    }


def _build_visual_conditioning(
    config: dict[str, Any],
    section_entries: list[dict[str, Any]],
    prompt_entries: list[dict[str, Any]],
    media_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    prompts_by_id = {entry.get("item_id"): entry for entry in prompt_entries}
    sections_by_id = {entry.get("item_id"): entry for entry in section_entries}
    image_media = [
        media for media in media_entries
        if media.get("section_type") == SECTION_TYPE_IMAGE and media.get("asset_id") and media.get("path")
    ]
    image_media.sort(key=lambda media: sections_by_id.get(media.get("item_id"), {}).get("start_frame", 0))
    requested = []
    for index, media in enumerate(image_media):
        section = sections_by_id.get(media.get("item_id"), {})
        prompt = prompts_by_id.get(media.get("item_id"), {})
        requested.append({
            "role": _keyframe_role(index, len(image_media)),
            "section_id": media.get("item_id"),
            "asset_id": media.get("asset_id"),
            "path": media.get("path"),
            "time": section.get("start_time"),
            "frame": int(section.get("start_frame") or 0),
            "latent_chunk": int(section.get("start_frame") or 0) // WAN_TEMPORAL_STRIDE,
            "guide_strength": float(media.get("guide_strength") if media.get("guide_strength") is not None else 1.0),
            "crop_mode": media.get("crop_mode"),
            "prompt": prompt.get("raw_prompt", ""),
            "effective_prompt": prompt.get("effective_prompt", ""),
        })
    return {
        "mode": config["visual_conditioning_mode"],
        "requested_keyframes": requested,
        "applied_keyframes": [],
        "unsupported_keyframes": [],
        "backend_capabilities": {
            "supports_start_image": None,
            "supports_end_image": None,
            "supports_timed_keyframes": None,
            "max_visual_keyframes": None,
            "supports_prompt_relay": None,
            "supports_video_sections": None,
            "supports_audio_conditioning": None,
        },
        "selection_policy": "Keep Start, keep End when supported, choose Timed keyframes evenly for remaining slots.",
    }


def _validate_wan_inputs(
    timeline: dict[str, Any],
    config: dict[str, Any],
    director_validation: dict[str, Any],
    gap_decisions: list[dict[str, Any]],
    visual_conditioning: dict[str, Any],
    media_entries: list[dict[str, Any]],
    audio_entries: list[dict[str, Any]],
    prompt_relay: dict[str, Any],
    frame_info: dict[str, Any],
    bernini: dict[str, Any],
) -> dict[str, Any]:
    entries = []
    if not director_validation.get("is_valid", False):
        entries.append(_entry(
            "WAN_DIRECTOR_TIMELINE_INVALID",
            SEVERITY_ERROR,
            "Timeline",
            None,
            "WAN planning requires a valid Director timeline.",
            "Fix Director validation errors before using the WAN planner output.",
        ))
    if config["visual_conditioning_mode"] == "Off" and visual_conditioning["requested_keyframes"]:
        entries.append(_entry(
            "WAN_VISUAL_KEYFRAMES_PLANNED",
            SEVERITY_WARNING,
            "Image Section",
            None,
            "Image Sections are present but visual conditioning is disabled.",
            "Switch Visual Conditioning Mode on if image keyframes should be used.",
        ))
    elif visual_conditioning["requested_keyframes"]:
        entries.append(_entry(
            "WAN_VISUAL_KEYFRAMES_PLANNED",
            SEVERITY_INFO,
            "Image Section",
            None,
            f"Planned {len(visual_conditioning['requested_keyframes'])} WAN visual keyframe candidate(s).",
            "Runtime backend capabilities decide which keyframes can be applied.",
        ))

    for decision in gap_decisions:
        if config["gap_policy"] == "Warning":
            entries.append(_entry(
                "WAN_GAP_HAS_NO_CONDITIONING",
                SEVERITY_WARNING,
                "Gap",
                decision["item_id"],
                "Timeline gap has no WAN conditioning.",
                "Fill the gap with a Text Section or change Gap Policy.",
                decision,
            ))
        elif config["gap_policy"] == "Merge With Previous Prompt":
            entries.append(_entry(
                "WAN_GAP_MERGED_WITH_PREVIOUS_PROMPT",
                SEVERITY_INFO,
                "Gap",
                decision["item_id"],
                "Timeline gap will be merged with the previous prompt segment.",
                "This preserves duration without creating a separate No Guidance segment.",
                decision,
            ))
        else:
            entries.append(_entry(
                "WAN_GAP_HAS_NO_CONDITIONING",
                SEVERITY_INFO,
                "Gap",
                decision["item_id"],
                "Timeline gap will be planned as an explicit No Guidance entry.",
                "Runtime support determines whether this becomes a mask or a prompt-only gap.",
                decision,
            ))

    for media in media_entries:
        if media.get("section_type") == SECTION_TYPE_VIDEO:
            if bernini.get("enabled"):
                entries.append(_entry(
                    "BERNINI_SOURCE_VIDEO_PLANNED",
                    SEVERITY_INFO,
                    "Video Section",
                    media.get("item_id"),
                    "Bernini Auto uses source-video conditioning for Video Sections.",
                    "Only the first usable Video Section is passed as Bernini source_video in this version.",
                ))
                continue
            prompt = _section_prompt(timeline, media.get("item_id"))
            if config["unsupported_video_section_policy"] == "Error":
                entries.append(_entry(
                    "WAN_UNSUPPORTED_VIDEO_SECTION",
                    SEVERITY_ERROR,
                    "Video Section",
                    media.get("item_id"),
                    "Video Sections are not supported by WAN Phase 13 runtime.",
                    "Remove the Video Section or change Unsupported Video Section Policy.",
                ))
            elif prompt:
                entries.append(_entry(
                    "WAN_VIDEO_SECTION_PROMPT_ONLY",
                    SEVERITY_WARNING,
                    "Video Section",
                    media.get("item_id"),
                    "Video Section media is unsupported and will be prompt-only for WAN Phase 13.",
                    "Use Image Sections for WAN visual keyframes.",
                ))
            else:
                entries.append(_entry(
                    "WAN_VIDEO_SECTION_NO_SUPPORTED_CONDITIONING",
                    SEVERITY_WARNING,
                    "Video Section",
                    media.get("item_id"),
                    "Video Section has no supported WAN conditioning and no prompt fallback.",
                    "Add a prompt or replace it with Image/Text Sections.",
                ))

    if bernini.get("enabled"):
        entries.append(_entry(
            "BERNINI_TASK_PROMPT_SELECTED",
            SEVERITY_INFO,
            "Bernini",
            None,
            f"Selected Bernini task prompt {bernini.get('task_type')}.",
            str(bernini.get("selection_reason") or ""),
            {
                "task_type": bernini.get("task_type"),
                "task_prompt_policy": bernini.get("task_prompt_policy"),
                "system_prompt": bernini.get("system_prompt"),
                "prompt_prefix_enabled": bernini.get("prompt_prefix_enabled"),
                "timeline_image_count": bernini.get("timeline_image_count"),
                "timeline_video_count": bernini.get("timeline_video_count"),
                "timeline_prompt_count": bernini.get("timeline_prompt_count"),
                "has_user_prompt_text": bernini.get("has_user_prompt_text"),
                "has_media_conditioning": bernini.get("has_media_conditioning"),
            },
        ))
        if not bernini.get("has_user_conditioning"):
            entries.append(_entry(
                "BERNINI_NO_USER_CONDITIONING",
                SEVERITY_WARNING,
                "Bernini",
                None,
                "Bernini has no user prompt text and no timeline media conditioning.",
                "Add a Text Section, Image Section, Video Section, or verify the Director timeline is connected and serialized.",
                {
                    "timeline_image_count": bernini.get("timeline_image_count"),
                    "timeline_video_count": bernini.get("timeline_video_count"),
                    "timeline_prompt_count": bernini.get("timeline_prompt_count"),
                },
            ))
        if bernini.get("ignored_timeline_media"):
            entries.append(_entry(
                "BERNINI_TIMELINE_MEDIA_DEFERRED",
                SEVERITY_WARNING,
                "Bernini",
                None,
                "Some timeline media is not passed to Bernini conditioning in this version.",
                "Reference-image tasks remain deferred until out-of-timeline reference support is added.",
                {"ignored_timeline_media": bernini.get("ignored_timeline_media")},
            ))

    if audio_entries:
        code = "WAN_AUDIO_FINAL_MIX_ONLY" if config["audio_policy"] == "Final Mix Only" else "WAN_AUDIO_IGNORED_BY_MODEL"
        entries.append(_entry(
            code,
            SEVERITY_INFO,
            "Audio",
            None,
            "WAN Phase 13 preserves audio clips as final-mix metadata only.",
            "Audio is not used as WAN generation conditioning in this phase.",
        ))

    if prompt_relay["enabled"] and sum(prompt_relay["segment_lengths"]) != prompt_relay["latent_chunk_count"]:
        entries.append(_entry(
            "WAN_PROMPT_RELAY_SEGMENT_LENGTH_MISMATCH",
            SEVERITY_ERROR,
            "Prompt Relay",
            None,
            "WAN Prompt Relay segment lengths do not sum to latent chunk count.",
            "This is an internal planning error.",
        ))
    elif prompt_relay["enabled"]:
        entries.append(_entry(
            "WAN_PROMPT_RELAY_SEGMENTS_BUILT",
            SEVERITY_INFO,
            "Prompt Relay",
            None,
            f"Built {len(prompt_relay['local_prompts'])} WAN Prompt Relay segment(s).",
            "Inspect DEBUG_INFO for frame and latent mapping.",
        ))

    entries.append(_entry(
        "WAN_RESOLUTION_RESOLVED",
        SEVERITY_INFO,
        "Output",
        None,
        "Resolved WAN output dimensions from Director project settings and WAN config.",
        "Use DEBUG_INFO to inspect width and height.",
    ))
    entries.append(_entry(
        "WAN_FRAME_COUNT_RESOLVED",
        SEVERITY_INFO,
        "Output",
        None,
        "Resolved WAN frame and latent chunk counts.",
        "Use DEBUG_INFO to inspect frame mapping.",
        frame_info,
    ))
    return create_validation_result(entries)


def _build_debug(timeline: dict[str, Any], config: dict[str, Any], plan: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    wan = plan["model_specific"]["wan"]
    prompt_relay = wan["prompt_relay"]
    visual = wan["visual_conditioning"]
    bernini = wan.get("bernini") or {}
    enabled = config["debug_mode"] != "Off"
    summary = {
        "model_mode": config["model_mode"],
        "prompt_routing": config["prompt_routing"],
        "visual_conditioning_mode": config["visual_conditioning_mode"],
        "runtime_backend_profile": config["runtime_backend_profile"],
        "bernini_task_type": bernini.get("task_type") if bernini.get("enabled") else None,
        "bernini_prompt_prefix_enabled": bool(bernini.get("prompt_prefix_enabled")),
        "width": plan["resolved_output"]["width"],
        "height": plan["resolved_output"]["height"],
        "requested_frame_count": plan["resolved_output"].get("requested_frame_count"),
        "video_frame_count": plan["resolved_output"]["frame_count"],
        "generation_duration_seconds": plan["resolved_output"].get("generation_duration_seconds"),
        "frame_count_rule": plan["resolved_output"].get("frame_count_rule"),
        "latent_chunk_count": prompt_relay["latent_chunk_count"],
        "section_count": len(timeline["director_track"]["sections"]),
        "planned_ranges": len(plan["section_plan"]),
        "prompt_relay_segments": len(prompt_relay["local_prompts"]),
        "segment_lengths": list(prompt_relay["segment_lengths"]),
        "requested_visual_keyframes": len(visual["requested_keyframes"]),
        "applied_visual_keyframes": len(visual["applied_keyframes"]),
        "unsupported_visual_keyframes": len(visual["unsupported_keyframes"]),
        "error_count": len(validation["errors"]),
        "warning_count": len(validation["warnings"]),
        "info_count": len(validation["info"]),
    }
    debug = {
        "type": "DEBUG_INFO",
        "source": "WAN Planner",
        "enabled": enabled,
        "mode": config["debug_mode"],
        "summary": summary,
    }
    if config["debug_mode"] == "Full":
        debug["details"] = {
            "section_plan": deepcopy(plan["section_plan"]),
            "prompt_plan": deepcopy(plan["prompt_plan"]),
            "visual_conditioning": deepcopy(visual),
            "prompt_relay": deepcopy(prompt_relay),
            "bernini": deepcopy(bernini),
            "media_plan": deepcopy(plan["media_plan"]),
            "audio_plan": deepcopy(plan["audio_plan"]),
            "gap_decisions": deepcopy(wan["gap_decisions"]),
            "validation": deepcopy(validation),
        }
    return debug


def _requested_frame_count(duration_seconds: float, frame_rate: float) -> int:
    return max(1, math.ceil(duration_seconds * frame_rate))


def _wan_frame_count(requested_frame_count: int) -> int:
    requested = max(1, int(requested_frame_count))
    return ((requested - 1 + WAN_TEMPORAL_STRIDE - 1) // WAN_TEMPORAL_STRIDE) * WAN_TEMPORAL_STRIDE + 1


def _wan_frame_info(requested_frame_count: int, resolved_frame_count: int, frame_rate: float) -> dict[str, Any]:
    return {
        "requested_frame_count": requested_frame_count,
        "resolved_frame_count": resolved_frame_count,
        "added_padding_frames": max(0, resolved_frame_count - requested_frame_count),
        "frame_rate": frame_rate,
        "generation_duration_seconds": resolved_frame_count / frame_rate if frame_rate > 0 else None,
        "temporal_stride": WAN_TEMPORAL_STRIDE,
        "rule": "WAN video length is rounded up to 4n+1 frames.",
    }


def _latent_chunk_count(video_frame_count: int) -> int:
    return ((max(1, int(video_frame_count)) - 1) // WAN_TEMPORAL_STRIDE) + 1


def _frame_to_latent(frame: int, latent_chunk_count: int) -> int:
    return min(max(0, int(frame) // WAN_TEMPORAL_STRIDE), max(0, latent_chunk_count - 1))


def _frame_end_to_latent(frame: int, latent_chunk_count: int) -> int:
    if frame <= 0:
        return 0
    return min(latent_chunk_count, ((int(frame) - 1) // WAN_TEMPORAL_STRIDE) + 1)


def _section_role(section_type: str | None) -> str:
    if section_type == SECTION_TYPE_TEXT:
        return "Prompt Relay"
    if section_type == SECTION_TYPE_IMAGE:
        return "Visual Keyframe Candidate"
    if section_type == SECTION_TYPE_VIDEO:
        return "Prompt Only Video Fallback"
    return "No Guidance"


def _distribute_segment_lengths(segments: list[dict[str, Any]], latent_chunk_count: int) -> list[int]:
    if not segments:
        return []
    total_frames = sum(max(0, int(segment.get("frame_count") or 0)) for segment in segments)
    if total_frames <= 0:
        return [0 for _ in segments]
    exact = [
        max(0, int(segment.get("frame_count") or 0)) * latent_chunk_count / total_frames
        for segment in segments
    ]
    lengths = [int(value) for value in exact]
    diff = latent_chunk_count - sum(lengths)
    if diff > 0:
        order = sorted(range(len(exact)), key=lambda index: (-(exact[index] - int(exact[index])), index))
        for index in range(diff):
            lengths[order[index % len(order)]] += 1
    elif diff < 0:
        order = sorted(range(len(lengths)), key=lambda index: (-lengths[index], index))
        for index in range(abs(diff)):
            target = order[index % len(order)]
            if lengths[target] > 0:
                lengths[target] -= 1
    return lengths


def _keyframe_role(index: int, count: int) -> str:
    if index == 0:
        return "Start"
    if index == count - 1:
        return "End"
    return "Timed"


def _section_prompt(timeline: dict[str, Any], item_id: Any) -> str:
    for section in timeline["director_track"]["sections"]:
        if section.get("item_id") == item_id:
            return str(section.get("prompt") or "").strip()
    return ""


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


def _entry(
    code: str,
    severity: str,
    item_type: str,
    item_id: Any,
    message: str,
    suggestion: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return create_validation_entry(
        code,
        severity,
        "WAN Planner",
        item_type,
        item_id,
        message,
        suggestion,
        details,
    )
