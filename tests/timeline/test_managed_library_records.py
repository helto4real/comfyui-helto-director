from __future__ import annotations

import copy
import json
import multiprocessing
from dataclasses import replace
from pathlib import Path

import pytest

import helto_privacy.envelope as shared_envelope
import helto_privacy.guard as shared_guard
import helto_privacy.keystore as shared_keystore
import helto_privacy.migration as shared_migration
import helto_privacy.record_relocation as shared_relocation
import helto_privacy.runtime as shared_runtime
import helto_privacy.suite_runtime as shared_suite_runtime
from helto_privacy import (
    AdapterSlot,
    DIRECTOR_V1_JSON_KEY_IMPORT_ID,
    LegacyKeyFormat,
    LockedRecordShell,
    PrivacyEnvelopeCodec,
    PrivacyProfile,
    PrivacyScope,
    ProfileResource,
    RecordError,
    RecordSnapshot,
    ResourceKind,
    confirm_record_mutation,
    install,
    lock_keystore,
)
from helto_privacy.guard import authorize_privacy_request
from helto_privacy.record_relocation import (
    RECORD_REFERENCE_MAP_SCHEMA,
    RecordRelocationCommit,
    RecordRelocationRollback,
    RecordRelocationWrite,
)

from shared.timeline import managed_library_records as managed_records_module
from shared.timeline.defaults import create_default_video_timeline
from shared.timeline.managed_library_records import (
    CHARACTER_RECORD_KIND,
    CHARACTER_REFERENCE_MIGRATION_ID,
    CHARACTER_RESOURCE_ID,
    CHARACTER_STORE_ADAPTER_ID,
    MANAGED_LIBRARY_SCHEMA_VERSION,
    PRIVATE_RECORD_LABEL,
    PROJECT_RECORD_KIND,
    PROJECT_REFERENCE_MIGRATION_ID,
    PROJECT_RESOURCE_ID,
    PROJECT_STORE_ADAPTER_ID,
    DirectorManagedLibraryError,
    DirectorManagedLibraryStoreAdapter,
    build_director_library_privacy_profile,
    build_director_library_server_adapters,
    director_library_legacy_reader_units,
    legacy_library_path,
    managed_library_path,
)
from shared.timeline.managed_privacy import (
    DIRECTOR_DISTRIBUTION,
    DIRECTOR_PROFILE_ID,
    DIRECTOR_TIMELINE_SCHEMA,
    DIRECTOR_NODE_TYPE,
    GLOBAL_MODE_ADAPTER_ID,
    GLOBAL_MODE_BROWSER_ADAPTER_ID,
    GLOBAL_MODE_RESOURCE_ID,
    GLOBAL_SCOPE_ID,
    DirectorGlobalModeAdapter,
    build_director_timeline_privacy_profile,
    build_director_timeline_server_adapters,
)
from tests.synthetic_legacy import director_legacy_fixture


PASSWORD = "synthetic Director D3 password"


class Request:
    def __init__(self, token: str) -> None:
        self.headers = {"X-Helto-Privacy-Token": token}
        self.cookies = {}


def _authorization(pack, token: str, operation: str):
    return authorize_privacy_request(Request(token), operation, pack_id=pack.profile.id)


def _project(title: str = "SYNTHETIC_PROJECT") -> dict[str, object]:
    timeline = create_default_video_timeline()
    timeline["project"]["identity"]["name"] = title
    timeline["project"]["duration_seconds"] = 7.0
    timeline["director_track"]["sections"] = [
        {
            "id": "synthetic-section",
            "type": "text",
            "start_time": 0.0,
            "duration": 1.0,
            "prompt": "SYNTHETIC_PROJECT_PROMPT",
        }
    ]
    timeline["embedded_media"] = "data:image/png;base64,SYNTHETIC_EMBEDDED_MEDIA"
    return timeline


def _character(label: str = "SYNTHETIC_CHARACTER") -> dict[str, object]:
    return {
        "id": "synthetic-character",
        "kind": "character",
        "label": label,
        "description": "SYNTHETIC_CHARACTER_DESCRIPTION",
        "enabled": True,
        "strength": 0.8,
        "image": None,
        "thumbnail_data": "SYNTHETIC_EMBEDDED_THUMBNAIL",
    }


def _write_managed_batch(base_dir: str, start: int, count: int) -> None:
    adapter = DirectorManagedLibraryStoreAdapter(PROJECT_RECORD_KIND, base_dir)
    for index in range(start, start + count):
        adapter.write_protected(
            f"hp-rec-{index:032d}",
            {"schema": "synthetic.test", "value": index},
        )


