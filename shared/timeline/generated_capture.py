from __future__ import annotations

from copy import deepcopy
from pathlib import PurePath
from typing import Any

from ..contracts.video_timeline import (
    ASSET_SOURCE_GENERATED,
    ASSET_TYPE_VIDEO,
    ASSET_TYPES,
    TAKE_STATUS_CANDIDATE,
    TAKE_STATUSES,
)
from .take_capture import TAKE_CAPTURE_SCHEMA_VERSION, TAKE_CAPTURE_TYPE


GENERATED_TAKE_CAPTURE_SCHEMA_VERSION = 1
GENERATED_TAKE_CAPTURE_TYPE = "HELTO_GENERATED_TAKE_CAPTURE"


class GeneratedCaptureError(ValueError):
    """Raised when generated media capture metadata is unsafe or incomplete."""


def build_generated_take_capture_sidecar(
    registration: dict[str, Any],
    *,
    media: dict[str, Any] | None = None,
    privacy_mode: bool | None = None,
) -> dict[str, Any]:
    """Build a sanitized sidecar payload for a generated take capture.

    The sidecar is intended to sit next to generated media on disk. It carries
    enough metadata to register the media as a generated asset/take later, but
    never stores media payloads or clear private filenames/LoRA names in privacy
    mode.
    """

    if not isinstance(registration, dict):
        raise GeneratedCaptureError("Take registration metadata must be an object.")
    media_payload = _raw_dict(media)
    _reject_embedded_payload(registration)
    _reject_embedded_payload(media_payload)

    privacy = (
        bool(privacy_mode)
        if privacy_mode is not None
        else _registration_privacy_mode(registration)
    )
    normalized_registration = _normalize_registration(
        registration,
        media_payload=media_payload,
        privacy_mode=privacy,
    )
    normalized_media = _normalize_media(
        media_payload,
        normalized_registration,
        privacy_mode=privacy,
    )
    privacy_payload = _privacy_payload(
        privacy,
        normalized_registration,
        normalized_media,
    )
    normalized_registration["privacy"] = privacy_payload

    return {
        "schema_version": GENERATED_TAKE_CAPTURE_SCHEMA_VERSION,
        "type": GENERATED_TAKE_CAPTURE_TYPE,
        "media": normalized_media,
        "registration": normalized_registration,
        "privacy": privacy_payload,
    }


def normalize_generated_take_capture_sidecar(capture: dict[str, Any]) -> dict[str, Any]:
    """Normalize a generated take sidecar loaded from disk."""

    if not isinstance(capture, dict):
        raise GeneratedCaptureError("Generated take capture sidecar must be an object.")
    if capture.get("type") not in {None, GENERATED_TAKE_CAPTURE_TYPE}:
        raise GeneratedCaptureError("Generated take capture sidecar has an unsupported type.")
    registration = capture.get("registration")
    if not isinstance(registration, dict):
        raise GeneratedCaptureError("Generated take capture sidecar is missing registration metadata.")
    privacy_mode = _capture_privacy_mode(capture)
    return build_generated_take_capture_sidecar(
        registration,
        media=_raw_dict(capture.get("media")),
        privacy_mode=privacy_mode,
    )


def generated_take_capture_to_registration(
    capture: dict[str, Any],
    *,
    path: str | None = None,
    file_path: str | None = None,
    accept: bool | None = None,
    update_clip_instance: bool | None = None,
) -> dict[str, Any]:
    """Return a register_generated_take(...) envelope from a sidecar payload."""

    normalized = normalize_generated_take_capture_sidecar(capture)
    registration = deepcopy(normalized["registration"])
    media = normalized["media"]
    asset = _raw_dict(registration.get("asset"))

    path_value = _safe_string(path or file_path or media.get("path") or media.get("file_path"))
    if path_value:
        asset["path"] = path_value
    media_name = _safe_string(asset.get("name") or media.get("name") or _basename(path_value))
    if media_name:
        asset["name"] = media_name
    if media.get("mime_type") and not asset.get("mime_type"):
        asset["mime_type"] = media.get("mime_type")
    if media.get("size_bytes") is not None and asset.get("size_bytes") is None:
        asset["size_bytes"] = media.get("size_bytes")

    asset_metadata = _raw_dict(asset.get("metadata"))
    asset_metadata.update(_asset_metadata_from_media(media))
    asset["metadata"] = asset_metadata
    registration["asset"] = asset

    if accept is not None:
        registration["accept"] = bool(accept)
    if update_clip_instance is not None:
        registration["update_clip_instance"] = bool(update_clip_instance)
    return registration


