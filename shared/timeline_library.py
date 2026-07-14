from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .contracts.video_timeline import (
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    SECTION_TYPE_IMAGE,
    SECTION_TYPE_VIDEO,
)
from .timeline.normalize import normalize_video_timeline
from .timeline.project_storage import generate_project_id, project_directory_name
from .timeline.references import normalize_character_references
from .timeline.validate import validate_video_timeline


LIBRARY_FILE_NAME = "director_library.json"
LIBRARY_SCHEMA_VERSION = "1.0"
LIBRARY_VERSION = 1
PROJECT_KIND = "project"
CHARACTER_KIND = "character"
PROJECT_LIBRARY_ITEM_TYPE = "PROJECT_LIBRARY_ITEM"
CHARACTER_LIBRARY_ITEM_TYPE = "CHARACTER_LIBRARY_ITEM"
ENTRY_KINDS = (PROJECT_KIND, CHARACTER_KIND)
PREVIEW_ASSET_LIMIT = 3
PRIVATE_PROJECT_NAME = "Private Project"
DEFAULT_PROJECT_LIBRARY_NAME = "Untitled Project"

_SENSITIVE_STRING_PREFIXES = ("data:", "blob:")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_PREVIEW_ASSET_KEYS = {
    "asset_id",
    "duration_seconds",
    "file_path",
    "frame_rate",
    "height",
    "id",
    "media_type",
    "mime_type",
    "name",
    "path",
    "size_bytes",
    "source_kind",
    "type",
    "width",
}
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