def _compare_and_swap_managed_record(
    base_dir: str,
    record_kind: str,
    record_id: str,
    expected: RecordSnapshot,
    replacement: RecordSnapshot,
    results,
) -> None:
    adapter = DirectorManagedLibraryStoreAdapter(record_kind, base_dir)
    results.put(adapter.compare_and_swap_record(record_id, expected, replacement))


def _legacy_entry(
    legacy_id: str,
    kind: str,
    payload: dict[str, object],
    *,
    private: bool = False,
    envelope: object | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": legacy_id,
        "kind": kind,
        "type": "PROJECT_LIBRARY_ITEM" if kind == "project" else "CHARACTER_LIBRARY_ITEM",
        "name": "Private Project" if private and kind == "project" else f"SYNTHETIC_{kind.upper()}_NAME",
        "tags": ["synthetic"],
        "private": private,
        "is_private": private,
        "summary": {"SYNTHETIC_LEGACY_CANARY": "must-not-survive"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }
    if private:
        entry["encrypted_payload"] = envelope
    else:
        entry["description"] = f"SYNTHETIC_{kind.upper()}_DESCRIPTION"
        entry["payload"] = payload
    return entry


def _write_legacy(base_dir, *, projects=(), characters=()):
    path = legacy_library_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": 1,
                "projects": list(projects),
                "characters": list(characters),
            }
        ),
        encoding="utf-8",
    )
    return path


def _installed_pack(tmp_path, monkeypatch, *, project_adapter=None):
    state = tmp_path / "shared-state"
    monkeypatch.setenv(shared_migration.MIGRATION_STATE_ENV, str(state / "migration.json"))
    monkeypatch.setenv(
        shared_relocation.RECORD_RELOCATION_STATE_ENV,
        str(state / "relocations.json"),
    )
    monkeypatch.setenv(shared_keystore.KEYSTORE_ENV, str(state / "keystore.json"))
    monkeypatch.setenv(shared_keystore.SESSION_DIR_ENV, str(state / "session"))
    monkeypatch.setattr(shared_runtime, "_INSTALLATIONS", {})
    monkeypatch.setattr(shared_runtime, "register_helto_privacy_ui", lambda **_kwargs: True)
    monkeypatch.setattr(shared_suite_runtime, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_keystore, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_envelope, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_guard, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_keystore, "SCRYPT_N", 2**12)
    shared_migration.reset_migration_runtime_for_tests()
    shared_migration.register_legacy_reader_units(director_library_legacy_reader_units())

    adapters = build_director_timeline_server_adapters()
    adapters[GLOBAL_MODE_ADAPTER_ID] = DirectorGlobalModeAdapter(
        loader=lambda: {"privacy": {"mode": True}},
        saver=lambda _settings: None,
    )
    adapters.update(build_director_library_server_adapters(tmp_path))
    if project_adapter is not None:
        adapters[PROJECT_STORE_ADAPTER_ID] = project_adapter
    pack = install(build_director_library_privacy_profile(), adapters)
    token = shared_keystore.initialize_keystore(PASSWORD)["token"]
    return pack, token, adapters


def test_d3_composes_records_onto_d2_without_losing_browser_or_execution_contracts():
    base = build_director_timeline_privacy_profile()
    profile = build_director_library_privacy_profile(base)

    assert profile.id == DIRECTOR_PROFILE_ID == "helto.director"
    assert profile.distribution == DIRECTOR_DISTRIBUTION
    assert profile.browser_adapters == base.browser_adapters
    assert profile.protected_fields == base.protected_fields
    assert profile.execution_projections == base.execution_projections
    assert profile.scopes == base.scopes
    assert {resource.id for resource in profile.resources} >= {
        PROJECT_RESOURCE_ID,
        CHARACTER_RESOURCE_ID,
    }
    declarations = {record.id: record for record in profile.records}
    assert set(declarations) == {PROJECT_RECORD_KIND, CHARACTER_RECORD_KIND}
    for kind, field in (
        (PROJECT_RECORD_KIND, "project"),
        (CHARACTER_RECORD_KIND, "character"),
    ):
        record = declarations[kind]
        assert record.scope_id == GLOBAL_SCOPE_ID
        assert record.current_schema == DIRECTOR_TIMELINE_SCHEMA
        assert record.safe_projection == ()
        assert record.fixed_private_label == PRIVATE_RECORD_LABEL
        assert record.mutation_operations == ("create", "duplicate", "patch", "replace")
        assert {item.operation: item.safe_fields for item in record.projections} == {
            "details": ("metadata",),
            "preview": ("preview",),
            "use": (field,),
        }
    assert {item.resource_id for item in profile.record_reference_migrations} == {
        PROJECT_RESOURCE_ID,
        CHARACTER_RESOURCE_ID,
    }
    assert all(
        binding.location_kind.value == "record"
        for binding in profile.legacy_bindings
    )
    record_key_imports = [
        item for item in profile.legacy_key_imports if item.location_kind.value == "record"
    ]
    assert len(record_key_imports) == 2
    assert {item.import_id for item in record_key_imports} == {
        DIRECTOR_V1_JSON_KEY_IMPORT_ID
    }


