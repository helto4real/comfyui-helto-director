from __future__ import annotations

from typing import Any

from ..bernini import BERNINI_SYSTEM_PROMPTS


def apply_wan_previous_tail_continuity(
    plan: dict[str, Any],
    tail,
    *,
    media_item_id: str = "segment_previous_tail",
    source: str = "segment",
    boundary_conditioning: dict[str, Any] | None = None,
) -> None:
    wan = plan.get("model_specific", {}).get("wan", {})
    if tail is None:
        return
    continuity = {
        "mode": "previous_tail",
        "previous_tail_images": tail,
        "frame_count": int(tail.shape[0]),
        "media_item_id": media_item_id,
        "source": source,
    }
    if isinstance(boundary_conditioning, dict) and boundary_conditioning:
        continuity["boundary_conditioning"] = _safe_boundary_conditioning(boundary_conditioning)
    wan["segment_continuity"] = continuity

    visual = wan.get("visual_conditioning")
    if isinstance(visual, dict):
        visual["transient_start_image"] = tail
        visual["continuation_source"] = "previous_tail"
        visual["continuation_media_id"] = media_item_id
        visual["continuation_kind"] = "boundary_conditioning" if source == "boundary" else "segment_continuity"
        visual["requested_keyframes"] = []
        visual["applied_keyframes"] = []
        visual["unsupported_keyframes"] = []

    bernini = wan.get("bernini")
    if isinstance(bernini, dict) and bernini.get("enabled"):
        bernini["segment_continuity"] = continuity
        if bernini.get("task_type") == "r2v":
            bernini["task_type"] = "rv2v"
            bernini["system_prompt"] = BERNINI_SYSTEM_PROMPTS["rv2v"]
            bernini["selection_reason"] = _selection_reason(source, with_references=True)
        elif bernini.get("task_type") == "t2v":
            bernini["task_type"] = "v2v"
            bernini["system_prompt"] = BERNINI_SYSTEM_PROMPTS["v2v"]
            bernini["selection_reason"] = _selection_reason(source, with_references=False)


def _selection_reason(source: str, *, with_references: bool) -> str:
    if source == "boundary":
        if with_references:
            return "Boundary conditioning uses the previous accepted clip tail as Bernini source_video with reference images."
        return "Boundary conditioning uses the previous accepted clip tail as Bernini source_video."
    if with_references:
        return "Continuation segment uses the previous decoded tail as Bernini source_video with reference images."
    return "Continuation segment uses the previous decoded tail as Bernini source_video."


def _safe_boundary_conditioning(boundary_conditioning: dict[str, Any]) -> dict[str, Any]:
    safe_keys = {
        "type",
        "mode",
        "policy",
        "model_status",
        "status",
        "source_status",
        "boundary_id",
        "source_shot_id",
        "target_shot_id",
        "asset_id",
        "asset_type",
        "source_kind",
        "take_id",
        "requested_tail_frames",
        "effective_tail_frames",
        "selected_frame_count",
        "blend_frames",
        "transition_prompt_applied",
        "fallback_reason",
        "message",
    }
    return {
        key: boundary_conditioning.get(key)
        for key in sorted(safe_keys)
        if key in boundary_conditioning
    }
