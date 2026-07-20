from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
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
    if not path.exists():
        return default_global_settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        payload = {}
    return normalize_global_settings(payload)


def save_global_settings(
    settings: Mapping[str, Any],
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    normalized = normalize_global_settings(settings)
    configured_root = str(normalized["storage"]["asset_root_directory"]).strip()
    if configured_root and not Path(configured_root).expanduser().is_absolute():
        raise GlobalSettingsError("GLOBAL_ASSET_ROOT_NOT_ABSOLUTE: Asset Root Directory must be an absolute path.")
    path = settings_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
    return normalized


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
