"""Director-owned media facts and read-only legacy path compatibility.

This module deliberately contains no privacy policy, encryption, cache writer,
route registration, or mutable folder configuration.  Managed privacy owns
those mechanics; these helpers only preserve product-level media behavior and
read old folder declarations while workflows migrate.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
import folder_paths
from PIL import Image


IMAGE_EXTENSIONS = frozenset(
    {".apng", ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
)
VIDEO_EXTENSIONS = frozenset(
    {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
)
AUDIO_EXTENSIONS = frozenset(
    {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".weba"}
)
MEDIA_DEFINITIONS = {
    "image": (IMAGE_EXTENSIONS, "timeline_image_folders.json"),
    "video": (VIDEO_EXTENSIONS, "timeline_video_folders.json"),
    "audio": (AUDIO_EXTENSIONS, "timeline_audio_folders.json"),
}
MEDIA_PATH_SECURITY_ERROR = "Security error: media path is outside approved ComfyUI directories."


@dataclass(frozen=True, slots=True)
class MediaFolderSpec:
    alias: str
    path: Path
    enabled: bool = True


def config_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "config"


def legacy_cache_root() -> Path:
    """Locate the retired cache without creating it."""

    return Path(folder_paths.get_temp_directory()) / "helto_timeline_director"


def default_folders(media_type: str) -> tuple[MediaFolderSpec, ...]:
    media_type = _media_type(media_type)
    folders = [MediaFolderSpec("input", Path(folder_paths.get_input_directory()).resolve(strict=False))]
    if media_type == "video":
        output = MediaFolderSpec("output", Path(folder_paths.get_output_directory()).resolve(strict=False))
        if not _same_path(output.path, folders[0].path):
            folders.append(output)
    return tuple(folders)


def legacy_configured_folders(media_type: str) -> tuple[MediaFolderSpec, ...]:
    """Read the retired folder JSON for compatibility; never mutate it."""

    media_type = _media_type(media_type)
    defaults = list(default_folders(media_type))
    config_name = MEDIA_DEFINITIONS[media_type][1]
    path = config_dir() / config_name
    if not path.is_file():
        return tuple(defaults)
    try:
        value = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return tuple(defaults)
    folders: list[MediaFolderSpec] = []
    seen: set[str] = set()
    entries = value.get("folders") if isinstance(value, dict) else None
    for entry in entries if isinstance(entries, list) else ():
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get("alias") or "").strip()
        raw_path = str(entry.get("path") or "").strip()
        if not alias or alias in seen or not raw_path:
            continue
        folders.append(
            MediaFolderSpec(
                alias,
                Path(raw_path).expanduser().resolve(strict=False),
                bool(entry.get("enabled", True)),
            )
        )
        seen.add(alias)
    for default in reversed(defaults):
        if default.alias not in seen:
            folders.insert(0, default)
    return tuple(folders)


def resolve_legacy_browser_media_path(media_type: str, alias: str, filename: str) -> Path:
    media_type = _media_type(media_type)
    folder = next(
        (item for item in legacy_configured_folders(media_type) if item.alias == str(alias or "")),
        None,
    )
    if folder is None or not folder.enabled:
        raise ValueError("Unknown or disabled legacy media folder alias.")
    root = folder.path.resolve(strict=False)
    candidate = (root / str(filename or "")).resolve(strict=False)
    if root != candidate and root not in candidate.parents:
        raise ValueError(MEDIA_PATH_SECURITY_ERROR)
    if candidate.suffix.lower() not in MEDIA_DEFINITIONS[media_type][0]:
        raise ValueError(f"Unsupported {media_type} extension: {candidate.suffix}")
    if not candidate.is_file():
        raise FileNotFoundError("Legacy media source was not found.")
    return candidate


def resolve_media_path(path_value: str, source_type: str | None = None) -> Path:
    if not path_value or not str(path_value).strip():
        raise ValueError("Media path is required.")
    raw_path = str(path_value).strip()
    if ".." in Path(raw_path).parts:
        raise ValueError(MEDIA_PATH_SECURITY_ERROR)
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        resolved = path.resolve(strict=False)
    else:
        filename, annotated_dir = folder_paths.annotated_filepath(raw_path)
        if not filename or Path(filename).is_absolute():
            raise ValueError(MEDIA_PATH_SECURITY_ERROR)
        base_dir = annotated_dir
        if base_dir is None and source_type:
            base_dir = folder_paths.get_directory_by_type(source_type)
        if base_dir is None:
            base_dir = folder_paths.get_input_directory()
        resolved = (Path(base_dir).expanduser() / filename).resolve(strict=False)
    if not _inside_allowed_root(resolved):
        raise ValueError(MEDIA_PATH_SECURITY_ERROR)
    if not resolved.is_file():
        raise FileNotFoundError(f"Media file not found: {resolved}")
    return resolved


def image_metadata(path: Path) -> dict[str, int]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            return {"width": width, "height": height}
    except Exception:
        return {"width": 0, "height": 0}


def video_metadata(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {"width": 0, "height": 0, "duration_seconds": None}
    try:
        with av.open(str(path)) as container:
            metadata["duration_seconds"] = _container_duration_seconds(container)
            stream = next((item for item in container.streams if item.type == "video"), None)
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
            stream = next((item for item in container.streams if item.type == stream_type), None)
            if stream and stream.duration is not None and stream.time_base is not None:
                return max(0.0, float(stream.duration * stream.time_base))
    except Exception:
        return None
    return None


def _media_type(value: str) -> str:
    media_type = str(value or "").strip().lower()
    if media_type not in MEDIA_DEFINITIONS:
        raise ValueError(f"Unsupported media type: {value}")
    return media_type


def _inside_allowed_root(path: Path) -> bool:
    roots: list[object] = [
        folder_paths.get_input_directory(),
        folder_paths.get_output_directory(),
        folder_paths.get_temp_directory(),
    ]
    for paths, _extensions in getattr(folder_paths, "folder_names_and_paths", {}).values():
        roots.extend(paths)
    try:
        from .timeline.global_settings import resolve_global_asset_root

        roots.append(resolve_global_asset_root(create=False))
    except Exception:
        pass
    for media_type in MEDIA_DEFINITIONS:
        roots.extend(folder.path for folder in legacy_configured_folders(media_type) if folder.enabled)
    for root_value in roots:
        if not root_value or not str(root_value).strip():
            continue
        root = Path(str(root_value)).expanduser().resolve(strict=False)
        if root == path or root in path.parents:
            return True
    return False


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(str(right.resolve(strict=False)))


def _container_duration_seconds(container: object) -> float | None:
    duration = getattr(container, "duration", None)
    if duration is None:
        return None
    return max(0.0, float(duration / av.time_base))


__all__ = [
    "AUDIO_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "MediaFolderSpec",
    "VIDEO_EXTENSIONS",
    "default_folders",
    "image_metadata",
    "legacy_cache_root",
    "legacy_configured_folders",
    "media_duration_seconds",
    "resolve_legacy_browser_media_path",
    "resolve_media_path",
    "video_metadata",
]
