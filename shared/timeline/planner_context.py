from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from ..contracts.video_timeline import (
    LORA_MERGE_MODE_ADD_TO_GLOBAL,
    LORA_MERGE_MODE_DISABLE_LORAS,
    LORA_MERGE_MODE_INHERIT_GLOBAL,
    LORA_MERGE_MODE_REPLACE_GLOBAL,
)
from .defaults import create_default_lora_stack


def build_section_shot_map(timeline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    section_to_shot: dict[str, dict[str, Any]] = {}
    for shot in timeline.get("sequence", {}).get("shots", []):
        if not isinstance(shot, dict):
            continue
        for section_id in shot.get("section_ids", []):
            if section_id is None:
                continue
            section_to_shot.setdefault(str(section_id), shot)
    return section_to_shot


def build_sequence_plan_metadata(timeline: dict[str, Any]) -> dict[str, Any]:
    sequence = timeline.get("sequence") if isinstance(timeline.get("sequence"), dict) else {}
    shots = [
        {
            "shot_id": shot.get("shot_id"),
            "type": shot.get("type"),
            "start_time": shot.get("start_time"),
            "end_time": shot.get("end_time"),
            "section_ids": list(shot.get("section_ids") or []),
            "accepted_take_id": shot.get("accepted_take_id"),
        }
        for shot in sequence.get("shots", [])
        if isinstance(shot, dict)
    ]
    boundaries = [
        {
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
        for boundary in sequence.get("boundaries", [])
        if isinstance(boundary, dict)
    ]
    section_to_shot = {
        section_id: shot.get("shot_id")
        for section_id, shot in build_section_shot_map(timeline).items()
    }
    return {
        "sequence_id": sequence.get("sequence_id"),
        "name": sequence.get("name"),
        "shots": shots,
        "boundaries": boundaries,
        "section_to_shot": section_to_shot,
    }


def build_model_lora_resolution(
    timeline: dict[str, Any],
    section_entries: list[dict[str, Any]],
    *,
    model_key: str,
    target_keys: list[str],
) -> dict[str, Any]:
    model_loras = timeline.get("project", {}).get("model_loras", {})
    global_targets = {
        target_key: _project_lora_stack(model_loras, model_key, target_key)
        for target_key in target_keys
    }
    section_to_shot = build_section_shot_map(timeline)
    shot_entries = []
    for shot in timeline.get("sequence", {}).get("shots", []):
        if not isinstance(shot, dict):
            continue
        effective_loras = resolve_model_loras_for_shot(
            model_loras,
            shot,
            model_key=model_key,
            target_keys=target_keys,
        )
        shot_entries.append(
            {
                "shot_id": shot.get("shot_id"),
                "override_enabled": bool(
                    (shot.get("lora_overrides") if isinstance(shot.get("lora_overrides"), dict) else {}).get("enabled")
                ),
                "merge_mode": _shot_lora_merge_mode(shot),
                "effective_loras": effective_loras,
                "signature": lora_targets_signature(effective_loras),
            }
        )

    section_loras = []
    for entry in section_entries:
        item_id = entry.get("item_id")
        shot = section_to_shot.get(str(item_id)) if item_id is not None else None
        effective_loras = (
            resolve_model_loras_for_shot(
                model_loras,
                shot,
                model_key=model_key,
                target_keys=target_keys,
            )
            if shot is not None
            else deepcopy(global_targets)
        )
        section_loras.append(
            {
                "item_id": item_id,
                "shot_id": shot.get("shot_id") if shot else None,
                "effective_loras": effective_loras,
                "signature": lora_targets_signature(effective_loras),
            }
        )

    real_section_loras = [
        entry
        for entry in section_loras
        if entry.get("shot_id") is not None
    ]
    unique_signatures = sorted({entry["signature"] for entry in real_section_loras})
    requires_per_shot_execution = len(unique_signatures) > 1
    return {
        "model": model_key,
        "targets": list(target_keys),
        "global": global_targets,
        "shot_loras": shot_entries,
        "section_loras": section_loras,
        "single_generation_loras": (
            deepcopy(real_section_loras[0]["effective_loras"])
            if real_section_loras and not requires_per_shot_execution
            else deepcopy(global_targets) if not real_section_loras else None
        ),
        "requires_per_shot_execution": requires_per_shot_execution,
        "execution_strategy": (
            "defer_per_shot_lora_execution"
            if requires_per_shot_execution
            else "single_stack"
        ),
        "unique_signature_count": len(unique_signatures),
    }


def resolve_model_loras_for_shot(
    model_loras: dict[str, Any],
    shot: dict[str, Any] | None,
    *,
    model_key: str,
    target_keys: list[str],
) -> dict[str, Any]:
    global_targets = {
        target_key: _project_lora_stack(model_loras, model_key, target_key)
        for target_key in target_keys
    }
    if not isinstance(shot, dict):
        return global_targets
    overrides = shot.get("lora_overrides")
    if not isinstance(overrides, dict) or not overrides.get("enabled"):
        return global_targets
    merge_mode = overrides.get("merge_mode") or LORA_MERGE_MODE_INHERIT_GLOBAL
    override_targets = _raw_dict(_raw_dict(overrides.get("targets")).get(model_key))
    if merge_mode == LORA_MERGE_MODE_DISABLE_LORAS:
        return {target_key: create_default_lora_stack() for target_key in target_keys}
    if merge_mode == LORA_MERGE_MODE_REPLACE_GLOBAL:
        return {
            target_key: _copy_lora_stack(override_targets.get(target_key))
            for target_key in target_keys
        }
    if merge_mode == LORA_MERGE_MODE_ADD_TO_GLOBAL:
        return {
            target_key: _merge_lora_stack(
                global_targets[target_key],
                override_targets.get(target_key),
            )
            for target_key in target_keys
        }
    return global_targets


def lora_targets_signature(targets: dict[str, Any]) -> str:
    return json.dumps(targets, sort_keys=True, separators=(",", ":"))


def _project_lora_stack(model_loras: dict[str, Any], model_key: str, target_key: str) -> dict[str, Any]:
    global_loras = _raw_dict(model_loras.get("global"))
    model_targets = _raw_dict(global_loras.get(model_key))
    return _copy_lora_stack(model_targets.get(target_key))


def _merge_lora_stack(base: dict[str, Any], override: Any) -> dict[str, Any]:
    merged = _copy_lora_stack(base)
    override_stack = _copy_lora_stack(override)
    merged["loras"].extend(deepcopy(override_stack.get("loras") or []))
    if isinstance(override, dict) and isinstance(override.get("ui"), dict):
        merged["ui"] = deepcopy(override_stack["ui"])
    return merged


def _copy_lora_stack(stack: Any) -> dict[str, Any]:
    if not isinstance(stack, dict):
        return create_default_lora_stack()
    ui = stack.get("ui") if isinstance(stack.get("ui"), dict) else {}
    rows = [
        deepcopy(row)
        for row in stack.get("loras", [])
        if isinstance(row, dict) and row.get("name")
    ] if isinstance(stack.get("loras"), list) else []
    return {
        "version": int(stack.get("version") or 1),
        "loras": rows,
        "ui": {
            "show_strengths": str(ui.get("show_strengths") or "single"),
            "match": str(ui.get("match") or ""),
        },
    }


def _shot_lora_merge_mode(shot: dict[str, Any]) -> str:
    overrides = shot.get("lora_overrides")
    if not isinstance(overrides, dict):
        return LORA_MERGE_MODE_INHERIT_GLOBAL
    return str(overrides.get("merge_mode") or LORA_MERGE_MODE_INHERIT_GLOBAL)


def _raw_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
