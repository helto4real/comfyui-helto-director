import asyncio
import json
import math
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import av
import folder_paths
import pytest
from PIL import Image

from routes import media_browser as media_browser_routes
from shared.contracts.video_timeline import ASSET_TYPE_VIDEO, MODEL_LORA_TARGET_MAIN
from shared import media_browser
from shared.privacy import CRYPTO_AVAILABLE
from shared.timeline.generated_capture import build_generated_take_capture_sidecar
import shared.timeline.global_settings as timeline_global_settings


@pytest.fixture(autouse=True)
def isolated_global_settings(tmp_path, monkeypatch):
    config_dir = tmp_path / "timeline_global_config"
    monkeypatch.setattr(timeline_global_settings, "CONFIG_DIR", config_dir)
    timeline_global_settings.save_global_settings({"privacy": {"mode": False}})


def test_media_browser_preview_route_jobs_are_awaited_and_concurrency_limited(monkeypatch):
    async def run_jobs():
        monkeypatch.setattr(media_browser_routes, "_PREVIEW_JOB_SEMAPHORE", asyncio.Semaphore(2))
        active = 0
        max_active = 0

        async def fake_to_thread(fn, *args, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return fn(*args, **kwargs)

        monkeypatch.setattr(media_browser_routes.asyncio, "to_thread", fake_to_thread)

        def preview_job(value):
            return f"thumb-{value}"

        results = await asyncio.gather(
            *(media_browser_routes._run_preview_job(preview_job, index) for index in range(6))
        )
        return results, max_active

    results, max_active = asyncio.run(run_jobs())

    assert results == ["thumb-0", "thumb-1", "thumb-2", "thumb-3", "thumb-4", "thumb-5"]
    assert max_active <= media_browser_routes.PREVIEW_JOB_CONCURRENCY


class _FakeRequest:
    def __init__(self, *, media_type="image", query=None, payload=None):
        self.match_info = {"media_type": media_type}
        self.rel_url = SimpleNamespace(query=query or {})
        self._payload = payload
        self.headers = {}
        self.cookies = {}

    async def json(self):
        return self._payload


class _RecordingRoutes:
    def __init__(self):
        self.handlers = {}

    def _record(self, method, path):
        def decorator(handler):
            self.handlers[(method, path)] = handler
            return handler

        return decorator

    def get(self, path):
        return self._record("GET", path)

    def post(self, path):
        return self._record("POST", path)

    def delete(self, path):
        return self._record("DELETE", path)


def _register_media_browser_test_routes(monkeypatch):
    routes = _RecordingRoutes()
    server = SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(routes=routes)))
    monkeypatch.setitem(sys.modules, "server", server)
    monkeypatch.setattr(media_browser_routes, "_ROUTES_REGISTERED", False)
    assert media_browser_routes.register_media_browser_routes() is True
    return routes


def test_media_browser_privacy_can_upgrade_but_not_downgrade_global_setting():
    assert media_browser_routes.requested_privacy_mode(None) is False
    assert media_browser_routes.requested_privacy_mode(True) is True

    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})

    assert media_browser_routes.requested_privacy_mode(None) is True
    assert media_browser_routes.requested_privacy_mode(False) is True
    assert media_browser_routes.requested_privacy_mode("false") is True


@pytest.mark.parametrize("privacy_value", [None, "false"])
def test_browser_thumbnail_route_uses_global_privacy_when_request_omits_or_disables_it(
    privacy_value,
    monkeypatch,
):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    routes = _register_media_browser_test_routes(monkeypatch)
    checked_requests = []
    preview_kwargs = {}

    monkeypatch.setattr(
        media_browser_routes,
        "check_privacy_token",
        lambda request: checked_requests.append(request) or None,
    )
    monkeypatch.setattr(
        media_browser_routes,
        "resolve_browser_media_path",
        lambda *_args: Path("/synthetic/private.png"),
    )

    async def fake_preview_job(_fn, *_args, **kwargs):
        preview_kwargs.update(kwargs)
        return b"RIFFsyntheticWEBP"

    monkeypatch.setattr(media_browser_routes, "_run_preview_job", fake_preview_job)
    query = {"alias": "input", "filename": "ignored.png"}
    if privacy_value is not None:
        query["privacy"] = privacy_value
    request = _FakeRequest(query=query)

    response = asyncio.run(
        routes.handlers[("GET", f"{media_browser_routes.ROUTE_PREFIX}/{{media_type}}/thumb")](request)
    )

    assert response.status == 200
    assert response.body == b"RIFFsyntheticWEBP"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert preview_kwargs["privacy_mode"] is True
    assert checked_requests == [request]


