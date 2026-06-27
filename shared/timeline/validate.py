from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from ..contracts.validation import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    create_validation_entry,
    create_validation_result,
)
from ..contracts.video_timeline import (
    ASSET_SOURCE_KINDS,
    ASSET_TYPES,
    BOUNDARY_MODE_BLEND_SEAM,
    BOUNDARY_MODE_CONTINUOUS_SHOT,
    BOUNDARY_MODE_HARD_CUT,
    BOUNDARY_MODES,
    LORA_MERGE_MODE_ADD_TO_GLOBAL,
    LORA_MERGE_MODE_DISABLE_LORAS,
    LORA_MERGE_MODE_INHERIT_GLOBAL,
    LORA_MERGE_MODE_REPLACE_GLOBAL,
    LORA_MERGE_MODES,
    MODEL_LORA_MODEL_LTX_2_3,
    MODEL_LORA_MODEL_WAN_2_2,
    MODEL_LORA_TARGET_HIGH_NOISE,
    MODEL_LORA_TARGET_LOW_NOISE,
    MODEL_LORA_TARGET_MAIN,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_TEXT,
    SECTION_TYPE_VIDEO,
    SHOT_TYPE_EDITED,
    SHOT_TYPE_EXTENDED,
    SHOT_TYPE_GENERATED,
    SHOT_TYPE_IMPORTED,
    TAKE_STATUSES,
)
from .gaps import detect_director_gaps
from .global_settings import load_global_settings, normalize_global_settings
from .migration import migrate_video_timeline
from .normalize import normalize_video_timeline
from .references import (
    REFERENCE_KIND_CHARACTER,
    are_character_references_enabled,
    get_character_references,
    parse_reference_tags,
)

VALID_MODEL_LORA_TARGETS = {
    MODEL_LORA_MODEL_LTX_2_3: {MODEL_LORA_TARGET_MAIN},
    MODEL_LORA_MODEL_WAN_2_2: {
        MODEL_LORA_TARGET_HIGH_NOISE,
        MODEL_LORA_TARGET_LOW_NOISE,
    },
}

RESOLVED_LORA_TARGETS_BY_FAMILY = {
    "ltx": {MODEL_LORA_TARGET_MAIN},
    "ltx_2_3": {MODEL_LORA_TARGET_MAIN},
    "wan": {MODEL_LORA_TARGET_HIGH_NOISE, MODEL_LORA_TARGET_LOW_NOISE},
    "wan_2_2": {MODEL_LORA_TARGET_HIGH_NOISE, MODEL_LORA_TARGET_LOW_NOISE},
}


def validate_video_timeline(timeline: Any, global_settings: Any | None = None) -> dict:
    migrated = migrate_video_timeline(timeline)
    normalized = normalize_video_timeline(migrated)
    settings = normalize_global_settings(
        global_settings if global_settings is not None else load_global_settings()
    )
    entries: list[dict[str, Any]] = []
    duration = _as_float(normalized["project"].get("duration_seconds"))
    assets = normalized.get("assets", [])
    assets_by_id = {asset.get("asset_id"): asset for asset in assets}
    sections = normalized["director_track"]["sections"]
    raw_sequence = _raw_dict(migrated.get("sequence"))
    raw_model_loras = _raw_dict(_raw_dict(migrated.get("project")).get("model_loras"))

    entries.extend(_validate_assets(assets))
    references = get_character_references(normalized)
    entries.extend(_validate_character_references(references))
    entries.extend(
        _validate_director_sections(
            sections,
            duration,
            assets_by_id,
            settings["timeline"]["minimum_section_duration_seconds"],
        )
    )
    entries.extend(
        _validate_project_model_loras(
            normalized["project"].get("model_loras", {}),
            raw_model_loras,
        )
    )
    entries.extend(
        _validate_sequence(
            normalized.get("sequence", {}),
            raw_sequence,
            duration,
            assets_by_id,
            sections,
            normalized["project"].get("model_loras", {}),
        )
    )
    entries.extend(
        _validate_prompt_reference_tags(
            sections,
            references,
            are_character_references_enabled(normalized),
        )
    )
    entries.extend(_gap_entries(normalized, settings))
    entries.extend(_validate_audio_tracks(normalized.get("audio_tracks", []), duration, assets_by_id))

    return create_validation_result(entries)


def _validate_assets(assets: list[dict]) -> list[dict]:
    entries = []
    seen = set()
    for asset in assets:
        asset_id = asset.get("asset_id")
        if asset_id in seen:
            entries.append(
                create_validation_entry(
                    "ASSET_DUPLICATE_ID",
                    SEVERITY_ERROR,
                    "Director",
                    "Asset",
                    asset_id,
                    "Asset IDs must be unique.",
                    "Replace or remove the duplicate asset record.",
                )
            )
        seen.add(asset_id)
        if asset.get("type") not in ASSET_TYPES:
            entries.append(
                create_validation_entry(
                    "ASSET_UNSUPPORTED_TYPE",
                    SEVERITY_ERROR,
                    "Director",
                    "Asset",
                    asset_id,
                    "Asset type is not supported.",
                    "Use Image, Video, or Audio.",
                    {"type": asset.get("type")},
                )
            )
        if asset.get("source_kind") not in ASSET_SOURCE_KINDS:
            entries.append(
                create_validation_entry(
                    "ASSET_UNSUPPORTED_SOURCE_KIND",
                    SEVERITY_ERROR,
                    "Director",
                    "Asset",
                    asset_id,
                    "Asset source kind is not supported.",
                    "Use FilePath, UploadedFile, Generated, or ComfyUIInput.",
                    {"source_kind": asset.get("source_kind")},
                )
            )
        if _contains_embedded_media(asset):
            entries.append(
                create_validation_entry(
                    "ASSET_EMBEDDED_MEDIA_NOT_ALLOWED",
                    SEVERITY_ERROR,
                    "Director",
                    "Asset",
                    asset_id,
                    "Assets must not embed media, thumbnails, or waveform data in workflow JSON.",
                    "Store only a file/source reference and regenerate previews from cache.",
                )
            )
    return entries


