"""Managed durable singleton declarations and strict Director product codecs.

The shared singleton layer owns protection, revision CAS, and authorization.
This module owns only Director's plaintext domain schemas and binds their
opaque protected representations to one closed SQLite store.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from typing import Final

from helto_privacy import (
    AdapterSlot,
    PrivacyProfile,
    ProfileResource,
    ResourceKind,
    SingletonDeclaration,
    SingletonPayloadKind,
)

from .managed_privacy import (
    DIRECTOR_DISTRIBUTION,
    DIRECTOR_PROFILE_ID,
    GLOBAL_SCOPE_ID,
    build_director_timeline_privacy_profile,
)
from .managed_singleton_store import DirectorManagedSingletonStore


DURABLE_STATE_RESOURCE_ID = "director-durable-state"
DURABLE_STATE_STORE_ADAPTER_ID = "director-durable-state-store"

MEDIA_FOLDER_SETTINGS_ID = "media-folder-settings"
CAPTURE_INDEX_ID = "capture-index"
TAKE_DELETION_JOURNAL_ID = "take-deletion-journal"
DURABLE_SINGLETON_IDS = (
    MEDIA_FOLDER_SETTINGS_ID,
    CAPTURE_INDEX_ID,
    TAKE_DELETION_JOURNAL_ID,
)

MEDIA_FOLDER_SETTINGS_SCHEMA = "helto.director.media-folder-settings"
CAPTURE_INDEX_SCHEMA = "helto.director.capture-index"
TAKE_DELETION_JOURNAL_SCHEMA = "helto.director.take-deletion-journal"
SCHEMA_VERSION = 1

MAX_MEDIA_FOLDERS = 256
MAX_CAPTURE_INDEX_ENTRIES = 4096
MAX_DELETION_INTENTS = 4096
MAX_DELETION_TARGETS = 16
MAX_RELATIVE_PARTS = 32
MAX_PLAINTEXT_BYTES = 16 * 1024 * 1024

_MEDIA_TYPES: Final = ("image", "video", "audio")
_ALIAS = re.compile(r"^[A-Za-z0-9_. -]{1,80}$")
_OWNER_ID = re.compile(r"^hp-owner-[A-Za-z0-9_-]{32}$")
_ARTIFACT_ID = re.compile(r"^hp-art-[A-Za-z0-9_-]{32}$")
_TRANSACTION_ID = re.compile(r"^hp-operation-[A-Za-z0-9_-]{32}$")
_INTENT_ID = re.compile(r"^[a-f0-9]{32}$")

DURABLE_STATE_RESOURCE = ProfileResource(
    DURABLE_STATE_RESOURCE_ID,
    ResourceKind.SINGLETON,
    (DURABLE_STATE_STORE_ADAPTER_ID,),
)
DURABLE_STATE_STORE_ADAPTER_SLOT = AdapterSlot(
    DURABLE_STATE_STORE_ADAPTER_ID,
    ResourceKind.SINGLETON,
    DURABLE_STATE_RESOURCE_ID,
)
DURABLE_SINGLETON_DECLARATIONS = (
    SingletonDeclaration(
        MEDIA_FOLDER_SETTINGS_ID,
        DURABLE_STATE_RESOURCE_ID,
        GLOBAL_SCOPE_ID,
        f"{MEDIA_FOLDER_SETTINGS_SCHEMA}.v1",
        MEDIA_FOLDER_SETTINGS_ID,
        DURABLE_STATE_STORE_ADAPTER_ID,
        SingletonPayloadKind.FIELD,
    ),
    SingletonDeclaration(
        CAPTURE_INDEX_ID,
        DURABLE_STATE_RESOURCE_ID,
        GLOBAL_SCOPE_ID,
        f"{CAPTURE_INDEX_SCHEMA}.v1",
        CAPTURE_INDEX_ID,
        DURABLE_STATE_STORE_ADAPTER_ID,
        SingletonPayloadKind.FIELD,
    ),
    SingletonDeclaration(
        TAKE_DELETION_JOURNAL_ID,
        DURABLE_STATE_RESOURCE_ID,
        GLOBAL_SCOPE_ID,
        f"{TAKE_DELETION_JOURNAL_SCHEMA}.v1",
        TAKE_DELETION_JOURNAL_ID,
        DURABLE_STATE_STORE_ADAPTER_ID,
        SingletonPayloadKind.FIELD,
    ),
)


class DirectorDurableStateError(ValueError):
    """Product-data-free rejection for a malformed durable plaintext value."""

    def __init__(self) -> None:
        super().__init__("Director durable state is invalid.")

    def __repr__(self) -> str:
        return "DirectorDurableStateError()"


def build_director_durable_state_privacy_profile(
    base_profile: PrivacyProfile | None = None,
) -> PrivacyProfile:
    """Compose the durable state declarations into Director's profile."""

    base = base_profile or build_director_timeline_privacy_profile()
    if base.id != DIRECTOR_PROFILE_ID or base.distribution != DIRECTOR_DISTRIBUTION:
        raise ValueError("Director durable state requires the Director profile.")
    return replace(
        base,
        resources=(*base.resources, DURABLE_STATE_RESOURCE),
        server_adapters=(*base.server_adapters, DURABLE_STATE_STORE_ADAPTER_SLOT),
        singletons=(*base.singletons, *DURABLE_SINGLETON_DECLARATIONS),
    )