def test_d3_composition_rejects_wrong_missing_and_duplicate_bases():
    wrong = type(
        "WrongProfile",
        (),
        {"id": "other.pack", "distribution": "other-pack", "scopes": ()},
    )()
    with pytest.raises(ValueError, match="Director profile"):
        build_director_library_privacy_profile(wrong)
    minimal = PrivacyProfile(
        id=DIRECTOR_PROFILE_ID,
        distribution=DIRECTOR_DISTRIBUTION,
        resources=(
            ProfileResource(
                GLOBAL_MODE_RESOURCE_ID,
                ResourceKind.MODE,
                (GLOBAL_MODE_ADAPTER_ID, GLOBAL_MODE_BROWSER_ADAPTER_ID),
            ),
        ),
        server_adapters=(
            AdapterSlot(
                GLOBAL_MODE_ADAPTER_ID,
                ResourceKind.MODE,
                GLOBAL_MODE_RESOURCE_ID,
            ),
        ),
        browser_adapters=(
            AdapterSlot(
                GLOBAL_MODE_BROWSER_ADAPTER_ID,
                ResourceKind.MODE,
                GLOBAL_MODE_RESOURCE_ID,
                (DIRECTOR_NODE_TYPE,),
            ),
        ),
        scopes=(
            PrivacyScope(
                GLOBAL_SCOPE_ID,
                GLOBAL_MODE_RESOURCE_ID,
                GLOBAL_MODE_ADAPTER_ID,
                GLOBAL_MODE_BROWSER_ADAPTER_ID,
            ),
        ),
    )
    with pytest.raises(ValueError, match="complete D2 contract"):
        build_director_library_privacy_profile(minimal)
    modified = build_director_timeline_privacy_profile()
    modified = replace(
        modified,
        protected_fields=(
            replace(modified.protected_fields[0], purpose="modified-timeline-state"),
        ),
    )
    with pytest.raises(ValueError, match="complete D2 contract"):
        build_director_library_privacy_profile(modified)
    composed = build_director_library_privacy_profile()
    with pytest.raises(Exception):
        build_director_library_privacy_profile(composed)


def test_d3_server_adapters_cover_only_new_contract_slots(tmp_path):
    base = build_director_timeline_privacy_profile()
    profile = build_director_library_privacy_profile(base)
    adapters = build_director_library_server_adapters(tmp_path)

    assert set(adapters) == {PROJECT_STORE_ADAPTER_ID, CHARACTER_STORE_ADAPTER_ID}
    for adapter_id in adapters:
        methods = profile.server_adapter_contracts[adapter_id]
        assert all(callable(getattr(adapters[adapter_id], method, None)) for method in methods)


@pytest.mark.parametrize("record_kind", (PROJECT_RECORD_KIND, CHARACTER_RECORD_KIND))
def test_record_store_cas_is_monotonic_and_rejects_stale_or_ambiguous_rollback(
    tmp_path,
    record_kind,
):
    adapter = DirectorManagedLibraryStoreAdapter(record_kind, tmp_path)
    record_id = "hp-rec-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    missing = RecordSnapshot(0)
    first = RecordSnapshot(1, {"schema": "synthetic.test", "value": "first"})
    second = RecordSnapshot(2, {"schema": "synthetic.test", "value": "second"})
    concurrent = RecordSnapshot(
        3,
        {"schema": "synthetic.test", "value": "concurrent"},
    )

    assert adapter.read_record(record_id) == missing
    assert adapter.compare_and_swap_record(record_id, missing, first) is True
    assert adapter.compare_and_swap_record(
        record_id,
        missing,
        RecordSnapshot(1, {"schema": "synthetic.test", "value": "stale"}),
    ) is False
    assert adapter.compare_and_swap_record(record_id, first, second) is True
    assert adapter.compare_and_swap_record(record_id, second, concurrent) is True

    ambiguous_rollback = RecordSnapshot(3, first.protected)
    assert adapter.compare_and_swap_record(
        record_id,
        second,
        ambiguous_rollback,
    ) is False
    assert adapter.read_record(record_id) == concurrent

    tombstone = RecordSnapshot(4)
    assert adapter.compare_and_swap_record(record_id, concurrent, tombstone) is True
    assert adapter.list_ids() == ()
    restored = RecordSnapshot(5, first.protected)
    assert adapter.compare_and_swap_record(record_id, tombstone, restored) is True
    assert adapter.read_record(record_id) == restored