def test_private_browser_items_require_authorization_before_folder_access(monkeypatch):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    routes = _register_media_browser_test_routes(monkeypatch)
    denied = media_browser_routes.web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(media_browser_routes, "check_privacy_token", lambda _request: denied)

    def unexpected_folder_access(*_args):
        raise AssertionError("unauthorized item listing must not resolve a folder")

    monkeypatch.setattr(media_browser_routes, "folder_by_alias", unexpected_folder_access)
    request = _FakeRequest(query={"alias": "private", "privacy": "false"})

    response = asyncio.run(
        routes.handlers[("GET", f"{media_browser_routes.ROUTE_PREFIX}/{{media_type}}/items")](request)
    )

    assert response is denied


def test_authorized_private_browser_items_emit_private_view_urls(monkeypatch):
    routes = _register_media_browser_test_routes(monkeypatch)
    monkeypatch.setattr(media_browser_routes, "check_privacy_token", lambda _request: None)
    monkeypatch.setattr(
        media_browser_routes,
        "folder_by_alias",
        lambda *_args: SimpleNamespace(enabled=True, path="/synthetic/private"),
    )
    monkeypatch.setattr(
        media_browser_routes,
        "list_media",
        lambda *_args, **_kwargs: [
            {"filename": "secret.png", "mtime": 1, "path": "/synthetic/private/secret.png"}
        ],
    )

    async def direct_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(media_browser_routes.asyncio, "to_thread", direct_to_thread)
    request = _FakeRequest(query={"alias": "private", "privacy": "1"})

    response = asyncio.run(
        routes.handlers[("GET", f"{media_browser_routes.ROUTE_PREFIX}/{{media_type}}/items")](request)
    )
    payload = json.loads(response.body)

    assert response.status == 200
    assert "privacy=1" in payload["images"][0]["view_url"]
    assert "privacy=1" in payload["images"][0]["thumb_url"]


def test_folder_mutations_require_authorization_before_reading_payload(monkeypatch):
    routes = _register_media_browser_test_routes(monkeypatch)
    denied = media_browser_routes.web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(media_browser_routes, "check_privacy_token", lambda _request: denied)
    request = _FakeRequest(payload={"path": "/synthetic/media"})

    response = asyncio.run(
        routes.handlers[("POST", f"{media_browser_routes.ROUTE_PREFIX}/{{media_type}}/folders")](request)
    )

    assert response is denied


def test_project_take_delete_requires_authorization_before_reading_payload(monkeypatch):
    routes = _register_media_browser_test_routes(monkeypatch)
    denied = media_browser_routes.web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(media_browser_routes, "check_privacy_token", lambda _request: denied)

    class UnreadableRequest:
        async def json(self):
            raise AssertionError("unauthorized delete must not read its payload")

    response = asyncio.run(
        routes.handlers[("POST", f"{media_browser_routes.ROUTE_PREFIX}/project_takes/delete")](
            UnreadableRequest()
        )
    )

    assert response is denied


