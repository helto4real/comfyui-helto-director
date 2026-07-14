from __future__ import annotations

import copy
import json
import multiprocessing
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import shared.timeline.managed_media_privacy as managed
from helto_privacy import (
    ExternalOperationInvocation,
    OperationReferenceOutput,
    RecordOperationDependency,
    ResourceKind,
    SingletonOperationDependency,
    root_bound_source,
)
from shared.timeline.managed_library_records import PROJECT_RECORD_KIND, PROJECT_RESOURCE_ID
from shared.timeline.managed_media_privacy import (
    MAX_OPERATION_REFERENCES,
    MAX_PROJECT_TAKE_REFERENCES,
    MEDIA_FOLDER_REFERENCE_KIND,
    MEDIA_FOLDERS_LIST,
    MEDIA_ITEMS_LIST,
    MEDIA_OPERATION_ADAPTER_IDS,
    MEDIA_OPERATION_IDS,
    MEDIA_OPERATION_RESOURCE_ID,
    MEDIA_PRIVACY_ACTIVATION_GAPS,
    MEDIA_SOURCE_PREVIEW,
    MEDIA_SOURCE_RESOLVE,
    MEDIA_SOURCE_ATTACH,
    MEDIA_SOURCE_REFERENCE_KIND,
    MEDIA_SOURCE_VIEW,
    PROJECT_TAKES_DELETE,
    PROJECT_TAKES_ATTACH,
    PROJECT_TAKES_LIST,
    PROJECT_TAKE_REFERENCE_KIND,
    DirectorManagedMediaPrivacyError,
    DirectorManagedMediaService,
    MediaFolder,
    MediaSourceLocator,
    build_director_media_privacy_profile,
    build_director_media_server_adapters,
    migrate_legacy_media_folder_settings,
)
from shared.timeline.defaults import create_default_video_timeline


CANARY = "SYNTHETIC_D5_PRIVATE_CANARY"
PROJECT_RECORD_ID = "hp-rec-" + "R" * 32
EXPECTED_D5_PROFILE_FINGERPRINT = (
    "948ad2440e27b7fdba7e40ac1928424afae3b8a19c27d859ee61ce25f42ab835"
)


class _ProjectRecords:
    def __init__(self, project):
        self.project = project
        self.reveals = []

    def reveal(self, record_id):
        self.reveals.append(record_id)
        return {"project": self.project} if record_id == PROJECT_RECORD_ID else None


class _ProjectDependencies:
    def __init__(self, project_records):
        self.project_records = project_records
        self.lookups = []

    def record(self, resource_id, record_kind, operation):
        self.lookups.append((resource_id, record_kind, operation))
        if (resource_id, record_kind, operation) != (
            PROJECT_RESOURCE_ID,
            PROJECT_RECORD_KIND,
            "use",
        ):
            raise AssertionError("undeclared record dependency")
        return self.project_records

    def singleton(self, *_args):
        raise AssertionError("singleton authority must not be available")

    def artifact(self, *_args):
        raise AssertionError("artifact authority must not be available")


class _FolderDependencies:
    def __init__(self, folder_settings):
        self.folder_settings = folder_settings
        self.lookups = []

    def singleton(self, singleton_id):
        self.lookups.append(singleton_id)
        if singleton_id != "media-folder-settings":
            raise AssertionError("undeclared singleton dependency")
        return self.folder_settings

    def record(self, *_args):
        raise AssertionError("record authority must not be available")

    def artifact(self, *_args):
        raise AssertionError("artifact authority must not be available")


class _ProjectServiceHarness:
    def __init__(self, service, project_records):
        self.service = service
        self.project_records = project_records

    def invoke(self, operation_id, value, references):
        return self.service.invoke(
            operation_id,
            value,
            references,
            project_records=self.project_records,
        )


class _FolderSettings:
    def __init__(self):
        self.revision = 0
        self.value = None
        self.fail_readback = False
        self.replacements = []

    def status(self, singleton_id=None):
        if singleton_id not in {None, "media-folder-settings"}:
            raise AssertionError("wrong singleton")
        return SimpleNamespace(exists=self.value is not None, revision=self.revision)

    def reveal(self):
        if self.value is None:
            raise AssertionError("missing singleton")
        value = copy.deepcopy(self.value)
        if self.fail_readback:
            value["folders"]["image"] = []
        return SimpleNamespace(revision=self.revision, value=value)

    def replace(self, value, expected_revision):
        if expected_revision != self.revision:
            raise AssertionError("revision conflict")
        self.revision += 1
        self.value = copy.deepcopy(value)
        self.replacements.append(copy.deepcopy(value))
        return SimpleNamespace(revision=self.revision)

    def reveal_field(self, singleton_id, _authorization):
        assert singleton_id == "media-folder-settings"
        return self.reveal()

    def replace_field(self, singleton_id, value, expected_revision, _authorization):
        assert singleton_id == "media-folder-settings"
        return self.replace(value, expected_revision)


