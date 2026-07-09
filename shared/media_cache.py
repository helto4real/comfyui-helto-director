from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
from typing import Any

import av
import folder_paths
from PIL import Image, ImageOps

from .atomic_write import atomic_write as _atomic_write
from .privacy import decrypt_bytes, encrypt_bytes


IMAGE_EXTENSIONS = {".apng", ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
AUDIO_EXTENSIONS = {".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".weba"}

DEFAULT_THUMBNAIL_SIZE = 320
DEFAULT_WAVEFORM_PEAKS = 96
MIN_WAVEFORM_PEAKS = 16
MAX_WAVEFORM_PEAKS = 512
THUMBNAIL_CACHE_PURPOSE = "timeline-thumbnail-cache"
WAVEFORM_CACHE_PURPOSE = "timeline-waveform-cache"
MEDIA_PATH_SECURITY_ERROR = "Security error: media path is outside approved ComfyUI directories."


def cache_root() -> Path:
    root = Path(folder_paths.get_temp_directory()) / "helto_timeline_director"
    root.mkdir(parents=True, exist_ok=True)
    (root / "thumbnails").mkdir(exist_ok=True)
    (root / "waveforms").mkdir(exist_ok=True)
    return root


def effective_media_privacy_mode(requested_privacy: bool = False) -> bool:
    """Return the server-authoritative privacy mode for media operations.

    Callers may request stronger protection, but a request cannot disable the
    global setting. Import lazily so media-path bootstrap remains usable while
    the nodepack is being loaded.
    """
    try:
        from .timeline.global_settings import global_privacy_mode
    except Exception:
        from shared.timeline.global_settings import global_privacy_mode

    return global_privacy_mode() or bool(requested_privacy)


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

    if not _is_path_inside_allowed_media_root(resolved):
        raise ValueError(MEDIA_PATH_SECURITY_ERROR)

    if not resolved.is_file():
        raise FileNotFoundError(f"Media file not found: {resolved}")
    return resolved


def _allowed_media_roots() -> list[Path]:
    roots: list[Path] = []
    candidates = [
        folder_paths.get_input_directory(),
        folder_paths.get_output_directory(),
        folder_paths.get_temp_directory(),
    ]
    for paths, _extensions in getattr(folder_paths, "folder_names_and_paths", {}).values():
        candidates.extend(paths)
    candidates.extend(_configured_director_asset_roots())
    candidates.extend(_configured_media_browser_roots())

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or not str(candidate).strip():
            continue
        root = Path(str(candidate)).expanduser().resolve(strict=False)
        root_key = str(root)
        if root_key in seen:
            continue
        roots.append(root)
        seen.add(root_key)
    return roots


def _configured_director_asset_roots() -> list[str]:
    try:
        from .timeline.global_settings import resolve_global_asset_root
    except Exception:
        try:
            from shared.timeline.global_settings import resolve_global_asset_root
        except Exception:
            return []

    try:
        return [str(resolve_global_asset_root(create=False))]
    except Exception:
        return []


def _configured_media_browser_roots() -> list[str]:
    try:
        from . import media_browser
    except Exception:
        try:
            import shared.media_browser as media_browser
        except Exception:
            return []

    roots: list[str] = []
    for media_type in getattr(media_browser, "MEDIA_TYPES", {}):
        try:
            roots.extend(folder.path for folder in media_browser.load_folders(media_type) if folder.enabled)
        except Exception:
            continue
    return roots


def _is_path_inside_allowed_media_root(path: Path) -> bool:
    path_str = str(path)
    for root in _allowed_media_roots():
        root_str = str(root)
        try:
            if os.path.commonpath((root_str, path_str)) == root_str:
                return True
        except ValueError:
            continue
    return False


def thumbnail_cache_path(media_path: Path, max_size: int = DEFAULT_THUMBNAIL_SIZE, privacy_mode: bool = False) -> Path:
    key = _cache_key(media_path, {"max_size": max_size, "kind": "thumbnail", "privacy": bool(privacy_mode)})
    suffix = ".webp.enc" if privacy_mode else ".webp"
    return cache_root() / "thumbnails" / f"{key}{suffix}"


def waveform_cache_path(media_path: Path, peaks: int = DEFAULT_WAVEFORM_PEAKS, privacy_mode: bool = False) -> Path:
    key = _cache_key(media_path, {"peaks": peaks, "kind": "waveform", "privacy": bool(privacy_mode)})
    suffix = ".json.enc" if privacy_mode else ".json"
    return cache_root() / "waveforms" / f"{key}{suffix}"


def make_thumbnail(media_path: Path, max_size: int = DEFAULT_THUMBNAIL_SIZE, privacy_mode: bool = False) -> Path | bytes:
    max_size = _clamp_int(max_size, 64, 1024)
    out = thumbnail_cache_path(media_path, max_size, privacy_mode=privacy_mode)
    if out.exists():
        if privacy_mode:
            return decrypt_bytes(out.read_text(encoding="utf-8"), THUMBNAIL_CACHE_PURPOSE)
        return out

    suffix = media_path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        image = _load_image_thumbnail(media_path, max_size)
    elif suffix in VIDEO_EXTENSIONS:
        image = _load_video_thumbnail(media_path, max_size)
    else:
        raise ValueError("Unsupported media type for thumbnail.")

    if privacy_mode:
        thumbnail = _image_to_webp_bytes(image)
        _write_private_json(out, encrypt_bytes(thumbnail, THUMBNAIL_CACHE_PURPOSE))
        return thumbnail

    _atomic_write(out, lambda tmp_path: image.save(tmp_path, "WEBP", quality=90, method=4))
    return out


def make_waveform(media_path: Path, peaks: int = DEFAULT_WAVEFORM_PEAKS, privacy_mode: bool = False) -> dict[str, Any]:
    peaks = _clamp_int(peaks, MIN_WAVEFORM_PEAKS, MAX_WAVEFORM_PEAKS)
    out = waveform_cache_path(media_path, peaks, privacy_mode=privacy_mode)
    if out.exists():
        if privacy_mode:
            decrypted = decrypt_bytes(out.read_text(encoding="utf-8"), WAVEFORM_CACHE_PURPOSE)
            return json.loads(decrypted.decode("utf-8"))
        return json.loads(out.read_text(encoding="utf-8"))

    payload = _decode_audio_waveform(media_path, peaks)
    payload["cache_key"] = out.stem
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if privacy_mode:
        _write_private_json(out, encrypt_bytes(payload_bytes, WAVEFORM_CACHE_PURPOSE))
        return payload

    _atomic_write(out, lambda tmp_path: tmp_path.write_bytes(payload_bytes))
    return payload


def clear_media_cache() -> None:
    root = cache_root()
    for child in root.rglob("*"):
        if child.is_file():
            child.unlink(missing_ok=True)


def clear_public_media_cache() -> None:
    """Remove plaintext preview caches while preserving encrypted previews."""
    root = cache_root()
    public_suffixes = {
        "thumbnails": ".webp",
        "waveforms": ".json",
    }
    for directory_name, suffix in public_suffixes.items():
        directory = root / directory_name
        for child in directory.iterdir():
            if child.is_file() and _is_public_cache_artifact(child.name, suffix):
                child.unlink(missing_ok=True)


def _load_image_thumbnail(path: Path, max_size: int) -> Image.Image:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        return image.copy()


def _load_video_thumbnail(path: Path, max_size: int) -> Image.Image:
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            image = frame.to_image().convert("RGB")
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            return image
    raise ValueError("Could not decode a video frame for thumbnail.")


def _image_to_webp_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=90, method=4)
    return buffer.getvalue()


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    _atomic_write(
        path,
        lambda tmp_path: tmp_path.write_text(encoded, encoding="utf-8"),
        mode=0o600,
    )


def _is_public_cache_artifact(name: str, suffix: str) -> bool:
    if name.endswith(suffix) or name.endswith(f"{suffix}.tmp"):
        return True
    return (
        name.startswith(".")
        and name.endswith(".tmp")
        and f"{suffix}." in name
        and f"{suffix}.enc." not in name
    )


def _decode_audio_waveform(path: Path, peaks: int) -> dict[str, Any]:
    samples = []
    sample_rate = None
    channels = 0
    duration_seconds = None

    with av.open(str(path)) as container:
        if container.duration is not None:
            duration_seconds = max(0.0, float(container.duration / av.time_base))
        for frame in container.decode(audio=0):
            sample_rate = sample_rate or frame.sample_rate
            channels = max(channels, len(frame.layout.channels))
            array = frame.to_ndarray()
            if array.ndim == 2:
                values = abs(array).max(axis=0)
            else:
                values = abs(array)
            samples.extend(float(value) for value in values)

    peak_values = _bin_peaks(samples, peaks)
    return {
        "duration_seconds": duration_seconds,
        "sample_rate": sample_rate,
        "channels": channels,
        "peaks": peak_values,
    }


def _bin_peaks(samples: list[float], peaks: int) -> list[float]:
    if not samples:
        return [0.0 for _ in range(peaks)]
    max_value = max(max(samples), 1.0)
    chunk_size = max(1, math.ceil(len(samples) / peaks))
    values = []
    for index in range(peaks):
        start = index * chunk_size
        chunk = samples[start:start + chunk_size]
        values.append(round(max(chunk or [0.0]) / max_value, 4))
    return values


def _cache_key(media_path: Path, params: dict[str, Any]) -> str:
    stat = media_path.stat()
    payload = {
        "path": str(media_path.resolve()),
        "mtime": stat.st_mtime_ns,
        "size": stat.st_size,
        **params,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