def _validate_character_references(references: list[dict]) -> list[dict]:
    entries = []
    seen_labels = set()
    for reference in references:
        item_id = reference.get("id") or reference.get("label") or "reference"
        label = reference.get("label")
        if label in seen_labels:
            entries.append(
                create_validation_entry(
                    "CHARACTER_REFERENCE_DUPLICATE_LABEL",
                    SEVERITY_ERROR,
                    "Director",
                    "CharacterReference",
                    item_id,
                    "Character reference labels must be unique.",
                    "Rename or remove the duplicate reference so prompt tags are unambiguous.",
                    {"label": label},
                )
            )
        seen_labels.add(label)
        if _contains_embedded_media(reference) or _contains_embedded_media(reference.get("image")):
            entries.append(
                create_validation_entry(
                    "CHARACTER_REFERENCE_EMBEDDED_MEDIA_NOT_ALLOWED",
                    SEVERITY_ERROR,
                    "Director",
                    "CharacterReference",
                    item_id,
                    "Character references must not embed media, thumbnails, or waveform data in workflow JSON.",
                    "Store only a file/source reference and regenerate previews from cache.",
                )
            )
        if reference.get("enabled") is not False and not _has_media_reference(reference.get("image")):
            entries.append(
                create_validation_entry(
                    "CHARACTER_REFERENCE_MISSING_IMAGE",
                    SEVERITY_ERROR,
                    "Director",
                    "CharacterReference",
                    item_id,
                    "Enabled character reference requires an image.",
                    "Choose an image, disable the reference, or remove it.",
                    {"label": label},
                )
            )
    return entries


def _validate_prompt_reference_tags(
    sections: list[dict],
    references: list[dict],
    references_enabled: bool,
) -> list[dict]:
    entries = []
    references_by_label = {
        reference.get("label"): reference
        for reference in references
    }
    seen_warnings = set()
    for section in sections:
        for tag in parse_reference_tags(section.get("prompt")):
            if tag.get("kind") != REFERENCE_KIND_CHARACTER:
                continue
            reference = references_by_label.get(tag["label"])
            key = (section.get("item_id"), tag["token"])
            if key in seen_warnings:
                continue
            seen_warnings.add(key)
            if not references_enabled:
                entries.append(
                    create_validation_entry(
                        "PROMPT_REFERENCE_DISABLED",
                        SEVERITY_WARNING,
                        "Director",
                        "Section",
                        section.get("item_id"),
                        "Prompt references are currently disabled.",
                        "Turn on character references or remove the tag.",
                        {
                            "token": tag["token"],
                            "label": tag["label"],
                            "global_disabled": True,
                        },
                    )
                )
            elif reference is None:
                entries.append(
                    create_validation_entry(
                        "PROMPT_REFERENCE_UNKNOWN",
                        SEVERITY_WARNING,
                        "Director",
                        "Section",
                        section.get("item_id"),
                        "Prompt references a missing character reference.",
                        "Add the referenced character image or remove the tag.",
                        {"token": tag["token"], "label": tag["label"]},
                    )
                )
            elif reference.get("enabled") is False:
                entries.append(
                    create_validation_entry(
                        "PROMPT_REFERENCE_DISABLED",
                        SEVERITY_WARNING,
                        "Director",
                        "Section",
                        section.get("item_id"),
                        "Prompt references a disabled character reference.",
                        "Enable the reference or remove the tag.",
                        {"token": tag["token"], "label": tag["label"]},
                    )
                )
    return entries