class _TestDirectorManagedMediaService(DirectorManagedMediaService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.folder_settings = _FolderSettings()

    def invoke(
        self,
        operation_id,
        value,
        references,
        *,
        project_records=None,
        folder_settings=None,
    ):
        if operation_id in {
            MEDIA_FOLDERS_LIST,
            "media-folders-add",
            "media-folders-remove",
            MEDIA_ITEMS_LIST,
            MEDIA_SOURCE_VIEW,
            MEDIA_SOURCE_PREVIEW,
            MEDIA_SOURCE_RESOLVE,
        } and folder_settings is None:
            folder_settings = self.folder_settings
        return super().invoke(
            operation_id,
            value,
            references,
            project_records=project_records,
            folder_settings=folder_settings,
        )

    def bind_source(
        self,
        resolved,
        operation_id,
        *,
        folder_settings=None,
    ):
        return super().bind_source(
            resolved,
            operation_id,
            folder_settings=folder_settings or self.folder_settings,
        )


def _service(tmp_path: Path, **kwargs) -> tuple[DirectorManagedMediaService, Path]:
    media = tmp_path / "media"
    media.mkdir()
    service = _TestDirectorManagedMediaService(
        config_dir=tmp_path / "config",
        default_folders={"image": (MediaFolder("input", media),)},
        **kwargs,
    )
    return service, media


def _resolved(value):
    return SimpleNamespace(value=value)


def _replace_config_in_process(root, name, value, started, finished):
    started.set()
    managed._replace_json_file(Path(root), name, value)
    finished.set()


def _write_legacy_folder_config(config_dir, media_type, folders):
    config_dir.mkdir(parents=True, exist_ok=True)
    name = managed.MEDIA_DEFINITIONS[media_type]["config_name"]
    path = config_dir / str(name)
    path.write_text(
        json.dumps(
            {
                "schema": managed.MEDIA_FOLDER_CONFIG_SCHEMA,
                "version": managed.MEDIA_FOLDER_CONFIG_VERSION,
                "folders": folders,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_d5_composes_exact_typed_operation_contract_over_d2_d3_d4_d6():
    profile = build_director_media_privacy_profile()
    assert profile.fingerprint == EXPECTED_D5_PROFILE_FINGERPRINT
    resource = next(item for item in profile.resources if item.id == MEDIA_OPERATION_RESOURCE_ID)
    assert resource.kind is ResourceKind.OPERATION
    assert set(resource.adapter_slots) == set(MEDIA_OPERATION_ADAPTER_IDS.values())
    assert len(resource.adapter_slots) == len(MEDIA_OPERATION_IDS)

    operations = {
        item.id: item for item in profile.protected_operations if item.id in MEDIA_OPERATION_IDS
    }
    assert set(operations) == set(MEDIA_OPERATION_IDS)
    assert all(item.scope_id == "director-global" for item in operations.values())
    assert all(item.sensitive_fields[0].path == "*" for item in operations.values())
    assert all(item.adapter_slot == MEDIA_OPERATION_ADAPTER_IDS[item.id] for item in operations.values())
    assert all(
        "invoke_with_dependencies" in profile.server_adapter_contracts[item.adapter_slot]
        for item in operations.values()
        if item.route is not None
    )
    assert all(
        "invoke" not in profile.server_adapter_contracts[item.adapter_slot]
        for item in operations.values()
    )
    assert all(
        "bind_source_with_dependencies"
        in profile.server_adapter_contracts[operations[operation_id].adapter_slot]
        for operation_id in (MEDIA_SOURCE_VIEW,)
    )
    assert "bind_source_with_dependencies" not in profile.server_adapter_contracts[
        operations[MEDIA_SOURCE_PREVIEW].adapter_slot
    ]
    project_dependency = RecordOperationDependency(
        PROJECT_RESOURCE_ID,
        PROJECT_RECORD_KIND,
        "use",
    )
    assert operations[PROJECT_TAKES_LIST].record_dependencies == (project_dependency,)
    assert operations[PROJECT_TAKES_ATTACH].record_dependencies == (project_dependency,)
    assert operations[PROJECT_TAKES_DELETE].record_dependencies == (project_dependency,)
    assert all(
        not item.record_dependencies
        for item in operations.values()
        if item.id not in {PROJECT_TAKES_LIST, PROJECT_TAKES_ATTACH, PROJECT_TAKES_DELETE}
    )
    read_dependency = SingletonOperationDependency(
        "media-folder-settings",
        ("reveal", "status"),
    )
    write_dependency = SingletonOperationDependency(
        "media-folder-settings",
        ("replace", "reveal", "status"),
    )
    assert all(
        operations[operation_id].singleton_dependencies == (read_dependency,)
        for operation_id in (
            MEDIA_FOLDERS_LIST,
            MEDIA_ITEMS_LIST,
            MEDIA_SOURCE_VIEW,
            MEDIA_SOURCE_PREVIEW,
            MEDIA_SOURCE_RESOLVE,
            MEDIA_SOURCE_ATTACH,
        )
    )
    assert all(
        operations[operation_id].singleton_dependencies == (write_dependency,)
        for operation_id in ("media-folders-add", "media-folders-remove")
    )
    assert all(
        not operations[operation_id].singleton_dependencies
        for operation_id in (PROJECT_TAKES_LIST, PROJECT_TAKES_DELETE)
    )

    folder_outputs = operations[MEDIA_FOLDERS_LIST].reference_outputs
    assert folder_outputs == (
        OperationReferenceOutput(MEDIA_FOLDER_REFERENCE_KIND, 0, MAX_OPERATION_REFERENCES),
    )
    item_outputs = operations[MEDIA_ITEMS_LIST].reference_outputs
    assert item_outputs == (
        OperationReferenceOutput(MEDIA_SOURCE_REFERENCE_KIND, 0, MAX_OPERATION_REFERENCES),
    )
    take_outputs = operations[PROJECT_TAKES_LIST].reference_outputs
    assert take_outputs == (
        OperationReferenceOutput(MEDIA_SOURCE_REFERENCE_KIND, 0, MAX_PROJECT_TAKE_REFERENCES),
        OperationReferenceOutput(PROJECT_TAKE_REFERENCE_KIND, 0, MAX_PROJECT_TAKE_REFERENCES),
    )
    assert operations[MEDIA_SOURCE_VIEW].returns_lease is True
    assert operations[MEDIA_SOURCE_PREVIEW].returns_lease is True
    assert operations[MEDIA_SOURCE_RESOLVE].reference_outputs == (
        OperationReferenceOutput(MEDIA_SOURCE_REFERENCE_KIND),
    )
    assert {
        dependency.artifact_kind: dependency.verbs
        for dependency in operations[MEDIA_SOURCE_PREVIEW].artifact_dependencies
    } == {
        "thumbnail": ("lease.preview", "read", "retire", "write"),
        "waveform": ("lease.preview", "read", "retire", "write"),
    }
    assert operations[MEDIA_SOURCE_ATTACH].route is None
    assert operations[MEDIA_SOURCE_ATTACH].external_operation_binding.field_id == "timeline-state"
    assert operations[MEDIA_SOURCE_ATTACH].reference_inputs[0].reference_kind_id == MEDIA_SOURCE_REFERENCE_KIND
    assert operations[PROJECT_TAKES_ATTACH].route is None
    assert operations[PROJECT_TAKES_ATTACH].external_operation_binding.field_id == "timeline-state"
    assert [
        item.reference_kind_id for item in operations[PROJECT_TAKES_ATTACH].reference_inputs
    ] == [MEDIA_SOURCE_REFERENCE_KIND, PROJECT_TAKE_REFERENCE_KIND]
    assert operations["media-folders-remove"].reference_inputs[0].revoke_on_success is True
    assert operations[PROJECT_TAKES_DELETE].reference_inputs[0].revoke_on_success is True
    assert {item.id for item in profile.opaque_reference_kinds}.issuperset(
        {MEDIA_FOLDER_REFERENCE_KIND, MEDIA_SOURCE_REFERENCE_KIND, PROJECT_TAKE_REFERENCE_KIND}
    )
    assert not any("d3-project-resolution" in item for item in MEDIA_PRIVACY_ACTIVATION_GAPS)
    assert MEDIA_PRIVACY_ACTIVATION_GAPS == ()
    assert all("deferred-d6-association" not in item for item in MEDIA_PRIVACY_ACTIVATION_GAPS)


def test_folder_schema_aliases_roots_extensions_and_metadata_are_preserved(tmp_path):
    def metadata_reader(source, media_type):
        assert source.read() == b"synthetic-image"
        assert media_type == "image"
        return {"width": 12, "height": 8}

    service, media = _service(tmp_path, metadata_reader=metadata_reader)
    nested = media / "nested"
    nested.mkdir()
    image = nested / "frame.png"
    image.write_bytes(b"synthetic-image")
    (nested / "ignored.txt").write_text(CANARY, encoding="utf-8")

    folders = service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {})
    assert folders.payload["folders"] == [{
        "alias": "input",
        "path": str(media),
        "display_name": "media",
        "enabled": True,
        "exists": True,
        "image_count": 1,
    }]
    assert len(folders.references) == 1
    assert folders.references[0].reference_kind_id == MEDIA_FOLDER_REFERENCE_KIND
    assert CANARY not in repr(folders.references[0])

    items = service.invoke(
        MEDIA_ITEMS_LIST,
        {"recursive": True},
        {"folder": _resolved(folders.references[0].value)},
    )
    assert items.payload["item_count"] == 1
    assert items.payload["items"][0] == {
        "filename": "nested/frame.png",
        "path": str(image),
        "name": "frame.png",
        "mtime": pytest.approx(image.stat().st_mtime_ns / 1_000_000_000),
        "size": len(b"synthetic-image"),
        "mime_type": "image/png",
        "width": 12,
        "height": 8,
    }
    assert items.references[0].reference_kind_id == MEDIA_SOURCE_REFERENCE_KIND
    assert str(image) not in repr(items.references[0])
    assert image.name not in repr(items.references[0])

    resolved = service.invoke(
        MEDIA_SOURCE_RESOLVE,
        {"media_type": "image", "path": str(image)},
        {},
    )
    assert resolved.payload["ready"] is True
    assert resolved.references[0].reference_kind_id == MEDIA_SOURCE_REFERENCE_KIND
    assert resolved.references[0].value == items.references[0].value

    relative = service.invoke(
        MEDIA_SOURCE_RESOLVE,
        {
            "media_type": "image",
            "path": "nested/frame.png",
            "source_type": "input",
        },
        {},
    )
    assert relative.references[0].value == items.references[0].value


def test_source_attach_resolves_path_only_inside_external_timeline_target(tmp_path):
    service, media = _service(tmp_path)
    image = media / f"{CANARY}.png"
    image.write_bytes(b"synthetic-image")
    folder = service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {}).references[0]
    source = service.invoke(
        MEDIA_ITEMS_LIST,
        {"recursive": True},
        {"folder": _resolved(folder.value)},
    ).references[0]
    timeline = create_default_video_timeline()
    timeline["director_track"]["sections"].append({
        "item_id": "section_001",
        "type": "Image",
        "start_time": 0.0,
        "end_time": 1.0,
        "image": None,
    })
    adapter = build_director_media_server_adapters(service)[
        MEDIA_OPERATION_ADAPTER_IDS[MEDIA_SOURCE_ATTACH]
    ]
    declaration = next(
        item
        for item in build_director_media_privacy_profile().protected_operations
        if item.id == MEDIA_SOURCE_ATTACH
    )
    invocation = ExternalOperationInvocation("hp-operation-" + "M" * 32)
    capture = adapter.capture_external_operation(
        {
            "asset_type": "Image",
            "item_id": "section_001",
            "timeline": timeline,
        },
        {"source": _resolved(source.value)},
        invocation,
        declaration,
        _FolderDependencies(service.folder_settings),
    )
    asset = capture.browser_value["assets"][0]
    assert asset["path"] == str(image)
    assert asset["name"] == image.name
    assert capture.browser_value["director_track"]["sections"][0]["image"] == {
        "asset_id": asset["asset_id"],
    }
    assert CANARY not in repr(capture.context)
    assert adapter.finalize_external_operation(
        capture.context,
        invocation,
        declaration,
        _FolderDependencies(service.folder_settings),
    ).payload == {"ok": True}


def test_folder_add_remove_revision_and_corrupt_config_fail_closed(tmp_path):
    service, media = _service(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    added = service.invoke(
        "media-folders-add",
        {"media_type": "image", "directory": str(extra), "alias": "extra"},
        {},
    )
    locator = added.references[0].value
    assert added.payload["folder_count"] == 2
    removed = service.invoke(
        "media-folders-remove",
        {},
        {"folder": _resolved(locator)},
    )
    assert removed.payload == {"ok": True, "folder_count": 1}
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke("media-folders-remove", {}, {"folder": _resolved(locator)})
    assert service.folder_settings.revision == 2
    assert len(service.folder_settings.replacements) == 2
    assert not (tmp_path / "config/timeline_image_folders.json").exists()

    service.folder_settings.value = {"schema": CANARY}
    with pytest.raises(DirectorManagedMediaPrivacyError) as error:
        service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {})
    assert CANARY not in str(error.value)
    assert media.is_dir()


@pytest.mark.parametrize(
    "mutation",
    (
        "top-extra",
        "schema",
        "version",
        "version-bool",
        "folders-type",
        "entry-extra",
        "entry-missing",
        "enabled-string",
        "enabled-int",
    ),
)
def test_folder_config_requires_exact_versioned_schema_and_types(tmp_path, mutation):
    service, media = _service(tmp_path)
    entry = {"alias": "custom", "enabled": True, "path": str(media)}
    payload = {
        "schema": "helto.director.media-folder-settings",
        "version": 1,
        "folders": {"image": [entry], "video": [], "audio": []},
    }
    if mutation == "top-extra":
        payload["extra"] = CANARY
    elif mutation == "schema":
        payload["schema"] = "wrong"
    elif mutation == "version":
        payload["version"] = 2
    elif mutation == "version-bool":
        payload["version"] = True
    elif mutation == "folders-type":
        payload["folders"] = {"alias": "custom"}
    elif mutation == "entry-extra":
        entry["extra"] = CANARY
    elif mutation == "entry-missing":
        del entry["path"]
    elif mutation == "enabled-string":
        entry["enabled"] = "true"
    elif mutation == "enabled-int":
        entry["enabled"] = 1
    service.folder_settings.value = payload
    service.folder_settings.revision = 1
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {})


def test_three_legacy_folder_files_migrate_as_one_verified_singleton(tmp_path):
    config_dir = tmp_path / "config"
    paths = {
        media_type: _write_legacy_folder_config(
            config_dir,
            media_type,
            [
                {
                    "alias": media_type,
                    "path": f"/legacy/{media_type}",
                    "enabled": media_type != "audio",
                }
            ],
        )
        for media_type in ("image", "video", "audio")
    }
    singleton = _FolderSettings()

    assert migrate_legacy_media_folder_settings(
        config_dir=config_dir,
        singleton_handle=singleton,
        authorization=object(),
    ) is True

    assert singleton.revision == 1
    assert singleton.value == {
        "schema": "helto.director.media-folder-settings",
        "version": 1,
        "folders": {
            media_type: [
                {
                    "alias": media_type,
                    "path": f"/legacy/{media_type}",
                    "enabled": media_type != "audio",
                }
            ]
            for media_type in ("image", "video", "audio")
        },
    }
    assert all(not path.exists() for path in paths.values())


def test_legacy_folder_migration_preserves_every_source_on_failed_readback(tmp_path):
    config_dir = tmp_path / "config"
    paths = [
        _write_legacy_folder_config(
            config_dir,
            media_type,
            [{"alias": media_type, "path": f"/legacy/{media_type}", "enabled": True}],
        )
        for media_type in ("image", "video", "audio")
    ]
    singleton = _FolderSettings()
    singleton.fail_readback = True

    with pytest.raises(DirectorManagedMediaPrivacyError):
        migrate_legacy_media_folder_settings(
            config_dir=config_dir,
            singleton_handle=singleton,
            authorization=object(),
        )

    assert all(path.exists() for path in paths)


def test_legacy_folder_cleanup_retry_accepts_only_matching_remaining_slice(tmp_path):
    config_dir = tmp_path / "config"
    remaining = _write_legacy_folder_config(
        config_dir,
        "video",
        [{"alias": "video", "path": "/legacy/video", "enabled": True}],
    )
    singleton = _FolderSettings()
    singleton.revision = 1
    singleton.value = {
        "schema": "helto.director.media-folder-settings",
        "version": 1,
        "folders": {
            "image": [{"alias": "image", "path": "/legacy/image", "enabled": True}],
            "video": [{"alias": "video", "path": "/legacy/video", "enabled": True}],
            "audio": [{"alias": "audio", "path": "/legacy/audio", "enabled": True}],
        },
    }

    assert migrate_legacy_media_folder_settings(
        config_dir=config_dir,
        singleton_handle=singleton,
        authorization=object(),
    ) is True
    assert not remaining.exists()
    assert singleton.revision == 1

    changed = _write_legacy_folder_config(
        config_dir,
        "video",
        [{"alias": "video", "path": "/changed/private", "enabled": True}],
    )
    with pytest.raises(DirectorManagedMediaPrivacyError):
        migrate_legacy_media_folder_settings(
            config_dir=config_dir,
            singleton_handle=singleton,
            authorization=object(),
        )
    assert changed.exists()


def test_legacy_folder_source_change_before_retirement_is_preserved(
    tmp_path,
    monkeypatch,
):
    config_dir = tmp_path / "config"
    path = _write_legacy_folder_config(
        config_dir,
        "image",
        [{"alias": "image", "path": "/legacy/image", "enabled": True}],
    )
    singleton = _FolderSettings()
    original = managed._read_direct_folder_singleton
    calls = 0

    def mutate_after_readback(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            _write_legacy_folder_config(
                config_dir,
                "image",
                [{"alias": "image", "path": "/changed/private", "enabled": True}],
            )
        return result

    monkeypatch.setattr(managed, "_read_direct_folder_singleton", mutate_after_readback)
    with pytest.raises(DirectorManagedMediaPrivacyError):
        migrate_legacy_media_folder_settings(
            config_dir=config_dir,
            singleton_handle=singleton,
            authorization=object(),
        )
    assert path.exists()
    assert "/changed/private" in path.read_text(encoding="utf-8")


def test_folder_reference_rejects_root_replacement_after_issuance(tmp_path):
    service, media = _service(tmp_path)
    (media / "original.png").write_bytes(b"original")
    folder = service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {}).references[0]
    detached = tmp_path / "detached-media"
    media.rename(detached)
    media.mkdir()
    replacement = media / "replacement.png"
    replacement.write_text(CANARY, encoding="utf-8")

    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke(MEDIA_ITEMS_LIST, {}, {"folder": _resolved(folder.value)})
    assert replacement.read_text(encoding="utf-8") == CANARY


def test_folder_reference_rejects_configured_entry_revision_change(tmp_path):
    service, _media = _service(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    added = service.invoke(
        "media-folders-add",
        {"media_type": "image", "directory": str(extra), "alias": "extra"},
        {},
    )
    locator = added.references[0].value
    next(
        item
        for item in service.folder_settings.value["folders"]["image"]
        if item["alias"] == "extra"
    )["enabled"] = False

    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke(MEDIA_ITEMS_LIST, {}, {"folder": _resolved(locator)})


@pytest.mark.parametrize(
    "metadata",
    (
        {"path": f"/{CANARY}"},
        {"name": CANARY},
        {"mime_type": CANARY},
        {"width": object()},
        {"duration_seconds": float("nan")},
        {"unknown": CANARY},
    ),
)
def test_metadata_reader_cannot_override_core_or_emit_unsafe_values(tmp_path, metadata):
    service, media = _service(tmp_path, metadata_reader=lambda *_args: metadata)
    (media / "frame.png").write_bytes(b"image")
    folder = service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {}).references[0]
    with pytest.raises(DirectorManagedMediaPrivacyError) as error:
        service.invoke(MEDIA_ITEMS_LIST, {}, {"folder": _resolved(folder.value)})
    assert CANARY not in str(error.value)


def test_traversal_symlink_outside_missing_and_revision_sources_are_rejected(tmp_path):
    service, media = _service(tmp_path)
    source = media / "frame.png"
    source.write_bytes(b"first")
    outside = tmp_path / "outside.png"
    outside.write_text(CANARY, encoding="utf-8")
    try:
        (media / "linked.png").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")

    folder = service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {}).references[0]
    items = service.invoke(MEDIA_ITEMS_LIST, {}, {"folder": _resolved(folder.value)})
    assert items.payload["item_count"] == 1
    locator = items.references[0].value
    bound = service.bind_source(_resolved(locator), MEDIA_SOURCE_VIEW)
    assert bound.media_type == "image/png"
    assert str(source) not in repr(bound)

    traversing = MediaSourceLocator(
        "image", media, ("..", outside.name), outside.stat().st_dev,
        outside.stat().st_ino, outside.stat().st_size, outside.stat().st_mtime_ns,
        "image/png",
    )
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.bind_source(_resolved(traversing), MEDIA_SOURCE_VIEW)

    source.write_bytes(b"changed")
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.bind_source(_resolved(locator), MEDIA_SOURCE_VIEW)
    source.unlink()
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.bind_source(_resolved(locator), MEDIA_SOURCE_VIEW)
    assert outside.read_text(encoding="utf-8") == CANARY


