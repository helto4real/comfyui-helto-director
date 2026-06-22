from __future__ import annotations

import json
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
import folder_paths
from PIL import Image

from .media_cache import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    make_thumbnail,
)
from .timeline.generated_capture import (
    GeneratedCaptureError,
    build_generated_take_capture_sidecar,
    normalize_generated_take_capture_sidecar,
)
from .timeline.project_storage import (
    resolve_project_take_directory,
    resolved_project_storage_summary,
)


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@dataclass(frozen=True)
class MediaFolder:
    alias: str
    path: str
    enabled: bool = True


MEDIA_TYPES = {
    "image": {
        "extensions": IMAGE_EXTENSIONS,
        "items_key": "images",
        "count_key": "image_count",
        "config_name": "timeline_image_folders.json",
    },
    "video": {
        "extensions": VIDEO_EXTENSIONS,
        "items_key": "videos",
        "count_key": "video_count",
        "config_name": "timeline_video_folders.json",
    },
    "audio": {
        "extensions": AUDIO_EXTENSIONS,
        "items_key": "audios",
        "count_key": "audio_count",
        "config_name": "timeline_audio_folders.json",
    },
}


def normalize_media_type(media_type: str) -> str:
    key = str(media_type or "").strip().lower()
    if key not in MEDIA_TYPES:
        raise ValueError(f"Unsupported media type: {media_type}")
    return key


def media_definition(media_type: str) -> dict[str, Any]:
    return MEDIA_TYPES[normalize_media_type(media_type)]


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def config_file(media_type: str) -> Path:
    ensure_config_dir()
    return CONFIG_DIR / media_definition(media_type)["config_name"]


def default_folder() -> MediaFolder:
    return MediaFolder(
        alias="input",
        path=os.path.normpath(folder_paths.get_input_directory()),
        enabled=True,
    )


def default_folders(media_type: str) -> list[MediaFolder]:
    defaults = [default_folder()]
    if normalize_media_type(media_type) == "video":
        output = MediaFolder(
            alias="output",
            path=os.path.normpath(folder_paths.get_output_directory()),
            enabled=True,
        )
        if os.path.normcase(os.path.abspath(output.path)) != os.path.normcase(os.path.abspath(defaults[0].path)):
            defaults.append(output)
    return defaults