def _validate_director_sections(
    sections: list[dict],
    duration: float | None,
    assets_by_id: dict,
    minimum_section_duration_seconds: float,
) -> list[dict]:
    entries = []
    sorted_sections = sorted(
        sections,
        key=lambda section: (
            _as_float(section.get("start_time")) or 0.0,
            _as_float(section.get("end_time")) or 0.0,
        ),
    )
    previous_end: float | None = None
    previous_id: str | None = None

    for section in sorted_sections:
        item_id = section.get("item_id")
        section_type = section.get("type")
        start = _as_float(section.get("start_time"))
        end = _as_float(section.get("end_time"))

        if start is None or end is None or end <= start:
            entries.append(
                create_validation_entry(
                    "SECTION_INVALID_TIME_RANGE",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Section requires a valid start_time and end_time.",
                    "Set end_time greater than start_time.",
                    {"start_time": section.get("start_time"), "end_time": section.get("end_time")},
                )
            )
        elif duration is not None and (start < 0 or end > duration):
            entries.append(
                create_validation_entry(
                    "SECTION_OUTSIDE_PROJECT_DURATION",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Section must stay within Project Duration.",
                    "Move or trim the section inside the project boundary.",
                    {"duration_seconds": duration, "start_time": start, "end_time": end},
                )
            )
        elif end - start < minimum_section_duration_seconds:
            entries.append(
                create_validation_entry(
                    "SECTION_BELOW_MINIMUM_DURATION",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Section is shorter than the global minimum duration.",
                    "Extend the section or lower Minimum Section Duration in Global Settings.",
                    {
                        "minimum_section_duration_seconds": minimum_section_duration_seconds,
                        "duration_seconds": end - start,
                    },
                )
            )

        if previous_end is not None and start is not None and start < previous_end:
            entries.append(
                create_validation_entry(
                    "DIRECTOR_SECTION_OVERLAP",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Director Track sections cannot overlap.",
                    "Move or trim one section so the Director Track is sequential.",
                    {"previous_item_id": previous_id},
                )
            )
        if end is not None and (previous_end is None or end > previous_end):
            previous_end = end
            previous_id = item_id

        if section_type == SECTION_TYPE_TEXT and not str(section.get("prompt", "")).strip():
            entries.append(
                create_validation_entry(
                    "TEXT_SECTION_EMPTY_PROMPT",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Text Section requires a non-empty prompt.",
                    "Add a prompt or remove the Text Section.",
                )
            )
        elif section_type == SECTION_TYPE_IMAGE and not _has_media_reference(section.get("image")):
            entries.append(
                create_validation_entry(
                    "IMAGE_SECTION_MISSING_IMAGE",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Image Section requires an image.",
                    "Choose an image or remove the Image Section.",
                )
            )
        elif section_type == SECTION_TYPE_IMAGE:
            entries.extend(
                _validate_media_reference(
                    section.get("image"),
                    assets_by_id,
                    "Section",
                    item_id,
                    "IMAGE_SECTION_MEDIA",
                )
            )
        elif section_type == SECTION_TYPE_VIDEO and not _has_media_reference(section.get("video")):
            entries.append(
                create_validation_entry(
                    "VIDEO_SECTION_MISSING_VIDEO",
                    SEVERITY_ERROR,
                    "Director",
                    "Section",
                    item_id,
                    "Video Section requires a video.",
                    "Choose a video or remove the Video Section.",
                )
            )
        elif section_type == SECTION_TYPE_VIDEO:
            entries.extend(
                _validate_media_reference(
                    section.get("video"),
                    assets_by_id,
                    "Section",
                    item_id,
                    "VIDEO_SECTION_MEDIA",
                )
            )

    return entries


def _validate_project_model_loras(model_loras: dict, raw_model_loras: dict) -> list[dict]:
    entries: list[dict[str, Any]] = []
    raw_global = _raw_dict(raw_model_loras.get("global"))
    for model_key, targets in raw_global.items():
        if model_key not in VALID_MODEL_LORA_TARGETS:
            entries.append(
                create_validation_entry(
                    "MODEL_LORA_MODEL_TARGET_INVALID",
                    SEVERITY_ERROR,
                    "Director",
                    "ProjectLoRA",
                    str(model_key),
                    "Project LoRA model target is not supported.",
                    "Use the model-targeted LoRA keys defined by the timeline schema.",
                    {"model": model_key},
                )
            )
            continue
        if not isinstance(targets, dict):
            entries.append(
                create_validation_entry(
                    "MODEL_LORA_TARGETS_INVALID",
                    SEVERITY_ERROR,
                    "Director",
                    "ProjectLoRA",
                    str(model_key),
                    "Project LoRA targets must be an object.",
                    "Use named target stacks for this model.",
                    {"model": model_key},
                )
            )
            continue
        for target_key, stack in targets.items():
            if target_key not in VALID_MODEL_LORA_TARGETS[model_key]:
                entries.append(
                    create_validation_entry(
                        "MODEL_LORA_TARGET_INVALID",
                        SEVERITY_ERROR,
                        "Director",
                        "ProjectLoRA",
                        f"{model_key}.{target_key}",
                        "Project LoRA target is not supported for this model.",
                        "Use only the target names defined by the timeline schema.",
                        {"model": model_key, "target": target_key},
                    )
                )
            entries.extend(
                _validate_lora_payload(
                    stack,
                    "ProjectLoRA",
                    f"{model_key}.{target_key}",
                    "MODEL_LORA_STACK",
                )
            )

    global_loras = _raw_dict(model_loras.get("global"))
    for model_key, targets in global_loras.items():
        for target_key, stack in _raw_dict(targets).items():
            entries.extend(
                _validate_lora_payload(
                    stack,
                    "ProjectLoRA",
                    f"{model_key}.{target_key}",
                    "MODEL_LORA_STACK",
                )
            )
    return entries


def _validate_sequence(
    sequence: dict,
    raw_sequence: dict,
    duration: float | None,
    assets_by_id: dict,
    sections: list[dict],
    model_loras: dict,
) -> list[dict]:
    entries: list[dict[str, Any]] = []
    shots = sequence.get("shots", [])
    boundaries = sequence.get("boundaries", [])
    sections_by_id = {section.get("item_id"): section for section in sections}
    raw_shots = [
        shot
        for shot in raw_sequence.get("shots", [])
        if isinstance(shot, dict)
    ] if isinstance(raw_sequence.get("shots"), list) else []
    raw_boundaries = [
        boundary
        for boundary in raw_sequence.get("boundaries", [])
        if isinstance(boundary, dict)
    ] if isinstance(raw_sequence.get("boundaries"), list) else []

    entries.extend(_validate_raw_sequence_modes(raw_shots, raw_boundaries))
    entries.extend(_validate_shots(shots, duration, assets_by_id, sections_by_id))
    entries.extend(_validate_boundaries(boundaries, shots, model_loras))
    return entries


