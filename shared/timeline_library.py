from __future__ import annotations

import copy
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .privacy import decrypt_state, encrypt_state
from .timeline.normalize import normalize_video_timeline
from .timeline.references import normalize_character_references
from .timeline.validate import validate_video_timeline


LIBRARY_FILE_NAME = "director_library.json"
LIBRARY_SCHEMA_VERSION = "1.0"
LIBRARY_VERSION = 1
TIMELINE_KIND = "timeline"
CHARACTER_KIND = "character"
TIMELINE_LIBRARY_ITEM_TYPE = "TIMELINE_LIBRARY_ITEM"
CHARACTER_LIBRARY_ITEM_TYPE = "CHARACTER_LIBRARY_ITEM"
ENTRY_KINDS = (TIMELINE_KIND, CHARACTER_KIND)

_SENSITIVE_STRING_PREFIXES = ("data:", "blob:")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_BLOCKED_KEYS = {
    "base64",
    "blob",
    "bytes",
    "data",
    "image_data",
    "media_data",
    "preview",
    "preview_data",
    "thumbnail",
    "thumbnail_data",
    "thumb",
    "thumb_data",
    "waveform",
    "waveform_data",
}


class TimelineLibraryError(ValueError):
    """Raised for user-fixable library validation failures."""


def config_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "config"


def library_path(base_dir: str | os.PathLike[str] | None = None) -> Path:
    root = Path(base_dir) if base_dir is not None else config_dir()
    return root / LIBRARY_FILE_NAME