def safe_alias(alias: str) -> str:
    value = str(alias or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", value):
        raise ValueError(
            "Alias must be 1-80 characters using letters, numbers, spaces, dot, underscore, or dash."
        )
    return value


def folder_display_name(path: str) -> str:
    normalized = os.path.normpath(os.path.expanduser(str(path or "")))
    name = Path(normalized).name
    return name or normalized or "folder"


def folder_alias_from_path(path: str, existing_aliases: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_. -]+", "_", folder_display_name(path)).strip(" ._-") or "folder"
    base = base[:72].strip(" ._-") or "folder"
    alias = safe_alias(base)
    if alias not in existing_aliases:
        return alias

    for index in range(2, 1000):
        suffix = f" {index}"
        candidate = safe_alias(f"{base[:80 - len(suffix)].strip(' ._-')}{suffix}")
        if candidate not in existing_aliases:
            return candidate
    raise ValueError(f"Folder alias already exists: {alias}")


def load_folders(media_type: str) -> list[MediaFolder]:
    file_path = config_file(media_type)
    defaults = default_folders(media_type)
    if not file_path.exists():
        return defaults

    try:
        data = json.loads(file_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return defaults

    folders: list[MediaFolder] = []
    seen: set[str] = set()
    for entry in data.get("folders", []):
        try:
            alias = safe_alias(entry.get("alias"))
        except ValueError:
            continue
        path = os.path.normpath(os.path.expanduser(str(entry.get("path", ""))))
        if alias in seen or not path:
            continue
        folders.append(MediaFolder(alias=alias, path=path, enabled=bool(entry.get("enabled", True))))
        seen.add(alias)

    for default in reversed(defaults):
        if default.alias not in seen:
            folders.insert(0, default)
    return folders


def save_folders(media_type: str, folders: list[MediaFolder]) -> None:
    file_path = config_file(media_type)
    payload = {
        "version": 1,
        "folders": [
            {
                "alias": folder.alias,
                "path": os.path.normpath(folder.path),
                "enabled": folder.enabled,
            }
            for folder in folders
        ],
    }
    file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def folder_by_alias(media_type: str, alias: str) -> MediaFolder:
    for folder in load_folders(media_type):
        if folder.alias == alias:
            return folder
    raise ValueError(f"Unknown folder alias: {alias}")


def add_folder(media_type: str, alias: str, path: str) -> list[MediaFolder]:
    folder_path = os.path.normpath(os.path.expanduser(str(path or "")))
    if not os.path.isdir(folder_path):
        raise ValueError(f"Folder does not exist: {folder_path}")

    folders = load_folders(media_type)
    existing_aliases = {folder.alias for folder in folders}
    if any(os.path.normcase(os.path.abspath(folder.path)) == os.path.normcase(os.path.abspath(folder_path)) for folder in folders):
        raise ValueError(f"Folder already exists: {folder_path}")
    if alias:
        alias = safe_alias(alias)
        if alias in existing_aliases:
            raise ValueError(f"Folder alias already exists: {alias}")
    else:
        alias = folder_alias_from_path(folder_path, existing_aliases)
    folders.append(MediaFolder(alias=alias, path=folder_path, enabled=True))
    save_folders(media_type, folders)
    return folders


def remove_folder(media_type: str, alias: str) -> list[MediaFolder]:
    if alias in {folder.alias for folder in default_folders(media_type)}:
        raise ValueError(f"Cannot remove the default {alias} folder.")
    folders = load_folders(media_type)
    next_folders = [folder for folder in folders if folder.alias != alias]
    if len(next_folders) == len(folders):
        raise ValueError(f"Folder alias not found: {alias}")
    save_folders(media_type, next_folders)
    return next_folders


def list_media(
    media_type: str,
    root: str | Path,
    recursive: bool = True,
    *,
    privacy_mode: bool = False,
) -> list[dict[str, Any]]:
    media_type = normalize_media_type(media_type)
    extensions = media_definition(media_type)["extensions"]
    root_path = Path(root)
    results: list[dict[str, Any]] = []
    if not root_path.is_dir():
        return results

    if recursive:
        walker = os.walk(root_path)
    else:
        walker = [(root_path, [], [path.name for path in root_path.iterdir() if path.is_file()])]

    for dirpath, _, filenames in walker:
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() not in extensions:
                continue
            stat = path.stat() if path.exists() else None
            item: dict[str, Any] = {
                "filename": path.relative_to(root_path).as_posix(),
                "path": str(path.resolve()),
                "name": path.name,
                "mtime": stat.st_mtime if stat else 0,
                "size": stat.st_size if stat else 0,
                "mime_type": mimetypes.guess_type(path.name)[0] or "",
            }
            if media_type == "image":
                item.update(image_metadata(path))
            elif media_type == "video":
                item.update(video_metadata(path))
                item.update(generated_take_capture_metadata(path, privacy_mode=privacy_mode))
            elif media_type == "audio":
                item["duration_seconds"] = media_duration_seconds(path, "audio")
            results.append(item)
    return sorted(results, key=lambda item: item["filename"].lower())


def list_project_take_captures(
    project: dict[str, Any],
    shot_id: str,
    *,
    privacy_mode: bool = False,
) -> dict[str, Any]:
    shot_id = str(shot_id or "").strip()
    if not shot_id:
        raise ValueError("shot_id is required.")
    take_directory = resolve_project_take_directory(project, shot_id, create=False)
    captures = [
        item
        for item in list_media("video", take_directory, recursive=True, privacy_mode=privacy_mode)
        if _capture_matches_shot(item, shot_id)
    ]
    captures.sort(key=lambda item: (-(float(item.get("mtime") or 0)), str(item.get("filename") or "")))
    return {
        "shot_id": shot_id,
        "take_directory": str(take_directory),
        "storage": resolved_project_storage_summary(project),
        "captures": captures,
    }


def delete_project_take_capture(
    project: dict[str, Any],
    shot_id: str,
    path: str,
    *,
    take_id: str | None = None,
    privacy_mode: bool = False,
) -> dict[str, Any]:
    shot_id = str(shot_id or "").strip()
    if not shot_id:
        raise ValueError("shot_id is required.")
    path_value = str(path or "").strip()
    if not path_value:
        raise ValueError("TAKE_DELETE_PATH_REQUIRED: Take media path is required.")

    take_directory = resolve_project_take_directory(project, shot_id, create=False).resolve()
    candidate = Path(path_value).expanduser().resolve(strict=False)
    _ensure_project_take_path(take_directory, candidate)
    if candidate.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"TAKE_DELETE_UNSUPPORTED_EXTENSION: Unsupported take media extension: {candidate.suffix}")
    if not candidate.is_file():
        raise FileNotFoundError(f"TAKE_DELETE_MEDIA_NOT_FOUND: Take media was not found: {candidate}")

    sidecar_path = generated_take_sidecar_path(candidate)
    if sidecar_path is None:
        raise ValueError("TAKE_DELETE_SIDECAR_REQUIRED: Take media must have a Helto take sidecar.")
    sidecar = _load_take_sidecar_for_delete(sidecar_path)
    if not _capture_registration_matches_shot(sidecar.get("registration"), shot_id):
        raise ValueError("TAKE_DELETE_SHOT_MISMATCH: Take sidecar does not match the selected shot.")
    sidecar_take_id = _sidecar_take_id(sidecar)
    requested_take_id = str(take_id or "").strip()
    if requested_take_id and requested_take_id != sidecar_take_id:
        raise ValueError("TAKE_DELETE_TAKE_MISMATCH: Take sidecar does not match the selected take.")

    sidecar_candidates = _take_sidecar_candidates(candidate)
    files_deleted = 0
    deleted_paths: list[str] = []
    for file_path in [candidate, *sidecar_candidates]:
        if not file_path.is_file():
            continue
        file_path.unlink()
        files_deleted += 1
        deleted_paths.append(str(file_path))

    _prune_empty_take_subdirectories(take_directory, candidate.parent)
    return {
        "ok": True,
        "deleted": files_deleted > 0,
        "files_deleted": files_deleted,
        "shot_id": shot_id,
        "take_id": requested_take_id or sidecar_take_id,
        "path": "Private path" if privacy_mode else str(candidate),
        "deleted_paths": ["Private path"] * len(deleted_paths) if privacy_mode else deleted_paths,
    }


def folder_payload(media_type: str) -> list[dict[str, Any]]:
    count_key = media_definition(media_type)["count_key"]
    folders = []
    for folder in load_folders(media_type):
        exists = os.path.isdir(folder.path)
        folders.append(
            {
                "alias": folder.alias,
                "path": os.path.normpath(folder.path),
                "display_name": folder_display_name(folder.path),
                "enabled": folder.enabled,
                "exists": exists,
                count_key: len(list_media(media_type, folder.path, recursive=True)) if exists else 0,
            }
        )
    return folders


def resolve_browser_media_path(media_type: str, alias: str, filename: str) -> Path:
    folder = folder_by_alias(media_type, alias)
    if not folder.enabled:
        raise ValueError(f"Folder alias is disabled: {alias}")

    root = Path(os.path.normpath(os.path.expanduser(folder.path))).resolve()
    candidate = (root / str(filename or "")).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("Invalid media path.")
    if candidate.suffix.lower() not in media_definition(media_type)["extensions"]:
        raise ValueError(f"Unsupported {media_type} extension: {candidate.suffix}")
    if not candidate.is_file():
        raise FileNotFoundError(f"Media not found: {alias}/{filename}")
    return candidate


def generated_take_capture_metadata(path: Path, *, privacy_mode: bool = False) -> dict[str, Any]:
    sidecar_path = generated_take_sidecar_path(path)
    if sidecar_path is None:
        return {"has_take_capture": False}
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8") or "{}")
        normalized = normalize_generated_take_capture_sidecar(payload)
        if privacy_mode:
            normalized = build_generated_take_capture_sidecar(
                normalized["registration"],
                media=normalized.get("media"),
                privacy_mode=True,
            )
        return {
            "has_take_capture": True,
            "take_capture": normalized,
        }
    except (GeneratedCaptureError, OSError, json.JSONDecodeError) as exc:
        return {
            "has_take_capture": False,
            "take_capture_error": str(exc),
        }


def generated_take_sidecar_path(path: Path) -> Path | None:
    for candidate in _take_sidecar_candidates(path):
        if candidate.is_file():
            return candidate
    return None


def _take_sidecar_candidates(path: Path) -> list[Path]:
    return [
        path.with_suffix(".helto_take.json"),
        Path(f"{path}.helto_take.json"),
    ]


def _capture_matches_shot(item: dict[str, Any], shot_id: str) -> bool:
    capture = item.get("take_capture")
    if not isinstance(capture, dict):
        return False
    registration = capture.get("registration")
    if not isinstance(registration, dict):
        return False
    return _capture_registration_matches_shot(registration, shot_id)


def _capture_registration_matches_shot(registration: dict[str, Any] | None, shot_id: str) -> bool:
    if not isinstance(registration, dict):
        return False
    shot_ids = [
        str(candidate)
        for candidate in registration.get("shot_ids") or []
        if candidate is not None
    ]
    direct = registration.get("shot_id")
    if direct is not None:
        shot_ids.append(str(direct))
    return shot_id in set(shot_ids)


def _load_take_sidecar_for_delete(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        return normalize_generated_take_capture_sidecar(payload)
    except (GeneratedCaptureError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"TAKE_DELETE_SIDECAR_INVALID: {exc}") from exc


def _sidecar_take_id(sidecar: dict[str, Any]) -> str:
    take = sidecar.get("registration", {}).get("take")
    if isinstance(take, dict):
        value = take.get("take_id")
        if value is not None:
            return str(value)
    return ""


def _ensure_project_take_path(take_directory: Path, candidate: Path) -> None:
    if take_directory != candidate and take_directory not in candidate.parents:
        raise ValueError("TAKE_DELETE_PATH_OUTSIDE_PROJECT: Take media must be inside the selected project take folder.")


def _prune_empty_take_subdirectories(take_directory: Path, start: Path) -> None:
    current = start
    while current != take_directory and take_directory in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def make_browser_thumbnail(media_type: str, alias: str, filename: str, max_size: int = 320, privacy_mode: bool = False) -> Path | bytes:
    media_type = normalize_media_type(media_type)
    if media_type not in {"image", "video"}:
        raise ValueError("Only image and video media have thumbnails.")
    return make_thumbnail(resolve_browser_media_path(media_type, alias, filename), max_size=max_size, privacy_mode=privacy_mode)


def image_metadata(path: Path) -> dict[str, int]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            return {"width": width, "height": height}
    except Exception:
        return {"width": 0, "height": 0}


def video_metadata(path: Path) -> dict[str, Any]:
    metadata = {"width": 0, "height": 0, "duration_seconds": None}
    try:
        with av.open(str(path)) as container:
            metadata["duration_seconds"] = _container_duration_seconds(container)
            stream = next((stream for stream in container.streams if stream.type == "video"), None)
            if stream is not None:
                metadata["width"] = int(stream.width or 0)
                metadata["height"] = int(stream.height or 0)
    except Exception:
        pass
    return metadata


def media_duration_seconds(path: Path, stream_type: str) -> float | None:
    try:
        with av.open(str(path)) as container:
            duration = _container_duration_seconds(container)
            if duration is not None:
                return duration
            stream = next((stream for stream in container.streams if stream.type == stream_type), None)
            if stream and stream.duration is not None and stream.time_base is not None:
                return max(0.0, float(stream.duration * stream.time_base))
    except Exception:
        return None
    return None


def _container_duration_seconds(container) -> float | None:
    if container.duration is None:
        return None
    return max(0.0, float(container.duration / av.time_base))