def test_preview_requires_d4_generator_and_revalidates_generated_locator(tmp_path):
    service, media = _service(tmp_path)
    source = media / "frame.png"
    source.write_bytes(b"image")
    folder = service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {}).references[0]
    locator = service.invoke(MEDIA_ITEMS_LIST, {}, {"folder": _resolved(folder.value)}).references[0].value
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.bind_source(_resolved(locator), MEDIA_SOURCE_PREVIEW)

    generated = _TestDirectorManagedMediaService(
        config_dir=tmp_path / "generated-config",
        default_folders={"image": (MediaFolder("input", media),)},
        preview_locator=lambda value: value,
    )
    assert generated.bind_source(_resolved(locator), MEDIA_SOURCE_PREVIEW).media_type == "image/png"


def _project_service(tmp_path: Path):
    asset_root = tmp_path / "assets"
    take_root = asset_root / "project_proj_123" / "takes" / "shot_001"
    take_root.mkdir(parents=True)
    project = {
        "identity": {"project_id": "proj_123", "name": CANARY},
        "storage": {"schema_version": 1, "project_directory_name": "project_proj_123"},
    }
    service = _TestDirectorManagedMediaService(
        config_dir=tmp_path / "config",
        default_folders={},
        project_asset_root=asset_root,
    )

    return _ProjectServiceHarness(service, _ProjectRecords(project)), take_root


