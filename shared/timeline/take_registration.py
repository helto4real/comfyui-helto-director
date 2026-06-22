from __future__ import annotations

from copy import deepcopy
import json
from pathlib import PurePath
from typing import Any

from ..contracts.video_timeline import (
    ASSET_SOURCE_GENERATED,
    ASSET_TYPES,
    ASSET_TYPE_VIDEO,
    TAKE_STATUS_ACCEPTED,
    TAKE_STATUS_CANDIDATE,
    TAKE_STATUS_REJECTED,
    TAKE_STATUSES,
)
from .defaults import create_default_clip_instance, create_default_take
from .generated_capture import (
    GENERATED_TAKE_CAPTURE_TYPE,
    generated_take_capture_to_registration,
)
from .normalize import normalize_video_timeline
from .take_capture import TAKE_CAPTURE_TYPE


class TakeRegistrationError(ValueError):
    """Raised when a generated take cannot be safely registered."""


def apply_take_registration(
    timeline: Any,
    registration: dict[str, Any] | str,
    *,
    generated_asset_path: str | None = None,
    accept: bool = False,
    update_clip_instance: bool = True,
) -> dict:
    """Apply a take-registration envelope or sidecar payload to a timeline."""

    envelope = prepare_take_registration(
        registration,
        generated_asset_path=generated_asset_path,
        accept=accept,
        update_clip_instance=update_clip_instance,
    )
    return register_generated_take(timeline, envelope)


def prepare_take_registration(
    registration: dict[str, Any] | str,
    *,
    generated_asset_path: str | None = None,
    accept: bool = False,
    update_clip_instance: bool = True,
) -> dict[str, Any]:
    """Return a normalized registration envelope ready for application."""

    envelope = _registration_envelope_from_input(registration)
    path = _safe_string(generated_asset_path)
    if path:
        asset = _raw_dict(envelope.get("asset"))
        if not _safe_string(asset.get("path") or asset.get("file_path")):
            asset["path"] = path
        if not _safe_string(asset.get("name")):
            asset["name"] = _basename(path)
        envelope["asset"] = asset
    asset = _raw_dict(envelope.get("asset"))
    if not _safe_string(asset.get("path") or asset.get("file_path")):
        raise TakeRegistrationError("generated_asset_path is required.")
    envelope["accept"] = bool(accept) or envelope.get("accept") is True
    envelope["update_clip_instance"] = bool(update_clip_instance)
    return envelope


def register_generated_take(timeline: Any, registration: dict[str, Any]) -> dict:
    """Register a generated asset and nested shot take.

    The registration envelope carries the target shot_id. The persisted take
    remains nested under the shot and does not duplicate the shot relationship.
    """

    if not isinstance(registration, dict):
        raise TakeRegistrationError("Take registration must be an object.")

    normalized = normalize_video_timeline(timeline)
    shot_id = _required_id(registration.get("shot_id"), "shot_id")
    shot = _find_shot(normalized, shot_id)

    asset_payload = _asset_payload_from_registration(registration)
    used_asset_ids = {
        str(asset.get("asset_id"))
        for asset in normalized.get("assets", [])
        if asset.get("asset_id") is not None
    }
    asset = _create_generated_asset(asset_payload, used_asset_ids)
    normalized.setdefault("assets", []).append(asset)

    take_payload = _take_payload_from_registration(registration)
    take_payload["asset_id"] = asset["asset_id"]
    take = _append_take(shot, take_payload)

    should_accept = (
        registration.get("accept") is True
        or take.get("status") == TAKE_STATUS_ACCEPTED
    )
    if should_accept:
        _accept_take_in_place(
            normalized,
            shot,
            take["take_id"],
            update_clip_instance=registration.get("update_clip_instance") is not False,
        )

    normalized = normalize_video_timeline(normalized)
    return {
        "timeline": normalized,
        "shot_id": shot_id,
        "asset_id": asset["asset_id"],
        "take_id": take["take_id"],
        "accepted": bool(should_accept),
    }


def register_take_for_asset(
    timeline: Any,
    shot_id: str,
    take: dict[str, Any],
    *,
    accept: bool = False,
    update_clip_instance: bool = True,
) -> dict:
    """Register a take for an existing asset record."""

    normalized = normalize_video_timeline(timeline)
    shot_id = _required_id(shot_id, "shot_id")
    shot = _find_shot(normalized, shot_id)
    take_payload = _sanitize_take_payload(take)
    asset_id = _required_id(take_payload.get("asset_id"), "asset_id")
    _find_asset(normalized, asset_id)

    saved_take = _append_take(shot, take_payload)
    should_accept = accept or saved_take.get("status") == TAKE_STATUS_ACCEPTED
    if should_accept:
        _accept_take_in_place(
            normalized,
            shot,
            saved_take["take_id"],
            update_clip_instance=update_clip_instance,
        )

    normalized = normalize_video_timeline(normalized)
    return {
        "timeline": normalized,
        "shot_id": shot_id,
        "asset_id": asset_id,
        "take_id": saved_take["take_id"],
        "accepted": bool(should_accept),
    }


