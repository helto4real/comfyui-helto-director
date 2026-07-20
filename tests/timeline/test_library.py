import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web as aiohttp_web

from routes import timeline_library as timeline_library_routes
from shared.privacy import CRYPTO_AVAILABLE
from shared.timeline.defaults import create_default_video_timeline
from shared.timeline_library import (
    create_item,
    delete_item,
    duplicate_item,
    library_path,
    list_items,
    load_library,
    patch_item,
    preview_character_item,
    preview_project_item,
    replace_item,
    use_item,
)


def test_library_defaults_without_config_file(tmp_path):
    assert load_library(tmp_path) == {"schema_version": "1.0", "version": 1, "projects": [], "characters": []}
    assert list_items(tmp_path) == {"schema_version": "1.0", "version": 1, "projects": [], "characters": []}
    assert not library_path(tmp_path).exists()


def test_legacy_timelines_migrate_to_projects_on_load(tmp_path):
    path = library_path(tmp_path)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": 1,
                "timelines": [
                    {
                        "id": "legacy-timeline",
                        "kind": "timeline",
                        "type": "TIMELINE_LIBRARY_ITEM",
                        "name": "Legacy Timeline",
                        "private": False,
                        "summary": {},
                        "created_at": "2026-06-20T00:00:00Z",
                        "updated_at": "2026-06-20T00:00:00Z",
                        "payload": sample_timeline(prompt="legacy prompt"),
                    }
                ],
                "characters": [],
            }
        ),
        encoding="utf-8",
    )

    library = load_library(tmp_path)
    assert "timelines" not in library
    assert library["projects"][0]["kind"] == "project"
    assert library["projects"][0]["type"] == "PROJECT_LIBRARY_ITEM"
    assert list_items(tmp_path)["projects"][0]["name"] == "Legacy Timeline"


def test_timeline_crud_duplicate_delete_and_use(tmp_path):
    created = create_item(
        "project",
        sample_timeline(prompt="first prompt"),
        metadata={"id": "timeline-a", "name": "Timeline A", "description": "visible", "tags": ["demo"]},
        base_dir=tmp_path,
    )

    assert created["id"] == "timeline-a"
    assert created["type"] == "PROJECT_LIBRARY_ITEM"
    assert created["is_private"] is False
    assert created["project"]["director_track"]["sections"][0]["prompt"] == "first prompt"
    assert created["project"]["project"]["identity"]["name"] == "Timeline A"
    assert created["summary"]["section_count"] == 1
    assert created["summary"]["character_reference_count"] == 0

    old_take_path = "/old/project/takes/shot_001/take.mp4"
    replacement_timeline = sample_timeline(prompt="replacement prompt")
    replacement_timeline["assets"].append(
        {"asset_id": "accepted_take_asset", "type": "Video", "path": old_take_path, "name": "take.mp4"}
    )
    replacement_timeline["sequence"]["shots"].append(
        {
            "shot_id": "shot_001",
            "type": "Generated",
            "start_time": 0.0,
            "end_time": 2.0,
            "section_ids": ["section-image"],
            "takes": [{"take_id": "take_001", "status": "Accepted", "asset_id": "accepted_take_asset"}],
            "accepted_take_id": "take_001",
            "clip_instance": None,
        }
    )

    replaced = replace_item(
        "project",
        "timeline-a",
        replacement_timeline,
        metadata={"name": "Timeline B", "description": "updated"},
        base_dir=tmp_path,
    )
    assert replaced["name"] == "Timeline B"
    assert replaced["project"]["project"]["identity"]["name"] == "Timeline B"
    assert replaced["project"]["director_track"]["sections"][0]["prompt"] == "replacement prompt"
    replaced_take_asset = next(asset for asset in replaced["project"]["assets"] if asset["asset_id"] == "accepted_take_asset")
    assert replaced_take_asset["path"] == old_take_path

    patched = patch_item(
        "project",
        "timeline-a",
        metadata={"description": "patched"},
        base_dir=tmp_path,
    )
    assert patched["description"] == "patched"
    assert patched["project"]["project"]["identity"]["name"] == "Timeline B"
    assert patched["project"]["director_track"]["sections"][0]["prompt"] == "replacement prompt"

    duplicate = duplicate_item("project", "timeline-a", metadata={"id": "timeline-b"}, base_dir=tmp_path)
    assert duplicate["id"] == "timeline-b"
    assert duplicate["project"]["director_track"]["sections"][0]["prompt"] == "replacement prompt"
    assert duplicate["project"]["project"]["identity"]["project_id"] != replaced["project"]["project"]["identity"]["project_id"]
    assert duplicate["project"]["project"]["identity"]["name"] == "Timeline B Copy"
    assert duplicate["project"]["project"]["storage"]["project_directory_name"] != replaced["project"]["project"]["storage"]["project_directory_name"]
    assert "asset_root_directory" not in duplicate["project"]["project"]["storage"]
    assert "asset_root_directory" not in replaced["project"]["project"]["storage"]
    duplicate_take_asset = next(asset for asset in duplicate["project"]["assets"] if asset["asset_id"] == "accepted_take_asset")
    assert duplicate_take_asset["path"] == old_take_path

    used = use_item("project", "timeline-a", base_dir=tmp_path)
    assert used["project"]["director_track"]["sections"][0]["prompt"] == "replacement prompt"
    assert used["last_used_at"]

    deleted = delete_item("project", "timeline-a", base_dir=tmp_path)
    assert deleted == {"id": "timeline-a", "kind": "project"}
    assert [item["id"] for item in list_items(tmp_path)["projects"]] == ["timeline-b"]