def build_director_durable_state_server_adapters(
    database_path: str | os.PathLike[str],
) -> dict[str, object]:
    """Build the sole protected-representation store for all three values."""

    return {
        DURABLE_STATE_STORE_ADAPTER_ID: DirectorManagedSingletonStore(
            database_path,
            DURABLE_SINGLETON_IDS,
        )
    }


def normalize_media_folder_settings(value: object) -> dict[str, object]:
    source = _exact_dict(value, {"schema", "version", "folders"})
    _schema(source, MEDIA_FOLDER_SETTINGS_SCHEMA)
    folders = _exact_dict(source["folders"], set(_MEDIA_TYPES))
    total = 0
    normalized: dict[str, list[dict[str, object]]] = {}
    for media_type in _MEDIA_TYPES:
        entries = _exact_list(folders[media_type])
        total += len(entries)
        if total > MAX_MEDIA_FOLDERS:
            raise DirectorDurableStateError()
        seen_aliases: set[str] = set()
        seen_paths: set[str] = set()
        result = []
        for item in entries:
            folder = _normalize_folder(item)
            path_key = os.path.normcase(folder["path"])
            if folder["alias"] in seen_aliases or path_key in seen_paths:
                raise DirectorDurableStateError()
            seen_aliases.add(folder["alias"])
            seen_paths.add(path_key)
            result.append(folder)
        normalized[media_type] = result
    result = {
        "schema": MEDIA_FOLDER_SETTINGS_SCHEMA,
        "version": SCHEMA_VERSION,
        "folders": normalized,
    }
    _require_bounded(result)
    return result


def media_folder_settings_view(
    value: object,
    defaults: object,
) -> dict[str, object]:
    """Inject runtime defaults without changing the persistable normalized value."""

    normalized = normalize_media_folder_settings(value)
    default_source = _exact_dict(defaults, set(_MEDIA_TYPES))
    view: dict[str, list[dict[str, object]]] = {}
    total = 0
    for media_type in _MEDIA_TYPES:
        configured = normalized["folders"][media_type]
        assert isinstance(configured, list)
        default_values = _exact_list(default_source[media_type])
        total += len(default_values)
        if total > MAX_MEDIA_FOLDERS:
            raise DirectorDurableStateError()
        default_entries = [_normalize_folder(item) for item in default_values]
        default_aliases: set[str] = set()
        default_paths: set[str] = set()
        for folder in default_entries:
            path_key = os.path.normcase(folder["path"])
            if folder["alias"] in default_aliases or path_key in default_paths:
                raise DirectorDurableStateError()
            default_aliases.add(folder["alias"])
            default_paths.add(path_key)
        configured_aliases = {str(item["alias"]) for item in configured}
        combined = [
            item for item in default_entries if item["alias"] not in configured_aliases
        ] + [dict(item) for item in configured]
        if sum(len(items) for items in view.values()) + len(combined) > MAX_MEDIA_FOLDERS:
            raise DirectorDurableStateError()
        view[media_type] = combined
    result = {
        "schema": MEDIA_FOLDER_SETTINGS_SCHEMA,
        "version": SCHEMA_VERSION,
        "folders": view,
    }
    _require_bounded(result)
    return result