def test_product_crud_is_normalized_stripped_projected_and_opaque(
    tmp_path,
    monkeypatch,
):
    pack, token, _adapters = _installed_pack(tmp_path, monkeypatch)
    records = pack.records(PROJECT_RESOURCE_ID)
    private_payload = _project()
    long_prompt = "A" * 512
    payload_description = (
        "This is legitimate prose describing how the image payload should be framed. "
        * 10
    )
    private_payload["director_track"]["sections"][0]["prompt"] = long_prompt
    private_payload["portrait_payload"] = b"S" * 512
    private_payload["image_urlsafe"] = "A_-" * 180
    private_payload["portrait"] = "A_-" * 180
    private_payload["image_payload_description"] = payload_description
    created = records.mutate(
        PROJECT_RECORD_KIND,
        "create",
        {
            "metadata": {
                "name": "SYNTHETIC_PRIVATE_PROJECT",
                "description": "SYNTHETIC_PRIVATE_DESCRIPTION",
                "tags": ["synthetic", "synthetic"],
            },
            "payload": private_payload,
        },
        _authorization(pack, token, "record.create"),
    )
    record_id = created.record_id
    assert record_id.startswith("hp-rec-")
    stored_text = managed_library_path(PROJECT_RECORD_KIND, tmp_path).read_text()
    for canary in (
        "SYNTHETIC_PRIVATE_PROJECT",
        "SYNTHETIC_PRIVATE_DESCRIPTION",
        "SYNTHETIC_PROJECT_PROMPT",
        "SYNTHETIC_EMBEDDED_MEDIA",
    ):
        assert canary not in stored_text
    document = json.loads(stored_text)
    assert document["schema_version"] == MANAGED_LIBRARY_SCHEMA_VERSION
    assert set(document) == {
        "schema_version",
        "version",
        "entries",
        "reference_mappings",
    }
    assert set(document["entries"][0]) == {"id", "revision", "protected"}
    assert document["entries"][0]["revision"] == 1

    details = records.reveal(
        PROJECT_RECORD_KIND,
        record_id,
        "details",
        _authorization(pack, token, "record.details"),
    ).value
    assert details["metadata"]["name"] == "SYNTHETIC_PRIVATE_PROJECT"
    assert details["metadata"]["tags"] == ["synthetic"]
    preview = records.reveal(
        PROJECT_RECORD_KIND,
        record_id,
        "preview",
        _authorization(pack, token, "record.preview"),
    ).value
    assert set(preview) == {"preview"}
    used = records.reveal(
        PROJECT_RECORD_KIND,
        record_id,
        "use",
        _authorization(pack, token, "record.use"),
    ).value
    assert "embedded_media" not in used["project"]
    assert "portrait_payload" not in used["project"]
    assert "image_urlsafe" not in used["project"]
    assert "portrait" not in used["project"]
    assert used["project"]["image_payload_description"] == payload_description
    assert "validation" in used["project"]
    assert used["project"]["director_track"]["sections"][0]["prompt"] == long_prompt

    patched = records.mutate(
        PROJECT_RECORD_KIND,
        "patch",
        {"metadata": {"description": "SYNTHETIC_PATCHED_DESCRIPTION"}},
        _authorization(pack, token, "record.patch"),
        record_id=record_id,
    )
    assert patched.record_id == record_id
    duplicate = records.mutate(
        PROJECT_RECORD_KIND,
        "duplicate",
        {"metadata": {"name": "SYNTHETIC_DUPLICATE"}},
        _authorization(pack, token, "record.duplicate"),
        record_id=record_id,
    )
    assert duplicate.record_id != record_id
    replacement = _project("SYNTHETIC_REPLACEMENT")
    replaced = records.mutate(
        PROJECT_RECORD_KIND,
        "replace",
        {"metadata": {"name": "SYNTHETIC_REPLACEMENT"}, "payload": replacement},
        _authorization(pack, token, "record.replace"),
        record_id=record_id,
    )
    assert replaced.record_id == record_id


