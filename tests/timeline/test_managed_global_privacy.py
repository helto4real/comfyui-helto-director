from __future__ import annotations

from pathlib import Path

from helto_privacy.mode_participants import participant_manifest

from shared.timeline.managed_global_privacy import (
    DIRECTOR_GLOBAL_PRIVACY_ACTIVATION_GAPS,
    DIRECTOR_GLOBAL_PRIVACY_REMOVED_FALLBACKS,
    build_director_global_privacy_profile,
    build_director_global_server_adapters,
)
from shared.timeline.managed_media_privacy import DirectorManagedMediaService


EXPECTED_GLOBAL_PROFILE_FINGERPRINT = (
    "948ad2440e27b7fdba7e40ac1928424afae3b8a19c27d859ee61ce25f42ab835"
)


def test_d1_assembles_the_exact_complete_profile_and_participant_order():
    profile = build_director_global_privacy_profile()
    assert profile.fingerprint == EXPECTED_GLOBAL_PROFILE_FINGERPRINT
    assert tuple(item.id for item in profile.records) == (
        "director-character",
        "director-project",
    )
    assert tuple(item.id for item in profile.singletons) == (
        "capture-index",
        "media-folder-settings",
        "take-deletion-journal",
    )
    assert {item.id for item in profile.artifacts} == {
        "thumbnail",
        "waveform",
        "capture-take-sidecar",
        "timeline-segment-spill",
    }

    manifest = participant_manifest(profile, profile.scopes[0])
    assert tuple(item["id"] for item in manifest) == (
        "external-workflow.timeline-state",
        "record.director-characters.director-character",
        "record.director-projects.director-project",
        "singleton.director-durable-state.capture-index",
        "singleton.director-durable-state.media-folder-settings",
        "singleton.director-durable-state.take-deletion-journal",
        "artifact.shared",
        "mode-source.director-global-mode-state",
    )
    assert manifest[-1]["kind"] == "mode-source"


def test_d1_complete_server_adapter_assembly_satisfies_every_contract(tmp_path):
    service = DirectorManagedMediaService(
        config_dir=tmp_path / "media-config",
        default_folders={"image": (), "video": (), "audio": ()},
    )
    adapters = build_director_global_server_adapters(
        media_service=service,
        singleton_database_path=tmp_path / "singletons.sqlite3",
        settings_dir=tmp_path / "settings",
        library_dir=tmp_path / "library",
        cache_root=tmp_path / "cache",
    )
    profile = build_director_global_privacy_profile()
    assert set(adapters) == {slot.id for slot in profile.server_adapters}
    assert all(
        callable(getattr(adapters[adapter_id], method, None))
        for adapter_id, methods in profile.server_adapter_contracts.items()
        for method in methods
    )


def test_d1_removed_every_local_authority_and_transition_fallback():
    assert DIRECTOR_GLOBAL_PRIVACY_REMOVED_FALLBACKS == (
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
    package_root = Path(__file__).resolve().parents[2]
    assert all(
        not (package_root / relative).exists()
        for relative in DIRECTOR_GLOBAL_PRIVACY_REMOVED_FALLBACKS
    )
    assert not (package_root / "shared/_vendored_keystore.py").exists()
    assert DIRECTOR_GLOBAL_PRIVACY_ACTIVATION_GAPS == ()
