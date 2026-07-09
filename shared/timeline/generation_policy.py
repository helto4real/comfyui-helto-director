from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..contracts.validation import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    create_validation_entry,
)
from ..contracts.video_timeline import ASSET_TYPE_VIDEO, SHOT_TYPE_IMPORTED
from .normalize import normalize_video_timeline


GENERATION_MODE_MISSING_ONLY = "Missing Only"
GENERATION_MODE_FORCE_SELECTED = "Force Selected"
GENERATION_MODE_FORCE_FULL_TIMELINE = "Force Full Timeline"
GENERATION_MODES = [
    GENERATION_MODE_MISSING_ONLY,
    GENERATION_MODE_FORCE_SELECTED,
    GENERATION_MODE_FORCE_FULL_TIMELINE,
]

GENERATION_STATUS_TARGETED = "targeted"
GENERATION_STATUS_SKIPPED = "skipped"
GENERATION_STATUS_BLOCKED = "blocked"
GENERATION_STATUS_FULL_TIMELINE = "full_timeline"

GENERATION_SKIP_ALL_READY = "all_shots_ready"
GENERATION_SKIP_NO_GENERATABLE_SHOTS = "no_generatable_pending_shots"
GENERATION_SKIP_NO_SHOTS = "no_shots"
GENERATION_BLOCK_SELECTED_REQUIRED = "selected_shot_required"
GENERATION_BLOCK_SELECTED_NOT_GENERATABLE = "selected_shot_not_generatable"
GENERATION_BLOCK_LEGACY_SHOT_NOT_FOUND = "legacy_shot_not_found"


