import folder_paths
import pytest

import shared.privacy as shared_privacy
import shared.privacy_keystore as privacy_keystore
import shared.timeline.global_settings as timeline_global_settings


@pytest.fixture(autouse=True)
def register_tmp_path_for_media_resolver(tmp_path, monkeypatch):
    registry = folder_paths.folder_names_and_paths.copy()
    monkeypatch.setattr(folder_paths, "folder_names_and_paths", registry)
    folder_paths.add_model_folder_path("helto_pytest_tmp", str(tmp_path))


@pytest.fixture(autouse=True)
def suite_isolated_global_settings(tmp_path_factory, monkeypatch):
    """Keep tests hermetic: never read or write the developer's real
    config/timeline_director_global_settings.json. Privacy defaults to off so
    take-registration and spill assertions see unredacted values; tests that
    exercise privacy behavior save {"privacy": {"mode": True}} explicitly.
    Lives outside tmp_path because some tests assert tmp_path stays empty."""
    config_dir = tmp_path_factory.mktemp("suite_global_settings_config")
    monkeypatch.setattr(timeline_global_settings, "CONFIG_DIR", config_dir)
    timeline_global_settings.save_global_settings({"privacy": {"mode": False}})
    return timeline_global_settings


@pytest.fixture(autouse=True)
def suite_isolated_privacy_keystore(tmp_path_factory, monkeypatch):
    """Never let tests see a real keystore in ~/.config/helto or leave a
    session cache in the developer's XDG_RUNTIME_DIR."""
    root = tmp_path_factory.mktemp("suite_privacy_keystore")
    monkeypatch.setenv(privacy_keystore.KEYSTORE_ENV, str(root / "privacy_keystore.json"))
    monkeypatch.setenv(privacy_keystore.SESSION_DIR_ENV, str(root / "session"))
    monkeypatch.setattr(privacy_keystore, "SCRYPT_N", 2**12, raising=False)
    backend = getattr(privacy_keystore, "_privacy_keystore_backend", None)
    if backend is not None:
        monkeypatch.setattr(backend, "SCRYPT_N", 2**12, raising=False)
    # Also isolate the legacy plaintext key path, or tests that fall back to
    # legacy mode would mint real key files in the repo's config directory.
    monkeypatch.setattr(shared_privacy, "config_dir", lambda: root / "legacy_config")
