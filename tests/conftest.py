import folder_paths
import pytest


@pytest.fixture(autouse=True)
def register_tmp_path_for_media_resolver(tmp_path, monkeypatch):
    registry = folder_paths.folder_names_and_paths.copy()
    monkeypatch.setattr(folder_paths, "folder_names_and_paths", registry)
    folder_paths.add_model_folder_path("helto_pytest_tmp", str(tmp_path))