def normalize_generation_mode(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in GENERATION_MODES else GENERATION_MODE_MISSING_ONLY


def resolve_generation_policy(
    timeline: Any,
    generation_mode: Any = GENERATION_MODE_MISSING_ONLY,
    *,
    legacy_shot_id: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve the Director-owned generation target for planner/runtime nodes."""

    normalized = normalize_video_timeline(timeline)
    mode = normalize_generation_mode(generation_mode)
    requested_legacy_shot_id, legacy_source = _legacy_shot_target(
        generation_mode,
        legacy_shot_id,
    )
    shots = _ordered_shots(normalized)
    assets_by_id = _assets_by_id(normalized)
    selected_shot_id = _selected_shot_id(normalized, shots)
    selected_readiness = None
    shot_states = []
    ready_shot_ids = []
    pending_shot_ids = []
    generatable_pending_shot_ids = []
    blocked_shot_ids = []

    for shot in shots:
        state = _shot_state(shot, assets_by_id)
        shot_states.append(state)
        if state["ready"]:
            ready_shot_ids.append(state["shot_id"])
        else:
            pending_shot_ids.append(state["shot_id"])
            if state["generatable"]:
                generatable_pending_shot_ids.append(state["shot_id"])
            else:
                blocked_shot_ids.append(state["shot_id"])
        if state["shot_id"] == selected_shot_id:
            selected_readiness = state

    policy = {
        "schema_version": 1,
        "mode": mode,
        "status": GENERATION_STATUS_SKIPPED,
        "selected_shot_id": selected_shot_id,
        "target_shot_id": None,
        "ready_shot_ids": ready_shot_ids,
        "pending_shot_ids": pending_shot_ids,
        "generatable_pending_shot_ids": generatable_pending_shot_ids,
        "blocked_shot_ids": blocked_shot_ids,
        "shot_states": shot_states,
        "skip_reason": None,
        "block_reason": None,
        "legacy_shot_id": requested_legacy_shot_id,
        "legacy_shot_id_source": legacy_source,
        "message": "",
    }

    if requested_legacy_shot_id:
        matched_shot = next(
            (
                state
                for state in shot_states
                if state["shot_id"] == requested_legacy_shot_id
            ),
            None,
        )
        if matched_shot is None:
            policy.update(
                {
                    "status": GENERATION_STATUS_BLOCKED,
                    "block_reason": GENERATION_BLOCK_LEGACY_SHOT_NOT_FOUND,
                    "message": (
                        f"Legacy shot ID '{requested_legacy_shot_id}' was not found "
                        "in the Director timeline."
                    ),
                }
            )
            return normalized, policy
        policy.update(
            {
                "status": GENERATION_STATUS_TARGETED,
                "target_shot_id": requested_legacy_shot_id,
                "message": (
                    "A deprecated legacy shot ID selected an explicit Director "
                    "shot for generation."
                ),
            }
        )
        return normalized, policy

    if mode == GENERATION_MODE_FORCE_FULL_TIMELINE:
        policy.update(
            {
                "status": GENERATION_STATUS_FULL_TIMELINE,
                "message": "Force Full Timeline requested; planner will use the complete Director timeline.",
            }
        )
        return normalized, policy

    if not shots:
        policy.update(
            {
                "status": GENERATION_STATUS_SKIPPED,
                "skip_reason": GENERATION_SKIP_NO_SHOTS,
                "message": "Timeline has no shots to generate.",
            }
        )
        return normalized, policy

    if mode == GENERATION_MODE_FORCE_SELECTED:
        if selected_readiness is None:
            policy.update(
                {
                    "status": GENERATION_STATUS_BLOCKED,
                    "block_reason": GENERATION_BLOCK_SELECTED_REQUIRED,
                    "message": "Force Selected requires a selected Director shot or a section inside a shot.",
                }
            )
            return normalized, policy
        if not selected_readiness["generatable"]:
            policy.update(
                {
                    "status": GENERATION_STATUS_BLOCKED,
                    "block_reason": GENERATION_BLOCK_SELECTED_NOT_GENERATABLE,
                    "message": "The selected Director shot is not a generatable shot.",
                }
            )
            return normalized, policy
        policy.update(
            {
                "status": GENERATION_STATUS_TARGETED,
                "target_shot_id": selected_readiness["shot_id"],
                "message": "Force Selected requested; planner will regenerate the selected Director shot.",
            }
        )
        return normalized, policy

    if selected_readiness and not selected_readiness["ready"] and selected_readiness["generatable"]:
        policy.update(
            {
                "status": GENERATION_STATUS_TARGETED,
                "target_shot_id": selected_readiness["shot_id"],
                "message": "Missing Only selected the active Director shot because it is not assembly-ready.",
            }
        )
        return normalized, policy

    if generatable_pending_shot_ids:
        policy.update(
            {
                "status": GENERATION_STATUS_TARGETED,
                "target_shot_id": generatable_pending_shot_ids[0],
                "message": "Missing Only selected the earliest generatable shot that is not assembly-ready.",
            }
        )
        return normalized, policy

    if pending_shot_ids:
        policy.update(
            {
                "status": GENERATION_STATUS_SKIPPED,
                "skip_reason": GENERATION_SKIP_NO_GENERATABLE_SHOTS,
                "message": "Timeline has pending shots, but none are generatable.",
            }
        )
        return normalized, policy

    policy.update(
        {
            "status": GENERATION_STATUS_SKIPPED,
            "skip_reason": GENERATION_SKIP_ALL_READY,
            "message": "All Director shots are assembly-ready; generation is skipped.",
        }
    )
    return normalized, policy


def generation_policy_skips_generation(policy: dict[str, Any] | None) -> bool:
    return isinstance(policy, dict) and policy.get("status") == GENERATION_STATUS_SKIPPED


def generation_policy_blocks_generation(policy: dict[str, Any] | None) -> bool:
    return isinstance(policy, dict) and policy.get("status") == GENERATION_STATUS_BLOCKED


def generation_policy_requires_generation(policy: dict[str, Any] | None) -> bool:
    if not isinstance(policy, dict):
        return True
    return policy.get("status") in {GENERATION_STATUS_TARGETED, GENERATION_STATUS_FULL_TIMELINE}


def generation_policy_validation_entries(policy: dict[str, Any] | None, source: str) -> list[dict[str, Any]]:
    if not isinstance(policy, dict):
        return []
    status = policy.get("status")
    reason = policy.get("skip_reason") or policy.get("block_reason")
    if status == GENERATION_STATUS_TARGETED and policy.get("legacy_shot_id"):
        return [
            create_validation_entry(
                "GENERATION_LEGACY_SHOT_ID_DEPRECATED",
                SEVERITY_WARNING,
                source,
                "Generation",
                policy.get("legacy_shot_id"),
                "A deprecated legacy shot ID selected this generation target.",
                "Use Generation Mode and select the shot in the Director timeline for new workflows.",
                _policy_validation_details(policy),
            )
        ]
    if status == GENERATION_STATUS_SKIPPED:
        if reason == GENERATION_SKIP_ALL_READY:
            return [
                create_validation_entry(
                    "GENERATION_SKIPPED_ALL_SHOTS_READY",
                    SEVERITY_INFO,
                    source,
                    "Generation",
                    None,
                    "All Director shots are assembly-ready; generation is skipped.",
                    "Sequence Assembly can still run from accepted takes and imported clips.",
                    _policy_validation_details(policy),
                )
            ]
        if reason == GENERATION_SKIP_NO_SHOTS:
            return [
                create_validation_entry(
                    "GENERATION_SKIPPED_NO_SHOTS",
                    SEVERITY_INFO,
                    source,
                    "Generation",
                    None,
                    "Timeline has no Director shots to generate.",
                    "Add a shot or section before running generation.",
                    _policy_validation_details(policy),
                )
            ]
        if reason == GENERATION_SKIP_NO_GENERATABLE_SHOTS:
            return [
                create_validation_entry(
                    "GENERATION_SKIPPED_NO_GENERATABLE_SHOTS",
                    SEVERITY_WARNING,
                    source,
                    "Generation",
                    None,
                    "Timeline has pending shots, but none are generatable.",
                    "Assign imported clips for Imported shots or select a generatable shot.",
                    _policy_validation_details(policy),
                )
            ]
    if status == GENERATION_STATUS_BLOCKED:
        if reason == GENERATION_BLOCK_LEGACY_SHOT_NOT_FOUND:
            return [
                create_validation_entry(
                    "GENERATION_LEGACY_SHOT_NOT_FOUND",
                    SEVERITY_ERROR,
                    source,
                    "Generation",
                    policy.get("legacy_shot_id"),
                    "The deprecated legacy shot ID was not found in the Director timeline.",
                    "Select an existing shot and use Generation Mode, or update the legacy shot ID.",
                    _policy_validation_details(policy),
                )
            ]
        if reason == GENERATION_BLOCK_SELECTED_REQUIRED:
            return [
                create_validation_entry(
                    "GENERATION_SELECTED_SHOT_REQUIRED",
                    SEVERITY_ERROR,
                    source,
                    "Generation",
                    None,
                    "Force Selected requires a selected Director shot or a section inside a shot.",
                    "Select a generatable shot in the Director or use Missing Only.",
                    _policy_validation_details(policy),
                )
            ]
        if reason == GENERATION_BLOCK_SELECTED_NOT_GENERATABLE:
            return [
                create_validation_entry(
                    "GENERATION_SELECTED_SHOT_NOT_GENERATABLE",
                    SEVERITY_ERROR,
                    source,
                    "Generation",
                    policy.get("selected_shot_id"),
                    "The selected Director shot is not a generatable shot.",
                    "Select a Generated, Extended, Edited, or Placeholder shot, or use its imported clip for assembly.",
                    _policy_validation_details(policy),
                )
            ]
    return []


def generation_policy_debug_summary(policy: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(policy, dict):
        return {}
    return {
        "generation_mode": policy.get("mode"),
        "generation_status": policy.get("status"),
        "generation_selected_shot_id": policy.get("selected_shot_id"),
        "generation_target_shot_id": policy.get("target_shot_id"),
        "generation_ready_shot_count": len(policy.get("ready_shot_ids") or []),
        "generation_pending_shot_count": len(policy.get("pending_shot_ids") or []),
        "generation_skip_reason": policy.get("skip_reason"),
        "generation_block_reason": policy.get("block_reason"),
        "generation_legacy_shot_id": policy.get("legacy_shot_id"),
        "generation_legacy_shot_id_source": policy.get("legacy_shot_id_source"),
    }


def _policy_validation_details(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": policy.get("mode"),
        "status": policy.get("status"),
        "selected_shot_id": policy.get("selected_shot_id"),
        "target_shot_id": policy.get("target_shot_id"),
        "ready_shot_ids": list(policy.get("ready_shot_ids") or []),
        "pending_shot_ids": list(policy.get("pending_shot_ids") or []),
        "generatable_pending_shot_ids": list(policy.get("generatable_pending_shot_ids") or []),
        "blocked_shot_ids": list(policy.get("blocked_shot_ids") or []),
        "skip_reason": policy.get("skip_reason"),
        "block_reason": policy.get("block_reason"),
        "legacy_shot_id": policy.get("legacy_shot_id"),
        "legacy_shot_id_source": policy.get("legacy_shot_id_source"),
    }


def _legacy_shot_target(
    generation_mode: Any,
    explicit_shot_id: Any,
) -> tuple[str | None, str | None]:
    explicit = str(explicit_shot_id or "").strip()
    if explicit:
        return explicit, "shot_id"

    raw_mode = str(generation_mode or "").strip()
    if raw_mode and raw_mode not in GENERATION_MODES:
        return raw_mode, "generation_mode"
    return None, None


def _ordered_shots(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    sequence = timeline.get("sequence") if isinstance(timeline.get("sequence"), dict) else {}
    shots = [shot for shot in sequence.get("shots", []) if isinstance(shot, dict)]
    return sorted(
        shots,
        key=lambda shot: (
            _safe_float(shot.get("start_time"), 0.0),
            _safe_float(shot.get("end_time"), 0.0),
            str(shot.get("shot_id") or ""),
        ),
    )


def _assets_by_id(timeline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(asset.get("asset_id")): asset
        for asset in timeline.get("assets", [])
        if isinstance(asset, dict) and asset.get("asset_id") is not None
    }


def _selected_shot_id(timeline: dict[str, Any], shots: list[dict[str, Any]]) -> str | None:
    selected_id = timeline.get("ui_state", {}).get("selected_item_id")
    if selected_id is None:
        return None
    selected = str(selected_id)
    for shot in shots:
        if str(shot.get("shot_id") or "") == selected:
            return selected
    for shot in shots:
        section_ids = {str(section_id) for section_id in shot.get("section_ids", []) if section_id is not None}
        if selected in section_ids:
            return str(shot.get("shot_id") or "")
    return None


def _shot_state(shot: dict[str, Any], assets_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    shot_id = str(shot.get("shot_id") or "")
    accepted = _accepted_take_source(shot, assets_by_id)
    clip = _clip_instance_source(shot, assets_by_id)
    ready_source = accepted or clip
    generatable = str(shot.get("type") or "") != SHOT_TYPE_IMPORTED
    return {
        "shot_id": shot_id,
        "type": shot.get("type"),
        "ready": ready_source is not None,
        "ready_source": deepcopy(ready_source),
        "generatable": bool(generatable),
        "reason": "assembly_ready" if ready_source else ("imported_clip_required" if not generatable else "missing_accepted_take_or_clip"),
    }


def _accepted_take_source(shot: dict[str, Any], assets_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    take_id = shot.get("accepted_take_id")
    if take_id is None:
        return None
    take_id = str(take_id)
    for take in shot.get("takes", []):
        if not isinstance(take, dict) or str(take.get("take_id") or "") != take_id:
            continue
        asset = assets_by_id.get(str(take.get("asset_id") or ""))
        if _is_video_asset_with_path(asset):
            return {
                "source_kind": "accepted_take",
                "take_id": take_id,
                "asset_id": str(asset.get("asset_id")),
                "path": asset.get("path") or asset.get("file_path"),
            }
    return None


def _clip_instance_source(shot: dict[str, Any], assets_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    clip_instance = shot.get("clip_instance")
    if not isinstance(clip_instance, dict) or clip_instance.get("enabled") is False:
        return None
    asset = assets_by_id.get(str(clip_instance.get("asset_id") or ""))
    if not _is_video_asset_with_path(asset):
        return None
    return {
        "source_kind": "clip_instance",
        "take_id": None,
        "asset_id": str(asset.get("asset_id")),
        "path": asset.get("path") or asset.get("file_path"),
    }


def _is_video_asset_with_path(asset: dict[str, Any] | None) -> bool:
    if not isinstance(asset, dict):
        return False
    if asset.get("type") != ASSET_TYPE_VIDEO:
        return False
    return bool(str(asset.get("path") or asset.get("file_path") or "").strip())


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