def _validate_raw_sequence_modes(raw_shots: list[dict], raw_boundaries: list[dict]) -> list[dict]:
    entries: list[dict[str, Any]] = []
    for index, shot in enumerate(raw_shots):
        shot_id = str(shot.get("shot_id") or f"shot_{index + 1:03d}")
        overrides = shot.get("lora_overrides")
        if isinstance(overrides, dict):
            merge_mode = overrides.get("merge_mode")
            if merge_mode is not None and merge_mode not in LORA_MERGE_MODES:
                entries.append(
                    create_validation_entry(
                        "SHOT_LORA_MERGE_MODE_INVALID",
                        SEVERITY_ERROR,
                        "Director",
                        "Shot",
                        shot_id,
                        "Shot LoRA merge mode is not supported.",
                        "Use Inherit Global, Add To Global, Replace Global, or Disable LoRAs.",
                        {"merge_mode": merge_mode},
                    )
                )
            entries.extend(
                _validate_lora_target_tree(
                    overrides.get("targets"),
                    "Shot",
                    shot_id,
                    "SHOT_LORA",
                )
            )
        for take in (
            shot.get("takes", []) if isinstance(shot.get("takes"), list) else []
        ):
            if not isinstance(take, dict):
                continue
            take_id = str(take.get("take_id") or "take")
            status = take.get("status")
            if status is not None and status not in TAKE_STATUSES:
                entries.append(
                    create_validation_entry(
                        "TAKE_STATUS_INVALID",
                        SEVERITY_ERROR,
                        "Director",
                        "Take",
                        take_id,
                        "Take status is not supported.",
                        "Use Candidate, Accepted, or Rejected.",
                        {"status": status, "shot_id": shot_id},
                    )
                )
    for index, boundary in enumerate(raw_boundaries):
        boundary_id = str(boundary.get("boundary_id") or f"boundary_{index + 1:03d}")
        mode = boundary.get("mode")
        if mode is not None and mode not in BOUNDARY_MODES:
            entries.append(
                create_validation_entry(
                    "BOUNDARY_MODE_INVALID",
                    SEVERITY_ERROR,
                    "Director",
                    "Boundary",
                    boundary_id,
                    "Boundary mode is not supported.",
                    "Use one of the generic boundary modes defined by the timeline schema.",
                    {"mode": mode},
                )
            )
    return entries


def _validate_lora_target_tree(targets: Any, scope: str, item_id: str, code_prefix: str) -> list[dict]:
    entries: list[dict[str, Any]] = []
    if targets is None:
        return entries
    if not isinstance(targets, dict):
        return [
            create_validation_entry(
                f"{code_prefix}_TARGETS_INVALID",
                SEVERITY_ERROR,
                "Director",
                scope,
                item_id,
                "LoRA targets must be an object.",
                "Use model keys with named target stacks.",
            )
        ]
    for model_key, model_targets in targets.items():
        if model_key not in VALID_MODEL_LORA_TARGETS:
            entries.append(
                create_validation_entry(
                    f"{code_prefix}_MODEL_TARGET_INVALID",
                    SEVERITY_ERROR,
                    "Director",
                    scope,
                    item_id,
                    "LoRA model target is not supported.",
                    "Use the model-targeted LoRA keys defined by the timeline schema.",
                    {"model": model_key},
                )
            )
            continue
        if not isinstance(model_targets, dict):
            entries.append(
                create_validation_entry(
                    f"{code_prefix}_TARGETS_INVALID",
                    SEVERITY_ERROR,
                    "Director",
                    scope,
                    item_id,
                    "LoRA model targets must be an object.",
                    "Use named target stacks for this model.",
                    {"model": model_key},
                )
            )
            continue
        for target_key, stack in model_targets.items():
            if target_key not in VALID_MODEL_LORA_TARGETS[model_key]:
                entries.append(
                    create_validation_entry(
                        f"{code_prefix}_TARGET_INVALID",
                        SEVERITY_ERROR,
                        "Director",
                        scope,
                        item_id,
                        "LoRA target is not supported for this model.",
                        "Use only the target names defined by the timeline schema.",
                        {"model": model_key, "target": target_key},
                    )
                )
            entries.extend(_validate_lora_payload(stack, scope, item_id, f"{code_prefix}_STACK"))
    return entries