def load_library(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = library_path(base_dir)
    if not path.exists():
        return _empty_library()
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:  # noqa: BLE001 - corrupt user config should be readable.
        raise TimelineLibraryError(f"Could not read Director Library config: {exc}") from exc
    return _normalize_library(payload)


def list_items(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    library = load_library(base_dir)
    return {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "version": LIBRARY_VERSION,
        "timelines": [_public_item(entry) for entry in library["timelines"]],
        "characters": [_public_item(entry) for entry in library["characters"]],
    }


def create_item(
    kind: str,
    payload: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    kind = _normalize_kind(kind)
    metadata = metadata or {}
    now = _utc_now()
    normalized_payload = _normalize_payload(kind, payload)
    summary = _summary_for(kind, normalized_payload)
    entry = _pack_entry(
        kind,
        item_id=str(metadata.get("id") or _new_id(kind)),
        name=_coerce_text(metadata.get("name")) or _default_name(kind, normalized_payload),
        description=_coerce_text(metadata.get("description")),
        tags=_coerce_tags(metadata.get("tags")),
        private=bool(metadata.get("private")),
        payload=normalized_payload,
        summary=summary,
        created_at=now,
        updated_at=now,
        base_dir=base_dir,
    )
    library = load_library(base_dir)
    collection = _collection(library, kind)
    if any(item.get("id") == entry["id"] for item in collection):
        raise TimelineLibraryError(f"Library {kind} already exists: {entry['id']}")
    collection.append(entry)
    _save_library(library, base_dir)
    return _with_payload(entry, base_dir=base_dir)


def replace_item(
    kind: str,
    item_id: str,
    payload: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    kind = _normalize_kind(kind)
    metadata = metadata or {}
    library = load_library(base_dir)
    entry = _find_entry(library, kind, item_id)
    created_at = str(entry.get("created_at") or _utc_now())
    normalized_payload = _normalize_payload(kind, payload)
    summary = _summary_for(kind, normalized_payload)
    replacement = _pack_entry(
        kind,
        item_id=item_id,
        name=_coerce_text(metadata.get("name")) or str(entry.get("name") or _default_name(kind, normalized_payload)),
        description=_coerce_text(metadata.get("description")),
        tags=_coerce_tags(metadata.get("tags", entry.get("tags", []))),
        private=bool(metadata.get("private", entry.get("private", False))),
        payload=normalized_payload,
        summary=summary,
        created_at=created_at,
        updated_at=_utc_now(),
        base_dir=base_dir,
    )
    _replace_entry(library, kind, item_id, replacement)
    _save_library(library, base_dir)
    return _with_payload(replacement, base_dir=base_dir)


def patch_item(
    kind: str,
    item_id: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    kind = _normalize_kind(kind)
    metadata = metadata or {}
    library = load_library(base_dir)
    entry = _find_entry(library, kind, item_id)
    current_payload = _unpack_payload(entry, base_dir=base_dir)
    next_payload = _normalize_payload(kind, payload if payload is not None else current_payload)
    summary = _summary_for(kind, next_payload)
    patched = _pack_entry(
        kind,
        item_id=item_id,
        name=_coerce_text(metadata.get("name", entry.get("name"))) or _default_name(kind, next_payload),
        description=_coerce_text(metadata.get("description", _unpack_description(entry, base_dir=base_dir))),
        tags=_coerce_tags(metadata.get("tags", entry.get("tags", []))),
        private=bool(metadata.get("private", entry.get("private", False))),
        payload=next_payload,
        summary=summary,
        created_at=str(entry.get("created_at") or _utc_now()),
        updated_at=_utc_now(),
        base_dir=base_dir,
    )
    _replace_entry(library, kind, item_id, patched)
    _save_library(library, base_dir)
    return _with_payload(patched, base_dir=base_dir)


def duplicate_item(
    kind: str,
    item_id: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    kind = _normalize_kind(kind)
    metadata = metadata or {}
    library = load_library(base_dir)
    source = _find_entry(library, kind, item_id)
    payload = _unpack_payload(source, base_dir=base_dir)
    description = _coerce_text(metadata.get("description", _unpack_description(source, base_dir=base_dir)))
    now = _utc_now()
    duplicate = _pack_entry(
        kind,
        item_id=str(metadata.get("id") or _new_id(kind)),
        name=_coerce_text(metadata.get("name")) or f"{source.get('name') or _default_name(kind, payload)} Copy",
        description=description,
        tags=_coerce_tags(metadata.get("tags", source.get("tags", []))),
        private=bool(metadata.get("private", source.get("private", False))),
        payload=payload,
        summary=_summary_for(kind, payload),
        created_at=now,
        updated_at=now,
        base_dir=base_dir,
    )
    collection = _collection(library, kind)
    if any(item.get("id") == duplicate["id"] for item in collection):
        raise TimelineLibraryError(f"Library {kind} already exists: {duplicate['id']}")
    collection.append(duplicate)
    _save_library(library, base_dir)
    return _with_payload(duplicate, base_dir=base_dir)


def delete_item(
    kind: str,
    item_id: str,
    *,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    kind = _normalize_kind(kind)
    library = load_library(base_dir)
    collection = _collection(library, kind)
    before = len(collection)
    library[_collection_key(kind)] = [entry for entry in collection if entry.get("id") != item_id]
    if len(library[_collection_key(kind)]) == before:
        raise TimelineLibraryError(f"Library {kind} not found: {item_id}")
    _save_library(library, base_dir)
    return {"id": item_id, "kind": kind}


def use_item(
    kind: str,
    item_id: str,
    *,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    kind = _normalize_kind(kind)
    library = load_library(base_dir)
    entry = _find_entry(library, kind, item_id)
    entry["last_used_at"] = _utc_now()
    _save_library(library, base_dir)
    return _with_payload(entry, base_dir=base_dir)


def _pack_entry(
    kind: str,
    *,
    item_id: str,
    name: str,
    description: str,
    tags: list[str],
    private: bool,
    payload: Mapping[str, Any],
    summary: Mapping[str, Any],
    created_at: str,
    updated_at: str,
    base_dir: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    entry = {
        "id": str(item_id),
        "kind": kind,
        "type": _type_for_kind(kind),
        "name": str(name),
        "tags": tags,
        "private": bool(private),
        "is_private": bool(private),
        "summary": {**dict(summary), "is_private": bool(private)},
        "created_at": created_at,
        "updated_at": updated_at,
    }
    if private:
        entry["encrypted_payload"] = encrypt_state(
            {"payload": payload, "description": description},
            base_dir=base_dir,
        )
    else:
        entry["description"] = description
        entry["payload"] = copy.deepcopy(payload)
    return entry


def _with_payload(entry: Mapping[str, Any], *, base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    item = _public_item(entry)
    payload = _unpack_payload(entry, base_dir=base_dir)
    item["description"] = _unpack_description(entry, base_dir=base_dir)
    item["payload"] = payload
    if item["kind"] == TIMELINE_KIND:
        item["timeline"] = payload
    else:
        item["character"] = payload
    return item


def _public_item(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(entry.get("id") or ""),
        "kind": _normalize_kind(entry.get("kind")),
        "type": str(entry.get("type") or _type_for_kind(entry.get("kind"))),
        "name": str(entry.get("name") or ""),
        "description": "" if entry.get("private") else str(entry.get("description") or ""),
        "tags": _coerce_tags(entry.get("tags")),
        "private": bool(entry.get("private")),
        "is_private": bool(entry.get("private") or entry.get("is_private")),
        "summary": copy.deepcopy(entry.get("summary") if isinstance(entry.get("summary"), dict) else {}),
        "created_at": str(entry.get("created_at") or ""),
        "updated_at": str(entry.get("updated_at") or ""),
        "last_used_at": entry.get("last_used_at") if entry.get("last_used_at") else None,
    }


def _unpack_payload(entry: Mapping[str, Any], *, base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    if entry.get("private"):
        state = decrypt_state(entry.get("encrypted_payload"), base_dir=base_dir)
        payload = state.get("payload")
    else:
        payload = entry.get("payload")
    return _normalize_payload(_normalize_kind(entry.get("kind")), payload if isinstance(payload, Mapping) else {})


def _unpack_description(entry: Mapping[str, Any], *, base_dir: str | os.PathLike[str] | None = None) -> str:
    if entry.get("private"):
        state = decrypt_state(entry.get("encrypted_payload"), base_dir=base_dir)
        return _coerce_text(state.get("description"))
    return _coerce_text(entry.get("description"))


def _normalize_payload(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_embedded_media(copy.deepcopy(dict(payload)))
    if kind == TIMELINE_KIND:
        timeline = normalize_video_timeline(sanitized)
        timeline["validation"] = validate_video_timeline(timeline)
        return timeline
    references = normalize_character_references([sanitized])
    if not references:
        raise TimelineLibraryError("Character entry requires an object payload.")
    return references[0]


def _sanitize_embedded_media(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for child_key, child in value.items():
            normalized_key = _normalize_key(child_key)
            if _is_blocked_key(normalized_key):
                continue
            if _is_suspicious_base64_field(normalized_key, child):
                continue
            sanitized = _sanitize_embedded_media(child, key=normalized_key)
            if sanitized is _REMOVED:
                continue
            cleaned[child_key] = sanitized
        return cleaned
    if isinstance(value, list):
        return [
            sanitized
            for item in value
            if (sanitized := _sanitize_embedded_media(item, key=key)) is not _REMOVED
        ]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(_SENSITIVE_STRING_PREFIXES):
            return _REMOVED
        if _is_suspicious_base64_field(key, text):
            return _REMOVED
    return value


class _Removed:
    pass


_REMOVED = _Removed()


def _is_blocked_key(key: str) -> bool:
    if key in _BLOCKED_KEYS:
        return True
    return (
        key.endswith("_base64")
        or key.endswith("base64")
        or key.endswith("_bytes")
        or key.endswith("_blob")
        or key.endswith("_thumbnail")
        or key.endswith("_waveform")
    )


def _is_suspicious_base64_field(key: str, value: Any) -> bool:
    if not isinstance(value, str) or len(value) < 256:
        return False
    if not any(token in key for token in ("image", "video", "audio", "media", "thumbnail", "waveform", "preview")):
        return False
    return bool(_BASE64_RE.fullmatch(value.strip()))


def _summary_for(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if kind == TIMELINE_KIND:
        validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
        sections = payload.get("director_track", {}).get("sections", [])
        audio_tracks = payload.get("audio_tracks", [])
        audio_clip_count = sum(len(track.get("clips", [])) for track in audio_tracks if isinstance(track, dict))
        project = payload.get("project", {}) if isinstance(payload.get("project"), dict) else {}
        metadata = project.get("metadata", {}) if isinstance(project.get("metadata"), dict) else {}
        references = metadata.get("character_references", [])
        return {
            "duration_seconds": _safe_float(project.get("duration_seconds"), 0.0),
            "frame_rate": _safe_float(project.get("frame_rate"), 0.0),
            "aspect_ratio": str(project.get("aspect_ratio") or ""),
            "orientation": str(project.get("orientation") or ""),
            "quality_preset": str(project.get("quality_preset") or ""),
            "section_count": len(sections) if isinstance(sections, list) else 0,
            "asset_count": len(payload.get("assets", [])) if isinstance(payload.get("assets"), list) else 0,
            "audio_clip_count": audio_clip_count,
            "character_count": len(references) if isinstance(references, list) else 0,
            "character_reference_count": len(references) if isinstance(references, list) else 0,
            "error_count": len(validation.get("errors", [])) if isinstance(validation.get("errors"), list) else 0,
            "warning_count": len(validation.get("warnings", [])) if isinstance(validation.get("warnings"), list) else 0,
        }
    image = payload.get("image") if isinstance(payload.get("image"), dict) else None
    return {
        "label": str(payload.get("label") or ""),
        "enabled": payload.get("enabled") is not False,
        "strength": _safe_float(payload.get("strength"), 1.0),
        "has_image": bool(image and image.get("path")),
    }


def _empty_library() -> dict[str, Any]:
    return {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "version": LIBRARY_VERSION,
        "timelines": [],
        "characters": [],
    }


def _normalize_library(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_library()
    library = _empty_library()
    for key in ("timelines", "characters"):
        items = payload.get(key)
        if isinstance(items, list):
            library[key] = [dict(item) for item in items if isinstance(item, dict)]
    return library


def _save_library(library: Mapping[str, Any], base_dir: str | os.PathLike[str] | None = None) -> None:
    payload = _normalize_library(library)
    path = library_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    tmp_path.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _collection(library: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    return library[_collection_key(kind)]  # type: ignore[index]


def _collection_key(kind: str) -> str:
    return "timelines" if kind == TIMELINE_KIND else "characters"


def _find_entry(library: Mapping[str, Any], kind: str, item_id: str) -> dict[str, Any]:
    for entry in _collection(library, kind):
        if entry.get("id") == item_id:
            return entry
    raise TimelineLibraryError(f"Library {kind} not found: {item_id}")


def _replace_entry(library: dict[str, Any], kind: str, item_id: str, replacement: dict[str, Any]) -> None:
    collection = _collection(library, kind)
    for index, entry in enumerate(collection):
        if entry.get("id") == item_id:
            collection[index] = replacement
            return
    raise TimelineLibraryError(f"Library {kind} not found: {item_id}")


def _normalize_kind(kind: Any) -> str:
    value = str(kind or "").strip().lower()
    if value in {"timeline", "timelines", TIMELINE_LIBRARY_ITEM_TYPE.lower()}:
        return TIMELINE_KIND
    if value in {"character", "characters", CHARACTER_LIBRARY_ITEM_TYPE.lower()}:
        return CHARACTER_KIND
    raise TimelineLibraryError("Library item kind must be 'timeline' or 'character'.")


def _type_for_kind(kind: Any) -> str:
    return TIMELINE_LIBRARY_ITEM_TYPE if _normalize_kind(kind) == TIMELINE_KIND else CHARACTER_LIBRARY_ITEM_TYPE


def _default_name(kind: str, payload: Mapping[str, Any]) -> str:
    if kind == TIMELINE_KIND:
        return "Untitled Timeline"
    return str(payload.get("label") or "Untitled Character")


def _new_id(kind: str) -> str:
    return f"{kind}_{secrets.token_hex(8)}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        tag = str(item or "").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


__all__ = [
    "CHARACTER_KIND",
    "CHARACTER_LIBRARY_ITEM_TYPE",
    "ENTRY_KINDS",
    "LIBRARY_FILE_NAME",
    "LIBRARY_SCHEMA_VERSION",
    "TIMELINE_LIBRARY_ITEM_TYPE",
    "TIMELINE_KIND",
    "TimelineLibraryError",
    "config_dir",
    "create_item",
    "delete_item",
    "duplicate_item",
    "library_path",
    "list_items",
    "load_library",
    "patch_item",
    "replace_item",
    "use_item",
]
