"""Managed privacy records for Director projects and characters."""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from helto_privacy import (
    DIRECTOR_V1_JSON_KEY_IMPORT_ID,
    AdapterSlot,
    LegacyKeyFormat,
    LegacyKeyImportBinding,
    LegacyLocationKind,
    LegacyReaderBinding,
    LegacyReaderUnit,
    PrivacyProfile,
    ProfileResource,
    RecordDeclaration,
    RecordProjectionResult,
    RecordReferenceMigration,
    RecordRevealProjection,
    RecordSnapshot,
    ResourceKind,
)
from helto_privacy.envelope import PrivacyEnvelopeCodec
from helto_privacy.record_relocation import (
    RECORD_REFERENCE_MAP_SCHEMA,
    LegacyRecordFinalize,
    LegacyRecordSource,
    RecordRelocationCommit,
    RecordRelocationReadback,
    RecordRelocationRollback,
    RecordRelocationWrite,
)

from .. import timeline_library
from .managed_privacy import (
    DIRECTOR_DISTRIBUTION,
    DIRECTOR_PROFILE_ID,
    DIRECTOR_TIMELINE_SCHEMA,
    GLOBAL_SCOPE_ID,
    build_director_timeline_privacy_profile,
)


PROJECT_RESOURCE_ID = "director-projects"
CHARACTER_RESOURCE_ID = "director-characters"
PROJECT_RECORD_KIND = "director-project"
CHARACTER_RECORD_KIND = "director-character"
PROJECT_STORE_ADAPTER_ID = "director-project-record-store"
CHARACTER_STORE_ADAPTER_ID = "director-character-record-store"

PROJECT_LEGACY_READER_ID = "director-project-library-v1"
CHARACTER_LEGACY_READER_ID = "director-character-library-v1"
PROJECT_LEGACY_BINDING_ID = "director-project-library-v1-binding"
CHARACTER_LEGACY_BINDING_ID = "director-character-library-v1-binding"
PROJECT_LEGACY_KEY_BINDING_ID = "director-project-library-json-key-v1"
CHARACTER_LEGACY_KEY_BINDING_ID = "director-character-library-json-key-v1"
PROJECT_REFERENCE_MIGRATION_ID = "director-project-library-v1-relocation"
CHARACTER_REFERENCE_MIGRATION_ID = "director-character-library-v1-relocation"

MANAGED_LIBRARY_SCHEMA_VERSION = "2.0"
MANAGED_LIBRARY_VERSION = 2
PROJECT_MANAGED_FILE_NAME = "director_projects_v2.json"
CHARACTER_MANAGED_FILE_NAME = "director_characters_v2.json"
PRIVATE_RECORD_LABEL = "Private record"

_KIND_FACTS = {
    PROJECT_RECORD_KIND: {
        "legacy_kind": timeline_library.PROJECT_KIND,
        "resource": PROJECT_RESOURCE_ID,
        "adapter": PROJECT_STORE_ADAPTER_ID,
        "file": PROJECT_MANAGED_FILE_NAME,
        "reader": PROJECT_LEGACY_READER_ID,
        "binding": PROJECT_LEGACY_BINDING_ID,
        "key_binding": PROJECT_LEGACY_KEY_BINDING_ID,
        "migration": PROJECT_REFERENCE_MIGRATION_ID,
        "use_field": "project",
    },
    CHARACTER_RECORD_KIND: {
        "legacy_kind": timeline_library.CHARACTER_KIND,
        "resource": CHARACTER_RESOURCE_ID,
        "adapter": CHARACTER_STORE_ADAPTER_ID,
        "file": CHARACTER_MANAGED_FILE_NAME,
        "reader": CHARACTER_LEGACY_READER_ID,
        "binding": CHARACTER_LEGACY_BINDING_ID,
        "key_binding": CHARACTER_LEGACY_KEY_BINDING_ID,
        "migration": CHARACTER_REFERENCE_MIGRATION_ID,
        "use_field": "character",
    },
}

_OPAQUE_TEXT = re.compile(r"^[A-Za-z0-9+/_-]+={0,2}$")
_MEDIA_KEY_TOKENS = (
    "audio",
    "image",
    "media",
    "portrait",
    "preview",
    "thumbnail",
    "video",
    "waveform",
)
_HARDENED_REMOVED = object()


class DirectorManagedLibraryError(ValueError):
    """Sanitized product-store failure for the managed library."""


def managed_library_path(
    record_kind: str,
    base_dir: str | os.PathLike[str] | None = None,
) -> Path:
    facts = _facts(record_kind)
    root = Path(base_dir) if base_dir is not None else timeline_library.config_dir()
    return _trusted_storage_path(root / str(facts["file"]))


def legacy_library_path(
    base_dir: str | os.PathLike[str] | None = None,
) -> Path:
    root = Path(base_dir) if base_dir is not None else timeline_library.config_dir()
    return _trusted_storage_path(root / timeline_library.LIBRARY_FILE_NAME)