def test_project_take_adapter_uses_only_declared_invocation_record_capability(tmp_path):
    harness, take_root = _project_service(tmp_path)
    _write_take(take_root)
    adapters = build_director_media_server_adapters(harness.service)
    profile = build_director_media_privacy_profile()
    declaration = next(
        item for item in profile.protected_operations
        if item.id == PROJECT_TAKES_LIST
    )
    dependencies = _ProjectDependencies(harness.project_records)

    listed = adapters[MEDIA_OPERATION_ADAPTER_IDS[PROJECT_TAKES_LIST]].invoke_with_dependencies(
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
        declaration,
        dependencies,
    )

    assert listed.payload["capture_count"] == 1
    assert dependencies.lookups == [
        (PROJECT_RESOURCE_ID, PROJECT_RECORD_KIND, "use")
    ]
    assert harness.project_records.reveals == [PROJECT_RECORD_ID]
    unrelated = adapters[MEDIA_OPERATION_ADAPTER_IDS[MEDIA_FOLDERS_LIST]]
    with pytest.raises(DirectorManagedMediaPrivacyError):
        unrelated.invoke_with_dependencies({}, {}, declaration, dependencies)


def test_folder_adapter_uses_only_declared_invocation_singleton_capability(tmp_path):
    service, _media = _service(tmp_path)
    adapter = build_director_media_server_adapters(service)[
        MEDIA_OPERATION_ADAPTER_IDS[MEDIA_FOLDERS_LIST]
    ]
    profile = build_director_media_privacy_profile()
    declaration = next(
        item for item in profile.protected_operations
        if item.id == MEDIA_FOLDERS_LIST
    )
    folder_settings = _FolderSettings()
    dependencies = _FolderDependencies(folder_settings)

    listed = adapter.invoke_with_dependencies(
        {"media_type": "image"},
        {},
        declaration,
        dependencies,
    )

    assert listed.payload["folder_count"] == 1
    assert dependencies.lookups == ["media-folder-settings"]