def _validate_shots(
    shots: list[dict],
    duration: float | None,
    assets_by_id: dict,
    sections_by_id: dict,
) -> list[dict]:
    entries: list[dict[str, Any]] = []
    seen_shot_ids: set[str] = set()
    assigned_section_ids: dict[str, str] = {}
    sorted_shots = sorted(
        shots,
        key=lambda shot: (
            _as_float(shot.get("start_time")) or 0.0,
            _as_float(shot.get("end_time")) or 0.0,
        ),
    )
    previous_end: float | None = None
    previous_id: str | None = None

    for index, shot in enumerate(sorted_shots):
        shot_id = str(shot.get("shot_id") or f"shot_{index + 1:03d}")
        start = _as_float(shot.get("start_time"))
        end = _as_float(shot.get("end_time"))
        if shot_id in seen_shot_ids:
            entries.append(
                create_validation_entry(
                    "SHOT_DUPLICATE_ID",
                    SEVERITY_ERROR,
                    "Director",
                    "Shot",
                    shot_id,
                    "Shot IDs must be unique.",
                    "Rename or remove the duplicate shot.",
                )
            )
        seen_shot_ids.add(shot_id)

        if start is None or end is None or end <= start:
            entries.append(
                create_validation_entry(
                    "SHOT_INVALID_TIME_RANGE",
                    SEVERITY_ERROR,
                    "Director",
                    "Shot",
                    shot_id,
                    "Shot requires a valid start_time and end_time.",
                    "Set end_time greater than start_time.",
                    {"start_time": shot.get("start_time"), "end_time": shot.get("end_time")},
                )
            )
        elif duration is not None and (start < 0 or end > duration):
            entries.append(
                create_validation_entry(
                    "SHOT_OUTSIDE_PROJECT_DURATION",
                    SEVERITY_ERROR,
                    "Director",
                    "Shot",
                    shot_id,
                    "Shot must stay within Project Duration.",
                    "Move or trim the shot inside the project boundary.",
                    {"duration_seconds": duration, "start_time": start, "end_time": end},
                )
            )

        if previous_end is not None and start is not None and start < previous_end:
            entries.append(
                create_validation_entry(
                    "SHOT_OVERLAP",
                    SEVERITY_ERROR,
                    "Director",
                    "Shot",
                    shot_id,
                    "Shots cannot overlap.",
                    "Move or trim one shot so the sequence is sequential.",
                    {"previous_shot_id": previous_id},
                )
            )
        if end is not None and (previous_end is None or end > previous_end):
            previous_end = end
            previous_id = shot_id

        for section_id in shot.get("section_ids", []):
            if section_id not in sections_by_id:
                entries.append(
                    create_validation_entry(
                        "SHOT_SECTION_NOT_FOUND",
                        SEVERITY_ERROR,
                        "Director",
                        "Shot",
                        shot_id,
                        "Shot references a missing Director section.",
                        "Assign the shot to an existing section or remove the stale section ID.",
                        {"section_id": section_id},
                    )
                )
            elif section_id in assigned_section_ids and assigned_section_ids[section_id] != shot_id:
                entries.append(
                    create_validation_entry(
                        "SECTION_ASSIGNED_TO_MULTIPLE_SHOTS",
                        SEVERITY_ERROR,
                        "Director",
                        "Shot",
                        shot_id,
                        "Director section is assigned to more than one shot.",
                        "Keep each section attached to one shot until multi-shot section semantics are defined.",
                        {"section_id": section_id, "previous_shot_id": assigned_section_ids[section_id]},
                    )
                )
            else:
                assigned_section_ids[section_id] = shot_id

        entries.extend(_validate_shot_media_and_takes(shot, assets_by_id))
    return entries


def _validate_shot_media_and_takes(shot: dict, assets_by_id: dict) -> list[dict]:
    entries: list[dict[str, Any]] = []
    shot_id = str(shot.get("shot_id") or "shot")
    shot_type = shot.get("type")
    clip_instance = shot.get("clip_instance")
    if clip_instance and clip_instance.get("asset_id") and clip_instance.get("asset_id") not in assets_by_id:
        entries.append(
            create_validation_entry(
                "SHOT_CLIP_INSTANCE_ASSET_NOT_FOUND",
                SEVERITY_ERROR,
                "Director",
                "Shot",
                shot_id,
                "Shot clip instance points to a missing asset record.",
                "Choose the clip again or remove the stale asset reference.",
                {"asset_id": clip_instance.get("asset_id")},
            )
        )
    if shot_type == SHOT_TYPE_IMPORTED and not (
        isinstance(clip_instance, dict) and clip_instance.get("asset_id")
    ):
        entries.append(
            create_validation_entry(
                "IMPORTED_SHOT_MISSING_CLIP_ASSET",
                SEVERITY_WARNING,
                "Director",
                "Shot",
                shot_id,
                "Imported shot does not point to a clip asset.",
                "Attach an imported clip before using this shot as media.",
            )
        )
    if shot_type in {SHOT_TYPE_GENERATED, SHOT_TYPE_EXTENDED, SHOT_TYPE_EDITED} and not shot.get("section_ids"):
        entries.append(
            create_validation_entry(
                "SHOT_MISSING_INTENT",
                SEVERITY_WARNING,
                "Director",
                "Shot",
                shot_id,
                "Generated or edited shot has no section intent.",
                "Assign at least one Director section or keep the shot as a placeholder/import.",
            )
        )

    take_ids: set[str] = set()
    accepted_take_id = shot.get("accepted_take_id")
    for take in shot.get("takes", []):
        take_id = str(take.get("take_id") or "take")
        if take_id in take_ids:
            entries.append(
                create_validation_entry(
                    "TAKE_DUPLICATE_ID",
                    SEVERITY_ERROR,
                    "Director",
                    "Take",
                    take_id,
                    "Take IDs must be unique within a shot.",
                    "Rename or remove the duplicate take.",
                    {"shot_id": shot_id},
                )
            )
        take_ids.add(take_id)
        asset_id = take.get("asset_id")
        if asset_id is not None and asset_id not in assets_by_id:
            entries.append(
                create_validation_entry(
                    "TAKE_ASSET_NOT_FOUND",
                    SEVERITY_ERROR,
                    "Director",
                    "Take",
                    take_id,
                    "Take points to a missing asset record.",
                    "Choose the output asset again or remove the stale take asset reference.",
                    {"shot_id": shot_id, "asset_id": asset_id},
                )
            )
        entries.extend(_validate_take_resolved_loras(take, shot_id))
    if accepted_take_id is not None and accepted_take_id not in take_ids:
        entries.append(
            create_validation_entry(
                "SHOT_ACCEPTED_TAKE_NOT_FOUND",
                SEVERITY_ERROR,
                "Director",
                "Shot",
                shot_id,
                "Shot accepted_take_id does not match one of its takes.",
                "Accept an existing take or clear the accepted take.",
                {"accepted_take_id": accepted_take_id},
            )
        )
    return entries