def test_character_crud_duplicate_delete_and_use(tmp_path):
    character = {
        "label": "image3",
        "description": "hero reference",
        "strength": 0.75,
        "image": {"path": "/refs/hero.png", "thumbnail": [1, 2, 3]},
    }

    created = create_item("character", character, metadata={"id": "char-a"}, base_dir=tmp_path)
    assert created["type"] == "CHARACTER_LIBRARY_ITEM"
    assert created["character"]["label"] == "image3"
    assert created["character"]["image"]["path"] == "/refs/hero.png"
    assert "thumbnail" not in created["character"]["image"]

    patched = patch_item("character", "char-a", metadata={"name": "Hero"}, base_dir=tmp_path)
    assert patched["name"] == "Hero"

    duplicate = duplicate_item("character", "char-a", metadata={"id": "char-b"}, base_dir=tmp_path)
    assert duplicate["character"]["image"]["path"] == "/refs/hero.png"

    used = use_item("character", "char-b", base_dir=tmp_path)
    assert used["character"]["strength"] == pytest.approx(0.75)

    delete_item("character", "char-a", base_dir=tmp_path)
    assert [item["id"] for item in list_items(tmp_path)["characters"]] == ["char-b"]


def test_timeline_summary_and_validation_are_recomputed(tmp_path):
    timeline = sample_timeline(prompt="summary prompt")
    timeline["validation"] = {
        "is_valid": False,
        "errors": [{"code": "STALE"}],
        "warnings": [{"code": "STALE_WARNING"}],
        "info": [],
    }

    created = create_item("project", timeline, metadata={"id": "summary"}, base_dir=tmp_path)

    assert created["summary"]["duration_seconds"] == pytest.approx(2.0)
    assert created["summary"]["frame_rate"] == pytest.approx(12.0)
    assert created["summary"]["section_count"] == 1
    assert created["summary"]["asset_count"] == 1
    assert created["summary"]["error_count"] == 0
    assert created["project"]["validation"]["errors"] == []
    assert created["project"]["validation"]["warnings"] == []