def _write_take(take_root: Path):
    media = take_root / "capture.mp4"
    media.write_bytes(b"synthetic-video")
    sidecar = take_root / "capture.helto_take.json"
    sidecar.write_text(json.dumps({
        "registration": {
            "shot_id": "shot_001",
            "shot_ids": ["shot_001"],
            "take": {"take_id": "take_001", "private": CANARY},
        }
    }), encoding="utf-8")
    return media, sidecar


def test_project_take_layout_discovery_and_delete_use_only_opaque_record_and_take(tmp_path):
    service, take_root = _project_service(tmp_path)
    media, sidecar = _write_take(take_root)
    listed = service.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    assert listed.payload["capture_count"] == 1
    assert [item.reference_kind_id for item in listed.references] == [
        MEDIA_SOURCE_REFERENCE_KIND,
        PROJECT_TAKE_REFERENCE_KIND,
    ]
    assert CANARY not in repr(listed.references)
    take_locator = listed.references[1].value
    deleted = service.invoke(
        PROJECT_TAKES_DELETE,
        {},
        {"take": _resolved(take_locator)},
    )
    assert deleted.payload == {
        "ok": True,
        "deleted": True,
        "files_deleted": 2,
        "media_missing": False,
    }
    assert not media.exists()
    assert not sidecar.exists()


