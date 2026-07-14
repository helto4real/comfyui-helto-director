from __future__ import annotations

import tomllib
import subprocess
import sys
from pathlib import Path

from shared.timeline import managed_install
from shared.timeline.managed_global_privacy import build_director_global_privacy_profile
from shared.timeline.managed_media_privacy import DirectorManagedMediaService


def test_registry_metadata_matches_requirements_and_packages_browser_assets():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [
        line.strip()
        for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert project["project"]["dependencies"] == requirements
    assert project["project"]["urls"]["Repository"] == (
        "https://github.com/helto4real/comfyui-helto-director"
    )
    assert project["tool"]["comfy"] == {
        "PublisherId": "helto",
        "DisplayName": "ComfyUI Helto Director",
        "Icon": "",
        "web": "web",
    }
    assert (root / "web/timeline/managed_privacy.js").is_file()


def test_managed_director_imports_do_not_load_the_staged_local_privacy_engine():
    root = Path(__file__).resolve().parents[2]
    script = """
import sys
import shared.segmented_executor
import shared.timeline.managed_library_records
import shared.timeline.managed_media_artifacts
assert "shared.privacy" not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_install_boundary_builds_real_complete_adapter_set_without_installing(monkeypatch, tmp_path):
    service = DirectorManagedMediaService(
        config_dir=tmp_path / "media",
        default_folders={"image": (), "video": (), "audio": ()},
    )
    profile = build_director_global_privacy_profile()
    installed = object()
    captured = {}

    monkeypatch.setattr(managed_install, "_PACK", None)
    monkeypatch.setattr(managed_install, "_ADAPTERS", None)
    monkeypatch.setattr(managed_install, "_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        managed_install,
        "_SINGLETON_DATABASE",
        tmp_path / "config" / "singletons.sqlite3",
    )
    monkeypatch.setattr(managed_install, "_build_media_service", lambda: service)
    monkeypatch.setattr(managed_install, "register_legacy_key_dir", lambda path: captured.setdefault("key_dir", path))
    monkeypatch.setattr(managed_install, "register_legacy_reader_units", lambda units: captured.setdefault("readers", tuple(units)))

    def fake_install(actual_profile, adapters):
        captured["profile"] = actual_profile
        captured["adapters"] = adapters
        return installed

    monkeypatch.setattr(managed_install, "install", fake_install)

    assert managed_install.install_director_privacy() is installed
    assert captured["profile"] == profile
    assert set(captured["adapters"]) == {slot.id for slot in profile.server_adapters}
    assert captured["key_dir"] == Path(tmp_path / "config")
    assert {unit.id for unit in captured["readers"]} == {
        "director-project-library-v1",
        "director-character-library-v1",
    }
    assert managed_install.install_director_privacy() is installed