def test_private_browser_view_requires_authorization_before_path_resolution(monkeypatch):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    routes = _register_media_browser_test_routes(monkeypatch)
    denied = media_browser_routes.web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(media_browser_routes, "check_privacy_token", lambda _request: denied)

    def unexpected_resolution(*_args):
        raise AssertionError("unauthorized view must not resolve a path")

    monkeypatch.setattr(media_browser_routes, "resolve_browser_media_path", unexpected_resolution)
    request = _FakeRequest(query={"alias": "private", "filename": "secret.png"})

    response = asyncio.run(
        routes.handlers[("GET", f"{media_browser_routes.ROUTE_PREFIX}/{{media_type}}/view")](request)
    )

    assert response is denied


def test_project_take_route_uses_global_privacy_for_redaction(monkeypatch):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    routes = _register_media_browser_test_routes(monkeypatch)
    observed = {}

    def fake_list_project_take_captures(_project, _shot_id, *, privacy_mode):
        observed["privacy_mode"] = privacy_mode
        return {
            "take_directory": "/private/project/takes",
            "storage": {
                "asset_root_directory": "/private/assets",
                "project_directory": "/private/project",
            },
            "captures": [],
        }

    async def direct_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(media_browser_routes, "list_project_take_captures", fake_list_project_take_captures)
    monkeypatch.setattr(media_browser_routes.asyncio, "to_thread", direct_to_thread)
    request = _FakeRequest(
        payload={"project": {}, "shot_id": "shot_001", "privacy": False},
    )

    response = asyncio.run(
        routes.handlers[("POST", f"{media_browser_routes.ROUTE_PREFIX}/project_takes")](request)
    )
    payload = json.loads(response.body)

    assert response.status == 200
    assert observed["privacy_mode"] is True
    assert payload["take_directory"] == "Private path"
    assert payload["storage"] == {
        "asset_root_directory": "Private path",
        "project_directory": "Private path",
    }


def test_global_privacy_redacts_project_take_route_paths():
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    privacy_mode = media_browser_routes.requested_privacy_mode(False)
    payload = {
        "take_directory": "/private/project/takes",
        "storage": {
            "asset_root_directory": "/private/assets",
            "project_directory": "/private/project",
        },
        "captures": [],
    }

    redacted = media_browser_routes.redact_project_take_payload(payload, privacy_mode)

    assert redacted["take_directory"] == "Private path"
    assert redacted["storage"] == {
        "asset_root_directory": "Private path",
        "project_directory": "Private path",
    }


def test_private_media_browser_errors_do_not_expose_sensitive_paths():
    response = media_browser_routes._browser_error_response(
        ValueError("Media file not found: /private/project/secret.mp4"),
        True,
    )

    payload = json.loads(response.body)
    assert response.status == 400
    assert payload == {"error": "PRIVATE_MEDIA_BROWSER_REQUEST_FAILED: Private media request failed."}
    assert "/private/project" not in response.text


def test_media_browser_folder_config_defaults_adds_removes_and_rejects_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    original_input = folder_paths.get_input_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    try:
        (tmp_path / "input").mkdir()
        custom = tmp_path / "custom"
        custom.mkdir()

        folders = media_browser.load_folders("image")
        assert folders[0].alias == "input"
        assert folders[0].path == str(tmp_path / "input")

        media_browser.add_folder("image", "custom", str(custom))
        assert [folder.alias for folder in media_browser.load_folders("image")] == ["input", "custom"]

        media_browser.remove_folder("image", "custom")
        assert [folder.alias for folder in media_browser.load_folders("image")] == ["input"]

        media_browser.add_folder("image", "", str(custom))
        folders = media_browser.load_folders("image")
        assert folders[-1].alias == "custom"
        payload = media_browser.folder_payload("image")
        assert payload[-1]["display_name"] == "custom"
        assert payload[-1]["path"] == str(custom)
        with pytest.raises(ValueError):
            media_browser.add_folder("image", "", str(custom))
        media_browser.remove_folder("image", "custom")

        with pytest.raises(ValueError):
            media_browser.add_folder("image", "../bad", str(custom))
    finally:
        folder_paths.set_input_directory(original_input)