def test_project_take_attach_is_record_authorized_external_timeline_write(tmp_path):
    harness, take_root = _project_service(tmp_path)
    media, _sidecar = _write_take(take_root)
    listed = harness.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    timeline = create_default_video_timeline()
    timeline["director_track"]["sections"].append({
        "item_id": "section_001",
        "type": "Text",
        "start_time": 0.0,
        "end_time": 1.0,
        "prompt": "synthetic",
    })
    timeline = managed.normalize_video_timeline(timeline)
    timeline["sequence"]["shots"][0]["shot_id"] = "shot_001"
    adapter = build_director_media_server_adapters(harness.service)[
        MEDIA_OPERATION_ADAPTER_IDS[PROJECT_TAKES_ATTACH]
    ]
    declaration = next(
        item
        for item in build_director_media_privacy_profile().protected_operations
        if item.id == PROJECT_TAKES_ATTACH
    )
    invocation = ExternalOperationInvocation("hp-operation-" + "P" * 32)
    dependencies = _ProjectDependencies(harness.project_records)
    capture = adapter.capture_external_operation(
        {
            "accept": True,
            "project_record_id": PROJECT_RECORD_ID,
            "shot_id": "shot_001",
            "timeline": timeline,
        },
        {
            "source": _resolved(listed.references[0].value),
            "take": _resolved(listed.references[1].value),
        },
        invocation,
        declaration,
        dependencies,
    )
    assert capture.browser_value["assets"][0]["path"] == str(media)
    shot = capture.browser_value["sequence"]["shots"][0]
    assert shot["takes"][0]["take_id"] == "take_001"
    assert shot["takes"][0]["status"] == "Accepted"
    assert shot["accepted_take_id"] == "take_001"
    assert dependencies.lookups == [
        (PROJECT_RESOURCE_ID, PROJECT_RECORD_KIND, "use")
    ]
    assert CANARY not in repr(capture.context)


def test_missing_take_media_can_delete_sidecar_only_with_opaque_take_identity(tmp_path):
    service, take_root = _project_service(tmp_path)
    media, sidecar = _write_take(take_root)
    listed = service.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    locator = listed.references[1].value
    media.unlink()
    deleted = service.invoke(PROJECT_TAKES_DELETE, {}, {"take": _resolved(locator)})
    assert deleted.payload["media_missing"] is True
    assert deleted.payload["files_deleted"] == 1
    assert not sidecar.exists()