def test_list_timeline_shell_includes_sanitized_preview_assets_for_non_private_items(tmp_path):
    timeline = sample_timeline(prompt="preview shell")
    timeline["assets"][0].update(
        {
            "thumbnail": "embedded thumb",
            "waveform": [0.1, 0.2],
            "preview_data": "secret",
            "cache_key": "not shell metadata",
            "width": 640,
            "height": 360,
        }
    )
    timeline["assets"].append(
        {
            "asset_id": "asset-video",
            "type": "Video",
            "source_kind": "FilePath",
            "path": "/media/clip.mp4",
            "name": "clip.mp4",
        }
    )
    timeline["project"]["duration_seconds"] = 4.0
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section-video",
            "type": "Video",
            "start_time": 2.0,
            "end_time": 4.0,
            "prompt": "video prompt",
            "video": {"asset_id": "asset-video"},
        }
    )

    create_item("project", timeline, metadata={"id": "preview-shell"}, base_dir=tmp_path)

    item = list_items(tmp_path)["projects"][0]
    assert item["preview_assets"] == [
        {
            "asset_id": "asset-image",
            "height": 360,
            "name": "ref.png",
            "path": "/media/ref.png",
            "source_kind": "FilePath",
            "type": "Image",
            "width": 640,
        },
        {
            "asset_id": "asset-video",
            "name": "clip.mp4",
            "path": "/media/clip.mp4",
            "source_kind": "FilePath",
            "type": "Video",
        },
    ]
    assert "payload" not in item
    assert "project" not in item
    assert "thumbnail" not in json.dumps(item)
    assert "waveform" not in json.dumps(item)
    assert "preview_data" not in json.dumps(item)
    assert "cache_key" not in json.dumps(item)


def test_replace_timeline_drops_orphan_assets_and_previews_direct_paths(tmp_path):
    create_item(
        "project",
        sample_timeline(path="/media/old.png"),
        metadata={"id": "replace-orphan"},
        base_dir=tmp_path,
    )

    replacement = sample_timeline(prompt="direct path", path="/media/old.png")
    replacement["assets"].append(
        {
            "asset_id": "orphan-image",
            "type": "Image",
            "source_kind": "FilePath",
            "path": "/media/orphan.png",
            "name": "orphan.png",
        }
    )
    replacement["director_track"]["sections"][0]["image"] = {
        "path": "/media/new-direct.png",
        "name": "new-direct.png",
    }

    replaced = replace_item("project", "replace-orphan", replacement, base_dir=tmp_path)
    stored_payload = load_library(tmp_path)["projects"][0]["payload"]
    listed_item = list_items(tmp_path)["projects"][0]
    preview = preview_project_item("replace-orphan", base_dir=tmp_path)

    assert replaced["project"]["assets"] == []
    assert stored_payload["assets"] == []
    assert "old.png" not in json.dumps(stored_payload)
    assert "orphan.png" not in json.dumps(stored_payload)
    assert listed_item["preview_assets"] == [
        {
            "name": "new-direct.png",
            "path": "/media/new-direct.png",
            "type": "Image",
        }
    ]
    assert preview["preview_assets"] == listed_item["preview_assets"]


