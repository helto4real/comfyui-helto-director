"""Director's complete managed privacy profile and server adapter assembly."""

from __future__ import annotations

import os

from helto_privacy import PrivacyProfile

from .managed_durable_state import build_director_durable_state_server_adapters
from .managed_library_records import build_director_library_server_adapters
from .managed_media_artifacts import build_director_media_artifact_server_adapters
from .managed_media_privacy import (
    DirectorManagedMediaService,
    build_director_media_privacy_profile,
    build_director_media_server_adapters,
)
from .managed_privacy import (
    GLOBAL_MODE_ADAPTER_ID,
    DirectorGlobalModeAdapter,
    build_director_timeline_server_adapters,
)
from .managed_segment_spills import build_director_segment_spill_server_adapters
from .managed_take_privacy import build_director_take_server_adapters


DIRECTOR_GLOBAL_PRIVACY_REMOVED_FALLBACKS = (
    "routes/media_browser.py",
    "routes/media_cache.py",
    "routes/media_privacy.py",
    "routes/privacy.py",
    "routes/timeline_library.py",
    "shared/media_browser.py",
    "shared/media_cache.py",
    "shared/privacy.py",
    "shared/privacy_keystore.py",
    "web/timeline/privacy.js",
    "web/timeline/privacy_unlock.js",
)

DIRECTOR_GLOBAL_PRIVACY_ACTIVATION_GAPS = ()


def build_director_global_privacy_profile(
    base_profile: PrivacyProfile | None = None,
) -> PrivacyProfile:
    """Return the exact complete D1-D6 profile."""

    complete = build_director_media_privacy_profile()
    if base_profile is None:
        return complete
    if base_profile != complete:
        raise ValueError("Director global privacy requires the complete D2-D6 profile.")
    return base_profile


def build_director_global_server_adapters(
    *,
    media_service: DirectorManagedMediaService,
    singleton_database_path: str | os.PathLike[str],
    settings_dir: str | os.PathLike[str] | None = None,
    library_dir: str | os.PathLike[str] | None = None,
    cache_root: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Assemble every server adapter for the complete profile."""

    if not isinstance(media_service, DirectorManagedMediaService):
        raise TypeError("Director global privacy requires the managed media service.")
    adapters = build_director_timeline_server_adapters()
    adapters[GLOBAL_MODE_ADAPTER_ID] = DirectorGlobalModeAdapter(base_dir=settings_dir)
    adapters.update(build_director_library_server_adapters(library_dir))
    adapters.update(build_director_media_artifact_server_adapters(cache_root=cache_root))
    adapters.update(build_director_take_server_adapters())
    adapters.update(build_director_segment_spill_server_adapters())
    adapters.update(
        build_director_durable_state_server_adapters(singleton_database_path)
    )
    adapters.update(build_director_media_server_adapters(media_service))

    profile = build_director_global_privacy_profile()
    expected = {slot.id for slot in profile.server_adapters}
    if set(adapters) != expected:
        raise ValueError("Director global privacy server adapter set is incomplete.")
    for adapter_id, methods in profile.server_adapter_contracts.items():
        adapter = adapters[adapter_id]
        if any(not callable(getattr(adapter, method, None)) for method in methods):
            raise ValueError("Director global privacy server adapter contract is incomplete.")
    return adapters


__all__ = [
    "DIRECTOR_GLOBAL_PRIVACY_ACTIVATION_GAPS",
    "DIRECTOR_GLOBAL_PRIVACY_REMOVED_FALLBACKS",
    "build_director_global_privacy_profile",
    "build_director_global_server_adapters",
]