def accept_take(
    timeline: Any,
    shot_id: str,
    take_id: str,
    *,
    update_clip_instance: bool = True,
) -> dict:
    return set_take_status(
        timeline,
        shot_id,
        take_id,
        TAKE_STATUS_ACCEPTED,
        update_clip_instance=update_clip_instance,
    )


def reject_take(timeline: Any, shot_id: str, take_id: str) -> dict:
    return set_take_status(timeline, shot_id, take_id, TAKE_STATUS_REJECTED)


def set_take_status(
    timeline: Any,
    shot_id: str,
    take_id: str,
    status: str,
    *,
    update_clip_instance: bool = True,
) -> dict:
    if status not in TAKE_STATUSES:
        raise TakeRegistrationError(
            "Take status must be Candidate, Accepted, or Rejected."
        )

    normalized = normalize_video_timeline(timeline)
    shot_id = _required_id(shot_id, "shot_id")
    take_id = _required_id(take_id, "take_id")
    shot = _find_shot(normalized, shot_id)
    take = _find_take(shot, take_id)

    if status == TAKE_STATUS_ACCEPTED:
        _accept_take_in_place(
            normalized,
            shot,
            take_id,
            update_clip_instance=update_clip_instance,
        )
    else:
        take["status"] = status
        if shot.get("accepted_take_id") == take_id:
            shot["accepted_take_id"] = None
            _clear_clip_instance_for_take(shot, take)

    normalized = normalize_video_timeline(normalized)
    return {
        "timeline": normalized,
        "shot_id": shot_id,
        "asset_id": take.get("asset_id"),
        "take_id": take_id,
        "status": status,
        "accepted": status == TAKE_STATUS_ACCEPTED,
    }


def _registration_envelope_from_input(registration: dict[str, Any] | str) -> dict[str, Any]:
    payload = _parse_registration_payload(registration)
    payload_type = payload.get("type")
    if payload_type == GENERATED_TAKE_CAPTURE_TYPE:
        return generated_take_capture_to_registration(payload)
    if payload_type not in {None, TAKE_CAPTURE_TYPE}:
        raise TakeRegistrationError(f"Unsupported take registration type: {payload_type}")
    return deepcopy(payload)