def test_embedded_media_and_cache_payloads_are_sanitized_before_persistence(tmp_path):
    timeline = sample_timeline(prompt="sanitize")
    timeline["assets"][0].update(
        {
            "thumbnail": [1, 2, 3],
            "waveform": [0.1, 0.2],
            "image_data": "data:image/png;base64,secret",
            "metadata": {"preview_data": "x", "kept": "yes"},
        }
    )
    timeline["director_track"]["sections"][0]["image"].update(
        {
            "thumbnail_data": "secret",
            "preview": {"bytes": "secret"},
            "path": "data:image/png;base64,secret",
        }
    )

    created = create_item("project", timeline, metadata={"id": "sanitize"}, base_dir=tmp_path)
    stored_text = library_path(tmp_path).read_text(encoding="utf-8")

    asset = created["project"]["assets"][0]
    section_image = created["project"]["director_track"]["sections"][0]["image"]
    assert "thumbnail" not in asset
    assert "waveform" not in asset
    assert "image_data" not in asset
    assert asset["metadata"] == {"kept": "yes"}
    assert "thumbnail_data" not in section_image
    assert "preview" not in section_image
    assert "data:image" not in stored_text


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for privacy encryption tests")
def test_private_timeline_encrypts_sensitive_payload_without_cleartext_leak(tmp_path):
    timeline = sample_timeline(prompt="secret prompt", path="/private/secret-reference.png")

    created = create_item(
        "project",
        timeline,
        metadata={
            "id": "private-timeline",
            "name": "Secret Project Name",
            "description": "secret description",
            "tags": ["secret-tag"],
            "private": True,
        },
        base_dir=tmp_path,
    )

    stored_text = library_path(tmp_path).read_text(encoding="utf-8")
    assert "secret prompt" not in stored_text
    assert "/private/secret-reference.png" not in stored_text
    assert "secret description" not in stored_text
    assert "Secret Project Name" not in stored_text
    assert "secret-tag" not in stored_text
    assert "encrypted_payload" in stored_text
    assert "payload" not in load_library(tmp_path)["projects"][0]
    public_item = list_items(tmp_path)["projects"][0]
    assert public_item["name"] == "Private Project"
    assert public_item["tags"] == []
    assert public_item["summary"] == {"is_private": True}
    assert public_item["created_at"] == ""
    assert public_item["updated_at"] == ""
    assert "preview_assets" not in public_item
    assert "/private/secret-reference.png" not in json.dumps(public_item)
    assert "Secret Project Name" not in json.dumps(public_item)
    assert created["name"] == "Secret Project Name"
    assert created["project"]["director_track"]["sections"][0]["prompt"] == "secret prompt"
    assert created["description"] == "secret description"
    assert created["tags"] == ["secret-tag"]

    used = use_item("project", "private-timeline", base_dir=tmp_path)
    assert used["name"] == "Secret Project Name"
    assert used["project"]["assets"][0]["path"] == "/private/secret-reference.png"


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for privacy encryption tests")
def test_private_timeline_preview_decrypts_without_mutating_or_leaking_items_shell(tmp_path):
    timeline = sample_timeline(prompt="secret preview", path="/private/reveal-reference.png")
    timeline["assets"][0].update(
        {
            "thumbnail": "embedded thumb",
            "waveform": [0.1, 0.2],
            "preview_data": "secret",
            "width": 1024,
            "height": 576,
        }
    )
    create_item(
        "project",
        timeline,
        metadata={"id": "private-preview", "name": "Private Preview", "private": True},
        base_dir=tmp_path,
    )

    before = load_library(tmp_path)["projects"][0]
    public_item = list_items(tmp_path)["projects"][0]
    preview = preview_project_item("private-preview", base_dir=tmp_path)
    after = load_library(tmp_path)["projects"][0]

    assert "preview_assets" not in public_item
    assert "/private/reveal-reference.png" not in json.dumps(public_item)
    assert "last_used_at" not in before
    assert "last_used_at" not in after
    assert before == after
    assert preview["item"]["is_private"] is True
    assert preview["item"]["name"] == "Private Preview"
    assert preview["item"]["preview_assets"] == preview["preview_assets"]
    assert preview["preview_assets"] == [
        {
            "asset_id": "asset-image",
            "height": 576,
            "name": "ref.png",
            "path": "/private/reveal-reference.png",
            "source_kind": "FilePath",
            "type": "Image",
            "width": 1024,
        }
    ]
    assert "thumbnail" not in json.dumps(preview)
    assert "waveform" not in json.dumps(preview)
    assert "preview_data" not in json.dumps(preview)
    assert "/private/reveal-reference.png" not in json.dumps(list_items(tmp_path))


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for privacy encryption tests")
def test_private_character_preview_decrypts_without_mutating_or_leaking_items_shell(tmp_path):
    character = {
        "label": "image7",
        "description": "private hero notes",
        "strength": 0.65,
        "image": {
            "path": "/private/hero-reference.png",
            "thumbnail": "embedded thumb",
            "preview_data": "secret",
            "width": 512,
            "height": 768,
        },
    }
    create_item(
        "character",
        character,
        metadata={
            "id": "private-character",
            "name": "Private Hero",
            "tags": ["secret-character-tag"],
            "private": True,
        },
        base_dir=tmp_path,
    )

    before = load_library(tmp_path)["characters"][0]
    public_item = list_items(tmp_path)["characters"][0]
    preview = preview_character_item("private-character", base_dir=tmp_path)
    after = load_library(tmp_path)["characters"][0]

    assert public_item["description"] == ""
    assert public_item["name"] == "Private Character"
    assert public_item["tags"] == []
    assert public_item["summary"] == {"is_private": True}
    assert "character" not in public_item
    assert "/private/hero-reference.png" not in json.dumps(public_item)
    assert "private hero notes" not in json.dumps(public_item)
    stored_text = library_path(tmp_path).read_text(encoding="utf-8")
    assert "Private Hero" not in stored_text
    assert "secret-character-tag" not in stored_text
    assert "last_used_at" not in before
    assert "last_used_at" not in after
    assert before == after
    assert preview["item"]["is_private"] is True
    assert preview["item"]["name"] == "Private Hero"
    assert preview["item"]["tags"] == ["secret-character-tag"]
    assert preview["item"]["description"] == "private hero notes"
    assert preview["item"]["character"] == preview["character"]
    assert preview["character"] == {
        "id": "image7",
        "label": "image7",
        "kind": "character",
        "enabled": True,
        "description": "private hero notes",
        "strength": pytest.approx(0.65),
        "image": {
            "height": 768,
            "name": "hero-reference.png",
            "path": "/private/hero-reference.png",
            "source_kind": "FilePath",
            "type": "Image",
            "width": 512,
        },
    }
    assert "thumbnail" not in json.dumps(preview)
    assert "preview_data" not in json.dumps(preview)
    assert "/private/hero-reference.png" not in json.dumps(list_items(tmp_path))