def test_locked_listing_is_minimal_and_never_reads_or_decrypts_records(
    tmp_path,
    monkeypatch,
):
    pack, token, adapters = _installed_pack(tmp_path, monkeypatch)
    records = pack.records(CHARACTER_RESOURCE_ID)
    character_payload = _character()
    character_payload["image"] = {
        "type": "image",
        "path": "/synthetic/referenced-character.png",
    }
    character_payload["portrait_payload"] = b"P" * 512
    character_payload["image_urlsafe"] = "B_-" * 180
    receipt = records.mutate(
        CHARACTER_RECORD_KIND,
        "create",
        {
            "metadata": {"name": "SYNTHETIC_LOCKED_CHARACTER"},
            "payload": character_payload,
        },
        _authorization(pack, token, "record.create"),
    )
    adapter = adapters[CHARACTER_STORE_ADAPTER_ID]
    used = records.reveal(
        CHARACTER_RECORD_KIND,
        receipt.record_id,
        "use",
        _authorization(pack, token, "record.use"),
    ).value["character"]
    assert "portrait_payload" not in used
    assert "image_urlsafe" not in used
    assert used["image"]["path"] == "/synthetic/referenced-character.png"
    original_read = adapter.read_protected
    adapter.read_protected = lambda _record_id: pytest.fail("locked listing read a record")
    lock_keystore()
    try:
        shells = records.list_shells(CHARACTER_RECORD_KIND)
    finally:
        adapter.read_protected = original_read
    assert shells == (
        LockedRecordShell(id=receipt.record_id, kind=CHARACTER_RECORD_KIND),
    )
    assert shells[0].to_payload() == {
        "id": receipt.record_id,
        "kind": CHARACTER_RECORD_KIND,
        "private": True,
        "label": PRIVATE_RECORD_LABEL,
    }
    assert "SYNTHETIC_LOCKED_CHARACTER" not in repr(shells)
    confirmation = confirm_record_mutation(
        pack_id=pack.profile.id,
        resource_id=CHARACTER_RESOURCE_ID,
        record_kind=CHARACTER_RECORD_KIND,
        record_id=receipt.record_id,
        operation="delete",
        confirmed=True,
    )
    deleted = records.delete(
        CHARACTER_RECORD_KIND,
        receipt.record_id,
        confirmation,
    )
    assert deleted.operation == "delete"
    assert records.list_shells(CHARACTER_RECORD_KIND) == ()


def test_failed_decrypt_stays_generic_and_preserves_opaque_record(
    tmp_path,
    monkeypatch,
):
    pack, token, adapters = _installed_pack(tmp_path, monkeypatch)
    adapter = adapters[PROJECT_STORE_ADAPTER_ID]
    record_id = "hp-rec-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    protected = PrivacyEnvelopeCodec(DIRECTOR_TIMELINE_SCHEMA).encrypt_state(
        {
            "metadata": {"name": "SYNTHETIC_DECRYPT_CANARY"},
            "payload": _project(),
        }
    )
    protected["ciphertext"] = (
        ("A" if protected["ciphertext"][0] != "A" else "B")
        + protected["ciphertext"][1:]
    )
    adapter.write_protected(record_id, protected)

    with pytest.raises(RecordError) as failed:
        pack.records(PROJECT_RESOURCE_ID).reveal(
            PROJECT_RECORD_KIND,
            record_id,
            "details",
            _authorization(pack, token, "record.details"),
        )
    assert failed.value.code == "PRIVACY_RECORD_DECRYPT_FAILED"
    assert "SYNTHETIC_DECRYPT_CANARY" not in str(failed.value)
    assert adapter.list_ids() == (record_id,)


def test_projection_normalization_writes_current_record_back(
    tmp_path,
    monkeypatch,
):
    pack, token, adapters = _installed_pack(tmp_path, monkeypatch)
    adapter = adapters[PROJECT_STORE_ADAPTER_ID]
    record_id = "hp-rec-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    stale = {
        "metadata": {
            "name": "SYNTHETIC_STALE_PROJECT",
            "description": "",
            "tags": ["synthetic", "synthetic"],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        "payload": _project(),
        "SYNTHETIC_STALE_CANARY": "remove-me",
    }
    before = PrivacyEnvelopeCodec(DIRECTOR_TIMELINE_SCHEMA).encrypt_state(stale)
    adapter.write_protected(record_id, before)

    pack.records(PROJECT_RESOURCE_ID).reveal(
        PROJECT_RECORD_KIND,
        record_id,
        "details",
        _authorization(pack, token, "record.details"),
    )
    after = adapter.read_protected(record_id)
    assert after["ciphertext"] != before["ciphertext"]
    current = PrivacyEnvelopeCodec(DIRECTOR_TIMELINE_SCHEMA).decrypt_state(after)
    assert set(current) == {"metadata", "payload"}
    assert current["metadata"]["tags"] == ["synthetic"]
    assert "summary" in current["metadata"]
    assert "validation" in current["payload"]


class _CrashAfterCommitStore(DirectorManagedLibraryStoreAdapter):
    def __init__(self, base_dir) -> None:
        super().__init__(PROJECT_RECORD_KIND, base_dir)
        self.crash_once = True

    def commit_record_relocation(self, write):
        result = super().commit_record_relocation(write)
        if self.crash_once:
            self.crash_once = False
            raise RuntimeError("synthetic crash after atomic commit")
        return result