def _normalize_payload(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_embedded_media(copy.deepcopy(dict(payload)))
    if kind == PROJECT_KIND:
        timeline = normalize_video_timeline(sanitized)
        _keep_referenced_assets_only(timeline)
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


def preview_assets_for_timeline(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    assets_by_id = _assets_by_id(payload)
    preview_assets: list[dict[str, Any]] = []
    for reference, asset_type in _timeline_preview_media_references(payload):
        preview_asset = _preview_shell_for_media_reference(reference, assets_by_id, asset_type)
        if not preview_asset:
            continue
        preview_assets.append(preview_asset)
        if len(preview_assets) >= PREVIEW_ASSET_LIMIT:
            break
    return preview_assets


def _keep_referenced_assets_only(timeline: dict[str, Any]) -> None:
    assets = timeline.get("assets")
    if not isinstance(assets, list):
        timeline["assets"] = []
        return
    referenced_asset_ids = _referenced_asset_ids_for_timeline(timeline)
    timeline["assets"] = [
        asset
        for asset in assets
        if isinstance(asset, Mapping) and str(asset.get("asset_id") or "") in referenced_asset_ids
    ]


def _referenced_asset_ids_for_timeline(timeline: Mapping[str, Any]) -> set[str]:
    asset_ids: set[str] = set()
    for reference, _asset_type in _timeline_media_references(timeline):
        asset_id = _asset_id_from_reference(reference)
        if asset_id:
            asset_ids.add(asset_id)
    return asset_ids


def _timeline_media_references(timeline: Mapping[str, Any]) -> list[tuple[Any, str | None]]:
    references: list[tuple[Any, str | None]] = []
    references.extend(_timeline_preview_media_references(timeline))
    audio_tracks = timeline.get("audio_tracks")
    for track in audio_tracks if isinstance(audio_tracks, list) else []:
        if not isinstance(track, Mapping):
            continue
        clips = track.get("clips")
        for clip in clips if isinstance(clips, list) else []:
            if isinstance(clip, Mapping):
                references.append((clip.get("audio"), None))
    project = _mapping_child(timeline, "project")
    metadata = _mapping_child(project, "metadata")
    character_references = metadata.get("character_references")
    for reference in character_references if isinstance(character_references, list) else []:
        if isinstance(reference, Mapping):
            references.append((reference.get("image"), ASSET_TYPE_IMAGE))
    sequence = _mapping_child(timeline, "sequence")
    shots = sequence.get("shots")
    for shot in shots if isinstance(shots, list) else []:
        if not isinstance(shot, Mapping):
            continue
        references.append((shot.get("clip_instance"), ASSET_TYPE_VIDEO))
        takes = shot.get("takes")
        for take in takes if isinstance(takes, list) else []:
            if not isinstance(take, Mapping):
                continue
            references.append((take, ASSET_TYPE_VIDEO))
            references.append((take.get("clip_instance"), ASSET_TYPE_VIDEO))
    return references


def _timeline_preview_media_references(timeline: Mapping[str, Any]) -> list[tuple[Any, str]]:
    director_track = _mapping_child(timeline, "director_track")
    sections = director_track.get("sections")
    references: list[tuple[Any, str]] = []
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, Mapping):
            continue
        if section.get("type") == SECTION_TYPE_IMAGE:
            references.append((section.get("image"), ASSET_TYPE_IMAGE))
        elif section.get("type") == SECTION_TYPE_VIDEO:
            references.append((section.get("video"), ASSET_TYPE_VIDEO))
    return references


def _preview_shell_for_media_reference(
    reference: Any,
    assets_by_id: Mapping[str, Mapping[str, Any]],
    asset_type: str,
) -> dict[str, Any] | None:
    asset_id = _asset_id_from_reference(reference)
    if asset_id:
        asset = assets_by_id.get(asset_id)
        if asset:
            return _preview_asset_shell(asset)
        return None
    direct_asset = _direct_reference_asset(reference, asset_type)
    return _preview_asset_shell(direct_asset)


def _assets_by_id(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    assets = payload.get("assets")
    by_id: dict[str, Mapping[str, Any]] = {}
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, Mapping):
            continue
        asset_id = str(asset.get("asset_id") or "")
        if asset_id:
            by_id[asset_id] = asset
    return by_id


def _asset_id_from_reference(reference: Any) -> str:
    if not isinstance(reference, Mapping):
        return ""
    return str(reference.get("asset_id") or "").strip()


def _direct_reference_asset(reference: Any, asset_type: str) -> dict[str, Any] | None:
    if isinstance(reference, Mapping):
        path = reference.get("path") or reference.get("file_path")
        if not path:
            return None
        asset = dict(reference)
        asset["type"] = asset_type
        return asset
    if isinstance(reference, str) and reference.strip():
        return {"type": asset_type, "path": reference.strip()}
    return None


def _mapping_child(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    child = parent.get(key)
    return child if isinstance(child, Mapping) else {}


def preview_character_shell(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    shell: dict[str, Any] = {}
    for key in ("id", "label", "kind", "enabled", "description", "strength"):
        value = payload.get(key)
        if isinstance(value, str):
            shell[key] = _safe_preview_text(value, key)
        elif value is None or isinstance(value, (bool, int, float)):
            shell[key] = value
    image = _preview_asset_shell(payload.get("image"))
    if image:
        shell["image"] = image
    return shell


def _preview_asset_shell(asset: Any) -> dict[str, Any] | None:
    if not isinstance(asset, Mapping):
        return None
    asset_type = str(asset.get("type") or "")
    if asset_type not in {ASSET_TYPE_IMAGE, ASSET_TYPE_VIDEO}:
        return None
    path = _safe_preview_text(asset.get("path") or asset.get("file_path"), "path")
    if not path:
        return None
    shell: dict[str, Any] = {}
    for key, value in asset.items():
        normalized_key = _normalize_key(key)
        if normalized_key not in _PREVIEW_ASSET_KEYS or _is_blocked_key(normalized_key):
            continue
        if isinstance(value, str):
            text = _safe_preview_text(value, normalized_key)
            if text:
                shell[str(key)] = text
            continue
        if value is None or isinstance(value, (bool, int, float)):
            shell[str(key)] = value
    shell["type"] = asset_type
    shell["path"] = path
    if not shell.get("name"):
        shell["name"] = _basename(path)
    return shell


def _safe_preview_text(value: Any, key: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(_SENSITIVE_STRING_PREFIXES):
        return ""
    if len(text) >= 256 and _BASE64_RE.fullmatch(text):
        return ""
    if _is_suspicious_base64_field(key, text):
        return ""
    return text


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
    if kind == PROJECT_KIND:
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


def _normalize_kind(kind: Any) -> str:
    value = str(kind or "").strip().lower()
    if value in {"project", "projects", PROJECT_LIBRARY_ITEM_TYPE.lower(), "timeline", "timelines", "timeline_library_item"}:
        return PROJECT_KIND
    if value in {"character", "characters", CHARACTER_LIBRARY_ITEM_TYPE.lower()}:
        return CHARACTER_KIND
    raise TimelineLibraryError("Library item kind must be 'project' or 'character'.")


def _type_for_kind(kind: Any) -> str:
    return PROJECT_LIBRARY_ITEM_TYPE if _normalize_kind(kind) == PROJECT_KIND else CHARACTER_LIBRARY_ITEM_TYPE


def _default_name(kind: str, payload: Mapping[str, Any]) -> str:
    if kind == PROJECT_KIND:
        project = payload.get("project") if isinstance(payload.get("project"), Mapping) else {}
        identity = project.get("identity") if isinstance(project.get("identity"), Mapping) else {}
        return _coerce_text(identity.get("name")) or DEFAULT_PROJECT_LIBRARY_NAME
    return str(payload.get("label") or "Untitled Character")


def _entry_name(
    kind: str,
    requested_name: Any,
    existing_entry: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
    *,
    base_dir: str | os.PathLike[str] | None,
) -> str:
    requested = _coerce_text(requested_name)
    if requested:
        return requested
    if existing_entry is not None:
        existing = _unpack_name(existing_entry, base_dir=base_dir)
        if existing and existing != PRIVATE_PROJECT_NAME:
            return existing
    return _default_name(kind, payload)


def _stamp_project_payload_name(kind: str, payload: dict[str, Any], name: str) -> None:
    if kind != PROJECT_KIND:
        return
    project = payload.setdefault("project", {})
    if not isinstance(project, dict):
        return
    identity = project.setdefault("identity", {})
    if not isinstance(identity, dict):
        project["identity"] = identity = {}
    identity["name"] = _coerce_text(name) or DEFAULT_PROJECT_LIBRARY_NAME


def _fork_project_payload_identity(kind: str, payload: dict[str, Any], name: str) -> None:
    if kind != PROJECT_KIND:
        return
    project = payload.setdefault("project", {})
    if not isinstance(project, dict):
        return
    new_project_id = generate_project_id()
    new_name = _coerce_text(name) or DEFAULT_PROJECT_LIBRARY_NAME
    project["identity"] = {
        "project_id": new_project_id,
        "name": new_name,
    }
    storage = project.get("storage")
    if not isinstance(storage, dict):
        storage = {}
    project["storage"] = {
        **storage,
        "project_directory_name": project_directory_name(new_name, new_project_id),
    }


def _basename(path: Any) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").split("/")[-1]


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


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
    "PROJECT_LIBRARY_ITEM_TYPE",
    "PROJECT_KIND",
    "TimelineLibraryError",
    "config_dir",
    "library_path",
    "preview_assets_for_timeline",
    "preview_character_shell",
]