@pytest.mark.parametrize("mutation", ("missing", "replaced", "diverged"))
def test_take_delete_rejects_missing_replaced_or_diverged_validated_sidecar(
    tmp_path,
    mutation,
):
    service, take_root = _project_service(tmp_path)
    media, sidecar = _write_take(take_root)
    listed = service.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    locator = listed.references[1].value
    if mutation == "missing":
        sidecar.unlink()
    elif mutation == "replaced":
        sidecar.unlink()
        sidecar.write_text(CANARY, encoding="utf-8")
    else:
        sidecar.write_text(sidecar.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke(PROJECT_TAKES_DELETE, {}, {"take": _resolved(locator)})
    assert media.read_bytes() == b"synthetic-video"


def test_take_delete_preserves_unvalidated_alternate_sidecar(tmp_path):
    service, take_root = _project_service(tmp_path)
    media, sidecar = _write_take(take_root)
    alternate = take_root / "capture.mp4.helto_take.json"
    alternate.write_text(CANARY, encoding="utf-8")
    listed = service.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    locator = listed.references[1].value
    deleted = service.invoke(PROJECT_TAKES_DELETE, {}, {"take": _resolved(locator)})

    assert deleted.payload["files_deleted"] == 2
    assert not media.exists()
    assert not sidecar.exists()
    assert alternate.read_text(encoding="utf-8") == CANARY


def test_take_delete_rejects_media_hardlink_replacement(tmp_path):
    service, take_root = _project_service(tmp_path)
    media, sidecar = _write_take(take_root)
    listed = service.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    locator = listed.references[1].value
    outside = tmp_path / "outside.mp4"
    outside.write_text(CANARY, encoding="utf-8")
    media.unlink()
    os.link(outside, media)

    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke(PROJECT_TAKES_DELETE, {}, {"take": _resolved(locator)})
    assert outside.read_text(encoding="utf-8") == CANARY
    assert sidecar.exists()


def test_take_delete_rejects_project_take_root_rebinding(tmp_path):
    service, take_root = _project_service(tmp_path)
    _write_take(take_root)
    listed = service.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    locator = listed.references[1].value
    asset_root = tmp_path / "assets"
    detached = tmp_path / "detached-assets"
    asset_root.rename(detached)
    replacement_take = asset_root / "project_proj_123/takes/shot_001"
    replacement_take.mkdir(parents=True)
    replacement_media, replacement_sidecar = _write_take(replacement_take)

    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke(PROJECT_TAKES_DELETE, {}, {"take": _resolved(locator)})
    assert replacement_media.read_bytes() == b"synthetic-video"
    assert replacement_sidecar.exists()


def test_take_delete_quarantines_and_rejects_leaf_swap_before_rename(
    tmp_path,
    monkeypatch,
):
    service, take_root = _project_service(tmp_path)
    media, sidecar = _write_take(take_root)
    listed = service.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    locator = listed.references[1].value
    original_rename = managed.os.rename
    parked = take_root / "parked-original.mp4"
    swapped = False

    def swap_before_quarantine(source, target, *args, **kwargs):
        nonlocal swapped
        if source == media.name and not swapped:
            swapped = True
            parent_fd = kwargs["src_dir_fd"]
            original_rename(
                source,
                parked.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            media.write_text(CANARY, encoding="utf-8")
        return original_rename(source, target, *args, **kwargs)

    monkeypatch.setattr(managed.os, "rename", swap_before_quarantine)
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke(PROJECT_TAKES_DELETE, {}, {"take": _resolved(locator)})

    assert media.read_text(encoding="utf-8") == CANARY
    assert parked.read_bytes() == b"synthetic-video"
    assert sidecar.exists()
    assert not tuple(take_root.glob(".helto-delete-*"))


def test_prune_root_rebinding_fails_sanitized_without_fd_delta(tmp_path):
    proc_fds = Path("/proc/self/fd")
    if not proc_fds.is_dir():
        pytest.skip("descriptor accounting requires procfs")
    root = tmp_path / "root"
    child = root / "nested"
    child.mkdir(parents=True)
    expected = (root.stat().st_dev, root.stat().st_ino)
    detached = tmp_path / "detached-root"
    root.rename(detached)
    child.mkdir(parents=True)
    before = len(tuple(proc_fds.iterdir()))

    with pytest.raises(DirectorManagedMediaPrivacyError) as error:
        managed._prune_empty(
            root,
            child,
            expected_root_binding=expected,
        )

    assert str(error.value) == "Director managed media operation failed."
    assert len(tuple(proc_fds.iterdir())) == before


def test_config_replacement_writers_are_serialized_at_commit(tmp_path, monkeypatch):
    root = tmp_path / "config"
    root.mkdir()
    name = "folders.json"
    managed._replace_json_file(root, name, {"writer": 0})
    original_replace = managed.os.replace
    first_at_commit = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_at_commit = threading.Event()
    commit_threads: list[str] = []

    def gated_replace(*args, **kwargs):
        commit_threads.append(threading.current_thread().name)
        if threading.current_thread().name == "config-writer-1":
            first_at_commit.set()
            assert release_first.wait(timeout=5)
        else:
            second_at_commit.set()
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(managed.os, "replace", gated_replace)
    failures: list[BaseException] = []

    def write(value):
        try:
            if threading.current_thread().name == "config-writer-2":
                second_started.set()
            managed._replace_json_file(root, name, value)
        except BaseException as error:
            failures.append(error)

    first = threading.Thread(
        target=write,
        args=({"writer": 1},),
        name="config-writer-1",
    )
    second = threading.Thread(
        target=write,
        args=({"writer": 2},),
        name="config-writer-2",
    )
    first.start()
    assert first_at_commit.wait(timeout=5)
    second.start()
    assert second_started.wait(timeout=5)
    assert second_at_commit.wait(timeout=0.05) is False
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert commit_threads == ["config-writer-1", "config-writer-2"]
    assert json.loads((root / name).read_text(encoding="utf-8")) == {"writer": 2}


def test_config_replacement_holds_cross_process_private_advisory_lock(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    name = "folders.json"
    managed._replace_json_file(root, name, {"writer": 0})
    directory_fd = os.open(root, managed._DIRECTORY_FLAGS)
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    finished = context.Event()
    process = context.Process(
        target=_replace_config_in_process,
        args=(str(root), name, {"writer": 1}, started, finished),
    )
    try:
        with managed._exclusive_private_file_lock(
            directory_fd,
            managed._CONFIG_LOCK_FILE,
        ):
            process.start()
            assert started.wait(timeout=5)
            assert finished.wait(timeout=0.1) is False
        process.join(timeout=5)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        os.close(directory_fd)

    assert process.exitcode == 0
    assert finished.is_set()
    assert json.loads((root / name).read_text(encoding="utf-8")) == {"writer": 1}
    lock = root / managed._CONFIG_LOCK_FILE
    assert lock.is_file()
    assert lock.stat().st_mode & 0o777 == 0o600


def test_open_then_fstat_faults_do_not_leak_descriptors(tmp_path, monkeypatch):
    proc_fds = Path("/proc/self/fd")
    if not proc_fds.is_dir():
        pytest.skip("descriptor accounting requires procfs")
    root = tmp_path / "fd-root"
    root.mkdir()
    (root / "value.json").write_text("{}", encoding="utf-8")
    original_fstat = managed.os.fstat

    before = len(tuple(proc_fds.iterdir()))
    with monkeypatch.context() as fault:
        fault.setattr(
            managed.os,
            "fstat",
            lambda _descriptor: (_ for _ in ()).throw(OSError("synthetic fstat")),
        )
        with pytest.raises(OSError):
            managed._open_absolute_directory_fd(root, create=False)
    assert len(tuple(proc_fds.iterdir())) == before

    def direct_open(path, *, create):
        assert create in {True, False}
        return os.open(path, managed._DIRECTORY_FLAGS)

    for operation in (
        lambda: managed._read_json_file(root, "value.json"),
        lambda: managed._replace_json_file(root, "value.json", {"value": 1}),
        lambda: managed._open_relative_parent(root, ("value.json",)).__enter__(),
    ):
        before = len(tuple(proc_fds.iterdir()))
        with monkeypatch.context() as fault:
            fault.setattr(managed, "_open_absolute_directory_fd", direct_open)
            fault.setattr(
                managed.os,
                "fstat",
                lambda _descriptor: (_ for _ in ()).throw(OSError("synthetic fstat")),
            )
            with pytest.raises((OSError, DirectorManagedMediaPrivacyError)):
                operation()
        assert len(tuple(proc_fds.iterdir())) == before

    nested = root / "nested"
    (nested / "leaf").mkdir(parents=True)
    nested_inode = nested.stat().st_ino

    def fail_nested_fstat(descriptor):
        value = original_fstat(descriptor)
        if value.st_ino == nested_inode:
            raise OSError("synthetic nested fstat")
        return value

    before = len(tuple(proc_fds.iterdir()))
    with monkeypatch.context() as fault:
        fault.setattr(managed.os, "fstat", fail_nested_fstat)
        with pytest.raises(OSError):
            managed._prune_empty(root, nested / "leaf")
    assert len(tuple(proc_fds.iterdir())) == before


def test_take_delete_file_fstat_fault_does_not_leak_descriptor(tmp_path, monkeypatch):
    proc_fds = Path("/proc/self/fd")
    if not proc_fds.is_dir():
        pytest.skip("descriptor accounting requires procfs")
    service, take_root = _project_service(tmp_path)
    media, sidecar = _write_take(take_root)
    listed = service.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    locator = listed.references[1].value
    original_fstat = managed.os.fstat

    def fail_regular_fstat(descriptor):
        value = original_fstat(descriptor)
        if managed.stat.S_ISREG(value.st_mode):
            raise OSError("synthetic file fstat")
        return value

    before = len(tuple(proc_fds.iterdir()))
    monkeypatch.setattr(managed.os, "fstat", fail_regular_fstat)
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke(PROJECT_TAKES_DELETE, {}, {"take": _resolved(locator)})

    assert len(tuple(proc_fds.iterdir())) == before
    assert media.exists() and sidecar.exists()


def test_ancestor_swap_list_fails_without_reading_outside_canary(tmp_path):
    anchor = tmp_path / "anchor"
    media = anchor / "media"
    media.mkdir(parents=True)
    (media / "inside.png").write_bytes(b"inside")
    outside_anchor = tmp_path / "outside-anchor"
    outside_media = outside_anchor / "media"
    outside_media.mkdir(parents=True)
    outside = outside_media / "outside.png"
    outside.write_text(CANARY, encoding="utf-8")
    detached = tmp_path / "detached-anchor"
    swapped = False

    def reader(source, _kind):
        nonlocal swapped
        assert source.read() == b"inside"
        if not swapped:
            anchor.rename(detached)
            outside_anchor.rename(anchor)
            swapped = True
        return {"width": 1, "height": 1}

    service = _TestDirectorManagedMediaService(
        config_dir=tmp_path / "config",
        default_folders={"image": (MediaFolder("input", media),)},
        metadata_reader=reader,
    )
    folder = service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {}).references[0]
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.invoke(MEDIA_ITEMS_LIST, {}, {"folder": _resolved(folder.value)})
    assert (anchor / "media/outside.png").read_text(encoding="utf-8") == CANARY


def test_ancestor_swap_view_rejects_rebound_source_and_preserves_outside_canary(
    tmp_path,
    monkeypatch,
):
    anchor = tmp_path / "anchor"
    media = anchor / "media"
    media.mkdir(parents=True)
    (media / "frame.png").write_bytes(b"inside")
    outside_anchor = tmp_path / "outside-anchor"
    outside_media = outside_anchor / "media"
    outside_media.mkdir(parents=True)
    outside = outside_media / "frame.png"
    outside.write_text(CANARY, encoding="utf-8")
    detached = tmp_path / "detached-anchor"
    service = _TestDirectorManagedMediaService(
        config_dir=tmp_path / "config",
        default_folders={"image": (MediaFolder("input", media),)},
    )
    folder = service.invoke(MEDIA_FOLDERS_LIST, {"media_type": "image"}, {}).references[0]
    locator = service.invoke(
        MEDIA_ITEMS_LIST,
        {},
        {"folder": _resolved(folder.value)},
    ).references[0].value
    original_bind = root_bound_source

    def swapping_bind(*args, **kwargs):
        anchor.rename(detached)
        outside_anchor.rename(anchor)
        return original_bind(*args, **kwargs)

    monkeypatch.setattr(managed, "root_bound_source", swapping_bind)
    with pytest.raises(DirectorManagedMediaPrivacyError):
        service.bind_source(_resolved(locator), MEDIA_SOURCE_VIEW)
    assert (anchor / "media/frame.png").read_text(encoding="utf-8") == CANARY


def test_ancestor_swap_delete_unlinks_only_bound_original_files(tmp_path, monkeypatch):
    service, take_root = _project_service(tmp_path)
    media, sidecar = _write_take(take_root)
    listed = service.invoke(
        PROJECT_TAKES_LIST,
        {"project_record_id": PROJECT_RECORD_ID, "shot_id": "shot_001"},
        {},
    )
    locator = listed.references[1].value
    asset_root = tmp_path / "assets"
    detached = tmp_path / "detached-assets"
    outside_root = tmp_path / "outside-assets"
    outside_take = outside_root / "project_proj_123/takes/shot_001"
    outside_take.mkdir(parents=True)
    outside = outside_take / "capture.mp4"
    outside.write_text(CANARY, encoding="utf-8")
    (outside_take / "capture.helto_take.json").write_text(CANARY, encoding="utf-8")
    original_unlink = managed.os.unlink
    swapped = False

    def swapping_unlink(name, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            asset_root.rename(detached)
            outside_root.rename(asset_root)
            swapped = True
        return original_unlink(name, *args, **kwargs)

    monkeypatch.setattr(managed.os, "unlink", swapping_unlink)
    with pytest.raises(DirectorManagedMediaPrivacyError) as error:
        service.invoke(PROJECT_TAKES_DELETE, {}, {"take": _resolved(locator)})
    assert CANARY not in str(error.value)
    assert (asset_root / "project_proj_123/takes/shot_001/capture.mp4").read_text(encoding="utf-8") == CANARY
    assert not (detached / "project_proj_123/takes/shot_001/capture.mp4").exists()
    assert not (detached / "project_proj_123/takes/shot_001/capture.helto_take.json").exists()


def test_ancestor_swap_migration_never_retires_outside_file(tmp_path, monkeypatch):
    config_root = tmp_path / "config"
    legacy = _write_legacy_folder_config(
        config_root,
        "image",
        [{"alias": "image", "path": "/legacy/image", "enabled": True}],
    )
    detached = tmp_path / "detached-config"
    outside_root = tmp_path / "outside-config"
    outside_root.mkdir()
    outside = outside_root / "timeline_image_folders.json"
    outside.write_text(CANARY, encoding="utf-8")
    original_verify = managed._verify_absolute_directory
    swapped = False

    def swapping_verify(path, expected):
        nonlocal swapped
        if path == config_root and not swapped:
            config_root.rename(detached)
            outside_root.rename(config_root)
            swapped = True
        return original_verify(path, expected)

    monkeypatch.setattr(managed, "_verify_absolute_directory", swapping_verify)
    with pytest.raises(DirectorManagedMediaPrivacyError):
        migrate_legacy_media_folder_settings(
            config_dir=config_root,
            singleton_handle=_FolderSettings(),
            authorization=object(),
        )
    assert (config_root / "timeline_image_folders.json").read_text(encoding="utf-8") == CANARY
    assert (detached / legacy.name).exists()


def test_adapters_project_strict_coarse_payload_and_errors_are_canary_free(tmp_path):
    service, _media = _service(tmp_path)
    adapters = build_director_media_server_adapters(service)
    profile = build_director_media_privacy_profile()
    operation = next(item for item in profile.protected_operations if item.id == MEDIA_FOLDERS_LIST)
    adapter = adapters[MEDIA_OPERATION_ADAPTER_IDS[MEDIA_FOLDERS_LIST]]
    projected = adapter.project({
        "folder_count": 1,
        "enabled_count": 1,
        "existing_count": 1,
        "folders": [{"path": f"/{CANARY}"}],
    }, operation)
    assert projected == {"enabled_count": 1, "existing_count": 1, "folder_count": 1}
    assert CANARY not in json.dumps(projected)
    with pytest.raises(DirectorManagedMediaPrivacyError) as error:
        adapter.project({"folder_count": CANARY}, operation)
    assert CANARY not in str(error.value)


def test_static_module_has_no_live_route_or_cache_imports():
    source = Path(managed.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "routes.media_browser",
        "routes.media_cache",
        "shared.media_browser",
        "shared.media_cache",
        "register_media_browser_routes",
        "register_media_cache_routes",
        "resolve_project_record",
        "bound_privacy_pack",
    ):
        assert forbidden not in source