def _validate_take_resolved_loras(take: dict, shot_id: str) -> list[dict]:
    entries: list[dict[str, Any]] = []
    resolved = take.get("resolved_loras")
    if resolved is None:
        return entries
    take_id = str(take.get("take_id") or "take")
    if not isinstance(resolved, dict):
        return [
            create_validation_entry(
                "TAKE_RESOLVED_LORAS_INVALID",
                SEVERITY_ERROR,
                "Director",
                "Take",
                take_id,
                "Take resolved_loras must be an object when present.",
                "Store the resolved model family, version, and target LoRA rows.",
                {"shot_id": shot_id},
            )
        ]
    if _contains_embedded_media(resolved):
        entries.append(
            create_validation_entry(
                "TAKE_RESOLVED_LORAS_EMBEDDED_MEDIA_NOT_ALLOWED",
                SEVERITY_ERROR,
                "Director",
                "Take",
                take_id,
                "Take resolved_loras must not embed media or preview payloads.",
                "Store only LoRA names and numeric strengths.",
                {"shot_id": shot_id},
            )
        )
    take_family = str(take.get("model_family") or "").strip()
    resolved_family = str(resolved.get("model_family") or "").strip()
    take_version = str(take.get("model_version") or "").strip()
    resolved_version = str(resolved.get("model_version") or "").strip()
    if take_family and resolved_family and take_family.lower() != resolved_family.lower():
        entries.append(
            create_validation_entry(
                "TAKE_RESOLVED_LORAS_MODEL_MISMATCH",
                SEVERITY_ERROR,
                "Director",
                "Take",
                take_id,
                "Take resolved_loras model family does not match the take metadata.",
                "Update the snapshot family or regenerate the take metadata.",
                {"shot_id": shot_id, "take_model_family": take_family, "resolved_model_family": resolved_family},
            )
        )
    if take_version and resolved_version and take_version != resolved_version:
        entries.append(
            create_validation_entry(
                "TAKE_RESOLVED_LORAS_MODEL_VERSION_MISMATCH",
                SEVERITY_ERROR,
                "Director",
                "Take",
                take_id,
                "Take resolved_loras model version does not match the take metadata.",
                "Update the snapshot version or regenerate the take metadata.",
                {"shot_id": shot_id, "take_model_version": take_version, "resolved_model_version": resolved_version},
            )
        )
    targets = resolved.get("targets")
    if not isinstance(targets, dict):
        entries.append(
            create_validation_entry(
                "TAKE_RESOLVED_LORAS_TARGETS_INVALID",
                SEVERITY_ERROR,
                "Director",
                "Take",
                take_id,
                "Take resolved_loras targets must be an object.",
                "Store each runtime target as a list of resolved LoRA rows.",
                {"shot_id": shot_id},
            )
        )
        return entries
    allowed_targets = _resolved_lora_targets_for_take(take_family or resolved_family)
    for target_key, rows in targets.items():
        if allowed_targets is not None and target_key not in allowed_targets:
            entries.append(
                create_validation_entry(
                    "TAKE_RESOLVED_LORAS_TARGET_INVALID",
                    SEVERITY_ERROR,
                    "Director",
                    "Take",
                    take_id,
                    "Take resolved_loras target does not match the take model.",
                    "Use target keys appropriate for the take model family.",
                    {"shot_id": shot_id, "target": target_key, "model_family": take_family or resolved_family},
                )
            )
        if not isinstance(rows, list):
            entries.append(
                create_validation_entry(
                    "TAKE_RESOLVED_LORAS_TARGET_ROWS_INVALID",
                    SEVERITY_ERROR,
                    "Director",
                    "Take",
                    take_id,
                    "Take resolved_loras target must be a list.",
                    "Store resolved LoRA rows as an array.",
                    {"shot_id": shot_id, "target": target_key},
                )
            )
            continue
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict) or not row.get("name"):
                entries.append(
                    create_validation_entry(
                        "TAKE_RESOLVED_LORAS_ROW_INVALID",
                        SEVERITY_ERROR,
                        "Director",
                        "Take",
                        take_id,
                        "Take resolved_loras row requires a LoRA name.",
                        "Store each resolved LoRA row with a name and strengths.",
                        {"shot_id": shot_id, "target": target_key, "row_index": row_index},
                    )
                )
    return entries


def _resolved_lora_targets_for_take(model_family: str) -> set[str] | None:
    key = model_family.strip().lower().replace(" ", "_").replace(".", "_")
    if not key:
        return None
    for family_key, targets in RESOLVED_LORA_TARGETS_BY_FAMILY.items():
        if key == family_key or key.startswith(f"{family_key}_"):
            return targets
    return None