def build_director_library_privacy_profile(
    base_profile: PrivacyProfile | None = None,
) -> PrivacyProfile:
    """Compose D3 declarations onto the complete D2 profile."""

    expected = build_director_timeline_privacy_profile()
    base = base_profile or expected
    if base.id != DIRECTOR_PROFILE_ID or base.distribution != DIRECTOR_DISTRIBUTION:
        raise ValueError("Director library records require the Director profile.")
    _require_complete_d2_base(base, expected)
    resources = (
        ProfileResource(
            PROJECT_RESOURCE_ID,
            ResourceKind.RECORD,
            (PROJECT_STORE_ADAPTER_ID,),
        ),
        ProfileResource(
            CHARACTER_RESOURCE_ID,
            ResourceKind.RECORD,
            (CHARACTER_STORE_ADAPTER_ID,),
        ),
    )
    adapters = (
        AdapterSlot(PROJECT_STORE_ADAPTER_ID, ResourceKind.RECORD, PROJECT_RESOURCE_ID),
        AdapterSlot(
            CHARACTER_STORE_ADAPTER_ID,
            ResourceKind.RECORD,
            CHARACTER_RESOURCE_ID,
        ),
    )
    records = tuple(_record_declaration(kind) for kind in _KIND_FACTS)
    legacy_bindings = tuple(_legacy_binding(kind) for kind in _KIND_FACTS)
    legacy_key_imports = tuple(_legacy_key_binding(kind) for kind in _KIND_FACTS)
    reference_migrations = tuple(
        _reference_migration(kind) for kind in _KIND_FACTS
    )
    return replace(
        base,
        resources=(*base.resources, *resources),
        server_adapters=(*base.server_adapters, *adapters),
        records=(*base.records, *records),
        legacy_bindings=(*base.legacy_bindings, *legacy_bindings),
        legacy_key_imports=(*base.legacy_key_imports, *legacy_key_imports),
        record_reference_migrations=(
            *base.record_reference_migrations,
            *reference_migrations,
        ),
    )


def _require_complete_d2_base(base: PrivacyProfile, expected: PrivacyProfile) -> None:
    if base.contract != expected.contract:
        raise ValueError("Director library records require the complete D2 contract.")
    attributes = (
        "resources",
        "server_adapters",
        "browser_adapters",
        "scopes",
        "protected_fields",
        "subject_mode_bindings",
        "execution_projections",
        "legacy_key_imports",
    )
    for attribute in attributes:
        actual_by_id = {item.id: item for item in getattr(base, attribute)}
        if any(actual_by_id.get(item.id) != item for item in getattr(expected, attribute)):
            raise ValueError("Director library records require the complete D2 contract.")
    actual_contracts = base.server_adapter_contracts
    for adapter_id, methods in expected.server_adapter_contracts.items():
        if not set(methods).issubset(actual_contracts.get(adapter_id, ())):
            raise ValueError("Director library records require the complete D2 contract.")