def _parse_registration_payload(registration: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(registration, str):
        text = registration.strip()
        if not text:
            raise TakeRegistrationError("Take registration JSON is required.")
        try:
            registration = json.loads(text)
        except Exception as exc:
            raise TakeRegistrationError("Take registration JSON is invalid.") from exc
    if not isinstance(registration, dict):
        raise TakeRegistrationError("Take registration must be an object.")
    return registration


def _asset_payload_from_registration(registration: dict[str, Any]) -> dict[str, Any]:
    payload = _raw_dict(registration.get("asset"))
    for key in (
        "asset_id",
        "type",
        "path",
        "file_path",
        "name",
        "mime_type",
        "size_bytes",
        "metadata",
    ):
        if key in registration and key not in payload:
            payload[key] = registration[key]
    return payload


def _take_payload_from_registration(registration: dict[str, Any]) -> dict[str, Any]:
    payload = _raw_dict(registration.get("take"))
    for key in (
        "take_id",
        "asset_id",
        "status",
        "seed",
        "model_family",
        "model_version",
        "plan_hash",
        "prompt_hash",
        "resolved_loras",
        "metadata",
    ):
        if key in registration and key not in payload:
            payload[key] = registration[key]
    return _sanitize_take_payload(payload)


def _create_generated_asset(
    payload: dict[str, Any],
    used_asset_ids: set[str],
) -> dict[str, Any]:
    path = _safe_string(payload.get("path") or payload.get("file_path"))
    asset_type = payload.get("type")
    if asset_type not in ASSET_TYPES:
        asset_type = ASSET_TYPE_VIDEO
    preferred_id = _safe_string(payload.get("asset_id"))
    asset_id = (
        _unique_id(preferred_id, used_asset_ids)
        if preferred_id
        else _next_numbered_id("asset_generated", used_asset_ids)
    )
    return {
        "asset_id": asset_id,
        "type": asset_type,
        "source_kind": ASSET_SOURCE_GENERATED,
        "path": path,
        "name": _safe_string(payload.get("name")) or _basename(path),
        "mime_type": _safe_string(payload.get("mime_type")),
        "size_bytes": payload.get("size_bytes"),
        "metadata": _strip_embedded_media(_raw_dict(payload.get("metadata"))),
    }


def _sanitize_take_payload(payload: Any) -> dict[str, Any]:
    payload = _raw_dict(payload)
    sanitized = create_default_take()
    used_keys = set(sanitized)
    for key in used_keys:
        if key in payload:
            sanitized[key] = deepcopy(payload[key])
    sanitized.pop("shot_id", None)
    sanitized["metadata"] = _strip_embedded_media(_raw_dict(sanitized.get("metadata")))
    sanitized["resolved_loras"] = _strip_embedded_media(
        sanitized.get("resolved_loras")
    )
    return sanitized


def _append_take(shot: dict[str, Any], take_payload: dict[str, Any]) -> dict[str, Any]:
    takes = shot.setdefault("takes", [])
    used_take_ids = {
        str(take.get("take_id"))
        for take in takes
        if isinstance(take, dict) and take.get("take_id") is not None
    }
    preferred_id = _safe_string(take_payload.get("take_id"))
    take_id = (
        _unique_id(preferred_id, used_take_ids)
        if preferred_id
        else _next_numbered_id("take", used_take_ids)
    )
    saved_take = deepcopy(take_payload)
    saved_take["take_id"] = take_id
    if saved_take.get("status") not in TAKE_STATUSES:
        saved_take["status"] = TAKE_STATUS_CANDIDATE
    takes.append(saved_take)
    return saved_take


def _accept_take_in_place(
    timeline: dict[str, Any],
    shot: dict[str, Any],
    take_id: str,
    *,
    update_clip_instance: bool,
) -> None:
    take = _find_take(shot, take_id)
    asset_id = _required_id(take.get("asset_id"), "asset_id")
    asset = _find_asset(timeline, asset_id)
    for other_take in shot.get("takes", []):
        if not isinstance(other_take, dict):
            continue
        other_take["status"] = (
            TAKE_STATUS_ACCEPTED
            if other_take.get("take_id") == take_id
            else (
                TAKE_STATUS_CANDIDATE
                if other_take.get("status") == TAKE_STATUS_ACCEPTED
                else other_take.get("status", TAKE_STATUS_CANDIDATE)
            )
        )
    shot["accepted_take_id"] = take_id
    if update_clip_instance and asset.get("type") == ASSET_TYPE_VIDEO:
        clip_instance = create_default_clip_instance()
        clip_instance["asset_id"] = asset_id
        shot["clip_instance"] = clip_instance


def _clear_clip_instance_for_take(shot: dict[str, Any], take: dict[str, Any]) -> None:
    clip_instance = shot.get("clip_instance")
    if (
        isinstance(clip_instance, dict)
        and clip_instance.get("asset_id") is not None
        and clip_instance.get("asset_id") == take.get("asset_id")
    ):
        shot["clip_instance"] = None


def _find_shot(timeline: dict[str, Any], shot_id: str) -> dict[str, Any]:
    for shot in timeline.get("sequence", {}).get("shots", []):
        if isinstance(shot, dict) and shot.get("shot_id") == shot_id:
            return shot
    raise TakeRegistrationError(f"Shot '{shot_id}' was not found.")


def _find_take(shot: dict[str, Any], take_id: str) -> dict[str, Any]:
    for take in shot.get("takes", []):
        if isinstance(take, dict) and take.get("take_id") == take_id:
            return take
    shot_id = shot.get("shot_id") or "shot"
    raise TakeRegistrationError(f"Take '{take_id}' was not found in shot '{shot_id}'.")


def _find_asset(timeline: dict[str, Any], asset_id: str) -> dict[str, Any]:
    for asset in timeline.get("assets", []):
        if isinstance(asset, dict) and asset.get("asset_id") == asset_id:
            return asset
    raise TakeRegistrationError(f"Asset '{asset_id}' was not found.")


def _required_id(value: Any, field_name: str) -> str:
    text = _safe_string(value)
    if not text:
        raise TakeRegistrationError(f"{field_name} is required.")
    return text


def _unique_id(base_id: str, used_ids: set[str]) -> str:
    candidate = base_id
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base_id}_{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _next_numbered_id(prefix: str, used_ids: set[str]) -> str:
    index = 1
    while True:
        candidate = f"{prefix}_{index:03d}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
        index += 1


def _strip_embedded_media(value: Any) -> Any:
    if isinstance(value, str):
        return None if _is_embedded_media_string(value) else value
    if isinstance(value, dict):
        return {
            key: _strip_embedded_media(child)
            for key, child in value.items()
            if key not in _EMBEDDED_MEDIA_KEYS
            and not _is_embedded_media_string(child)
        }
    if isinstance(value, list):
        return [
            cleaned
            for child in value
            if not _is_embedded_media_string(child)
            for cleaned in [_strip_embedded_media(child)]
        ]
    return deepcopy(value)


def _is_embedded_media_string(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("data:", "blob:"))


def _raw_dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _safe_string(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    text = str(value)
    return "" if _is_embedded_media_string(text) else text


def _basename(path: str) -> str:
    return PurePath(path).name if path else ""


_EMBEDDED_MEDIA_KEYS = {
    "data",
    "blob",
    "bytes",
    "thumbnail",
    "thumbnail_data",
    "waveform",
    "waveform_data",
}