def _validate_boundaries(boundaries: list[dict], shots: list[dict], model_loras: dict) -> list[dict]:
    entries: list[dict[str, Any]] = []
    shots_by_id = {shot.get("shot_id"): shot for shot in shots}
    seen_boundary_ids: set[str] = set()
    for boundary in boundaries:
        boundary_id = str(boundary.get("boundary_id") or "boundary")
        left_id = boundary.get("left_shot_id")
        right_id = boundary.get("right_shot_id")
        mode = boundary.get("mode")
        if boundary_id in seen_boundary_ids:
            entries.append(
                create_validation_entry(
                    "BOUNDARY_DUPLICATE_ID",
                    SEVERITY_ERROR,
                    "Director",
                    "Boundary",
                    boundary_id,
                    "Boundary IDs must be unique.",
                    "Rename or remove the duplicate boundary.",
                )
            )
        seen_boundary_ids.add(boundary_id)
        if left_id not in shots_by_id:
            entries.append(
                create_validation_entry(
                    "BOUNDARY_LEFT_SHOT_NOT_FOUND",
                    SEVERITY_ERROR,
                    "Director",
                    "Boundary",
                    boundary_id,
                    "Boundary references a missing left shot.",
                    "Choose an existing left shot or remove the stale boundary.",
                    {"left_shot_id": left_id},
                )
            )
        if right_id not in shots_by_id:
            entries.append(
                create_validation_entry(
                    "BOUNDARY_RIGHT_SHOT_NOT_FOUND",
                    SEVERITY_ERROR,
                    "Director",
                    "Boundary",
                    boundary_id,
                    "Boundary references a missing right shot.",
                    "Choose an existing right shot or remove the stale boundary.",
                    {"right_shot_id": right_id},
                )
            )
        if left_id == right_id and left_id is not None:
            entries.append(
                create_validation_entry(
                    "BOUNDARY_SELF_REFERENCE",
                    SEVERITY_ERROR,
                    "Director",
                    "Boundary",
                    boundary_id,
                    "Boundary cannot connect a shot to itself.",
                    "Choose two adjacent shots or remove the boundary.",
                    {"shot_id": left_id},
                )
            )
        if mode in {BOUNDARY_MODE_CONTINUOUS_SHOT, BOUNDARY_MODE_BLEND_SEAM} and left_id in shots_by_id and right_id in shots_by_id:
            left_loras = _effective_lora_signature(model_loras, shots_by_id[left_id])
            right_loras = _effective_lora_signature(model_loras, shots_by_id[right_id])
            if left_loras != right_loras:
                entries.append(
                    create_validation_entry(
                        "BOUNDARY_LORA_STACK_MISMATCH",
                        SEVERITY_WARNING,
                        "Director",
                        "Boundary",
                        boundary_id,
                        "Adjacent shots resolve to different LoRA stacks.",
                        (
                            "Use Hard Cut, keep LoRAs consistent, or accept the style change across this boundary."
                            if mode == BOUNDARY_MODE_CONTINUOUS_SHOT
                            else "Blend seams may show style changes when adjacent LoRA stacks differ."
                        ),
                        {"mode": mode, "left_shot_id": left_id, "right_shot_id": right_id},
                    )
                )
        elif mode == BOUNDARY_MODE_HARD_CUT:
            continue
    return entries


def _effective_lora_signature(model_loras: dict, shot: dict) -> str:
    global_targets = _raw_dict(model_loras.get("global"))
    overrides = _raw_dict(shot.get("lora_overrides"))
    if not overrides.get("enabled"):
        return _stable_json(global_targets)
    merge_mode = overrides.get("merge_mode")
    targets = _raw_dict(overrides.get("targets"))
    if merge_mode == LORA_MERGE_MODE_DISABLE_LORAS:
        return _stable_json({})
    if merge_mode == LORA_MERGE_MODE_REPLACE_GLOBAL:
        return _stable_json(targets)
    if merge_mode == LORA_MERGE_MODE_ADD_TO_GLOBAL:
        merged = deepcopy(global_targets)
        _merge_lora_targets(merged, targets)
        return _stable_json(merged)
    if merge_mode == LORA_MERGE_MODE_INHERIT_GLOBAL:
        return _stable_json(global_targets)
    return _stable_json(global_targets)


def _merge_lora_targets(base: dict, overrides: dict) -> None:
    for model_key, model_targets in overrides.items():
        if not isinstance(model_targets, dict):
            continue
        base_model = base.setdefault(model_key, {})
        if not isinstance(base_model, dict):
            base[model_key] = {}
            base_model = base[model_key]
        for target_key, stack in model_targets.items():
            if not isinstance(stack, dict):
                continue
            base_stack = base_model.setdefault(target_key, {"version": 1, "loras": [], "ui": {}})
            if not isinstance(base_stack, dict):
                base_model[target_key] = {"version": 1, "loras": [], "ui": {}}
                base_stack = base_model[target_key]
            base_loras = base_stack.setdefault("loras", [])
            override_loras = stack.get("loras") if isinstance(stack.get("loras"), list) else []
            if isinstance(base_loras, list):
                base_loras.extend(deepcopy(override_loras))
            else:
                base_stack["loras"] = deepcopy(override_loras)
            if isinstance(stack.get("ui"), dict):
                base_stack["ui"] = deepcopy(stack["ui"])


def _validate_lora_payload(payload: Any, scope: str, item_id: str, code_prefix: str) -> list[dict]:
    if _contains_embedded_media(payload):
        return [
            create_validation_entry(
                f"{code_prefix}_EMBEDDED_MEDIA_NOT_ALLOWED",
                SEVERITY_ERROR,
                "Director",
                scope,
                item_id,
                "LoRA stack must not embed media or preview payloads.",
                "Store only LoRA names, numeric strengths, and UI preferences.",
            )
        ]
    return []