def test_relocation_retry_is_idempotent_and_finalizes_only_exact_source(
    tmp_path,
    monkeypatch,
):
    legacy_id = "arbitrary old/project id"
    untouched_id = "untouched-project"
    _write_legacy(
        tmp_path,
        projects=(
            _legacy_entry(legacy_id, "project", _project()),
            _legacy_entry(untouched_id, "project", _project("SYNTHETIC_UNTOUCHED")),
        ),
    )
    store = _CrashAfterCommitStore(tmp_path)
    pack, token, _adapters = _installed_pack(
        tmp_path,
        monkeypatch,
        project_adapter=store,
    )
    records = pack.records(PROJECT_RESOURCE_ID)
    with pytest.raises(Exception):
        records.migrate_legacy_reference(
            PROJECT_RECORD_KIND,
            PROJECT_REFERENCE_MIGRATION_ID,
            legacy_id,
            _authorization(pack, token, "record.reference.migrate"),
        )
    committed_id = store.list_ids()[0]
    assert committed_id.startswith("hp-rec-")
    assert any(item["id"] == legacy_id for item in json.loads(legacy_library_path(tmp_path).read_text())["projects"])

    receipt = records.migrate_legacy_reference(
        PROJECT_RECORD_KIND,
        PROJECT_REFERENCE_MIGRATION_ID,
        legacy_id,
        _authorization(pack, token, "record.reference.migrate"),
    )
    assert receipt.record_id == committed_id
    legacy = json.loads(legacy_library_path(tmp_path).read_text())
    assert [item["id"] for item in legacy["projects"]] == [untouched_id]
    managed_text = managed_library_path(PROJECT_RECORD_KIND, tmp_path).read_text()
    assert legacy_id not in managed_text
    assert "SYNTHETIC_PROJECT_PROMPT" not in managed_text
    managed = json.loads(managed_text)
    assert len(managed["entries"]) == len(managed["reference_mappings"]) == 1
    assert set(managed["reference_mappings"][0]) == {"id", "protected"}

    resolved = records.resolve_legacy_reference(
        PROJECT_RECORD_KIND,
        PROJECT_REFERENCE_MIGRATION_ID,
        legacy_id,
        _authorization(pack, token, "record.reference.resolve"),
    )
    assert resolved.record_id == committed_id


def test_atomic_adapter_cas_and_guarded_rollback_reject_divergence(
    tmp_path,
    monkeypatch,
):
    _write_legacy(
        tmp_path,
        characters=(
            _legacy_entry("legacy-character", "character", _character()),
        ),
    )
    _pack, _token, _adapters = _installed_pack(tmp_path, monkeypatch)
    adapter = DirectorManagedLibraryStoreAdapter(CHARACTER_RECORD_KIND, tmp_path)
    source = adapter.read_legacy_record(
        CHARACTER_REFERENCE_MIGRATION_ID,
        "legacy-character",
    )
    record_id = "hp-rec-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    mapping_id = "hp-rmap-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    protected_record = PrivacyEnvelopeCodec(DIRECTOR_TIMELINE_SCHEMA).encrypt_state(
        source.value
    )
    protected_mapping = PrivacyEnvelopeCodec(RECORD_REFERENCE_MAP_SCHEMA).encrypt_state(
        {
            "pack": DIRECTOR_PROFILE_ID,
            "fingerprint": build_director_library_privacy_profile().fingerprint,
            "resource": CHARACTER_RESOURCE_ID,
            "kind": CHARACTER_RECORD_KIND,
            "migration": CHARACTER_REFERENCE_MIGRATION_ID,
            "binding": "director-character-library-v1-binding",
            "reference": "legacy-character",
            "target": record_id,
        }
    )
    write = RecordRelocationWrite(
        "hp-rmap-txn-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
        CHARACTER_REFERENCE_MIGRATION_ID,
        record_id,
        mapping_id,
        source.revision,
        "legacy-character",
        protected_record,
        protected_mapping,
    )
    first = adapter.commit_record_relocation(write)
    second = adapter.commit_record_relocation(write)
    assert second == first
    assert len(adapter.list_ids()) == 1
    readback = adapter.read_record_relocation(first)
    assert readback.protected_record == protected_record
    with pytest.raises(DirectorManagedLibraryError):
        adapter.read_record_relocation(
            RecordRelocationCommit(
                "wrong-director-commit",
                first.record_revision,
                first.mapping_revision,
            )
        )

    legacy_document = json.loads(legacy_library_path(tmp_path).read_text())
    legacy_document["characters"][0]["updated_at"] = "2026-03-01T00:00:00Z"
    legacy_library_path(tmp_path).write_text(
        json.dumps(legacy_document),
        encoding="utf-8",
    )
    with pytest.raises(DirectorManagedLibraryError, match="changed"):
        adapter.commit_record_relocation(write)

    adapter.write_protected(
        record_id,
        PrivacyEnvelopeCodec(DIRECTOR_TIMELINE_SCHEMA).encrypt_state(source.value),
    )
    result = adapter.rollback_record_relocation(
        RecordRelocationRollback(
            write.transaction_id,
            record_id,
            mapping_id,
            first.record_revision,
            first.mapping_revision,
        )
    )
    assert result == "diverged"
    assert adapter.list_ids() == (record_id,)