def test_video_browser_defaults_include_output_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    original_input = folder_paths.get_input_directory()
    original_output = folder_paths.get_output_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    folder_paths.set_output_directory(str(tmp_path / "output"))
    try:
        (tmp_path / "input").mkdir()
        (tmp_path / "output").mkdir()

        folders = media_browser.load_folders("video")

        assert [folder.alias for folder in folders] == ["input", "output"]
        with pytest.raises(ValueError):
            media_browser.remove_folder("video", "output")
    finally:
        folder_paths.set_input_directory(original_input)
        folder_paths.set_output_directory(original_output)


def test_media_browser_lists_only_matching_extensions(tmp_path):
    Image.new("RGB", (16, 16), color=(10, 20, 30)).save(tmp_path / "image.png")
    (tmp_path / "movie.mp4").write_bytes(b"not a real movie")
    write_test_wav(tmp_path / "tone.wav")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    assert [item["filename"] for item in media_browser.list_media("image", tmp_path)] == ["image.png"]
    assert [item["filename"] for item in media_browser.list_media("video", tmp_path)] == ["movie.mp4"]
    assert [item["filename"] for item in media_browser.list_media("audio", tmp_path)] == ["tone.wav"]


def test_video_browser_reads_generated_take_sidecar_and_privacy_redacts(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"not a real movie")
    sidecar = build_generated_take_capture_sidecar(
        {
            "shot_id": "shot_001",
            "asset": {"name": "private_subject.mp4"},
            "take": {
                "take_id": "take_001",
                "seed": 321,
                "resolved_loras": {
                    "model_family": "LTX",
                    "model_version": "2.3",
                    "targets": {
                        MODEL_LORA_TARGET_MAIN: [
                            {"name": "secret_style.safetensors", "strength_model": 0.8}
                        ]
                    },
                },
            },
        },
        media={
            "type": ASSET_TYPE_VIDEO,
            "filename": "clip.mp4",
            "frame_rate": 24.0,
            "frame_count": 12,
        },
    )
    (tmp_path / "clip.helto_take.json").write_text(json.dumps(sidecar), encoding="utf-8")

    public_item = media_browser.list_media("video", tmp_path)[0]
    private_item = media_browser.list_media("video", tmp_path, privacy_mode=True)[0]

    assert public_item["has_take_capture"] is True
    assert public_item["take_capture"]["registration"]["take"]["seed"] == 321
    assert public_item["take_capture"]["media"]["frame_rate"] == 24.0
    assert "secret_style" in json.dumps(public_item["take_capture"])
    assert "secret_style" not in json.dumps(private_item["take_capture"])
    row = private_item["take_capture"]["registration"]["take"]["resolved_loras"]["targets"][MODEL_LORA_TARGET_MAIN][0]
    assert row["name"] == "lora_001"


def test_project_take_capture_discovery_filters_by_shot_and_ignores_malformed_sidecars(tmp_path):
    timeline_global_settings.save_global_settings({"storage": {"asset_root_directory": str(tmp_path)}, "privacy": {"mode": False}})
    project = {
        "identity": {"project_id": "proj_capturetest", "name": "Capture Test"},
        "storage": {
            "schema_version": 2,
            "project_directory_name": "capture_test_proj_capturetest",
        },
    }
    take_dir = tmp_path / "capture_test_proj_capturetest" / "takes" / "shot_001"
    take_dir.mkdir(parents=True)
    matching = take_dir / "matching.mp4"
    mismatched = take_dir / "mismatched.mp4"
    malformed = take_dir / "malformed.mp4"
    matching.write_bytes(b"video")
    mismatched.write_bytes(b"video")
    malformed.write_bytes(b"video")
    matching.write_text("video", encoding="utf-8")
    mismatched.write_text("video", encoding="utf-8")
    malformed.write_text("video", encoding="utf-8")
    matching.with_suffix(".helto_take.json").write_text(
        json.dumps(
            build_generated_take_capture_sidecar(
                {
                    "shot_id": "shot_001",
                    "shot_ids": ["shot_001"],
                    "take": {"take_id": "take_match"},
                },
                media={"type": ASSET_TYPE_VIDEO, "filename": matching.name},
            )
        ),
        encoding="utf-8",
    )
    mismatched.with_suffix(".helto_take.json").write_text(
        json.dumps(
            build_generated_take_capture_sidecar(
                {
                    "shot_id": "shot_002",
                    "shot_ids": ["shot_002"],
                    "take": {"take_id": "take_other"},
                },
                media={"type": ASSET_TYPE_VIDEO, "filename": mismatched.name},
            )
        ),
        encoding="utf-8",
    )
    malformed.with_suffix(".helto_take.json").write_text("{bad json", encoding="utf-8")

    payload = media_browser.list_project_take_captures(project, "shot_001")

    assert payload["shot_id"] == "shot_001"
    assert payload["take_directory"] == str(take_dir)
    assert [item["filename"] for item in payload["captures"]] == ["matching.mp4"]
    assert payload["captures"][0]["take_capture"]["registration"]["take"]["take_id"] == "take_match"