def normalize_capture_index(value: object) -> dict[str, object]:
    source = _exact_dict(value, {"schema", "version", "captures"})
    _schema(source, CAPTURE_INDEX_SCHEMA)
    captures = _exact_dict(source["captures"])
    if len(captures) > MAX_CAPTURE_INDEX_ENTRIES:
        raise DirectorDurableStateError()
    if any(
        not isinstance(capture_id, str)
        or _OWNER_ID.fullmatch(capture_id) is None
        for capture_id in captures
    ):
        raise DirectorDurableStateError()
    normalized = {}
    for capture_id in sorted(captures):
        item = _exact_dict(captures[capture_id], {
            "phase",
            "externalTransactionId",
            "artifactOwnerId",
            "sidecarReference",
        })
        phase = item["phase"]
        transaction_id = item["externalTransactionId"]
        owner_id = item["artifactOwnerId"]
        if (
            phase not in {"sidecar-pending", "committed"}
            or not isinstance(transaction_id, str)
            or _TRANSACTION_ID.fullmatch(transaction_id) is None
            or owner_id != capture_id
        ):
            raise DirectorDurableStateError()
        reference = _artifact_reference(item["sidecarReference"])
        if reference is None:
            raise DirectorDurableStateError()
        normalized[capture_id] = {
            "phase": phase,
            "externalTransactionId": transaction_id,
            "artifactOwnerId": owner_id,
            "sidecarReference": reference,
        }
    result = {
        "schema": CAPTURE_INDEX_SCHEMA,
        "version": SCHEMA_VERSION,
        "captures": normalized,
    }
    _require_bounded(result)
    return result


def normalize_take_deletion_journal(value: object) -> dict[str, object]:
    source = _exact_dict(value, {"schema", "version", "intents"})
    _schema(source, TAKE_DELETION_JOURNAL_SCHEMA)
    intents = _exact_dict(source["intents"])
    if len(intents) > MAX_DELETION_INTENTS:
        raise DirectorDurableStateError()
    if any(
        not isinstance(intent_id, str)
        or _INTENT_ID.fullmatch(intent_id) is None
        for intent_id in intents
    ):
        raise DirectorDurableStateError()
    normalized = {}
    for intent_id in sorted(intents):
        item = _exact_dict(intents[intent_id], {
            "phase",
            "externalTransactionId",
            "captureId",
            "targets",
        })
        if item["phase"] != "delete-intent":
            raise DirectorDurableStateError()
        transaction_id = item["externalTransactionId"]
        capture_id = item["captureId"]
        if (
            not isinstance(transaction_id, str)
            or _TRANSACTION_ID.fullmatch(transaction_id) is None
            or not isinstance(capture_id, str)
            or _OWNER_ID.fullmatch(capture_id) is None
        ):
            raise DirectorDurableStateError()
        targets = _exact_list(item["targets"])
        if not targets or len(targets) > MAX_DELETION_TARGETS:
            raise DirectorDurableStateError()
        normalized_targets = [_deletion_target(target) for target in targets]
        identities = {
            (
                target["rootDevice"],
                target["rootInode"],
                target["targetDevice"],
                target["targetInode"],
                tuple(target["relativePath"]),
            )
            for target in normalized_targets
        }
        if len(identities) != len(normalized_targets):
            raise DirectorDurableStateError()
        normalized[intent_id] = {
            "phase": "delete-intent",
            "externalTransactionId": transaction_id,
            "captureId": capture_id,
            "targets": sorted(
                normalized_targets,
                key=lambda target: (
                    tuple(target["relativePath"]),
                    target["targetDevice"],
                    target["targetInode"],
                ),
            ),
        }
    result = {
        "schema": TAKE_DELETION_JOURNAL_SCHEMA,
        "version": SCHEMA_VERSION,
        "intents": normalized,
    }
    _require_bounded(result)
    return result