def _normalize_registration(
    registration: dict[str, Any],
    *,
    media_payload: dict[str, Any],
    privacy_mode: bool,
) -> dict[str, Any]:
    shot_id = _required_string(registration.get("shot_id"), "shot_id")
    shot_ids = [
        str(item)
        for item in registration.get("shot_ids") or [shot_id]
        if item is not None
    ]
    if shot_id not in shot_ids:
        shot_ids.insert(0, shot_id)

    take = _normalize_take(_raw_dict(registration.get("take")), privacy_mode=privacy_mode)
    asset = _normalize_registration_asset(
        _raw_dict(registration.get("asset")),
        shot_id=shot_id,
        take=take,
        media_payload=media_payload,
        privacy_mode=privacy_mode,
    )

    normalized = {
        "schema_version": TAKE_CAPTURE_SCHEMA_VERSION,
        "type": TAKE_CAPTURE_TYPE,
        "shot_id": shot_id,
        "shot_ids": shot_ids,
        "expected_asset_type": str(registration.get("expected_asset_type") or asset["type"]),
        "suggested_asset_name": asset.get("name") or "",
        "plan_hash": _safe_string(registration.get("plan_hash") or take.get("plan_hash")),
        "prompt_hash": _safe_string(registration.get("prompt_hash") or take.get("prompt_hash")),
        "asset": asset,
        "take": take,
    }
    for key in ("shot_context", "segment_context", "model_specific", "project_context"):
        if key in registration:
            normalized[key] = _sanitize_value(registration.get(key), privacy_mode=privacy_mode)
    return normalized


def _normalize_registration_asset(
    asset: dict[str, Any],
    *,
    shot_id: str,
    take: dict[str, Any],
    media_payload: dict[str, Any],
    privacy_mode: bool,
) -> dict[str, Any]:
    asset_type = asset.get("type") or media_payload.get("type") or ASSET_TYPE_VIDEO
    if asset_type not in ASSET_TYPES:
        asset_type = ASSET_TYPE_VIDEO
    media_metadata = _asset_metadata_from_media(media_payload)
    asset_metadata = _sanitize_metadata(asset.get("metadata"), privacy_mode=privacy_mode)
    asset_metadata.update(
        {
            "shot_id": shot_id,
            "take_id": take.get("take_id"),
            "model_family": take.get("model_family"),
            "model_version": take.get("model_version"),
            "plan_hash": take.get("plan_hash"),
            "prompt_hash": take.get("prompt_hash"),
            **media_metadata,
        }
    )
    asset_metadata = {
        key: value
        for key, value in asset_metadata.items()
        if value not in (None, "")
    }
    name = _safe_string(asset.get("name") or media_payload.get("name") or media_payload.get("filename"))
    if privacy_mode:
        name = _private_asset_name(asset_type)
    return {
        "type": asset_type,
        "source_kind": ASSET_SOURCE_GENERATED,
        "name": name,
        "mime_type": _safe_string(asset.get("mime_type") or media_payload.get("mime_type")),
        "size_bytes": _safe_number(asset.get("size_bytes") if asset.get("size_bytes") is not None else media_payload.get("size_bytes")),
        "metadata": asset_metadata,
    }