def test_current_schema_legacy_envelope_migrates_after_json_key_import(
    tmp_path,
    monkeypatch,
):
    legacy_root = tmp_path / "legacy"
    envelope, key_source = director_legacy_fixture(
        legacy_root,
        {
            "name": "SYNTHETIC_IMPORTED_PROJECT",
            "description": "SYNTHETIC_IMPORTED_DESCRIPTION",
            "payload": _project("SYNTHETIC_IMPORTED_PROJECT"),
        },
    )
    _write_legacy(
        legacy_root,
        projects=(
            _legacy_entry(
                "imported-project",
                "project",
                _project(),
                private=True,
                envelope=envelope,
            ),
        ),
    )
    pack, token, _adapters = _installed_pack(legacy_root, monkeypatch)
    pack.migration.import_legacy_key_source(
        DIRECTOR_V1_JSON_KEY_IMPORT_ID,
        key_source,
        PASSWORD,
        LegacyKeyFormat.JSON,
        _authorization(pack, token, "migration.key-import"),
    )
    token = shared_keystore.session_token()
    records = pack.records(PROJECT_RESOURCE_ID)
    migrated = records.migrate_legacy_reference(
        PROJECT_RECORD_KIND,
        PROJECT_REFERENCE_MIGRATION_ID,
        "imported-project",
        _authorization(pack, token, "record.reference.migrate"),
    )
    details = records.reveal(
        PROJECT_RECORD_KIND,
        migrated.record_id,
        "details",
        _authorization(pack, token, "record.details"),
    ).value
    assert details["metadata"]["name"] == "SYNTHETIC_IMPORTED_PROJECT"
    assert envelope["schema"] == DIRECTOR_TIMELINE_SCHEMA
    assert not key_source.exists()


def test_legacy_character_relocation_strips_urlsafe_embedded_media(
    tmp_path,
    monkeypatch,
):
    character = _character("SYNTHETIC_LEGACY_CHARACTER")
    character["image"] = {
        "type": "image",
        "path": "/synthetic/preserved-reference.png",
    }
    character["portrait_payload"] = "C" * 512
    character["image_urlsafe"] = "C_-" * 180
    _write_legacy(
        tmp_path,
        characters=(
            _legacy_entry("legacy-character-media", "character", character),
        ),
    )
    pack, token, _adapters = _installed_pack(tmp_path, monkeypatch)
    records = pack.records(CHARACTER_RESOURCE_ID)
    migrated = records.migrate_legacy_reference(
        CHARACTER_RECORD_KIND,
        CHARACTER_REFERENCE_MIGRATION_ID,
        "legacy-character-media",
        _authorization(pack, token, "record.reference.migrate"),
    )
    used = records.reveal(
        CHARACTER_RECORD_KIND,
        migrated.record_id,
        "use",
        _authorization(pack, token, "record.use"),
    ).value["character"]
    assert "portrait_payload" not in used
    assert "image_urlsafe" not in used
    assert used["image"]["path"] == "/synthetic/preserved-reference.png"


def test_storage_rejects_symlinked_base_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    adapter = DirectorManagedLibraryStoreAdapter(PROJECT_RECORD_KIND, linked)

    with pytest.raises(DirectorManagedLibraryError, match="storage path"):
        adapter.write_protected(
            "hp-rec-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
            {"schema": "synthetic.test", "value": "SYNTHETIC"},
        )
    assert list(outside.iterdir()) == []


def test_locked_operation_rejects_replaced_base_without_split_lock_or_write(
    tmp_path,
    monkeypatch,
):
    base = tmp_path / "base"
    base.mkdir()
    moved = tmp_path / "bound-original"
    original_flock = managed_records_module.fcntl.flock
    swapped = False

    def swap_after_lock(file_descriptor, operation):
        nonlocal swapped
        result = original_flock(file_descriptor, operation)
        if operation == managed_records_module.fcntl.LOCK_EX and not swapped:
            swapped = True
            base.rename(moved)
            base.mkdir()
        return result

    monkeypatch.setattr(
        managed_records_module.fcntl,
        "flock",
        swap_after_lock,
    )
    adapter = DirectorManagedLibraryStoreAdapter(PROJECT_RECORD_KIND, base)
    with pytest.raises(DirectorManagedLibraryError, match="binding changed"):
        adapter.write_protected(
            "hp-rec-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
            {"schema": "synthetic.test", "value": "SYNTHETIC_BOUND_WRITE"},
        )

    assert list(base.iterdir()) == []
    stored = json.loads((moved / "director_projects_v2.json").read_text())
    assert stored["entries"][0]["protected"]["value"] == "SYNTHETIC_BOUND_WRITE"
    assert (moved / ".director_projects_v2.json.lock").is_file()
    assert not (base / ".director_projects_v2.json.lock").exists()