def build_director_library_server_adapters(
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    return {
        PROJECT_STORE_ADAPTER_ID: DirectorManagedLibraryStoreAdapter(
            PROJECT_RECORD_KIND,
            base_dir,
        ),
        CHARACTER_STORE_ADAPTER_ID: DirectorManagedLibraryStoreAdapter(
            CHARACTER_RECORD_KIND,
            base_dir,
        ),
    }


def director_library_legacy_reader_units() -> tuple[LegacyReaderUnit, ...]:
    """Return exact read-only units; activation remains the caller's decision."""

    return tuple(
        LegacyReaderUnit(
            str(_facts(kind)["reader"]),
            f"Director {str(_facts(kind)['legacy_kind'])} library v1",
            _DirectorLibraryV1Reader(kind),
            key_import_ids=(DIRECTOR_V1_JSON_KEY_IMPORT_ID,),
        )
        for kind in _KIND_FACTS
    )


class _DirectorLibraryV1Reader:
    """Exact single-entry reader used only by shared migration inventory."""

    def __init__(self, record_kind: str) -> None:
        # The shared package currently exposes Director key-import identity but
        # not a public current-schema reader constructor.  Keep that narrow
        # compatibility dependency out of module/profile import time.
        from helto_privacy.legacy_readers._state_envelope import (
            ExactStateEnvelopeReader,
        )

        self._record_kind = record_kind
        self._legacy_kind = str(_facts(record_kind)["legacy_kind"])
        self._envelope_reader = ExactStateEnvelopeReader(
            DIRECTOR_TIMELINE_SCHEMA,
            DIRECTOR_V1_JSON_KEY_IMPORT_ID,
        )

    def probe(self, source: object, context: object) -> bool:
        entry = _mapping_or_none(source)
        if entry is None or not _legacy_entry_matches(entry, self._legacy_kind):
            return False
        if _entry_is_private(entry):
            return self._envelope_reader.probe(entry.get("encrypted_payload"), context)
        return isinstance(entry.get("payload"), Mapping)

    def read(self, source: object, context: object) -> dict[str, object]:
        entry = _mapping_or_none(source)
        if entry is None or not self.probe(entry, context):
            raise DirectorManagedLibraryError("Legacy Director record is invalid.")
        state = (
            self._envelope_reader.read(entry["encrypted_payload"], context)
            if _entry_is_private(entry)
            else None
        )
        return _normalize_legacy_entry(self._record_kind, entry, state=state)


class DirectorManagedLibraryStoreAdapter:
    """Opaque v2 storage plus atomic CAS relocation from director_library.json."""

    def __init__(
        self,
        record_kind: str,
        base_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.record_kind = _record_kind(record_kind)
        self._facts = _facts(self.record_kind)
        self._base_dir = base_dir

    def list_ids(self) -> tuple[str, ...]:
        with self._managed_lock() as managed:
            return tuple(
                entry["id"]
                for entry in self._read_managed_unlocked(managed)["entries"]
                if entry["protected"] is not None
            )

    def read_record(self, record_id: str) -> RecordSnapshot:
        with self._managed_lock() as managed:
            entry = _find_optional(
                self._read_managed_unlocked(managed)["entries"],
                record_id,
            )
            if entry is None:
                return RecordSnapshot(0)
            return RecordSnapshot(entry["revision"], entry["protected"])

    def compare_and_swap_record(
        self,
        record_id: str,
        expected: RecordSnapshot,
        replacement: RecordSnapshot,
    ) -> bool:
        _require_snapshot(expected)
        _require_snapshot(replacement)
        if replacement.revision != expected.revision + 1:
            raise DirectorManagedLibraryError("Director record revision is invalid.")
        with self._managed_lock() as managed:
            document = self._read_managed_unlocked(managed)
            entry = _find_optional(document["entries"], record_id)
            current = (
                RecordSnapshot(0)
                if entry is None
                else RecordSnapshot(entry["revision"], entry["protected"])
            )
            if not _snapshot_equal(current, expected):
                return False
            stored = {
                "id": str(record_id),
                "revision": replacement.revision,
                "protected": copy.deepcopy(replacement.protected),
            }
            if entry is None:
                document["entries"].append(stored)
            else:
                document["entries"][document["entries"].index(entry)] = stored
            self._write_managed_unlocked(managed, document)
            return True

    def read_protected(self, record_id: str) -> object:
        snapshot = self.read_record(record_id)
        if snapshot.protected is None:
            raise DirectorManagedLibraryError("Managed Director entry was not found.")
        return copy.deepcopy(snapshot.protected)

    def write_protected(self, record_id: str, protected: object) -> None:
        _require_mapping(protected, "Protected Director record is invalid.")
        with self._managed_lock() as managed:
            document = self._read_managed_unlocked(managed)
            existing = _find_optional(document["entries"], record_id)
            replacement = {
                "id": str(record_id),
                "revision": 1 if existing is None else existing["revision"] + 1,
                "protected": copy.deepcopy(protected),
            }
            for index, entry in enumerate(document["entries"]):
                if entry["id"] == record_id:
                    document["entries"][index] = replacement
                    self._write_managed_unlocked(managed, document)
                    return
            document["entries"].append(replacement)
            self._write_managed_unlocked(managed, document)

    def delete(self, record_id: str) -> None:
        with self._managed_lock() as managed:
            document = self._read_managed_unlocked(managed)
            entry = _find_optional(document["entries"], record_id)
            if entry is None or entry["protected"] is None:
                raise DirectorManagedLibraryError("Director record was not found.")
            document["entries"][document["entries"].index(entry)] = {
                "id": str(record_id),
                "revision": entry["revision"] + 1,
                "protected": None,
            }
            self._write_managed_unlocked(managed, document)

    def mutate(self, current: object, operation: str, value: object) -> dict[str, object]:
        request = _require_mapping(value, "Director record mutation is invalid.")
        metadata = _metadata_request(request.get("metadata"))
        now = _utc_now()
        legacy_kind = str(self._facts["legacy_kind"])
        if operation == "create":
            if "payload" not in request:
                raise DirectorManagedLibraryError("Director record payload is required.")
            payload = _normalize_private_payload(legacy_kind, request["payload"])
            name = _requested_name(metadata, legacy_kind, payload)
            timeline_library._stamp_project_payload_name(legacy_kind, payload, name)
            return _managed_record(
                legacy_kind,
                payload,
                metadata,
                name=name,
                created_at=now,
                updated_at=now,
            )

        source = _normalize_managed_record(self.record_kind, current)
        if operation == "duplicate":
            payload = copy.deepcopy(source["payload"])
            name = str(metadata.get("name") or f"{source['metadata']['name']} Copy")
            timeline_library._fork_project_payload_identity(legacy_kind, payload, name)
            timeline_library._stamp_project_payload_name(legacy_kind, payload, name)
            duplicate_metadata = {
                "name": name,
                "description": metadata.get(
                    "description", source["metadata"]["description"]
                ),
                "tags": metadata.get("tags", source["metadata"]["tags"]),
            }
            return _managed_record(
                legacy_kind,
                payload,
                duplicate_metadata,
                name=name,
                created_at=now,
                updated_at=now,
            )
        if operation not in {"replace", "patch"}:
            raise DirectorManagedLibraryError("Director record mutation is invalid.")
        if operation == "replace" and "payload" not in request:
            raise DirectorManagedLibraryError("Director replacement payload is required.")

        payload = (
            _normalize_private_payload(legacy_kind, request["payload"])
            if "payload" in request
            else copy.deepcopy(source["payload"])
        )
        next_metadata = {
            "name": metadata.get("name", source["metadata"]["name"]),
            "description": metadata.get(
                "description", source["metadata"]["description"]
            ),
            "tags": metadata.get("tags", source["metadata"]["tags"]),
        }
        name = _requested_name(next_metadata, legacy_kind, payload)
        timeline_library._stamp_project_payload_name(legacy_kind, payload, name)
        result = _managed_record(
            legacy_kind,
            payload,
            next_metadata,
            name=name,
            created_at=str(source["metadata"]["created_at"]),
            updated_at=now,
        )
        result["metadata"]["last_used_at"] = source["metadata"]["last_used_at"]
        return result

    def project(self, value: object, operation: str) -> RecordProjectionResult:
        original = _require_mapping(value, "Director record is invalid.")
        record = _normalize_managed_record(self.record_kind, original)
        replacement: Mapping[str, object] | None = (
            record if _canonical_json(record) != _canonical_json(original) else None
        )
        if operation == "details":
            return RecordProjectionResult(
                {"metadata": copy.deepcopy(record["metadata"])},
                replacement,
            )
        if operation == "preview":
            return RecordProjectionResult(
                {"preview": _preview(self.record_kind, record)},
                replacement,
            )
        if operation != "use":
            raise DirectorManagedLibraryError("Director record projection is invalid.")
        used = copy.deepcopy(record)
        used["metadata"]["last_used_at"] = _utc_now()
        return RecordProjectionResult(
            {str(self._facts["use_field"]): copy.deepcopy(record["payload"])},
            used,
        )

    def read_legacy_record(
        self,
        migration_id: str,
        legacy_reference: str,
    ) -> LegacyRecordSource:
        self._require_migration(migration_id)
        with self._legacy_lock() as legacy:
            located = self._locate_legacy_unlocked(legacy, legacy_reference)
            if located is None:
                raise DirectorManagedLibraryError("Legacy Director record was not found.")
            _collection, _index, entry = located
            value = _normalize_legacy_entry(self.record_kind, entry)
            return LegacyRecordSource(_legacy_revision(_collection, entry), value)

    def commit_record_relocation(
        self,
        write: RecordRelocationWrite,
    ) -> RecordRelocationCommit:
        self._require_migration(write.migration_id)
        with self._relocation_locks() as (legacy, managed):
            located = self._locate_legacy_unlocked(legacy, write.legacy_reference)
            if (
                located is None
                or _legacy_revision(located[0], located[2]) != write.source_revision
            ):
                raise DirectorManagedLibraryError("Legacy Director record changed.")
            document = self._read_managed_unlocked(managed)
            record = _find_optional(document["entries"], write.record_id)
            mapping = _find_optional(document["reference_mappings"], write.mapping_id)
            expected_record = {
                "id": write.record_id,
                "revision": 1,
                "protected": copy.deepcopy(write.protected_record),
            }
            expected_mapping = {
                "id": write.mapping_id,
                "protected": copy.deepcopy(write.protected_mapping),
            }
            if record is None and mapping is None:
                document["entries"].append(expected_record)
                document["reference_mappings"].append(expected_mapping)
                self._write_managed_unlocked(managed, document)
            elif not (
                record is not None
                and mapping is not None
                and _canonical_json(record) == _canonical_json(expected_record)
                and _canonical_json(mapping) == _canonical_json(expected_mapping)
            ):
                raise DirectorManagedLibraryError("Director relocation target exists.")
            record_revision = _opaque_revision(expected_record)
            mapping_revision = _opaque_revision(expected_mapping)
            return RecordRelocationCommit(
                _relocation_commit_id(record_revision, mapping_revision),
                record_revision,
                mapping_revision,
            )

    def read_record_relocation(
        self,
        commit: RecordRelocationCommit,
    ) -> RecordRelocationReadback:
        if commit.commit_id != _relocation_commit_id(
            commit.record_revision,
            commit.mapping_revision,
        ):
            raise DirectorManagedLibraryError("Director relocation read-back failed.")
        with self._managed_lock() as managed:
            document = self._read_managed_unlocked(managed)
            records = [
                entry
                for entry in document["entries"]
                if _opaque_revision(entry) == commit.record_revision
            ]
            mappings = [
                entry
                for entry in document["reference_mappings"]
                if _opaque_revision(entry) == commit.mapping_revision
            ]
            if len(records) != 1 or len(mappings) != 1:
                raise DirectorManagedLibraryError("Director relocation read-back failed.")
            return RecordRelocationReadback(
                commit.record_revision,
                commit.mapping_revision,
                copy.deepcopy(records[0]["protected"]),
                copy.deepcopy(mappings[0]["protected"]),
            )

    def rollback_record_relocation(self, rollback: RecordRelocationRollback) -> str:
        with self._managed_lock() as managed:
            document = self._read_managed_unlocked(managed)
            record = _find_optional(document["entries"], rollback.record_id)
            mapping = _find_optional(
                document["reference_mappings"],
                rollback.mapping_id,
            )
            if record is None and mapping is None:
                return "already-original"
            if (
                record is None
                or mapping is None
                or _opaque_revision(record) != rollback.expected_record_revision
                or _opaque_revision(mapping) != rollback.expected_mapping_revision
            ):
                return "diverged"
            document["entries"].remove(record)
            document["reference_mappings"].remove(mapping)
            self._write_managed_unlocked(managed, document)
            return "rolled-back"

    def finalize_legacy_record(self, finalize: LegacyRecordFinalize) -> str:
        self._require_migration(finalize.migration_id)
        with self._relocation_locks() as (legacy, managed):
            document = self._read_managed_unlocked(managed)
            if _find_optional(document["entries"], finalize.committed_record_id) is None:
                return "diverged"
            if not self._has_finalize_mapping(document, finalize):
                return "diverged"
            located = self._locate_legacy_unlocked(legacy, finalize.legacy_reference)
            if located is None:
                return "already-finalized"
            collection, index, entry = located
            if _legacy_revision(collection, entry) != finalize.expected_source_revision:
                return "diverged"
            legacy_document = self._read_legacy_document_unlocked(legacy)
            values = legacy_document.get(collection)
            if not isinstance(values, list) or index >= len(values):
                return "diverged"
            candidate = values[index]
            if not isinstance(candidate, Mapping) or _canonical_json(candidate) != _canonical_json(entry):
                return "diverged"
            del values[index]
            self._write_private_json(legacy, legacy_document)
            return "finalized"

    def _has_finalize_mapping(
        self,
        document: Mapping[str, object],
        finalize: LegacyRecordFinalize,
    ) -> bool:
        mappings = document.get("reference_mappings")
        if not isinstance(mappings, list):
            return False
        matches = 0
        codec = PrivacyEnvelopeCodec(RECORD_REFERENCE_MAP_SCHEMA)
        for entry in mappings:
            if not isinstance(entry, Mapping):
                return False
            try:
                value = codec.decrypt_state(entry.get("protected"))
            except Exception:
                return False
            if (
                value.get("migration") == finalize.migration_id
                and value.get("reference") == finalize.legacy_reference
                and value.get("target") == finalize.committed_record_id
            ):
                matches += 1
        return matches == 1

    def list_record_reference_mapping_ids(
        self,
        migration_id: str,
    ) -> tuple[str, ...]:
        self._require_migration(migration_id)
        with self._managed_lock() as managed:
            return tuple(
                entry["id"]
                for entry in self._read_managed_unlocked(managed)["reference_mappings"]
            )

    def read_record_reference_mapping(self, mapping_id: str) -> object:
        with self._managed_lock() as managed:
            mapping = _find_opaque_entry(
                self._read_managed_unlocked(managed)["reference_mappings"],
                mapping_id,
            )
            return copy.deepcopy(mapping["protected"])

    def prepare_mode_transition(self, *_args: object) -> None:
        return None

    def commit_mode_transition(self, *_args: object) -> None:
        return None

    def rollback_mode_transition(self, *_args: object) -> None:
        return None

    def _read_managed_unlocked(self, managed: _BoundStorage) -> dict[str, Any]:
        if not _storage_file_exists(managed):
            return _empty_managed_document()
        document = _read_json(managed, "Managed Director library is unreadable.")
        if (
            document.get("schema_version") != MANAGED_LIBRARY_SCHEMA_VERSION
            or document.get("version") != MANAGED_LIBRARY_VERSION
            or set(document) != {
                "schema_version",
                "version",
                "entries",
                "reference_mappings",
            }
        ):
            raise DirectorManagedLibraryError("Managed Director library is invalid.")
        entries = _opaque_record_entries(document.get("entries"))
        mappings = _opaque_entries(document.get("reference_mappings"), "mapping")
        return {
            **_empty_managed_document(),
            "entries": entries,
            "reference_mappings": mappings,
        }

    def _write_managed_unlocked(
        self,
        managed: _BoundStorage,
        document: Mapping[str, object],
    ) -> None:
        self._write_private_json(managed, document)

    def _read_legacy_document_unlocked(
        self,
        legacy: _BoundStorage,
    ) -> dict[str, Any]:
        if not _storage_file_exists(legacy):
            raise DirectorManagedLibraryError("Legacy Director library was not found.")
        document = _read_json(legacy, "Legacy Director library is unreadable.")
        if (
            document.get("schema_version") != timeline_library.LIBRARY_SCHEMA_VERSION
            or document.get("version") != timeline_library.LIBRARY_VERSION
        ):
            raise DirectorManagedLibraryError("Legacy Director library is invalid.")
        return document

    def _locate_legacy_unlocked(
        self,
        legacy: _BoundStorage,
        legacy_reference: str,
    ) -> tuple[str, int, dict[str, object]] | None:
        document = self._read_legacy_document_unlocked(legacy)
        collections = (
            ("projects", "timelines")
            if self._facts["legacy_kind"] == timeline_library.PROJECT_KIND
            else ("characters",)
        )
        matches: list[tuple[str, int, dict[str, object]]] = []
        for collection in collections:
            values = document.get(collection, [])
            if not isinstance(values, list):
                raise DirectorManagedLibraryError("Legacy Director library is invalid.")
            for index, candidate in enumerate(values):
                if not isinstance(candidate, Mapping):
                    raise DirectorManagedLibraryError("Legacy Director record is invalid.")
                if candidate.get("id") == legacy_reference:
                    entry = dict(candidate)
                    if not _legacy_entry_matches(
                        entry,
                        str(self._facts["legacy_kind"]),
                    ):
                        raise DirectorManagedLibraryError("Legacy Director record is invalid.")
                    matches.append((collection, index, entry))
        if len(matches) > 1:
            raise DirectorManagedLibraryError("Legacy Director record is ambiguous.")
        return matches[0] if matches else None

    def _require_migration(self, migration_id: str) -> None:
        if migration_id != self._facts["migration"]:
            raise DirectorManagedLibraryError("Director relocation is invalid.")

    @contextmanager
    def _managed_lock(self):
        path = managed_library_path(self.record_kind, self._base_dir)
        with _file_locks((path,)) as bound:
            yield bound[path]

    @contextmanager
    def _legacy_lock(self):
        path = legacy_library_path(self._base_dir)
        with _file_locks((path,)) as bound:
            yield bound[path]

    @contextmanager
    def _relocation_locks(self):
        legacy_path = legacy_library_path(self._base_dir)
        managed_path = managed_library_path(self.record_kind, self._base_dir)
        with _file_locks((legacy_path, managed_path)) as bound:
            yield bound[legacy_path], bound[managed_path]

    @staticmethod
    def _write_private_json(
        storage: _BoundStorage,
        value: Mapping[str, object],
    ) -> None:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        parent_fd = storage.parent_fd
        filename = storage.filename
        temporary = f".{filename}.{secrets.token_hex(8)}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
            handle = os.fdopen(descriptor, "w", encoding="utf-8")
            descriptor = None
            with handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                temporary,
                filename,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.chmod(filename, 0o600, dir_fd=parent_fd, follow_symlinks=False)
            os.fsync(parent_fd)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


def _record_declaration(record_kind: str) -> RecordDeclaration:
    facts = _facts(record_kind)
    return RecordDeclaration(
        record_kind,
        str(facts["resource"]),
        GLOBAL_SCOPE_ID,
        DIRECTOR_TIMELINE_SCHEMA,
        str(facts["adapter"]),
        projections=(
            RecordRevealProjection("details", ("metadata",)),
            RecordRevealProjection("preview", ("preview",)),
            RecordRevealProjection("use", (str(facts["use_field"]),)),
        ),
        mutation_operations=("create", "replace", "patch", "duplicate"),
        safe_projection=(),
        fixed_private_label=PRIVATE_RECORD_LABEL,
    )


def _legacy_binding(record_kind: str) -> LegacyReaderBinding:
    facts = _facts(record_kind)
    return LegacyReaderBinding(
        str(facts["binding"]),
        str(facts["reader"]),
        str(facts["resource"]),
        LegacyLocationKind.RECORD,
        record_kind,
    )


def _legacy_key_binding(record_kind: str) -> LegacyKeyImportBinding:
    facts = _facts(record_kind)
    return LegacyKeyImportBinding(
        str(facts["key_binding"]),
        DIRECTOR_V1_JSON_KEY_IMPORT_ID,
        str(facts["resource"]),
        LegacyLocationKind.RECORD,
        record_kind,
        LegacyKeyFormat.JSON,
    )


def _reference_migration(record_kind: str) -> RecordReferenceMigration:
    facts = _facts(record_kind)
    return RecordReferenceMigration(
        str(facts["migration"]),
        str(facts["resource"]),
        record_kind,
        str(facts["binding"]),
    )


def _normalize_private_payload(
    legacy_kind: str,
    value: object,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise DirectorManagedLibraryError("Director record payload is invalid.")
    stripped = _strip_opaque_embedded_media(value)
    if stripped is _HARDENED_REMOVED or not isinstance(stripped, Mapping):
        raise DirectorManagedLibraryError("Director record payload is invalid.")
    normalized = timeline_library._normalize_payload(legacy_kind, stripped)
    hardened = _strip_opaque_embedded_media(normalized)
    if hardened is _HARDENED_REMOVED or not isinstance(hardened, Mapping):
        raise DirectorManagedLibraryError("Director record payload is invalid.")
    # Re-enter the product normalizer after hardening so validation, referenced
    # assets, and character semantics reflect the exact value that is stored.
    return timeline_library._normalize_payload(legacy_kind, hardened)


def _strip_opaque_embedded_media(value: object, *, key: str = "") -> object:
    normalized_key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _HARDENED_REMOVED
    if isinstance(value, Mapping):
        cleaned: dict[object, object] = {}
        for child_key, child in value.items():
            child_name = str(child_key)
            child_normalized = re.sub(
                r"[^a-z0-9]+",
                "_",
                child_name.lower(),
            ).strip("_")
            child_path = "_".join(part for part in (normalized_key, child_normalized) if part)
            stripped = _strip_opaque_embedded_media(child, key=child_path)
            if stripped is not _HARDENED_REMOVED:
                cleaned[child_key] = stripped
        return cleaned
    if isinstance(value, list):
        cleaned_items = []
        for item in value:
            stripped = _strip_opaque_embedded_media(item, key=normalized_key)
            if stripped is not _HARDENED_REMOVED:
                cleaned_items.append(stripped)
        return cleaned_items
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith(("data:", "blob:")):
            return _HARDENED_REMOVED
        if _looks_opaque_media_text(text, normalized_key):
            return _HARDENED_REMOVED
    return value


def _looks_opaque_media_text(value: str, key: str) -> bool:
    if not any(token in key for token in _MEDIA_KEY_TOKENS):
        return False
    if key.endswith(("_path", "_file_path", "_asset_id", "_library_item_id")):
        return False
    if len(value) < 128 or _OPAQUE_TEXT.fullmatch(value) is None:
        return False
    try:
        decoded = base64.urlsafe_b64decode(
            (value + "=" * (-len(value) % 4)).encode("ascii")
        )
    except Exception:
        return False
    return len(decoded) >= 64


def _normalize_legacy_entry(
    record_kind: str,
    entry: Mapping[str, object],
    *,
    state: Mapping[str, object] | None = None,
) -> dict[str, object]:
    facts = _facts(record_kind)
    legacy_kind = str(facts["legacy_kind"])
    if not _legacy_entry_matches(entry, legacy_kind):
        raise DirectorManagedLibraryError("Legacy Director record is invalid.")
    if _entry_is_private(entry):
        if state is None:
            protected = entry.get("encrypted_payload")
            if isinstance(protected, str):
                try:
                    protected = json.loads(protected)
                except json.JSONDecodeError:
                    raise DirectorManagedLibraryError(
                        "Legacy Director record is invalid."
                    ) from None
            state = PrivacyEnvelopeCodec(DIRECTOR_TIMELINE_SCHEMA).decrypt_state(protected)
        payload_source = state.get("payload")
        name_source = state.get("name") if legacy_kind == timeline_library.PROJECT_KIND else entry.get("name")
        description_source = state.get("description")
    else:
        payload_source = entry.get("payload")
        name_source = entry.get("name")
        description_source = entry.get("description")
    if not isinstance(payload_source, Mapping):
        raise DirectorManagedLibraryError("Legacy Director record is invalid.")
    payload = _normalize_private_payload(legacy_kind, payload_source)
    metadata = {
        "name": _text(name_source),
        "description": _text(description_source),
        "tags": _tags(entry.get("tags")),
    }
    name = _requested_name(metadata, legacy_kind, payload)
    timeline_library._stamp_project_payload_name(legacy_kind, payload, name)
    record = _managed_record(
        legacy_kind,
        payload,
        metadata,
        name=name,
        created_at=_text(entry.get("created_at")) or _utc_now(),
        updated_at=_text(entry.get("updated_at")) or _utc_now(),
    )
    record["metadata"]["last_used_at"] = _text(entry.get("last_used_at")) or None
    return record


def _normalize_managed_record(record_kind: str, value: object) -> dict[str, object]:
    record = _require_mapping(value, "Director record is invalid.")
    metadata = _require_mapping(record.get("metadata"), "Director metadata is invalid.")
    payload_source = record.get("payload")
    if not isinstance(payload_source, Mapping):
        raise DirectorManagedLibraryError("Director record payload is invalid.")
    legacy_kind = str(_facts(record_kind)["legacy_kind"])
    payload = _normalize_private_payload(legacy_kind, payload_source)
    normalized_metadata = {
        "name": _text(metadata.get("name")),
        "description": _text(metadata.get("description")),
        "tags": _tags(metadata.get("tags")),
        "summary": {},
        "created_at": _text(metadata.get("created_at")) or _utc_now(),
        "updated_at": _text(metadata.get("updated_at")) or _utc_now(),
        "last_used_at": _text(metadata.get("last_used_at")) or None,
    }
    name = _requested_name(normalized_metadata, legacy_kind, payload)
    normalized_metadata["name"] = name
    normalized_metadata["summary"] = timeline_library._summary_for(legacy_kind, payload)
    timeline_library._stamp_project_payload_name(legacy_kind, payload, name)
    return {"metadata": normalized_metadata, "payload": payload}


def _managed_record(
    legacy_kind: str,
    payload: Mapping[str, object],
    metadata: Mapping[str, object],
    *,
    name: str,
    created_at: str,
    updated_at: str,
) -> dict[str, object]:
    return {
        "metadata": {
            "name": name,
            "description": _text(metadata.get("description")),
            "tags": _tags(metadata.get("tags")),
            "summary": timeline_library._summary_for(legacy_kind, payload),
            "created_at": created_at,
            "updated_at": updated_at,
            "last_used_at": None,
        },
        "payload": copy.deepcopy(dict(payload)),
    }


def _preview(record_kind: str, record: Mapping[str, object]) -> dict[str, object]:
    metadata = copy.deepcopy(record["metadata"])
    payload = record["payload"]
    if record_kind == PROJECT_RECORD_KIND:
        return {
            "metadata": metadata,
            "preview_assets": timeline_library.preview_assets_for_timeline(payload),
        }
    return {
        "metadata": metadata,
        "character": timeline_library.preview_character_shell(payload),
    }


def _requested_name(
    metadata: Mapping[str, object],
    legacy_kind: str,
    payload: Mapping[str, object],
) -> str:
    return _text(metadata.get("name")) or timeline_library._default_name(
        legacy_kind,
        payload,
    )


def _metadata_request(value: object) -> dict[str, object]:
    if value is None:
        return {}
    metadata = _require_mapping(value, "Director metadata is invalid.")
    allowed = {"name", "description", "tags"}
    if any(key not in allowed for key in metadata):
        raise DirectorManagedLibraryError("Director metadata is invalid.")
    result: dict[str, object] = {}
    if "name" in metadata:
        result["name"] = _text(metadata.get("name"))
    if "description" in metadata:
        result["description"] = _text(metadata.get("description"))
    if "tags" in metadata:
        result["tags"] = _tags(metadata.get("tags"))
    return result


def _legacy_entry_matches(entry: Mapping[str, object], legacy_kind: str) -> bool:
    if not isinstance(entry.get("id"), str) or not str(entry.get("id")):
        return False
    try:
        return timeline_library._normalize_kind(entry.get("kind")) == legacy_kind
    except timeline_library.TimelineLibraryError:
        return False


def _entry_is_private(entry: Mapping[str, object]) -> bool:
    return bool(entry.get("private") or entry.get("is_private"))


def _empty_managed_document() -> dict[str, object]:
    return {
        "schema_version": MANAGED_LIBRARY_SCHEMA_VERSION,
        "version": MANAGED_LIBRARY_VERSION,
        "entries": [],
        "reference_mappings": [],
    }


def _opaque_entries(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise DirectorManagedLibraryError(f"Managed Director {label}s are invalid.")
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"id", "protected"}
            or not isinstance(item.get("id"), str)
            or not item.get("id")
            or item["id"] in seen
            or not isinstance(item.get("protected"), Mapping)
        ):
            raise DirectorManagedLibraryError(f"Managed Director {label} is invalid.")
        seen.add(str(item["id"]))
        entries.append(copy.deepcopy(dict(item)))
    return entries


def _opaque_record_entries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise DirectorManagedLibraryError("Managed Director records are invalid.")
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"id", "revision", "protected"}
            or not isinstance(item.get("id"), str)
            or not item.get("id")
            or item["id"] in seen
            or not isinstance(item.get("revision"), int)
            or isinstance(item.get("revision"), bool)
            or item["revision"] < 1
            or (
                item.get("protected") is not None
                and not isinstance(item.get("protected"), Mapping)
            )
        ):
            raise DirectorManagedLibraryError("Managed Director record is invalid.")
        seen.add(str(item["id"]))
        entries.append(copy.deepcopy(dict(item)))
    return entries