def _normalize_media(
    media: dict[str, Any],
    registration: dict[str, Any],
    *,
    privacy_mode: bool,
) -> dict[str, Any]:
    asset = _raw_dict(registration.get("asset"))
    media_type = media.get("type") or asset.get("type") or ASSET_TYPE_VIDEO
    if media_type not in ASSET_TYPES:
        media_type = ASSET_TYPE_VIDEO
    filename = _safe_string(media.get("filename") or _basename(media.get("path") or media.get("file_path")))
    name = _safe_string(media.get("name") or filename or asset.get("name"))
    if privacy_mode:
        filename = _private_asset_filename(media_type, filename)
        name = _private_asset_name(media_type)
    normalized = {
        "type": media_type,
        "source_kind": ASSET_SOURCE_GENERATED,
        "folder": _safe_string(media.get("folder") or "output"),
        "subfolder": _safe_string(media.get("subfolder")),
        "filename": filename,
        "name": name,
        "mime_type": _safe_string(media.get("mime_type") or asset.get("mime_type")),
        "size_bytes": _safe_number(media.get("size_bytes") if media.get("size_bytes") is not None else asset.get("size_bytes")),
    }
    normalized.update(_asset_metadata_from_media(media))
    return {
        key: value
        for key, value in normalized.items()
        if value not in (None, "")
    }


def _normalize_take(take: dict[str, Any], *, privacy_mode: bool) -> dict[str, Any]:
    normalized = {
        "take_id": _safe_string(take.get("take_id")),
        "status": take.get("status") if take.get("status") in TAKE_STATUSES else TAKE_STATUS_CANDIDATE,
        "seed": take.get("seed"),
        "model_family": _safe_string(take.get("model_family")),
        "model_version": _safe_string(take.get("model_version")),
        "plan_hash": _safe_string(take.get("plan_hash")),
        "prompt_hash": _safe_string(take.get("prompt_hash")),
        "resolved_loras": _sanitize_resolved_loras(take.get("resolved_loras"), privacy_mode=privacy_mode),
        "metadata": _sanitize_metadata(take.get("metadata"), privacy_mode=privacy_mode),
    }
    return {
        key: value
        for key, value in normalized.items()
        if value not in (None, "")
    }


def _asset_metadata_from_media(media: dict[str, Any]) -> dict[str, Any]:
    metadata = _raw_dict(media.get("metadata"))
    output = {
        key: _sanitize_value(media.get(key), privacy_mode=False)
        for key in (
            "frame_rate",
            "frame_count",
            "duration_seconds",
            "width",
            "height",
            "bit_depth",
        )
        if key in media
    }
    output.update(
        {
            key: _sanitize_value(metadata.get(key), privacy_mode=False)
            for key in (
                "frame_rate",
                "frame_count",
                "duration_seconds",
                "width",
                "height",
                "bit_depth",
            )
            if key in metadata and key not in output
        }
    )
    return output


def _privacy_payload(
    privacy_mode: bool,
    registration: dict[str, Any],
    media: dict[str, Any],
) -> dict[str, Any]:
    redacted = []
    if privacy_mode:
        redacted.extend(
            [
                "media.filename",
                "media.name",
                "registration.asset.name",
                "registration.take.resolved_loras.targets.*.name",
            ]
        )
    privacy = _raw_dict(registration.get("privacy"))
    for field in privacy.get("redacted_fields") or []:
        if field not in redacted:
            redacted.append(str(field))
    if media.get("filename") == _private_asset_filename(media.get("type"), None):
        if "media.filename" not in redacted:
            redacted.append("media.filename")
    return {
        "privacy_mode": bool(privacy_mode),
        "redacted_fields": redacted,
    }


def _sanitize_resolved_loras(value: Any, *, privacy_mode: bool) -> Any:
    sanitized = _sanitize_value(value, privacy_mode=privacy_mode)
    if not privacy_mode or not isinstance(sanitized, dict):
        return sanitized
    targets = sanitized.get("targets") if isinstance(sanitized.get("targets"), dict) else {}
    for rows in targets.values():
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            if name:
                row["name_hash"] = _short_hash(name)
                row["name"] = f"lora_{index + 1:03d}"
    return sanitized