def _gap_entries(timeline: dict, global_settings: dict) -> list[dict]:
    entries = []
    allow_gaps = bool(global_settings["timeline"]["allow_gaps"])
    auto_close_gaps = bool(global_settings["timeline"]["auto_close_gaps"])
    for index, gap in enumerate(detect_director_gaps(timeline)):
        details = {
            **gap,
            "allow_gaps": allow_gaps,
            "auto_close_gaps": auto_close_gaps,
        }
        entries.append(
            create_validation_entry(
                "DIRECTOR_GAP",
                SEVERITY_INFO if allow_gaps else SEVERITY_ERROR,
                "Director",
                "Gap",
                f"gap_{index + 1:03d}",
                "Director Track gap means No Guidance." if allow_gaps else "Director Track gaps are disabled in Global Settings.",
                "This is allowed. Planner nodes may apply model-specific policy later." if allow_gaps else "Close the gap or turn on Allow Gaps in Global Settings.",
                details,
            )
        )
    return entries


def _validate_audio_tracks(audio_tracks: list[dict], duration: float | None, assets_by_id: dict) -> list[dict]:
    entries = []
    for track in audio_tracks:
        lanes: dict[int, list[dict]] = {}
        for clip in track.get("clips", []):
            item_id = clip.get("item_id")
            start = _as_float(clip.get("start_time"))
            end = _as_float(clip.get("end_time"))
            if start is None or end is None or end <= start:
                entries.append(
                    create_validation_entry(
                        "AUDIO_CLIP_INVALID_TIME_RANGE",
                        SEVERITY_ERROR,
                        "Director",
                        "AudioClip",
                        item_id,
                        "Audio Clip requires a valid start_time and end_time.",
                        "Set end_time greater than start_time.",
                    )
                )
            elif duration is not None and (start < 0 or end > duration):
                entries.append(
                    create_validation_entry(
                        "AUDIO_CLIP_OUTSIDE_PROJECT_DURATION",
                        SEVERITY_ERROR,
                        "Director",
                        "AudioClip",
                        item_id,
                        "Audio Clip must stay within Project Duration.",
                        "Move or trim the clip inside the project boundary.",
                    )
                )
            if not _has_media_reference(clip.get("audio")):
                entries.append(
                    create_validation_entry(
                        "AUDIO_CLIP_MISSING_AUDIO",
                        SEVERITY_ERROR,
                        "Director",
                        "AudioClip",
                        item_id,
                        "Audio Clip requires audio.",
                        "Choose audio or remove the clip.",
                    )
                )
            else:
                entries.extend(
                    _validate_media_reference(
                        clip.get("audio"),
                        assets_by_id,
                        "AudioClip",
                        item_id,
                        "AUDIO_CLIP_MEDIA",
                    )
                )
            lane = int(clip.get("lane", 0))
            lanes.setdefault(lane, []).append(clip)

        for lane, clips in lanes.items():
            entries.extend(_validate_audio_lane(track.get("track_id"), lane, clips))
    return entries


def _validate_audio_lane(track_id: str | None, lane: int, clips: list[dict]) -> list[dict]:
    entries = []
    sorted_clips = sorted(clips, key=lambda clip: _as_float(clip.get("start_time")) or 0.0)
    previous_end: float | None = None
    previous_id: str | None = None
    for clip in sorted_clips:
        start = _as_float(clip.get("start_time"))
        end = _as_float(clip.get("end_time"))
        if previous_end is not None and start is not None and start < previous_end:
            entries.append(
                create_validation_entry(
                    "AUDIO_CLIP_LANE_OVERLAP",
                    SEVERITY_ERROR,
                    "Director",
                    "AudioClip",
                    clip.get("item_id"),
                    "Audio Clips cannot overlap within the same lane.",
                    "Move one clip to another lane or trim the overlap.",
                    {
                        "track_id": track_id,
                        "lane": lane,
                        "previous_item_id": previous_id,
                    },
                )
            )
        if end is not None and (previous_end is None or end > previous_end):
            previous_end = end
            previous_id = clip.get("item_id")
    return entries


def _has_media_reference(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value.get("asset_id") or value.get("path") or value.get("file_path"))
    return value is not None


def _validate_media_reference(
    reference: Any,
    assets_by_id: dict,
    scope: str,
    item_id: str | None,
    code_prefix: str,
) -> list[dict]:
    if _contains_embedded_media(reference):
        return [
            create_validation_entry(
                f"{code_prefix}_EMBEDDED_MEDIA_NOT_ALLOWED",
                SEVERITY_ERROR,
                "Director",
                scope,
                item_id,
                "Media references must not embed media, thumbnails, or waveform data in workflow JSON.",
                "Reference an asset_id or file path instead.",
            )
        ]
    if isinstance(reference, dict) and reference.get("asset_id") and reference.get("asset_id") not in assets_by_id:
        return [
            create_validation_entry(
                f"{code_prefix}_ASSET_NOT_FOUND",
                SEVERITY_ERROR,
                "Director",
                scope,
                item_id,
                "Media reference points to a missing asset record.",
                "Choose the media again or remove the stale reference.",
                {"asset_id": reference.get("asset_id")},
            )
        ]
    return []


def _contains_embedded_media(value: Any) -> bool:
    if not isinstance(value, dict):
        return isinstance(value, str) and value.startswith(("data:", "blob:"))
    stack = [value]
    blocked_keys = {
        "data",
        "blob",
        "bytes",
        "thumbnail",
        "thumbnail_data",
        "waveform",
        "waveform_data",
    }
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        for key, child in current.items():
            if key in blocked_keys:
                return True
            if isinstance(child, str) and child.startswith(("data:", "blob:")):
                return True
            if isinstance(child, dict):
                stack.append(child)
            elif isinstance(child, list):
                stack.extend(item for item in child if isinstance(item, dict))
    return False


def _raw_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
