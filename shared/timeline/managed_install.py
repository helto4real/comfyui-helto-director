"""Atomic installation boundary for Director's complete shared privacy profile."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from helto_privacy import (
    BoundPrivacyPack,
    ConsumerSuiteDeclaration,
    install,
    register_consumer_suite_declaration,
    register_legacy_key_dir,
    register_legacy_reader_units,
)
from helto_privacy.runtime import bound_privacy_pack

from .. import media_domain
from .global_settings import resolve_global_asset_root
from .managed_global_privacy import (
    build_director_global_privacy_profile,
    build_director_global_server_adapters,
)
from .managed_library_records import director_library_legacy_reader_units
from .managed_media_privacy import DirectorManagedMediaService, MediaFolder
from .managed_privacy import DIRECTOR_PROFILE_ID


_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _PACKAGE_ROOT / "config"
_SINGLETON_DATABASE = _CONFIG_DIR / "director_privacy_singletons.sqlite3"
_INSTALL_LOCK = RLock()
DIRECTOR_SUITE_ID = "helto-suite-2026-07-16.3"
_PACK: BoundPrivacyPack | None = None
_ADAPTERS: dict[str, object] | None = None


def install_director_privacy() -> BoundPrivacyPack:
    """Install only the exact complete D1-D6 profile with every adapter bound."""

    global _ADAPTERS, _PACK
    with _INSTALL_LOCK:
        if _PACK is not None:
            return _PACK
        register_legacy_key_dir(_CONFIG_DIR)
        register_legacy_reader_units(director_library_legacy_reader_units())
        profile = build_director_global_privacy_profile()
        media_service = _build_media_service()
        adapters = build_director_global_server_adapters(
            media_service=media_service,
            singleton_database_path=_SINGLETON_DATABASE,
            settings_dir=_CONFIG_DIR,
            library_dir=_CONFIG_DIR,
        )
        expected = {slot.id for slot in profile.server_adapters}
        if set(adapters) != expected:
            raise RuntimeError("Director privacy adapter binding is incomplete.")
        pack = install(profile, adapters)
        register_consumer_suite_declaration(
            ConsumerSuiteDeclaration(profile.distribution, DIRECTOR_SUITE_ID)
        )
        _PACK = pack
        _ADAPTERS = adapters
        return pack


def director_privacy_pack() -> BoundPrivacyPack:
    return _PACK if _PACK is not None else bound_privacy_pack(DIRECTOR_PROFILE_ID)


def director_privacy_adapter(adapter_id: str) -> object:
    if _ADAPTERS is None or adapter_id not in _ADAPTERS:
        raise RuntimeError("Director privacy adapters are not installed.")
    return _ADAPTERS[adapter_id]


def _build_media_service() -> DirectorManagedMediaService:
    defaults = {
        media_type: tuple(
            MediaFolder(folder.alias, folder.path, folder.enabled)
            for folder in media_domain.default_folders(media_type)
        )
        for media_type in ("image", "video", "audio")
    }
    project_root = resolve_global_asset_root(create=False)
    return DirectorManagedMediaService(
        config_dir=_CONFIG_DIR,
        default_folders=defaults,
        project_asset_root=project_root if project_root.is_dir() else None,
        metadata_reader=_read_media_metadata,
    )


def _read_media_metadata(path: object, media_type: str) -> dict[str, object]:
    candidate = Path(path)
    if media_type == "image":
        return dict(media_domain.image_metadata(candidate))
    if media_type == "video":
        return dict(media_domain.video_metadata(candidate))
    if media_type == "audio":
        duration = media_domain.media_duration_seconds(candidate, "audio")
        return {} if duration is None else {"duration_seconds": duration}
    return {}


__all__ = [
    "director_privacy_adapter",
    "director_privacy_pack",
    "install_director_privacy",
]