def test_project_take_capture_discovery_recreates_deleted_project_storage(tmp_path):
    timeline_global_settings.save_global_settings({"storage": {"asset_root_directory": str(tmp_path)}, "privacy": {"mode": False}})
    project = {
        "identity": {"project_id": "proj_deleted", "name": "Deleted Project"},
        "storage": {
            "schema_version": 2,
            "project_directory_name": "deleted_project_proj_deleted",
        },
    }
    take_dir = tmp_path / "deleted_project_proj_deleted" / "takes" / "shot_001"

    payload = media_browser.list_project_take_captures(project, "shot_001")

    assert payload["shot_id"] == "shot_001"
    assert payload["take_directory"] == str(take_dir)
    assert payload["storage"]["project_directory"] == str(tmp_path / "deleted_project_proj_deleted")
    assert payload["captures"] == []
    assert take_dir.is_dir()


def test_delete_project_take_capture_removes_media_sidecar_and_discovery_entry(tmp_path):
    project = project_storage_payload(tmp_path)
    take_dir = tmp_path / "capture_test_proj_capturetest" / "takes" / "shot_001"
    take_dir.mkdir(parents=True)
    media_path = take_dir / "matching.mp4"
    media_path.write_text("video", encoding="utf-8")
    media_path.with_suffix(".helto_take.json").write_text(
        json.dumps(
            build_generated_take_capture_sidecar(
                {
                    "shot_id": "shot_001",
                    "shot_ids": ["shot_001"],
                    "take": {"take_id": "take_match"},
                },
                media={"type": ASSET_TYPE_VIDEO, "filename": media_path.name},
            )
        ),
        encoding="utf-8",
    )

    result = media_browser.delete_project_take_capture(project, "shot_001", str(media_path), take_id="take_match")

    assert result["ok"] is True
    assert result["deleted"] is True
    assert result["files_deleted"] == 2
    assert result["take_id"] == "take_match"
    assert not media_path.exists()
    assert not media_path.with_suffix(".helto_take.json").exists()
    assert media_browser.list_project_take_captures(project, "shot_001")["captures"] == []


def test_delete_project_take_capture_allows_missing_media_with_valid_take_id(tmp_path):
    project = project_storage_payload(tmp_path)
    take_dir = tmp_path / "capture_test_proj_capturetest" / "takes" / "shot_001"
    take_dir.mkdir(parents=True)
    media_path = take_dir / "deleted.mp4"
    sidecar_path = media_path.with_suffix(".helto_take.json")
    sidecar_path.write_text(
        json.dumps(
            build_generated_take_capture_sidecar(
                {"shot_id": "shot_001", "shot_ids": ["shot_001"], "take": {"take_id": "take_deleted"}},
                media={"type": ASSET_TYPE_VIDEO, "filename": media_path.name},
            )
        ),
        encoding="utf-8",
    )

    result = media_browser.delete_project_take_capture(project, "shot_001", str(media_path), take_id="take_deleted")

    assert result["ok"] is True
    assert result["media_missing"] is True
    assert result["deleted"] is True
    assert result["files_deleted"] == 1
    assert result["take_id"] == "take_deleted"
    assert not sidecar_path.exists()


