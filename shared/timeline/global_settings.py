from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

import folder_paths

from ..contracts.video_timeline import (
    DEFAULT_ALLOW_GAPS,
    DEFAULT_AUTO_CLOSE_GAPS,
    DEFAULT_MINIMUM_SECTION_DURATION_SECONDS,
    DEFAULT_PROJECT_ASSET_ROOT_SUBDIRECTORY,
)


CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SETTINGS_FILE_NAME = "timeline_director_global_settings.json"
SETTINGS_LOCK_FILE_NAME = f".{SETTINGS_FILE_NAME}.lock"
MODE_SOURCE_REVISION_KEY = "_helto_privacy_mode_revision"
GLOBAL_SETTINGS_SCHEMA_VERSION = 1


DEFAULT_GLOBAL_SETTINGS: dict[str, Any] = {
    "schema_version": GLOBAL_SETTINGS_SCHEMA_VERSION,
    "storage": {
        "asset_root_directory": "",
    },
    "timeline": {
        "show_resolved_model_output": False,
        "allow_gaps": DEFAULT_ALLOW_GAPS,
        "auto_close_gaps": DEFAULT_AUTO_CLOSE_GAPS,
        "minimum_section_duration_seconds": DEFAULT_MINIMUM_SECTION_DURATION_SECONDS,
    },
    "global_prompt": {
        "show_effective_prompt": False,
    },
    "audio": {
        "always_normalize": False,
    },
    "privacy": {
        "mode": True,
    },
    "display": {
        "show_section_labels": True,
        "show_thumbnails": True,
        "show_audio_waveforms": True,
    },
}


class GlobalSettingsError(ValueError):
    """Raised when Director global settings cannot be saved safely."""


def settings_path(base_dir: str | os.PathLike[str] | None = None) -> Path:
    root = Path(base_dir) if base_dir is not None else CONFIG_DIR
    return root / SETTINGS_FILE_NAME


