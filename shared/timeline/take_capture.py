from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import re
from typing import Any

from ..contracts.video_timeline import (
    ASSET_SOURCE_GENERATED,
    ASSET_TYPE_VIDEO,
    TAKE_STATUS_CANDIDATE,
)
from .. import privacy_keystore
from .global_settings import global_privacy_mode


TAKE_CAPTURE_SCHEMA_VERSION = 1
TAKE_CAPTURE_TYPE = "TAKE_REGISTRATION_ENVELOPE"


def build_take_capture_metadata(
    plan: dict[str, Any],
    *,
    model_key: str,
    model_family: str,
    model_version: str,
    source: str,
    expected_asset_type: str = ASSET_TYPE_VIDEO,
    resolved_loras: dict[str, Any] | None = None,
    seed: int | None = None,
    settings: dict[str, Any] | None = None,
    segment: dict[str, Any] | None = None,
    model_specific: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build privacy-safe metadata that can later register a generated take."""

    if not isinstance(plan, dict):
        return None
    model_plan = _raw_dict(_raw_dict(plan.get("model_specific")).get(model_key))
    shot_context = _shot_context(plan, model_key)
    segment_context = _segment_context(segment)
    shot_id, shot_ids = _resolve_shot_ids(model_plan, shot_context, segment_context)
    if not shot_id and segment_context is None:
        return None

    privacy_mode = global_privacy_mode()
    safe_resolved_loras = _safe_resolved_loras(resolved_loras, privacy_mode)
    prompt_hash = _metadata_hash(_prompt_projection(plan), privacy_mode=privacy_mode)
    plan_hash = _metadata_hash(
        {
            "type": plan.get("type"),
            "model_family": model_family,
            "model_version": model_version,
            "resolved_output": _resolved_output_summary(plan),
            "sections": _section_projection(plan),
            "shot_id": shot_id,
            "shot_ids": shot_ids,
            "segment": segment_context,
            "prompt_hash": prompt_hash,
            "resolved_loras_hash": _metadata_hash(safe_resolved_loras, privacy_mode=privacy_mode),
        },
        privacy_mode=privacy_mode,
    )
    suggested_name = _suggested_asset_name(
        model_family,
        expected_asset_type,
        shot_id=shot_id,
        segment=segment_context,
        privacy_mode=privacy_mode,
    )
    take_id = _suggested_take_id(
        model_family,
        shot_id=shot_id,
        segment=segment_context,
    )
    asset_suggestion = {
        "type": str(expected_asset_type or ASSET_TYPE_VIDEO),
        "source_kind": ASSET_SOURCE_GENERATED,
        "name": suggested_name,
        "metadata": {
            "model_family": str(model_family or ""),
            "model_version": str(model_version or ""),
        },
    }
    runtime_settings = {
        **_runtime_output_settings(plan),
        **_strip_embedded_media(settings or {}),
    }
    safe_model_specific = _strip_embedded_media(model_specific or {})
    take_metadata = {
        "source": str(source or ""),
        "expected_asset_type": str(expected_asset_type or ASSET_TYPE_VIDEO),
        "suggested_asset_name": suggested_name,
        "settings": runtime_settings,
        "shot": _safe_shot_context(shot_context, model_plan, shot_id),
        "segment": segment_context,
        "privacy": {
            "privacy_mode": privacy_mode,
            "redacted_fields": (
                ["resolved_loras.targets.*.name"] if privacy_mode else []
            ),
        },
    }
    if shot_ids:
        take_metadata["shot_ids"] = shot_ids
    if safe_model_specific:
        take_metadata["model_specific"] = {
            model_key: deepcopy(safe_model_specific),
        }

    envelope = {
        "schema_version": TAKE_CAPTURE_SCHEMA_VERSION,
        "type": TAKE_CAPTURE_TYPE,
        "shot_id": shot_id,
        "shot_ids": shot_ids,
        "registration_ready": shot_id is not None,
        "capture_blockers": _capture_blockers(shot_id, shot_ids),
        "expected_asset_type": str(expected_asset_type or ASSET_TYPE_VIDEO),
        "suggested_asset_name": suggested_name,
        "asset_suggestion": deepcopy(asset_suggestion),
        "plan_hash": plan_hash,
        "prompt_hash": prompt_hash,
        "asset": {
            **deepcopy(asset_suggestion),
            "metadata": {
                **deepcopy(asset_suggestion["metadata"]),
                "plan_hash": plan_hash,
                "prompt_hash": prompt_hash,
            },
        },
        "take": {
            "take_id": take_id,
            "status": TAKE_STATUS_CANDIDATE,
            "seed": seed,
            "model_family": str(model_family or ""),
            "model_version": str(model_version or ""),
            "plan_hash": plan_hash,
            "prompt_hash": prompt_hash,
            "resolved_loras": safe_resolved_loras,
            "metadata": take_metadata,
        },
        "shot_context": take_metadata["shot"],
        "segment_context": segment_context,
        "model_specific": {
            model_key: {
                "source": str(source or ""),
                "expected_asset_type": str(expected_asset_type or ASSET_TYPE_VIDEO),
                "resolved_output": _resolved_output_summary(plan),
                **deepcopy(safe_model_specific),
            }
        },
        "privacy": take_metadata["privacy"],
    }
    return _strip_embedded_media(envelope)


def _shot_context(plan: dict[str, Any], model_key: str) -> dict[str, Any] | None:
    model_plan = _raw_dict(_raw_dict(plan.get("model_specific")).get(model_key))
    shot_context = model_plan.get("shot_context")
    if isinstance(shot_context, dict):
        return deepcopy(shot_context)
    sequence_metadata = _raw_dict(model_plan.get("timeline_structure")).get("metadata")
    extracted = _raw_dict(sequence_metadata).get("shot_extraction")
    return deepcopy(extracted) if isinstance(extracted, dict) else None


def _segment_context(segment: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(segment, dict):
        return None
    keys = (
        "id",
        "index",
        "start_frame",
        "end_frame_exclusive",
        "frame_count",
        "visible_frame_count",
        "generation_frame_count",
        "trim_leading_frames",
        "trim_trailing_frames",
        "source_section_ids",
        "continuity",
    )
    return {
        key: _strip_embedded_media(deepcopy(segment.get(key)))
        for key in keys
        if key in segment
    }


def _resolve_shot_ids(
    model_plan: dict[str, Any],
    shot_context: dict[str, Any] | None,
    segment_context: dict[str, Any] | None,
) -> tuple[str | None, list[str]]:
    if isinstance(shot_context, dict) and shot_context.get("shot_id"):
        shot_id = str(shot_context["shot_id"])
        return shot_id, [shot_id]
    source_section_ids = []
    if isinstance(segment_context, dict):
        source_section_ids = [
            str(section_id)
            for section_id in segment_context.get("source_section_ids") or []
            if section_id is not None
        ]
    section_to_shot = _raw_dict(_raw_dict(model_plan.get("timeline_structure")).get("section_to_shot"))
    shot_ids = []
    for section_id in source_section_ids:
        shot_id = section_to_shot.get(section_id)
        if shot_id is not None and str(shot_id) not in shot_ids:
            shot_ids.append(str(shot_id))
    return (shot_ids[0] if len(shot_ids) == 1 else None), shot_ids


def _safe_shot_context(
    shot_context: dict[str, Any] | None,
    model_plan: dict[str, Any],
    shot_id: str | None,
) -> dict[str, Any] | None:
    if isinstance(shot_context, dict):
        context = _strip_embedded_media(deepcopy(shot_context))
        context["local_duration_seconds"] = context.get("duration_seconds")
        return context
    if not shot_id:
        return None
    for shot in _raw_dict(model_plan.get("timeline_structure")).get("shots") or []:
        if not isinstance(shot, dict) or str(shot.get("shot_id") or "") != shot_id:
            continue
        start = _as_float(shot.get("start_time"), 0.0)
        end = _as_float(shot.get("end_time"), start)
        duration = max(0.0, end - start)
        return {
            "shot_id": shot_id,
            "shot_type": shot.get("type"),
            "original_start_time": start,
            "original_end_time": end,
            "duration_seconds": duration,
            "local_start_time": 0.0,
            "local_end_time": duration,
            "local_duration_seconds": duration,
            "section_ids": list(shot.get("section_ids") or []),
        }
    return {"shot_id": shot_id}


def _prompt_projection(plan: dict[str, Any]) -> dict[str, Any]:
    prompts = []
    for entry in plan.get("prompt_plan") or []:
        if not isinstance(entry, dict):
            continue
        prompts.append(
            {
                "item_id": entry.get("item_id"),
                "type": entry.get("type"),
                "prompt": (
                    entry.get("runtime_prompt")
                    or entry.get("effective_prompt")
                    or entry.get("raw_prompt")
                    or ""
                ),
            }
        )
    return {
        "global_prompt": _raw_dict(_raw_dict(plan.get("project")).get("global_prompt")).get("prompt") or "",
        "prompts": prompts,
    }


def _section_projection(plan: dict[str, Any]) -> list[dict[str, Any]]:
    sections = []
    for entry in plan.get("section_plan") or []:
        if not isinstance(entry, dict):
            continue
        sections.append(
            {
                "item_id": entry.get("item_id"),
                "type": entry.get("type"),
                "start_frame": entry.get("start_frame"),
                "end_frame_exclusive": entry.get("end_frame_exclusive"),
                "start_time": entry.get("start_time"),
                "end_time": entry.get("end_time"),
                "frame_count": entry.get("frame_count"),
            }
        )
    return sections


def _runtime_output_settings(plan: dict[str, Any]) -> dict[str, Any]:
    output = _resolved_output_summary(plan)
    return {
        key: output.get(key)
        for key in (
            "width",
            "height",
            "frame_rate",
            "frame_count",
            "duration_seconds",
            "latent_chunk_count",
        )
        if key in output
    }


def _resolved_output_summary(plan: dict[str, Any]) -> dict[str, Any]:
    output = _raw_dict(plan.get("resolved_output"))
    return {
        key: _strip_embedded_media(output.get(key))
        for key in (
            "width",
            "height",
            "frame_rate",
            "frame_count",
            "requested_frame_count",
            "duration_seconds",
            "generation_duration_seconds",
            "latent_chunk_count",
        )
        if key in output
    }


def _safe_resolved_loras(value: dict[str, Any] | None, privacy_mode: bool) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    snapshot = _strip_embedded_media(deepcopy(value))
    if not privacy_mode:
        return snapshot
    targets = snapshot.get("targets") if isinstance(snapshot.get("targets"), dict) else {}
    for rows in targets.values():
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            if name:
                row["name_hash"] = _private_hash(name)[:16]
                row["name"] = f"lora_{index + 1:03d}"
    return snapshot


def _suggested_asset_name(
    model_family: str,
    asset_type: str,
    *,
    shot_id: str | None,
    segment: dict[str, Any] | None,
    privacy_mode: bool,
) -> str:
    extension = ".mp4" if asset_type == ASSET_TYPE_VIDEO else ""
    if privacy_mode:
        return f"generated_{_slug(asset_type or 'asset')}{extension}"
    parts = [str(model_family or "model")]
    if shot_id:
        parts.append(shot_id)
    if isinstance(segment, dict) and segment.get("id"):
        parts.append(str(segment["id"]))
    parts.append("generated")
    return f"{_slug('_'.join(parts))}{extension}"


def _suggested_take_id(
    model_family: str,
    *,
    shot_id: str | None,
    segment: dict[str, Any] | None,
) -> str:
    parts = ["take"]
    if model_family:
        parts.append(str(model_family))
    if shot_id:
        parts.append(str(shot_id))
    if isinstance(segment, dict) and segment.get("id"):
        parts.append(str(segment["id"]))
    parts.append("generated")
    return _slug("_".join(parts))


def _capture_blockers(shot_id: str | None, shot_ids: list[str]) -> list[str]:
    if shot_id:
        return []
    if shot_ids:
        return ["TAKE_CAPTURE_MULTIPLE_SHOTS"]
    return ["TAKE_CAPTURE_NO_SHOT_ID"]


def _strip_embedded_media(value: Any) -> Any:
    if isinstance(value, bytes | bytearray | memoryview):
        return None
    if _is_tensor_like(value):
        return {"tensor_shape": _tensor_shape(value)}
    if isinstance(value, str):
        return None if value.startswith(("data:", "blob:")) else value
    if isinstance(value, dict):
        return {
            str(key): _strip_embedded_media(child)
            for key, child in value.items()
            if key not in _EMBEDDED_MEDIA_KEYS
        }
    if isinstance(value, list | tuple):
        return [
            cleaned
            for item in value
            if not (
                isinstance(item, str)
                and item.startswith(("data:", "blob:"))
            )
            for cleaned in [_strip_embedded_media(item)]
        ]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _metadata_hash(value: Any, *, privacy_mode: bool) -> str:
    return _private_hash(value) if privacy_mode else _stable_hash(value)


def _private_hash(value: Any) -> str:
    try:
        key, _key_id = privacy_keystore.primary_session_key()
    except privacy_keystore.PrivacyKeystoreError as exc:
        raise RuntimeError(
            f"{exc}: Private capture metadata requires an unlocked privacy keystore."
        ) from exc
    return hmac.new(key, _canonical_bytes(value), hashlib.sha256).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _strip_embedded_media(value),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _raw_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_float(value: Any, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int | float):
        return float(value)
    return fallback


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return slug.lower() or "generated"


def _is_tensor_like(value: Any) -> bool:
    return hasattr(value, "shape") and hasattr(value, "detach")


def _tensor_shape(value: Any) -> list[int]:
    try:
        return [int(dim) for dim in value.shape]
    except Exception:
        return []


_EMBEDDED_MEDIA_KEYS = {
    "data",
    "blob",
    "bytes",
    "thumbnail",
    "thumbnail_data",
    "waveform",
    "waveform_data",
    "image",
    "video",
    "audio",
    "path",
    "file_path",
}