def test_delete_project_take_capture_allows_missing_media_without_sidecar_when_take_id_is_known(tmp_path):
    project = project_storage_payload(tmp_path)
    media_path = tmp_path / "capture_test_proj_capturetest" / "takes" / "shot_001" / "deleted.mp4"

    result = media_browser.delete_project_take_capture(project, "shot_001", str(media_path), take_id="take_deleted")

    assert result["ok"] is True
    assert result["media_missing"] is True
    assert result["deleted"] is False
    assert result["files_deleted"] == 0
    assert result["take_id"] == "take_deleted"


def test_delete_project_take_capture_missing_media_still_requires_take_id_and_matching_sidecar(tmp_path):
    project = project_storage_payload(tmp_path)
    take_dir = tmp_path / "capture_test_proj_capturetest" / "takes" / "shot_001"
    take_dir.mkdir(parents=True)
    media_path = take_dir / "deleted.mp4"
    sidecar_path = media_path.with_suffix(".helto_take.json")
    sidecar_path.write_text(
        json.dumps(
            build_generated_take_capture_sidecar(
                {"shot_id": "shot_001", "take": {"take_id": "take_other"}},
                media={"type": ASSET_TYPE_VIDEO, "filename": media_path.name},
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="TAKE_DELETE_MEDIA_NOT_FOUND"):
        media_browser.delete_project_take_capture(project, "shot_001", str(media_path))
    with pytest.raises(ValueError, match="TAKE_DELETE_TAKE_MISMATCH"):
        media_browser.delete_project_take_capture(project, "shot_001", str(media_path), take_id="take_deleted")
    assert sidecar_path.exists()


def test_delete_project_take_capture_rejects_outside_paths_and_mismatched_sidecars(tmp_path):
    project = project_storage_payload(tmp_path)
    take_dir = tmp_path / "capture_test_proj_capturetest" / "takes" / "shot_001"
    take_dir.mkdir(parents=True)
    outside_path = tmp_path / "outside.mp4"
    outside_path.write_text("video", encoding="utf-8")
    shot_mismatch = take_dir / "shot_mismatch.mp4"
    take_mismatch = take_dir / "take_mismatch.mp4"
    shot_mismatch.write_text("video", encoding="utf-8")
    take_mismatch.write_text("video", encoding="utf-8")
    shot_mismatch.with_suffix(".helto_take.json").write_text(
        json.dumps(
            build_generated_take_capture_sidecar(
                {"shot_id": "shot_other", "take": {"take_id": "take_match"}},
                media={"type": ASSET_TYPE_VIDEO, "filename": shot_mismatch.name},
            )
        ),
        encoding="utf-8",
    )
    take_mismatch.with_suffix(".helto_take.json").write_text(
        json.dumps(
            build_generated_take_capture_sidecar(
                {"shot_id": "shot_001", "take": {"take_id": "take_other"}},
                media={"type": ASSET_TYPE_VIDEO, "filename": take_mismatch.name},
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="TAKE_DELETE_PATH_OUTSIDE_PROJECT"):
        media_browser.delete_project_take_capture(project, "shot_001", str(outside_path))
    with pytest.raises(ValueError, match="TAKE_DELETE_SHOT_MISMATCH"):
        media_browser.delete_project_take_capture(project, "shot_001", str(shot_mismatch), take_id="take_match")
    with pytest.raises(ValueError, match="TAKE_DELETE_TAKE_MISMATCH"):
        media_browser.delete_project_take_capture(project, "shot_001", str(take_mismatch), take_id="take_match")

    assert outside_path.exists()
    assert shot_mismatch.exists()
    assert take_mismatch.exists()


def test_delete_project_take_capture_privacy_redacts_paths(tmp_path):
    project = project_storage_payload(tmp_path)
    take_dir = tmp_path / "capture_test_proj_capturetest" / "takes" / "shot_001"
    take_dir.mkdir(parents=True)
    media_path = take_dir / "private.mp4"
    media_path.write_text("video", encoding="utf-8")
    media_path.with_suffix(".helto_take.json").write_text(
        json.dumps(
            build_generated_take_capture_sidecar(
                {"shot_id": "shot_001", "take": {"take_id": "take_private"}},
                media={"type": ASSET_TYPE_VIDEO, "filename": media_path.name},
            )
        ),
        encoding="utf-8",
    )

    result = media_browser.delete_project_take_capture(project, "shot_001", str(media_path), privacy_mode=True)

    assert result["path"] == "Private path"
    assert result["deleted_paths"] == ["Private path", "Private path"]


def test_media_browser_rejects_wrong_extension_and_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    original_input = folder_paths.get_input_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    try:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        Image.new("RGB", (16, 16), color=(10, 20, 30)).save(input_dir / "image.png")

        with pytest.raises(ValueError):
            media_browser.resolve_browser_media_path("video", "input", "image.png")
        with pytest.raises(ValueError):
            media_browser.resolve_browser_media_path("image", "input", "../image.png")
    finally:
        folder_paths.set_input_directory(original_input)


def test_image_and_video_browser_thumbnails_return_cached_webp(tmp_path, monkeypatch):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    original_input = folder_paths.get_input_directory()
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        Image.new("RGB", (64, 32), color=(10, 20, 30)).save(input_dir / "image.png")
        write_test_video(input_dir / "clip.mp4")

        image_thumb = media_browser.make_browser_thumbnail("image", "input", "image.png")
        video_thumb = media_browser.make_browser_thumbnail("video", "input", "clip.mp4")

        assert image_thumb.suffix == ".webp"
        assert video_thumb.suffix == ".webp"
        assert image_thumb.is_file()
        assert video_thumb.is_file()
    finally:
        folder_paths.set_input_directory(original_input)
        folder_paths.set_temp_directory(original_temp)


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for encrypted preview tests")
def test_image_browser_thumbnail_privacy_returns_bytes_and_encrypted_cache(
    tmp_path,
    monkeypatch,
    unlocked_privacy_keystore,
):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    original_input = folder_paths.get_input_directory()
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        Image.new("RGB", (64, 32), color=(10, 20, 30)).save(input_dir / "image.png")

        thumb = media_browser.make_browser_thumbnail("image", "input", "image.png", privacy_mode=True)

        assert isinstance(thumb, bytes)
        assert thumb.startswith(b"RIFF")
        assert list((tmp_path / "temp" / "helto_timeline_director" / "thumbnails").glob("*.webp.enc"))
        assert list((tmp_path / "temp" / "helto_timeline_director" / "thumbnails").glob("*.webp")) == []
    finally:
        folder_paths.set_input_directory(original_input)
        folder_paths.set_temp_directory(original_temp)


def project_storage_payload(tmp_path):
    timeline_global_settings.save_global_settings({"storage": {"asset_root_directory": str(tmp_path)}, "privacy": {"mode": False}})
    return {
        "identity": {"project_id": "proj_capturetest", "name": "Capture Test"},
        "storage": {
            "schema_version": 2,
            "project_directory_name": "capture_test_proj_capturetest",
        },
    }


def write_test_wav(path):
    sample_rate = 8000
    duration_seconds = 0.1
    sample_count = int(sample_rate * duration_seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(sample_count):
            value = int(math.sin(index / sample_rate * math.tau * 440.0) * 16000)
            frames.extend(value.to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(frames))


def write_test_video(path):
    try:
        with av.open(str(path), "w") as container:
            stream = container.add_stream("mpeg4", rate=1)
            stream.width = 32
            stream.height = 32
            stream.pix_fmt = "yuv420p"
            frame = av.VideoFrame.from_image(Image.new("RGB", (32, 32), color=(30, 60, 90)))
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
    except Exception as exc:
        pytest.skip(f"PyAV video encode unavailable: {exc}")