def test_temp_cleanup_error_does_not_leak_bound_parent_fd(tmp_path, monkeypatch):
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("fd accounting requires procfs")
    adapter = DirectorManagedLibraryStoreAdapter(PROJECT_RECORD_KIND, tmp_path)
    original_unlink = managed_records_module.os.unlink

    def fail_temp_cleanup(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            raise PermissionError("synthetic temp cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(managed_records_module.os, "unlink", fail_temp_cleanup)
    before = len(list(Path("/proc/self/fd").iterdir()))
    with pytest.raises(PermissionError, match="synthetic temp cleanup failure"):
        adapter.write_protected(
            "hp-rec-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
            {"schema": "synthetic.test", "value": "SYNTHETIC_FD_TEST"},
        )
    after = len(list(Path("/proc/self/fd").iterdir()))
    assert after == before


def test_lock_unlock_failure_closes_lock_and_parent_fds(tmp_path, monkeypatch):
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("fd accounting requires procfs")
    adapter = DirectorManagedLibraryStoreAdapter(PROJECT_RECORD_KIND, tmp_path)
    original_flock = managed_records_module.fcntl.flock

    def fail_unlock(file_descriptor, operation):
        if operation == managed_records_module.fcntl.LOCK_UN:
            raise OSError("synthetic unlock failure")
        return original_flock(file_descriptor, operation)

    monkeypatch.setattr(managed_records_module.fcntl, "flock", fail_unlock)
    before = len(list(Path("/proc/self/fd").iterdir()))
    with pytest.raises(DirectorManagedLibraryError, match="lock cleanup failed"):
        adapter.list_ids()
    after = len(list(Path("/proc/self/fd").iterdir()))
    assert after == before


def test_temp_fdopen_failure_closes_raw_temp_and_parent_fds(tmp_path, monkeypatch):
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("fd accounting requires procfs")
    adapter = DirectorManagedLibraryStoreAdapter(PROJECT_RECORD_KIND, tmp_path)
    original_fdopen = managed_records_module.os.fdopen

    def fail_write_fdopen(file_descriptor, mode="r", *args, **kwargs):
        if mode == "w":
            raise OSError("synthetic fdopen failure")
        return original_fdopen(file_descriptor, mode, *args, **kwargs)

    monkeypatch.setattr(managed_records_module.os, "fdopen", fail_write_fdopen)
    before = len(list(Path("/proc/self/fd").iterdir()))
    with pytest.raises(OSError, match="synthetic fdopen failure"):
        adapter.write_protected(
            "hp-rec-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
            {"schema": "synthetic.test", "value": "SYNTHETIC_FDOPEN_TEST"},
        )
    after = len(list(Path("/proc/self/fd").iterdir()))
    assert after == before
    assert not managed_library_path(PROJECT_RECORD_KIND, tmp_path).exists()


def test_fcntl_store_lock_preserves_all_cross_process_writes(tmp_path):
    if "spawn" not in multiprocessing.get_all_start_methods():
        pytest.skip("spawn multiprocessing is required for the fcntl stress test")
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(
            target=_write_managed_batch,
            args=(str(tmp_path), worker * 30, 30),
        )
        for worker in range(8)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0

    adapter = DirectorManagedLibraryStoreAdapter(PROJECT_RECORD_KIND, tmp_path)
    identifiers = adapter.list_ids()
    assert len(identifiers) == 240
    assert len(set(identifiers)) == 240


def test_fcntl_store_lock_allows_only_one_cross_process_cas_winner(tmp_path):
    if "spawn" not in multiprocessing.get_all_start_methods():
        pytest.skip("spawn multiprocessing is required for the fcntl CAS test")
    adapter = DirectorManagedLibraryStoreAdapter(CHARACTER_RECORD_KIND, tmp_path)
    record_id = "hp-rec-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    adapter.write_protected(
        record_id,
        {"schema": "synthetic.test", "value": "before"},
    )
    expected = adapter.read_record(record_id)
    replacement = RecordSnapshot(
        expected.revision + 1,
        {"schema": "synthetic.test", "value": "winner"},
    )
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    workers = [
        context.Process(
            target=_compare_and_swap_managed_record,
            args=(
                str(tmp_path),
                CHARACTER_RECORD_KIND,
                record_id,
                expected,
                replacement,
                results,
            ),
        )
        for _index in range(8)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    outcomes = [results.get(timeout=2) for _worker in workers]
    results.close()
    results.join_thread()

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7
    assert adapter.read_record(record_id) == replacement