def load_global_settings(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = settings_path(base_dir)
    return normalize_global_settings(_read_settings_payload(path))


def save_global_settings(
    settings: Mapping[str, Any],
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    normalized = normalize_global_settings(settings)
    configured_root = str(normalized["storage"]["asset_root_directory"]).strip()
    if configured_root and not Path(configured_root).expanduser().is_absolute():
        raise GlobalSettingsError("GLOBAL_ASSET_ROOT_NOT_ABSOLUTE: Asset Root Directory must be an absolute path.")
    path = settings_path(base_dir)
    with _settings_lock(path, exclusive=True):
        current_payload = _read_settings_payload(path)
        current = normalize_global_settings(current_payload)
        revision = _mode_source_revision(current_payload)
        if normalized["privacy"]["mode"] != current["privacy"]["mode"]:
            revision += 1
        _write_settings_payload(path, normalized, revision)
    return normalized


def read_global_privacy_mode_source(
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Read the revisioned declaration from the atomically replaced settings file."""

    path = settings_path(base_dir)
    payload = _read_mode_source_payload(path)
    normalized = normalize_global_settings(payload)
    return _mode_source_payload(normalized, _mode_source_revision(payload))


def compare_and_set_global_privacy_mode_source(
    expected_revision: int,
    expected_declared: object,
    target_declared: object,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Atomically replace the global declaration when its snapshot still matches."""

    expected = _declared_mode_value(expected_declared)
    target = _declared_mode_value(target_declared)
    if type(expected_revision) is not int or expected_revision < 0:
        raise GlobalSettingsError("GLOBAL_PRIVACY_MODE_SOURCE_INVALID: Invalid mode source snapshot.")
    path = settings_path(base_dir)
    with _settings_lock(path, exclusive=True):
        payload = _read_mode_source_payload(path)
        normalized = normalize_global_settings(payload)
        revision = _mode_source_revision(payload)
        current = _mode_source_payload(normalized, revision)
        if current != {"revision": expected_revision, "declared": expected}:
            raise GlobalSettingsError("GLOBAL_PRIVACY_MODE_SOURCE_CONFLICT: Global privacy mode changed concurrently.")
        normalized["privacy"]["mode"] = target == "private"
        revision += 1
        _write_settings_payload(path, normalized, revision)
        return {"revision": revision, "declared": target}


def rollback_global_privacy_mode_source(
    target_snapshot: object,
    prior_snapshot: object,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Idempotently restore a previously committed global declaration."""

    target = _mode_source_snapshot(target_snapshot)
    prior = _mode_source_snapshot(prior_snapshot)
    if target["revision"] != prior["revision"] + 1:
        raise GlobalSettingsError("GLOBAL_PRIVACY_MODE_SOURCE_INVALID: Invalid mode source snapshot.")
    restored = {
        "revision": target["revision"] + 1,
        "declared": prior["declared"],
    }
    path = settings_path(base_dir)
    with _settings_lock(path, exclusive=True):
        payload = _read_mode_source_payload(path)
        normalized = normalize_global_settings(payload)
        current = _mode_source_payload(normalized, _mode_source_revision(payload))
        if current == restored:
            return restored
        if current != target:
            raise GlobalSettingsError("GLOBAL_PRIVACY_MODE_SOURCE_CONFLICT: Global privacy mode changed concurrently.")
        normalized["privacy"]["mode"] = prior["declared"] == "private"
        _write_settings_payload(path, normalized, restored["revision"])
        return restored


def patch_global_settings(
    patch: Mapping[str, Any],
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    merged = _deep_merge(load_global_settings(base_dir), patch)
    return save_global_settings(merged, base_dir)


def global_settings_status(base_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    settings = load_global_settings(base_dir)
    default_root = default_asset_root_directory()
    effective_root = resolve_global_asset_root(settings=settings, create=False)
    return {
        "ok": True,
        "settings": settings,
        "configPath": str(settings_path(base_dir)),
        "storage": {
            "asset_root_directory": settings["storage"]["asset_root_directory"],
            "effective_asset_root_directory": str(effective_root),
            "default_asset_root_directory": str(default_root),
            "configured": bool(settings["storage"]["asset_root_directory"]),
        },
    }


def default_global_settings() -> dict[str, Any]:
    return deepcopy(DEFAULT_GLOBAL_SETTINGS)


def normalize_global_settings(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    normalized = default_global_settings()
    normalized["schema_version"] = GLOBAL_SETTINGS_SCHEMA_VERSION
    normalized["storage"]["asset_root_directory"] = _safe_string(
        _section(source, "storage").get("asset_root_directory")
    )
    timeline = _section(source, "timeline")
    normalized["timeline"]["show_resolved_model_output"] = bool(timeline.get("show_resolved_model_output"))
    normalized["timeline"]["allow_gaps"] = timeline.get("allow_gaps") is not False
    normalized["timeline"]["auto_close_gaps"] = bool(timeline.get("auto_close_gaps"))
    normalized["timeline"]["minimum_section_duration_seconds"] = _positive_float(
        timeline.get("minimum_section_duration_seconds"),
        DEFAULT_MINIMUM_SECTION_DURATION_SECONDS,
    )
    normalized["global_prompt"]["show_effective_prompt"] = bool(
        _section(source, "global_prompt").get("show_effective_prompt")
    )
    normalized["audio"]["always_normalize"] = bool(_section(source, "audio").get("always_normalize"))
    normalized["privacy"]["mode"] = _section(source, "privacy").get("mode") is not False
    display = _section(source, "display")
    normalized["display"]["show_section_labels"] = display.get("show_section_labels") is not False
    normalized["display"]["show_thumbnails"] = display.get("show_thumbnails") is not False
    normalized["display"]["show_audio_waveforms"] = display.get("show_audio_waveforms") is not False
    return normalized


def default_asset_root_directory() -> Path:
    return (Path(folder_paths.get_output_directory()) / DEFAULT_PROJECT_ASSET_ROOT_SUBDIRECTORY).resolve()


def resolve_global_asset_root(
    *,
    settings: Mapping[str, Any] | None = None,
    create: bool = True,
) -> Path:
    normalized = normalize_global_settings(settings if settings is not None else load_global_settings())
    configured = _safe_string(normalized["storage"].get("asset_root_directory"))
    root = Path(configured).expanduser() if configured else default_asset_root_directory()
    if configured and not root.is_absolute():
        raise GlobalSettingsError("GLOBAL_ASSET_ROOT_NOT_ABSOLUTE: Asset Root Directory must be an absolute path.")
    root = root.resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def global_privacy_mode(settings: Mapping[str, Any] | None = None) -> bool:
    normalized = normalize_global_settings(settings if settings is not None else load_global_settings())
    return bool(normalized["privacy"]["mode"])


def global_always_normalize_audio(settings: Mapping[str, Any] | None = None) -> bool:
    normalized = normalize_global_settings(settings if settings is not None else load_global_settings())
    return bool(normalized["audio"]["always_normalize"])


def _section(source: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = source.get(key)
    return value if isinstance(value, Mapping) else {}


def _deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _positive_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if number <= 0:
        return float(fallback)
    return number


def _safe_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _read_settings_payload(path: Path) -> Mapping[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _read_mode_source_payload(path: Path) -> Mapping[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        raise GlobalSettingsError(
            "GLOBAL_PRIVACY_MODE_SOURCE_INVALID: Invalid mode source snapshot."
        ) from None
    if not isinstance(payload, Mapping):
        raise GlobalSettingsError(
            "GLOBAL_PRIVACY_MODE_SOURCE_INVALID: Invalid mode source snapshot."
        )
    return payload


def _mode_source_revision(payload: Mapping[str, object]) -> int:
    value = payload.get(MODE_SOURCE_REVISION_KEY, 0)
    if type(value) is not int or value < 0:
        raise GlobalSettingsError("GLOBAL_PRIVACY_MODE_SOURCE_INVALID: Invalid mode source snapshot.")
    return value


def _mode_source_payload(
    settings: Mapping[str, object],
    revision: int,
) -> dict[str, object]:
    privacy = settings["privacy"]
    assert isinstance(privacy, Mapping)
    return {
        "revision": revision,
        "declared": "private" if privacy["mode"] is True else "public",
    }


def _mode_source_snapshot(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"revision", "declared"}:
        raise GlobalSettingsError("GLOBAL_PRIVACY_MODE_SOURCE_INVALID: Invalid mode source snapshot.")
    revision = value["revision"]
    if type(revision) is not int or revision < 0:
        raise GlobalSettingsError("GLOBAL_PRIVACY_MODE_SOURCE_INVALID: Invalid mode source snapshot.")
    return {"revision": revision, "declared": _declared_mode_value(value["declared"])}


def _declared_mode_value(value: object) -> str:
    candidate = getattr(value, "value", value)
    if candidate not in {"private", "public"}:
        raise GlobalSettingsError("GLOBAL_PRIVACY_MODE_SOURCE_INVALID: Invalid privacy declaration.")
    return str(candidate)


def _write_settings_payload(
    path: Path,
    settings: Mapping[str, object],
    revision: int,
) -> None:
    payload = deepcopy(dict(settings))
    payload[MODE_SOURCE_REVISION_KEY] = revision
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


@contextmanager
def _settings_lock(path: Path, *, exclusive: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / SETTINGS_LOCK_FILE_NAME
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