def _require_snapshot(value: object) -> RecordSnapshot:
    if not isinstance(value, RecordSnapshot) or (
        value.protected is not None and not isinstance(value.protected, Mapping)
    ):
        raise DirectorManagedLibraryError("Director record snapshot is invalid.")
    return value


def _snapshot_equal(left: RecordSnapshot, right: RecordSnapshot) -> bool:
    if left.revision != right.revision:
        return False
    if left.protected is None or right.protected is None:
        return left.protected is right.protected
    return _canonical_json(left.protected) == _canonical_json(right.protected)


def _find_opaque_entry(
    entries: list[dict[str, object]],
    identifier: str,
) -> dict[str, object]:
    found = _find_optional(entries, identifier)
    if found is None:
        raise DirectorManagedLibraryError("Managed Director entry was not found.")
    return found


def _find_optional(
    entries: list[dict[str, object]],
    identifier: str,
) -> dict[str, object] | None:
    matches = [entry for entry in entries if entry.get("id") == identifier]
    if len(matches) > 1:
        raise DirectorManagedLibraryError("Managed Director entry is ambiguous.")
    return matches[0] if matches else None


@dataclass(frozen=True, slots=True)
class _BoundStorage:
    path: Path
    parent_fd: int
    filename: str


def _read_json(storage: _BoundStorage, message: str) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        before = os.stat(
            storage.filename,
            dir_fd=storage.parent_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(before.st_mode):
            raise DirectorManagedLibraryError(message)
        descriptor = os.open(
            storage.filename,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=storage.parent_fd,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise DirectorManagedLibraryError(message)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = None
            value = json.load(handle)
    except DirectorManagedLibraryError:
        raise
    except (OSError, json.JSONDecodeError):
        raise DirectorManagedLibraryError(message) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise DirectorManagedLibraryError(message)
    return value


@contextmanager
def _file_locks(paths: tuple[Path, ...]):
    targets = tuple(sorted({_trusted_storage_path(path) for path in paths}))
    parents: dict[Path, tuple[int, tuple[int, int]]] = {}
    bound: dict[Path, _BoundStorage] = {}
    handles: list[object] = []
    try:
        for path in targets:
            parent = path.parent
            if parent not in parents:
                parent_fd = _open_absolute_directory(parent, create=True)
                opened = os.fstat(parent_fd)
                parents[parent] = (parent_fd, (opened.st_dev, opened.st_ino))
            parent_fd = parents[parent][0]
            bound[path] = _BoundStorage(path, parent_fd, path.name)

        for path in targets:
            storage = bound[path]
            lock_name = f".{storage.filename}.lock"
            descriptor: int | None = None
            handle = None
            try:
                descriptor = os.open(
                    lock_name,
                    os.O_RDWR
                    | os.O_CREAT
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=storage.parent_fd,
                )
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    os.close(descriptor)
                    descriptor = None
                    raise DirectorManagedLibraryError(
                        "Director storage path is invalid."
                    )
                handle = os.fdopen(descriptor, "a+b")
                descriptor = None
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except BaseException:
                if handle is not None:
                    handle.close()
                elif descriptor is not None:
                    os.close(descriptor)
                raise
            handles.append(handle)
        yield bound
    finally:
        identity_error: Exception | None = None
        for parent, (_parent_fd, expected) in parents.items():
            current_fd: int | None = None
            try:
                current_fd = _open_absolute_directory(parent, create=False)
                current = os.fstat(current_fd)
                if (current.st_dev, current.st_ino) != expected:
                    raise DirectorManagedLibraryError(
                        "Director storage binding changed during operation."
                    )
            except Exception:
                identity_error = DirectorManagedLibraryError(
                    "Director storage binding changed during operation."
                )
            finally:
                if current_fd is not None:
                    os.close(current_fd)
        cleanup_error: DirectorManagedLibraryError | None = None
        for handle in reversed(handles):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                cleanup_error = cleanup_error or DirectorManagedLibraryError(
                    "Director storage lock cleanup failed."
                )
            try:
                handle.close()
            except Exception:
                cleanup_error = cleanup_error or DirectorManagedLibraryError(
                    "Director storage lock cleanup failed."
                )
        for parent_fd, _expected in parents.values():
            try:
                os.close(parent_fd)
            except Exception:
                cleanup_error = cleanup_error or DirectorManagedLibraryError(
                    "Director storage lock cleanup failed."
                )
        if identity_error is not None:
            raise identity_error
        if cleanup_error is not None:
            raise cleanup_error


def _storage_file_exists(storage: _BoundStorage) -> bool:
    try:
        current = os.stat(
            storage.filename,
            dir_fd=storage.parent_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(current.st_mode):
            raise DirectorManagedLibraryError("Director storage path is invalid.")
        return True
    except FileNotFoundError:
        return False


def _open_absolute_directory(path: Path, *, create: bool) -> int:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if not parts or parts[0] != os.path.sep:
        raise DirectorManagedLibraryError("Director storage path is invalid.")
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    current_fd = os.open(os.path.sep, flags)
    try:
        for part in parts[1:]:
            try:
                before = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=current_fd)
                before = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise DirectorManagedLibraryError("Director storage path is invalid.")
            next_fd = os.open(part, flags, dir_fd=current_fd)
            opened = os.fstat(next_fd)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                os.close(next_fd)
                raise DirectorManagedLibraryError("Director storage path is invalid.")
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _trusted_storage_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    try:
        if absolute.resolve(strict=False) != absolute:
            raise DirectorManagedLibraryError("Director storage path is invalid.")
    except OSError:
        raise DirectorManagedLibraryError("Director storage path is invalid.") from None
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        except OSError:
            raise DirectorManagedLibraryError("Director storage path is invalid.") from None
        if stat.S_ISLNK(mode):
            raise DirectorManagedLibraryError("Director storage path is invalid.")
    return absolute


def _legacy_revision(collection: str, entry: Mapping[str, object]) -> str:
    return hashlib.sha256(
        _canonical_json({"collection": collection, "entry": entry})
    ).hexdigest()


def _opaque_revision(entry: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(entry)).hexdigest()


def _relocation_commit_id(record_revision: object, mapping_revision: object) -> str:
    digest = hashlib.sha256(
        f"{record_revision}\0{mapping_revision}".encode("ascii")
    ).hexdigest()
    return f"director-record-commit-{digest}"


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise DirectorManagedLibraryError("Director record is invalid.") from None


def _record_kind(value: object) -> str:
    if value not in _KIND_FACTS:
        raise DirectorManagedLibraryError("Director record kind is invalid.")
    return str(value)


def _facts(record_kind: object) -> Mapping[str, object]:
    return _KIND_FACTS[_record_kind(record_kind)]


def _require_mapping(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise DirectorManagedLibraryError(message)
    return copy.deepcopy(dict(value))


def _mapping_or_none(value: object) -> dict[str, object] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _text(value: object) -> str:
    return str(value or "").strip()


def _tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        tag = _text(item)
        if tag and tag not in result:
            result.append(tag)
    return result


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


__all__ = [
    "CHARACTER_MANAGED_FILE_NAME",
    "CHARACTER_RECORD_KIND",
    "CHARACTER_REFERENCE_MIGRATION_ID",
    "CHARACTER_RESOURCE_ID",
    "CHARACTER_STORE_ADAPTER_ID",
    "DirectorManagedLibraryError",
    "DirectorManagedLibraryStoreAdapter",
    "MANAGED_LIBRARY_SCHEMA_VERSION",
    "MANAGED_LIBRARY_VERSION",
    "PRIVATE_RECORD_LABEL",
    "PROJECT_MANAGED_FILE_NAME",
    "PROJECT_RECORD_KIND",
    "PROJECT_REFERENCE_MIGRATION_ID",
    "PROJECT_RESOURCE_ID",
    "PROJECT_STORE_ADAPTER_ID",
    "build_director_library_privacy_profile",
    "build_director_library_server_adapters",
    "director_library_legacy_reader_units",
    "legacy_library_path",
    "managed_library_path",
]
