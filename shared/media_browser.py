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


def safe_alias(alias: str) -> str:
    value = str(alias or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", value):
        raise ValueError(
            "Alias must be 1-80 characters using letters, numbers, spaces, dot, underscore, or dash."
        )
    return value


def load_folders(media_type: str) -> list[MediaFolder]:
    file_path = config_file(media_type)
    default = default_folder()
    if not file_path.exists():
        return [default]

    try:
        data = json.loads(file_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return [default]

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
    alias = safe_alias(alias)
    folder_path = os.path.normpath(os.path.expanduser(str(path or "")))
    if not os.path.isdir(folder_path):
        raise ValueError(f"Folder does not exist: {folder_path}")

    folders = load_folders(media_type)
    if any(folder.alias == alias for folder in folders):
        raise ValueError(f"Folder alias already exists: {alias}")
    folders.append(MediaFolder(alias=alias, path=folder_path, enabled=True))
    save_folders(media_type, folders)
    return folders


def remove_folder(media_type: str, alias: str) -> list[MediaFolder]:
    if alias == "input":
        raise ValueError("Cannot remove the default input folder.")
    folders = load_folders(media_type)
    next_folders = [folder for folder in folders if folder.alias != alias]
    if len(next_folders) == len(folders):
        raise ValueError(f"Folder alias not found: {alias}")
    save_folders(media_type, next_folders)
    return next_folders


def list_media(media_type: str, root: str | Path, recursive: bool = True) -> list[dict[str, Any]]:
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
            elif media_type == "audio":
                item["duration_seconds"] = media_duration_seconds(path, "audio")
            results.append(item)
    return sorted(results, key=lambda item: item["filename"].lower())


def folder_payload(media_type: str) -> list[dict[str, Any]]:
    count_key = media_definition(media_type)["count_key"]
    folders = []
    for folder in load_folders(media_type):
        exists = os.path.isdir(folder.path)
        folders.append(
            {
                "alias": folder.alias,
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


def make_browser_thumbnail(media_type: str, alias: str, filename: str, max_size: int = 320) -> Path:
    media_type = normalize_media_type(media_type)
    if media_type not in {"image", "video"}:
        raise ValueError("Only image and video media have thumbnails.")
    return make_thumbnail(resolve_browser_media_path(media_type, alias, filename), max_size=max_size)


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