def _sanitize_metadata(value: Any, *, privacy_mode: bool) -> dict[str, Any]:
    sanitized = _sanitize_value(value, privacy_mode=privacy_mode)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_value(value: Any, *, privacy_mode: bool) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_value(child, privacy_mode=privacy_mode)
            for key, child in value.items()
            if str(key) not in _DROP_KEYS
        }
    if isinstance(value, list | tuple):
        return [
            _sanitize_value(item, privacy_mode=privacy_mode)
            for item in value
        ]
    if _is_tensor_like(value):
        return {"tensor_shape": _tensor_shape(value)}
    if isinstance(value, bytes | bytearray | memoryview):
        return None
    if isinstance(value, str):
        if _is_embedded_media_string(value):
            return None
        if privacy_mode and _looks_like_path(value):
            return "private"
        return value
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)


def _reject_embedded_payload(value: Any, path: str = "capture") -> None:
    if isinstance(value, bytes | bytearray | memoryview):
        raise GeneratedCaptureError(f"{path} contains embedded media bytes.")
    if _is_tensor_like(value):
        raise GeneratedCaptureError(f"{path} contains tensor media payloads.")
    if isinstance(value, str):
        if _is_embedded_media_string(value):
            raise GeneratedCaptureError(f"{path} contains an embedded media string.")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if key_text in _EMBEDDED_PAYLOAD_KEYS:
                raise GeneratedCaptureError(f"{child_path} contains embedded media payload data.")
            _reject_embedded_payload(child, child_path)
        return
    if isinstance(value, list | tuple):
        for index, child in enumerate(value):
            _reject_embedded_payload(child, f"{path}[{index}]")


def _registration_privacy_mode(registration: dict[str, Any]) -> bool:
    privacy = _raw_dict(registration.get("privacy"))
    if "privacy_mode" in privacy:
        return bool(privacy.get("privacy_mode"))
    take_metadata = _raw_dict(_raw_dict(registration.get("take")).get("metadata"))
    metadata_privacy = _raw_dict(take_metadata.get("privacy"))
    return bool(metadata_privacy.get("privacy_mode"))


def _capture_privacy_mode(capture: dict[str, Any]) -> bool:
    privacy = _raw_dict(capture.get("privacy"))
    if "privacy_mode" in privacy:
        return bool(privacy.get("privacy_mode"))
    return _registration_privacy_mode(_raw_dict(capture.get("registration")))


def _private_asset_name(asset_type: Any) -> str:
    return f"Private Generated {_safe_string(asset_type) or 'Asset'}"


def _private_asset_filename(asset_type: Any, filename: Any) -> str:
    suffix = PurePath(str(filename or "")).suffix
    if not suffix and asset_type == ASSET_TYPE_VIDEO:
        suffix = ".mp4"
    return f"generated_private{suffix}"


def _required_string(value: Any, field_name: str) -> str:
    text = _safe_string(value)
    if not text:
        raise GeneratedCaptureError(f"{field_name} is required.")
    return text


def _safe_string(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if _is_embedded_media_string(text) else text


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None


def _basename(path: Any) -> str:
    text = _safe_string(path)
    return PurePath(text).name if text else ""


def _raw_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value


def _is_embedded_media_string(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("data:", "blob:"))


def _is_tensor_like(value: Any) -> bool:
    return hasattr(value, "shape") and hasattr(value, "detach")


def _tensor_shape(value: Any) -> list[int]:
    try:
        return [int(dim) for dim in value.shape]
    except Exception:
        return []


def _short_hash(value: Any) -> str:
    import hashlib

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


_DROP_KEYS = {
    "path",
    "file_path",
    "absolute_path",
    "data",
    "blob",
    "bytes",
    "thumbnail",
    "thumbnail_data",
    "thumbnails",
    "waveform",
    "waveform_data",
    "waveforms",
    "image_data",
    "video_data",
    "audio_data",
}

_EMBEDDED_PAYLOAD_KEYS = {
    "data",
    "blob",
    "bytes",
    "thumbnail",
    "thumbnail_data",
    "thumbnails",
    "waveform",
    "waveform_data",
    "waveforms",
    "image_data",
    "video_data",
    "audio_data",
    "media_bytes",
}