def test_route_prefix_and_registration_shape():
    assert timeline_library_routes.ROUTE_PREFIX == "/helto_director/library"
    assert callable(timeline_library_routes.register_timeline_library_routes)

    root_init = Path(__file__).resolve().parents[2] / "__init__.py"
    source = root_init.read_text(encoding="utf-8")
    assert "routes.timeline_library" in source
    assert "register_timeline_library_routes()" in source
    assert '"register_timeline_library_routes"' in source
    route_source = Path(timeline_library_routes.__file__).read_text(encoding="utf-8")
    assert '/projects" + "/{item_id}/preview"' in route_source
    assert "preview_project_item(request.match_info" in route_source
    assert '/characters" + "/{item_id}/preview"' in route_source
    assert "preview_character_item(request.match_info" in route_source


def test_private_library_route_requires_token_before_reading_payload(monkeypatch):
    class RecordingRoutes:
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

        def put(self, path):
            return self._record("PUT", path)

        def patch(self, path):
            return self._record("PATCH", path)

        def delete(self, path):
            return self._record("DELETE", path)

    class UnreadableRequest:
        match_info = {"item_id": "private-project"}

        async def json(self):
            raise AssertionError("unauthorized private request must not read its payload")

    routes = RecordingRoutes()
    monkeypatch.setitem(
        sys.modules,
        "server",
        SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(routes=routes))),
    )
    monkeypatch.setattr(timeline_library_routes, "web", aiohttp_web)
    monkeypatch.setattr(timeline_library_routes, "_ROUTES_REGISTERED", False)
    monkeypatch.setattr(timeline_library_routes, "item_is_private", lambda *_args: True)
    denied = aiohttp_web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(timeline_library_routes, "check_privacy_token", lambda _request: denied)
    assert timeline_library_routes.register_timeline_library_routes() is True

    handler = routes.handlers[
        ("PUT", f"{timeline_library_routes.ROUTE_PREFIX}/projects/{{item_id}}")
    ]
    response = asyncio.run(handler(UnreadableRequest()))

    assert response is denied


def sample_timeline(prompt="hello", path="/media/ref.png"):
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["project"]["frame_rate"] = 12.0
    timeline["assets"].append(
        {
            "asset_id": "asset-image",
            "type": "Image",
            "source_kind": "FilePath",
            "path": path,
            "name": "ref.png",
        }
    )
    timeline["director_track"]["sections"].append(
        {
            "item_id": "section-image",
            "type": "Image",
            "start_time": 0.0,
            "end_time": 2.0,
            "prompt": prompt,
            "image": {"asset_id": "asset-image", "path": path},
        }
    )
    return timeline