def _normalize_folder(value: object) -> dict[str, object]:
    item = _exact_dict(value, {"alias", "path", "enabled"})
    alias = item["alias"]
    path = item["path"]
    enabled = item["enabled"]
    if (
        not isinstance(alias, str)
        or alias.strip() != alias
        or _ALIAS.fullmatch(alias) is None
        or not isinstance(path, str)
        or not path
        or _utf8_size(path) > 4096
        or not os.path.isabs(path)
        or os.path.normpath(path) != path
        or any(ord(character) < 32 for character in path)
        or not isinstance(enabled, bool)
    ):
        raise DirectorDurableStateError()
    return {"alias": alias, "path": path, "enabled": enabled}


def _artifact_reference(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    item = _exact_dict(value, {"schema", "version", "id"})
    if (
        item["schema"] != "helto.private-artifact-reference"
        or item["version"] != 1
        or isinstance(item["version"], bool)
        or not isinstance(item["id"], str)
        or _ARTIFACT_ID.fullmatch(item["id"]) is None
    ):
        raise DirectorDurableStateError()
    return {"schema": item["schema"], "version": 1, "id": item["id"]}


def _deletion_target(value: object) -> dict[str, object]:
    item = _exact_dict(value, {
        "rootDevice",
        "rootInode",
        "targetDevice",
        "targetInode",
        "relativePath",
    })
    integers = (
        item["rootDevice"],
        item["rootInode"],
        item["targetDevice"],
        item["targetInode"],
    )
    if (
        any(type(number) is not int or number < 0 for number in integers)
        or integers[1] == 0
        or integers[3] == 0
    ):
        raise DirectorDurableStateError()
    parts = _exact_list(item["relativePath"])
    if not parts or len(parts) > MAX_RELATIVE_PARTS:
        raise DirectorDurableStateError()
    normalized_parts = []
    for part in parts:
        if (
            not isinstance(part, str)
            or part in {"", ".", ".."}
            or _utf8_size(part) > 255
            or "/" in part
            or "\\" in part
            or any(ord(character) < 32 for character in part)
        ):
            raise DirectorDurableStateError()
        normalized_parts.append(part)
    return {
        "rootDevice": integers[0],
        "rootInode": integers[1],
        "targetDevice": integers[2],
        "targetInode": integers[3],
        "relativePath": normalized_parts,
    }


def _schema(value: dict[str, object], expected: str) -> None:
    if (
        value["schema"] != expected
        or type(value["version"]) is not int
        or value["version"] != SCHEMA_VERSION
    ):
        raise DirectorDurableStateError()


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeError:
        raise DirectorDurableStateError() from None


def _exact_dict(value: object, keys: set[str] | None = None) -> dict[str, object]:
    if type(value) is not dict or (keys is not None and set(value) != keys):
        raise DirectorDurableStateError()
    return value


def _exact_list(value: object) -> list[object]:
    if type(value) is not list:
        raise DirectorDurableStateError()
    return value


def _require_bounded(value: object) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise DirectorDurableStateError() from None
    if len(encoded) > MAX_PLAINTEXT_BYTES:
        raise DirectorDurableStateError()


__all__ = [
    "CAPTURE_INDEX_ID",
    "CAPTURE_INDEX_SCHEMA",
    "DURABLE_SINGLETON_DECLARATIONS",
    "DURABLE_SINGLETON_IDS",
    "DURABLE_STATE_RESOURCE_ID",
    "DURABLE_STATE_STORE_ADAPTER_ID",
    "DirectorDurableStateError",
    "MEDIA_FOLDER_SETTINGS_ID",
    "MEDIA_FOLDER_SETTINGS_SCHEMA",
    "TAKE_DELETION_JOURNAL_ID",
    "TAKE_DELETION_JOURNAL_SCHEMA",
    "build_director_durable_state_privacy_profile",
    "build_director_durable_state_server_adapters",
    "media_folder_settings_view",
    "normalize_capture_index",
    "normalize_media_folder_settings",
    "normalize_take_deletion_journal",
]
