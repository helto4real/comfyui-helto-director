"""D5 media-browser and source-serving privacy composition.

Product paths and names live only inside RAM-only opaque reference values;
private operation projections expose coarse booleans and counts, timeline
attachment uses exact external operations, and bytes leave only through shared
source or generated-artifact leases.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import mimetypes
import os
import re
import secrets
import stat
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from helto_privacy import (
    AdapterSlot,
    ArtifactOperationDependency,
    ExternalOperationBinding,
    ExternalOperationCapture,
    ExternalOperationClassification,
    ExternalOperationDisposition,
    ExternalOperationInvocation,
    ExternalOperationPolicy,
    OpaqueReferenceCandidate,
    OpaqueReferenceKind,
    OperationReferenceInput,
    OperationReferenceOutput,
    PrivacyProfile,
    ProfileResource,
    ProtectedOperation,
    ProtectedOperationAdapterResult,
    RecordOperationDependency,
    ResourceKind,
    SafeDiagnosticField,
    SafeDiagnosticKind,
    SensitiveFieldClass,
    SensitiveFieldDeclaration,
    SingletonOperationDependency,
    root_bound_source,
)

from ..media_domain import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

from .managed_durable_state import (
    MEDIA_FOLDER_SETTINGS_ID,
    MEDIA_FOLDER_SETTINGS_SCHEMA,
    build_director_durable_state_privacy_profile,
    media_folder_settings_view,
    normalize_media_folder_settings,
)

from .managed_library_records import (
    PROJECT_RESOURCE_ID,
    PROJECT_RECORD_KIND,
    build_director_library_privacy_profile,
)
from .managed_media_artifacts import (
    THUMBNAIL_ARTIFACT_KIND,
    WAVEFORM_ARTIFACT_KIND,
    DirectorManagedMediaArtifacts,
    build_director_media_artifact_privacy_profile,
)
from .managed_segment_spills import build_director_take_segment_privacy_profile
from .managed_privacy import (
    DIRECTOR_DISTRIBUTION,
    DIRECTOR_PROFILE_ID,
    GLOBAL_SCOPE_ID,
    TIMELINE_BROWSER_ADAPTER_ID,
    TIMELINE_FIELD_ID,
    build_director_timeline_privacy_profile,
)
from .normalize import normalize_video_timeline
from .take_registration import register_generated_take


MEDIA_OPERATION_RESOURCE_ID = "director-media-operations"
MEDIA_FOLDER_REFERENCE_KIND = "media-folder"
MEDIA_SOURCE_REFERENCE_KIND = "media-source"
PROJECT_TAKE_REFERENCE_KIND = "project-take"
MAX_OPERATION_REFERENCES = 256
MAX_PROJECT_TAKE_REFERENCES = 128
MAX_LEGACY_FOLDER_CONFIG_BYTES = 2 * 1024 * 1024

MEDIA_FOLDERS_LIST = "media-folders-list"
MEDIA_FOLDERS_ADD = "media-folders-add"
MEDIA_FOLDERS_REMOVE = "media-folders-remove"
MEDIA_ITEMS_LIST = "media-items-list"
MEDIA_SOURCE_VIEW = "media-source-view"
MEDIA_SOURCE_PREVIEW = "media-source-preview"
MEDIA_SOURCE_RESOLVE = "media-source-resolve"
MEDIA_SOURCE_ATTACH = "media-source-attach"
PROJECT_TAKES_LIST = "project-takes-list"
PROJECT_TAKES_ATTACH = "project-takes-attach"
PROJECT_TAKES_DELETE = "project-takes-delete"

MEDIA_OPERATION_IDS = (
    MEDIA_FOLDERS_LIST,
    MEDIA_FOLDERS_ADD,
    MEDIA_FOLDERS_REMOVE,
    MEDIA_ITEMS_LIST,
    MEDIA_SOURCE_VIEW,
    MEDIA_SOURCE_PREVIEW,
    MEDIA_SOURCE_RESOLVE,
    MEDIA_SOURCE_ATTACH,
    PROJECT_TAKES_LIST,
    PROJECT_TAKES_ATTACH,
    PROJECT_TAKES_DELETE,
)
MEDIA_OPERATION_ADAPTER_IDS = {
    operation_id: f"director-{operation_id}-operation"
    for operation_id in MEDIA_OPERATION_IDS
}

MEDIA_DEFINITIONS = {
    "image": {
        "extensions": IMAGE_EXTENSIONS,
        "items_key": "images",
        "count_key": "image_count",
        "config_name": "timeline_image_folders.json",
    },
    "video": {
        "extensions": VIDEO_EXTENSIONS,
        "items_key": "videos",
        "count_key": "video_count",
        "config_name": "timeline_video_folders.json",
    },
    "audio": {
        "extensions": AUDIO_EXTENSIONS,
        "items_key": "audios",
        "count_key": "audio_count",
        "config_name": "timeline_audio_folders.json",
    },
}

MEDIA_PRIVACY_ACTIVATION_GAPS = ()

_ALIAS = re.compile(r"^[A-Za-z0-9_. -]{1,80}$")
_RECORD_ID = re.compile(r"^hp-rec-[A-Za-z0-9_-]{32}$")
_SAFE_PART = re.compile(r"[^A-Za-z0-9_.-]+")
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_SAFE_METADATA_FIELDS = frozenset(
    {"channels", "duration_seconds", "frame_count", "height", "sample_rate", "width"}
)
MEDIA_FOLDER_CONFIG_SCHEMA = "helto.director-media-folders"
MEDIA_FOLDER_CONFIG_VERSION = 1
_CONFIG_WRITE_LOCK = threading.RLock()
_CONFIG_LOCK_FILE = ".helto-media-folders.lock"
_TAKE_DELETE_LOCK_FILE = ".helto-take-delete.lock"
_FOLDER_SINGLETON_READ_VERBS = ("reveal", "status")
_FOLDER_SINGLETON_WRITE_VERBS = ("replace", "reveal", "status")
_FOLDER_SINGLETON_OPERATIONS = frozenset(
    {
        MEDIA_FOLDERS_LIST,
        MEDIA_FOLDERS_ADD,
        MEDIA_FOLDERS_REMOVE,
        MEDIA_ITEMS_LIST,
        MEDIA_SOURCE_VIEW,
        MEDIA_SOURCE_PREVIEW,
        MEDIA_SOURCE_RESOLVE,
        MEDIA_SOURCE_ATTACH,
    }
)


class DirectorManagedMediaPrivacyError(RuntimeError):
    """Product-data-free failure shared by every managed D5 adapter."""

    def __init__(self) -> None:
        super().__init__("Director managed media operation failed.")


@dataclass(frozen=True, slots=True)
class MediaFolder:
    alias: str = field(repr=False)
    path: Path = field(repr=False)
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class MediaFolderLocator:
    media_type: str
    alias: str = field(repr=False)
    root: Path = field(repr=False)
    enabled: bool
    entry_revision: str = field(repr=False)
    root_device: int | None = field(repr=False)
    root_inode: int | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class MediaSourceLocator:
    media_type: str
    root: Path = field(repr=False)
    relative_parts: tuple[str, ...] = field(repr=False)
    device: int = field(repr=False)
    inode: int = field(repr=False)
    size: int
    mtime_ns: int = field(repr=False)
    content_type: str


@dataclass(frozen=True, slots=True)
class ProjectTakeLocator:
    project_record_id: str = field(repr=False)
    shot_id: str = field(repr=False)
    source: MediaSourceLocator = field(repr=False)
    sidecars: tuple["BoundFileRevision", ...] = field(repr=False)
    take_id: str = field(repr=False)
    root_device: int = field(repr=False)
    root_inode: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class BoundFileRevision:
    name: str = field(repr=False)
    device: int = field(repr=False)
    inode: int = field(repr=False)
    size: int
    mtime_ns: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class _LegacyFolderSource:
    media_type: str
    revision: BoundFileRevision = field(repr=False)
    digest: bytes = field(repr=False)
    folders: tuple[MediaFolder, ...] = field(repr=False)


class _OperationMediaArtifactHandle:
    """Narrow two-kind facade over invocation-scoped artifact capabilities."""

    def __init__(self, dependencies: object) -> None:
        try:
            self._capabilities = {
                kind: dependencies.artifact(kind)
                for kind in (THUMBNAIL_ARTIFACT_KIND, WAVEFORM_ARTIFACT_KIND)
            }
        except Exception:
            raise DirectorManagedMediaPrivacyError() from None

    def _capability(self, kind: str):
        capability = self._capabilities.get(kind)
        if capability is None:
            raise DirectorManagedMediaPrivacyError()
        return capability

    async def write(self, kind: str, owner_id: str, value: object):
        return await self._capability(kind).write(owner_id, value)

    async def read(self, kind: str, reference: object):
        return await self._capability(kind).read(reference)

    async def retire(self, kind: str, reference: object) -> int:
        return await self._capability(kind).retire(reference)

    async def lease(
        self,
        kind: str,
        reference: object,
        operation: str,
        _authorization: object = None,
    ):
        return await self._capability(kind).lease(reference, operation)


def _sensitive(*paths: tuple[str, SensitiveFieldClass]) -> tuple[SensitiveFieldDeclaration, ...]:
    return (
        SensitiveFieldDeclaration("*", SensitiveFieldClass.CONSUMER_DERIVED),
        *(SensitiveFieldDeclaration(path, field_class) for path, field_class in paths),
    )


def _safe(*names: str) -> tuple[SafeDiagnosticField, ...]:
    return tuple(
        SafeDiagnosticField(
            name,
            SafeDiagnosticKind.BOOLEAN if name in {"deleted", "media_missing", "ok", "ready"} else SafeDiagnosticKind.COUNT,
        )
        for name in names
    )


def _operation(
    operation_id: str,
    *,
    route: str | None,
    method: str = "POST",
    sensitive: tuple[tuple[str, SensitiveFieldClass], ...] = (),
    safe: tuple[str, ...] = (),
    inputs: tuple[OperationReferenceInput, ...] = (),
    outputs: tuple[OperationReferenceOutput, ...] = (),
    returns_lease: bool = False,
    record_dependencies: tuple[RecordOperationDependency, ...] = (),
    singleton_dependencies: tuple[SingletonOperationDependency, ...] = (),
    artifact_dependencies: tuple[ArtifactOperationDependency, ...] = (),
    external_operation_binding: ExternalOperationBinding | None = None,
) -> ProtectedOperation:
    return ProtectedOperation(
        operation_id,
        MEDIA_OPERATION_RESOURCE_ID,
        MEDIA_OPERATION_ADAPTER_IDS[operation_id],
        route,
        method=method,
        scope_id=GLOBAL_SCOPE_ID,
        sensitive_fields=_sensitive(*sensitive),
        safe_projection=_safe(*safe),
        reference_inputs=inputs,
        reference_outputs=outputs,
        returns_lease=returns_lease,
        record_dependencies=record_dependencies,
        singleton_dependencies=singleton_dependencies,
        artifact_dependencies=artifact_dependencies,
        external_operation_binding=external_operation_binding,
    )


MEDIA_PROTECTED_OPERATIONS = (
    _operation(
        MEDIA_FOLDERS_LIST,
        route="/helto_director/managed/media/folders/list",
        sensitive=(("folders", SensitiveFieldClass.PATH_OR_NAME),),
        safe=("enabled_count", "existing_count", "folder_count"),
        outputs=(OperationReferenceOutput(MEDIA_FOLDER_REFERENCE_KIND, 0, MAX_OPERATION_REFERENCES),),
        singleton_dependencies=(
            SingletonOperationDependency(
                MEDIA_FOLDER_SETTINGS_ID,
                _FOLDER_SINGLETON_READ_VERBS,
            ),
        ),
    ),
    _operation(
        MEDIA_FOLDERS_ADD,
        route="/helto_director/managed/media/folders/add",
        sensitive=(("folder", SensitiveFieldClass.PATH_OR_NAME), ("input", SensitiveFieldClass.USER_AUTHORED)),
        safe=("folder_count", "ok"),
        outputs=(OperationReferenceOutput(MEDIA_FOLDER_REFERENCE_KIND),),
        singleton_dependencies=(
            SingletonOperationDependency(
                MEDIA_FOLDER_SETTINGS_ID,
                _FOLDER_SINGLETON_WRITE_VERBS,
            ),
        ),
    ),
    _operation(
        MEDIA_FOLDERS_REMOVE,
        route="/helto_director/managed/media/folders/remove",
        sensitive=(("folder", SensitiveFieldClass.PATH_OR_NAME),),
        safe=("folder_count", "ok"),
        inputs=(OperationReferenceInput("folder", MEDIA_FOLDER_REFERENCE_KIND, True),),
        singleton_dependencies=(
            SingletonOperationDependency(
                MEDIA_FOLDER_SETTINGS_ID,
                _FOLDER_SINGLETON_WRITE_VERBS,
            ),
        ),
    ),
    _operation(
        MEDIA_ITEMS_LIST,
        route="/helto_director/managed/media/items/list",
        sensitive=(("items", SensitiveFieldClass.PATH_OR_NAME), ("metadata", SensitiveFieldClass.CONSUMER_DERIVED)),
        safe=("item_count",),
        inputs=(OperationReferenceInput("folder", MEDIA_FOLDER_REFERENCE_KIND),),
        outputs=(OperationReferenceOutput(MEDIA_SOURCE_REFERENCE_KIND, 0, MAX_OPERATION_REFERENCES),),
        singleton_dependencies=(
            SingletonOperationDependency(
                MEDIA_FOLDER_SETTINGS_ID,
                _FOLDER_SINGLETON_READ_VERBS,
            ),
        ),
    ),
    _operation(
        MEDIA_SOURCE_VIEW,
        route="/helto_director/managed/media/source/view",
        sensitive=(("source", SensitiveFieldClass.PATH_OR_NAME),),
        safe=("ready",),
        inputs=(OperationReferenceInput("source", MEDIA_SOURCE_REFERENCE_KIND),),
        returns_lease=True,
        singleton_dependencies=(
            SingletonOperationDependency(
                MEDIA_FOLDER_SETTINGS_ID,
                _FOLDER_SINGLETON_READ_VERBS,
            ),
        ),
    ),
    _operation(
        MEDIA_SOURCE_PREVIEW,
        route="/helto_director/managed/media/source/preview",
        sensitive=(("source", SensitiveFieldClass.PATH_OR_NAME),),
        safe=("ready",),
        inputs=(OperationReferenceInput("source", MEDIA_SOURCE_REFERENCE_KIND),),
        returns_lease=True,
        singleton_dependencies=(
            SingletonOperationDependency(
                MEDIA_FOLDER_SETTINGS_ID,
                _FOLDER_SINGLETON_READ_VERBS,
            ),
        ),
        artifact_dependencies=(
            ArtifactOperationDependency(
                THUMBNAIL_ARTIFACT_KIND,
                ("lease.preview", "read", "retire", "write"),
            ),
            ArtifactOperationDependency(
                WAVEFORM_ARTIFACT_KIND,
                ("lease.preview", "read", "retire", "write"),
            ),
        ),
    ),
    _operation(
        MEDIA_SOURCE_RESOLVE,
        route="/helto_director/managed/media/source/resolve",
        sensitive=(
            ("source", SensitiveFieldClass.PATH_OR_NAME),
            ("metadata", SensitiveFieldClass.CONSUMER_DERIVED),
        ),
        safe=("ready",),
        outputs=(OperationReferenceOutput(MEDIA_SOURCE_REFERENCE_KIND),),
        singleton_dependencies=(
            SingletonOperationDependency(
                MEDIA_FOLDER_SETTINGS_ID,
                _FOLDER_SINGLETON_READ_VERBS,
            ),
        ),
    ),
    _operation(
        MEDIA_SOURCE_ATTACH,
        route=None,
        sensitive=(
            ("timeline", SensitiveFieldClass.USER_AUTHORED),
            ("source", SensitiveFieldClass.PATH_OR_NAME),
        ),
        safe=("ok",),
        inputs=(OperationReferenceInput("source", MEDIA_SOURCE_REFERENCE_KIND),),
        singleton_dependencies=(
            SingletonOperationDependency(
                MEDIA_FOLDER_SETTINGS_ID,
                _FOLDER_SINGLETON_READ_VERBS,
            ),
        ),
        external_operation_binding=ExternalOperationBinding(
            TIMELINE_FIELD_ID,
            TIMELINE_BROWSER_ADAPTER_ID,
            ExternalOperationPolicy(
                max_identity_bytes=1024,
                max_original_bytes=2 * 1024 * 1024,
                max_target_bytes=2 * 1024 * 1024,
                lease_seconds=300,
            ),
        ),
    ),
    _operation(
        PROJECT_TAKES_LIST,
        route="/helto_director/managed/media/project-takes/list",
        sensitive=(("captures", SensitiveFieldClass.PATH_OR_NAME), ("project", SensitiveFieldClass.USER_AUTHORED)),
        safe=("capture_count",),
        outputs=(
            OperationReferenceOutput(MEDIA_SOURCE_REFERENCE_KIND, 0, MAX_PROJECT_TAKE_REFERENCES),
            OperationReferenceOutput(PROJECT_TAKE_REFERENCE_KIND, 0, MAX_PROJECT_TAKE_REFERENCES),
        ),
        record_dependencies=(
            RecordOperationDependency(PROJECT_RESOURCE_ID, PROJECT_RECORD_KIND, "use"),
        ),
    ),
    _operation(
        PROJECT_TAKES_ATTACH,
        route=None,
        sensitive=(
            ("timeline", SensitiveFieldClass.USER_AUTHORED),
            ("capture", SensitiveFieldClass.PATH_OR_NAME),
        ),
        safe=("ok",),
        inputs=(
            OperationReferenceInput("source", MEDIA_SOURCE_REFERENCE_KIND),
            OperationReferenceInput("take", PROJECT_TAKE_REFERENCE_KIND),
        ),
        record_dependencies=(
            RecordOperationDependency(PROJECT_RESOURCE_ID, PROJECT_RECORD_KIND, "use"),
        ),
        external_operation_binding=ExternalOperationBinding(
            TIMELINE_FIELD_ID,
            TIMELINE_BROWSER_ADAPTER_ID,
            ExternalOperationPolicy(
                max_identity_bytes=1024,
                max_original_bytes=2 * 1024 * 1024,
                max_target_bytes=2 * 1024 * 1024,
                lease_seconds=300,
            ),
        ),
    ),
    _operation(
        PROJECT_TAKES_DELETE,
        route="/helto_director/managed/media/project-takes/delete",
        sensitive=(("capture", SensitiveFieldClass.PATH_OR_NAME),),
        safe=("deleted", "files_deleted", "media_missing", "ok"),
        inputs=(OperationReferenceInput("take", PROJECT_TAKE_REFERENCE_KIND, True),),
        record_dependencies=(
            RecordOperationDependency(PROJECT_RESOURCE_ID, PROJECT_RECORD_KIND, "use"),
        ),
    ),
)


def build_director_media_privacy_profile(
    base_profile: PrivacyProfile | None = None,
) -> PrivacyProfile:
    """Compose D5 onto D2/D3/D4/D6 without installing the result."""

    if base_profile is None:
        base = build_director_timeline_privacy_profile()
        base = build_director_library_privacy_profile(base)
        base = build_director_media_artifact_privacy_profile(base)
        base = build_director_take_segment_privacy_profile(base)
    else:
        base = base_profile
    if base.id != DIRECTOR_PROFILE_ID or base.distribution != DIRECTOR_DISTRIBUTION:
        raise ValueError("Director media privacy requires the Director profile.")
    ids = {item.id for item in base.resources} | {item.id for item in base.protected_operations}
    if MEDIA_OPERATION_RESOURCE_ID in ids or ids.intersection(MEDIA_OPERATION_IDS):
        raise ValueError("Director media privacy fragment is already present.")
    if not any(item.id == MEDIA_FOLDER_SETTINGS_ID for item in base.singletons):
        base = build_director_durable_state_privacy_profile(base)

    adapter_slots = tuple(
        AdapterSlot(
            MEDIA_OPERATION_ADAPTER_IDS[operation_id],
            ResourceKind.OPERATION,
            MEDIA_OPERATION_RESOURCE_ID,
        )
        for operation_id in MEDIA_OPERATION_IDS
    )
    resource = ProfileResource(
        MEDIA_OPERATION_RESOURCE_ID,
        ResourceKind.OPERATION,
        tuple(item.id for item in adapter_slots),
    )
    reference_kinds = tuple(
        OpaqueReferenceKind(kind, MEDIA_OPERATION_RESOURCE_ID, GLOBAL_SCOPE_ID)
        for kind in (
            MEDIA_FOLDER_REFERENCE_KIND,
            MEDIA_SOURCE_REFERENCE_KIND,
            PROJECT_TAKE_REFERENCE_KIND,
        )
    )
    return replace(
        base,
        resources=(*base.resources, resource),
        server_adapters=(*base.server_adapters, *adapter_slots),
        protected_operations=(*base.protected_operations, *MEDIA_PROTECTED_OPERATIONS),
        opaque_reference_kinds=(*base.opaque_reference_kinds, *reference_kinds),
    )


class DirectorManagedMediaService:
    """Pure product service behind D5 protected-operation adapters."""

    def __init__(
        self,
        *,
        config_dir: str | os.PathLike[str],
        default_folders: Mapping[str, Sequence[MediaFolder]],
        project_asset_root: str | os.PathLike[str] | None = None,
        metadata_reader: Callable[[object, str], Mapping[str, object]] | None = None,
        preview_locator: Callable[[MediaSourceLocator], MediaSourceLocator] | None = None,
    ) -> None:
        self._config_dir = _absolute_directory(config_dir, create=True)
        self._defaults = {
            _media_type(kind): tuple(_folder(folder) for folder in folders)
            for kind, folders in default_folders.items()
        }
        self._project_asset_root = (
            _absolute_directory(project_asset_root, create=False)
            if project_asset_root is not None
            else None
        )
        self._metadata_reader = metadata_reader or (lambda _path, _kind: {})
        self._preview_locator = preview_locator

    def invoke(
        self,
        operation_id: str,
        value: object,
        references: Mapping[str, object],
        *,
        project_records: object = None,
        folder_settings: object = None,
    ) -> ProtectedOperationAdapterResult:
        try:
            source = _mapping(value)
            if operation_id == MEDIA_FOLDERS_LIST:
                return self._list_folders(source, folder_settings)
            if operation_id == MEDIA_FOLDERS_ADD:
                return self._add_folder(source, folder_settings)
            if operation_id == MEDIA_FOLDERS_REMOVE:
                return self._remove_folder(
                    _resolved_value(references, "folder"),
                    folder_settings,
                )
            if operation_id == MEDIA_ITEMS_LIST:
                return self._list_items(
                    source,
                    _resolved_value(references, "folder"),
                    folder_settings,
                )
            if operation_id == MEDIA_SOURCE_RESOLVE:
                return self._resolve_source(source, folder_settings)
            if operation_id in {MEDIA_SOURCE_VIEW, MEDIA_SOURCE_PREVIEW}:
                _require_folder_settings(folder_settings)
                return ProtectedOperationAdapterResult({"ready": True})
            if operation_id == PROJECT_TAKES_LIST:
                return self._list_project_takes(source, project_records)
            if operation_id == PROJECT_TAKES_DELETE:
                return self._delete_project_take(
                    _resolved_value(references, "take"),
                    project_records,
                )
        except DirectorManagedMediaPrivacyError:
            raise
        except Exception:
            raise DirectorManagedMediaPrivacyError() from None
        raise DirectorManagedMediaPrivacyError()

    def bind_source(
        self,
        resolved: object,
        operation_id: str,
        *,
        folder_settings: object,
    ):
        try:
            locator = getattr(resolved, "value", None)
            if not isinstance(locator, MediaSourceLocator):
                raise DirectorManagedMediaPrivacyError()
            if operation_id == MEDIA_SOURCE_PREVIEW:
                if self._preview_locator is None:
                    raise DirectorManagedMediaPrivacyError()
                locator = self._preview_locator(locator)
                if not isinstance(locator, MediaSourceLocator):
                    raise DirectorManagedMediaPrivacyError()
            allowed_roots = self._allowed_roots(locator.media_type, folder_settings)
            path = _revalidate_source(locator, allowed_roots)
            bound = root_bound_source(
                path,
                tuple(allowed_roots),
                media_type=locator.content_type,
            )
            if (
                getattr(bound, "_device", None) != locator.device
                or getattr(bound, "_inode", None) != locator.inode
            ):
                raise DirectorManagedMediaPrivacyError()
            return bound
        except DirectorManagedMediaPrivacyError:
            raise
        except Exception:
            raise DirectorManagedMediaPrivacyError() from None

    async def preview_artifact(
        self,
        value: object,
        resolved: object,
        *,
        folder_settings: object,
        dependencies: object,
    ):
        try:
            request = _mapping(value)
            if not set(request).issubset({"max_size", "peaks"}):
                raise DirectorManagedMediaPrivacyError()
            locator = getattr(resolved, "value", None)
            if not isinstance(locator, MediaSourceLocator):
                raise DirectorManagedMediaPrivacyError()
            roots = self._allowed_roots(locator.media_type, folder_settings)
            path = _revalidate_source(locator, roots)
            artifacts = DirectorManagedMediaArtifacts(
                _OperationMediaArtifactHandle(dependencies),
                authorized_roots=lambda: [str(root) for root in roots],
            )
            if locator.media_type == "audio":
                return await artifacts.waveform_lease(
                    path,
                    None,
                    request.get("peaks", 96),
                )
            return await artifacts.thumbnail_lease(
                path,
                None,
                request.get("max_size", 320),
            )
        except DirectorManagedMediaPrivacyError:
            raise
        except Exception:
            raise DirectorManagedMediaPrivacyError() from None

    def attach_source(
        self,
        value: object,
        resolved: object,
        *,
        folder_settings: object,
    ) -> dict[str, object]:
        """Resolve one opaque source into a new protected timeline value."""

        try:
            source = _mapping(value)
            if set(source) != {"asset_type", "item_id", "timeline"}:
                raise DirectorManagedMediaPrivacyError()
            locator = getattr(resolved, "value", None)
            if not isinstance(locator, MediaSourceLocator):
                raise DirectorManagedMediaPrivacyError()
            asset_type = str(source["asset_type"])
            expected_type = {
                "image": "Image",
                "video": "Video",
                "audio": "Audio",
            }[locator.media_type]
            if asset_type != expected_type:
                raise DirectorManagedMediaPrivacyError()
            allowed_roots = self._allowed_roots(locator.media_type, folder_settings)
            path = _revalidate_source(locator, allowed_roots)
            metadata = dict(self._metadata_reader(path, locator.media_type))
            return _attach_source_to_timeline(
                source["timeline"],
                item_id=source["item_id"],
                asset_type=asset_type,
                path=path,
                locator=locator,
                metadata=metadata,
            )
        except DirectorManagedMediaPrivacyError:
            raise
        except Exception:
            raise DirectorManagedMediaPrivacyError() from None

    def attach_project_take(
        self,
        value: object,
        source_resolved: object,
        take_resolved: object,
        *,
        project_records: object,
    ) -> dict[str, object]:
        """Resolve one record-authorized project capture into the timeline."""

        try:
            request = _mapping(value)
            if set(request) != {
                "accept", "project_record_id", "shot_id", "timeline"
            }:
                raise DirectorManagedMediaPrivacyError()
            project_record_id = str(request["project_record_id"] or "")
            shot_id = str(request["shot_id"] or "").strip()
            source = getattr(source_resolved, "value", None)
            take = getattr(take_resolved, "value", None)
            if (
                _RECORD_ID.fullmatch(project_record_id) is None
                or not shot_id
                or not isinstance(source, MediaSourceLocator)
                or not isinstance(take, ProjectTakeLocator)
                or take.project_record_id != project_record_id
                or take.shot_id != shot_id
                or take.source != source
            ):
                raise DirectorManagedMediaPrivacyError()
            project = self._project(project_record_id, project_records)
            take_root = self._project_take_root(project, shot_id)
            if source.root != take_root:
                raise DirectorManagedMediaPrivacyError()
            path = _revalidate_source(source, (take_root,))
            result = register_generated_take(
                request["timeline"],
                {
                    "shot_id": shot_id,
                    "asset": {
                        "type": "Video",
                        "path": str(path),
                        "name": path.name,
                        "mime_type": source.content_type,
                        "size_bytes": source.size,
                    },
                    "take": {
                        "take_id": take.take_id,
                        "status": "Accepted" if request["accept"] is True else "Candidate",
                    },
                    "accept": request["accept"] is True,
                    "update_clip_instance": True,
                },
            )
            timeline = result.get("timeline")
            if type(timeline) is not dict:
                raise DirectorManagedMediaPrivacyError()
            return timeline
        except DirectorManagedMediaPrivacyError:
            raise
        except Exception:
            raise DirectorManagedMediaPrivacyError() from None

    def _list_folders(
        self,
        source: Mapping[str, object],
        folder_settings: object,
    ) -> ProtectedOperationAdapterResult:
        media_type = _media_type(source.get("media_type"))
        folders = self._load_folders(media_type, folder_settings)
        if len(folders) > MAX_OPERATION_REFERENCES:
            raise DirectorManagedMediaPrivacyError()
        payloads = [self._folder_payload(media_type, folder) for folder in folders]
        return ProtectedOperationAdapterResult(
            {
                "folders": payloads,
                "folder_count": len(payloads),
                "enabled_count": sum(bool(item["enabled"]) for item in payloads),
                "existing_count": sum(bool(item["exists"]) for item in payloads),
            },
            tuple(
                OpaqueReferenceCandidate(
                    MEDIA_FOLDER_REFERENCE_KIND,
                    self._folder_locator(media_type, folder),
                )
                for folder in folders
            ),
        )

    def _add_folder(
        self,
        source: Mapping[str, object],
        folder_settings: object,
    ) -> ProtectedOperationAdapterResult:
        media_type = _media_type(source.get("media_type"))
        path = _absolute_directory(source.get("directory"), create=False)
        configured, view, revision = self._folder_settings(folder_settings)
        folders = _folders_from_state(view, media_type)
        if any(_same_path(folder.path, path) for folder in folders):
            raise DirectorManagedMediaPrivacyError()
        alias = _safe_alias(source.get("alias")) if source.get("alias") else _alias_for(path, folders)
        if any(folder.alias == alias for folder in folders):
            raise DirectorManagedMediaPrivacyError()
        added = MediaFolder(alias, path, True)
        replacement = _replace_configured_folders(
            configured,
            media_type,
            [*_folders_from_state(configured, media_type), added],
        )
        _replace_folder_settings(folder_settings, replacement, revision)
        folders = _folders_from_state(
            media_folder_settings_view(replacement, self._default_folder_values()),
            media_type,
        )
        return ProtectedOperationAdapterResult(
            {"ok": True, "folder_count": len(folders), "folder": self._folder_payload(media_type, added)},
            (OpaqueReferenceCandidate(MEDIA_FOLDER_REFERENCE_KIND, self._folder_locator(media_type, added)),),
        )

    def _remove_folder(
        self,
        locator: object,
        folder_settings: object,
    ) -> ProtectedOperationAdapterResult:
        if not isinstance(locator, MediaFolderLocator):
            raise DirectorManagedMediaPrivacyError()
        configured, view, revision = self._folder_settings(folder_settings)
        folders = _folders_from_state(view, locator.media_type)
        current = next((item for item in folders if item.alias == locator.alias), None)
        if current is None or self._folder_locator(locator.media_type, current) != locator:
            raise DirectorManagedMediaPrivacyError()
        if any(item.alias == locator.alias for item in self._defaults.get(locator.media_type, ())):
            raise DirectorManagedMediaPrivacyError()
        configured_folders = [
            item
            for item in _folders_from_state(configured, locator.media_type)
            if item.alias != locator.alias
        ]
        if len(configured_folders) == len(
            _folders_from_state(configured, locator.media_type)
        ):
            raise DirectorManagedMediaPrivacyError()
        replacement = _replace_configured_folders(
            configured,
            locator.media_type,
            configured_folders,
        )
        _replace_folder_settings(folder_settings, replacement, revision)
        folders = _folders_from_state(
            media_folder_settings_view(replacement, self._default_folder_values()),
            locator.media_type,
        )
        return ProtectedOperationAdapterResult({"ok": True, "folder_count": len(folders)})

    def _list_items(
        self,
        source: Mapping[str, object],
        locator: object,
        folder_settings: object,
    ) -> ProtectedOperationAdapterResult:
        if not isinstance(locator, MediaFolderLocator) or not locator.enabled:
            raise DirectorManagedMediaPrivacyError()
        current = self._current_folder(locator, folder_settings)
        recursive = source.get("recursive", True) is not False
        item_payloads: list[dict[str, object]] = []
        references: list[OpaqueReferenceCandidate] = []
        for source_locator, path, metadata in _scan_sources(
            current.path,
            locator.media_type,
            recursive=recursive,
            metadata_reader=self._metadata_reader,
            expected_root_binding=(locator.root_device, locator.root_inode),
        ):
            if len(references) >= MAX_OPERATION_REFERENCES:
                break
            item_payloads.append(_source_payload(source_locator, path, metadata))
            references.append(OpaqueReferenceCandidate(MEDIA_SOURCE_REFERENCE_KIND, source_locator))
        return ProtectedOperationAdapterResult(
            {"items": item_payloads, "item_count": len(item_payloads)},
            tuple(references),
        )

    def _resolve_source(
        self,
        source: Mapping[str, object],
        folder_settings: object,
    ) -> ProtectedOperationAdapterResult:
        if not {"media_type", "path"}.issubset(source) or not set(source).issubset(
            {"media_type", "path", "source_type"}
        ):
            raise DirectorManagedMediaPrivacyError()
        media_type = _media_type(source["media_type"])
        folders = tuple(
            folder
            for folder in self._load_folders(media_type, folder_settings)
            if folder.enabled
        )
        roots = [folder.path for folder in folders]
        if media_type == "video" and self._project_asset_root is not None:
            roots.append(self._project_asset_root)
        raw_path = Path(os.fspath(source["path"]))
        if raw_path.is_absolute():
            candidates = (Path(os.path.abspath(raw_path)),)
        else:
            source_type = str(source.get("source_type") or "").strip()
            if not source_type:
                raise DirectorManagedMediaPrivacyError()
            candidates = tuple(
                folder.path.joinpath(raw_path)
                for folder in folders
                if folder.alias == source_type
            )
            if len(candidates) != 1:
                raise DirectorManagedMediaPrivacyError()
        locator = None
        path = None
        for candidate_path in candidates:
            for root in roots:
                try:
                    relative = candidate_path.relative_to(root)
                except ValueError:
                    continue
                try:
                    info = os.stat(candidate_path, follow_symlinks=False)
                    candidate = _source_locator_from_stat(
                        media_type,
                        root,
                        tuple(relative.parts),
                        info,
                    )
                    if _revalidate_source(candidate, roots) != candidate_path:
                        continue
                except (DirectorManagedMediaPrivacyError, FileNotFoundError, OSError):
                    continue
                locator = candidate
                path = candidate_path
                break
            if locator is not None:
                break
        if locator is None or path is None:
            raise DirectorManagedMediaPrivacyError()
        return ProtectedOperationAdapterResult(
            {"ready": True, "source": _source_payload(locator, path, {})},
            (OpaqueReferenceCandidate(MEDIA_SOURCE_REFERENCE_KIND, locator),),
        )

    def _list_project_takes(
        self,
        source: Mapping[str, object],
        project_records: object,
    ) -> ProtectedOperationAdapterResult:
        project_record_id = str(source.get("project_record_id") or "")
        shot_id = str(source.get("shot_id") or "").strip()
        if _RECORD_ID.fullmatch(project_record_id) is None or not shot_id:
            raise DirectorManagedMediaPrivacyError()
        project = self._project(project_record_id, project_records)
        root = self._project_take_root(project, shot_id)
        captures: list[tuple[MediaSourceLocator, ProjectTakeLocator, dict[str, object]]] = []
        root_binding = _directory_binding(root, missing_ok=True)
        if root_binding is not None:
            for source_locator, path, _metadata in _scan_sources(
                root,
                "video",
                recursive=True,
                expected_root_binding=root_binding,
            ):
                take = _project_take_locator(
                    project_record_id,
                    shot_id,
                    root,
                    path,
                    source_locator,
                    root_binding,
                )
                if take is None:
                    continue
                captures.append((take.source, take, _source_payload(take.source, path, {})))
                if len(captures) >= MAX_PROJECT_TAKE_REFERENCES:
                    break
        captures.sort(key=lambda item: (-item[0].mtime_ns, item[0].relative_parts))
        source_references = tuple(
            OpaqueReferenceCandidate(MEDIA_SOURCE_REFERENCE_KIND, item[0])
            for item in captures
        )
        take_references = tuple(
            OpaqueReferenceCandidate(PROJECT_TAKE_REFERENCE_KIND, item[1])
            for item in captures
        )
        return ProtectedOperationAdapterResult(
            {"captures": [item[2] for item in captures], "capture_count": len(captures)},
            (*source_references, *take_references),
        )

    def _delete_project_take(
        self,
        locator: object,
        project_records: object,
    ) -> ProtectedOperationAdapterResult:
        if not isinstance(locator, ProjectTakeLocator):
            raise DirectorManagedMediaPrivacyError()
        project = self._project(locator.project_record_id, project_records)
        take_root = self._project_take_root(project, locator.shot_id)
        candidate = take_root.joinpath(*locator.source.relative_parts)
        media_missing, deleted = _delete_take_files(take_root, locator)
        if media_missing and not locator.take_id:
            raise DirectorManagedMediaPrivacyError()
        _prune_empty(
            take_root,
            candidate.parent,
            expected_root_binding=(locator.root_device, locator.root_inode),
        )
        return ProtectedOperationAdapterResult(
            {"ok": True, "deleted": deleted > 0, "files_deleted": deleted, "media_missing": media_missing}
        )

    def _project(
        self,
        record_id: str,
        project_records: object,
    ) -> Mapping[str, object]:
        reveal = getattr(project_records, "reveal", None)
        if not callable(reveal):
            raise DirectorManagedMediaPrivacyError()
        value = reveal(record_id)
        if not isinstance(value, Mapping):
            raise DirectorManagedMediaPrivacyError()
        if value.get("kind") not in {None, PROJECT_RECORD_KIND}:
            raise DirectorManagedMediaPrivacyError()
        project = value.get("project", value.get("payload", value))
        if not isinstance(project, Mapping):
            raise DirectorManagedMediaPrivacyError()
        return project

    def _project_take_root(self, project: Mapping[str, object], shot_id: str) -> Path:
        if self._project_asset_root is None:
            raise DirectorManagedMediaPrivacyError()
        identity = project.get("identity")
        storage = project.get("storage")
        if not isinstance(identity, Mapping) or not isinstance(storage, Mapping):
            raise DirectorManagedMediaPrivacyError()
        project_id = str(identity.get("project_id") or "")
        directory = str(storage.get("project_directory_name") or "")
        if not _safe_component(project_id) or not _safe_component(directory) or project_id.lower() not in directory.lower():
            raise DirectorManagedMediaPrivacyError()
        shot = _safe_component(shot_id)
        if not shot:
            raise DirectorManagedMediaPrivacyError()
        root = self._project_asset_root / directory / "takes" / shot
        return _inside(self._project_asset_root, root, require_exists=False)

    def _folder_settings(
        self,
        folder_settings: object,
    ) -> tuple[dict[str, object], dict[str, object], int]:
        configured, revision = _read_folder_settings(folder_settings)
        view = media_folder_settings_view(configured, self._default_folder_values())
        return configured, view, revision

    def _default_folder_values(self) -> dict[str, object]:
        return {
            media_type: [
                {
                    "alias": folder.alias,
                    "path": str(folder.path),
                    "enabled": folder.enabled,
                }
                for folder in self._defaults.get(media_type, ())
            ]
            for media_type in MEDIA_DEFINITIONS
        }

    def _load_folders(
        self,
        media_type: str,
        folder_settings: object,
    ) -> list[MediaFolder]:
        _configured, view, _revision = self._folder_settings(folder_settings)
        return _folders_from_state(view, media_type)

    def _folder_payload(self, media_type: str, folder: MediaFolder) -> dict[str, object]:
        root_binding = _directory_binding(folder.path, missing_ok=True)
        exists = root_binding is not None
        count_key = str(MEDIA_DEFINITIONS[media_type]["count_key"])
        return {
            "alias": folder.alias,
            "path": str(folder.path),
            "display_name": folder.path.name or str(folder.path),
            "enabled": folder.enabled,
            "exists": exists,
            count_key: len(
                _scan_sources(
                    folder.path,
                    media_type,
                    recursive=True,
                    expected_root_binding=root_binding,
                )
            ) if exists else 0,
        }

    def _folder_locator(self, media_type: str, folder: MediaFolder) -> MediaFolderLocator:
        root_binding = _directory_binding(folder.path, missing_ok=True)
        entry_revision = hashlib.sha256(
            json.dumps(
                {
                    "alias": folder.alias,
                    "enabled": folder.enabled,
                    "media_type": media_type,
                    "path": str(folder.path),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return MediaFolderLocator(
            media_type,
            folder.alias,
            folder.path,
            folder.enabled,
            entry_revision,
            None if root_binding is None else root_binding[0],
            None if root_binding is None else root_binding[1],
        )

    def _current_folder(
        self,
        locator: MediaFolderLocator,
        folder_settings: object,
    ) -> MediaFolder:
        current = next(
            (
                item
                for item in self._load_folders(locator.media_type, folder_settings)
                if item.alias == locator.alias
            ),
            None,
        )
        if current is None or self._folder_locator(locator.media_type, current) != locator:
            raise DirectorManagedMediaPrivacyError()
        if locator.root_device is None or locator.root_inode is None:
            raise DirectorManagedMediaPrivacyError()
        return current

    def _allowed_roots(
        self,
        media_type: str,
        folder_settings: object,
    ) -> tuple[Path, ...]:
        roots = [
            folder.path
            for folder in self._load_folders(media_type, folder_settings)
            if folder.enabled
        ]
        if media_type == "video" and self._project_asset_root is not None:
            roots.append(self._project_asset_root)
        return tuple(roots)


def _empty_media_folder_settings() -> dict[str, object]:
    return {
        "schema": MEDIA_FOLDER_SETTINGS_SCHEMA,
        "version": 1,
        "folders": {media_type: [] for media_type in MEDIA_DEFINITIONS},
    }


def _require_folder_settings(value: object) -> object:
    if value is None or not callable(getattr(value, "status", None)):
        raise DirectorManagedMediaPrivacyError()
    return value


def _read_folder_settings(
    capability: object,
) -> tuple[dict[str, object], int]:
    capability = _require_folder_settings(capability)
    try:
        status = capability.status()
        exists = getattr(status, "exists", None)
        revision = getattr(status, "revision", None)
        if type(exists) is not bool or type(revision) is not int or revision < 0:
            raise DirectorManagedMediaPrivacyError()
        if not exists:
            if revision != 0:
                raise DirectorManagedMediaPrivacyError()
            return _empty_media_folder_settings(), 0
        reveal = getattr(capability, "reveal", None)
        if not callable(reveal):
            raise DirectorManagedMediaPrivacyError()
        revealed = reveal()
        revealed_revision = getattr(revealed, "revision", None)
        if type(revealed_revision) is not int or revealed_revision < 1:
            raise DirectorManagedMediaPrivacyError()
        return normalize_media_folder_settings(
            getattr(revealed, "value", None)
        ), revealed_revision
    except DirectorManagedMediaPrivacyError:
        raise
    except Exception:
        raise DirectorManagedMediaPrivacyError() from None


def _replace_folder_settings(
    capability: object,
    replacement: object,
    expected_revision: int,
) -> int:
    capability = _require_folder_settings(capability)
    normalized = normalize_media_folder_settings(replacement)
    replace_value = getattr(capability, "replace", None)
    if not callable(replace_value):
        raise DirectorManagedMediaPrivacyError()
    try:
        receipt = replace_value(normalized, expected_revision)
        revision = getattr(receipt, "revision", None)
        if type(revision) is not int or revision != expected_revision + 1:
            raise DirectorManagedMediaPrivacyError()
        return revision
    except DirectorManagedMediaPrivacyError:
        raise
    except Exception:
        raise DirectorManagedMediaPrivacyError() from None


def _folders_from_state(value: object, media_type: str) -> list[MediaFolder]:
    media_type = _media_type(media_type)
    try:
        normalized = normalize_media_folder_settings(value)
        folders = normalized["folders"]
        assert isinstance(folders, dict)
        entries = folders[media_type]
        assert isinstance(entries, list)
        return [
            MediaFolder(
                str(entry["alias"]),
                _absolute_directory(
                    entry["path"],
                    create=False,
                    require_exists=False,
                ),
                bool(entry["enabled"]),
            )
            for entry in entries
            if isinstance(entry, dict)
        ]
    except DirectorManagedMediaPrivacyError:
        raise
    except Exception:
        raise DirectorManagedMediaPrivacyError() from None


def _replace_configured_folders(
    configured: object,
    media_type: str,
    folders: Sequence[MediaFolder],
) -> dict[str, object]:
    media_type = _media_type(media_type)
    normalized = normalize_media_folder_settings(configured)
    folder_values = normalized["folders"]
    assert isinstance(folder_values, dict)
    replacement = {
        "schema": MEDIA_FOLDER_SETTINGS_SCHEMA,
        "version": 1,
        "folders": {
            kind: (
                [
                    {
                        "alias": item.alias,
                        "path": str(item.path),
                        "enabled": item.enabled,
                    }
                    for item in folders
                ]
                if kind == media_type
                else folder_values[kind]
            )
            for kind in MEDIA_DEFINITIONS
        },
    }
    try:
        return normalize_media_folder_settings(replacement)
    except Exception:
        raise DirectorManagedMediaPrivacyError() from None


class DirectorMediaOperationAdapter:
    """One operation per adapter slot, matching the shared invoke contract."""

    def __init__(self, service: DirectorManagedMediaService, operation_id: str) -> None:
        self._service = service
        self._operation_id = operation_id

    def invoke_with_dependencies(
        self,
        value: object,
        references: Mapping[str, object],
        declaration: object,
        dependencies: object,
    ):
        if getattr(declaration, "id", None) != self._operation_id:
            raise DirectorManagedMediaPrivacyError()
        project_records = None
        folder_settings = None
        try:
            if self._operation_id in {PROJECT_TAKES_LIST, PROJECT_TAKES_DELETE}:
                project_records = dependencies.record(
                    PROJECT_RESOURCE_ID,
                    PROJECT_RECORD_KIND,
                    "use",
                )
            elif self._operation_id in _FOLDER_SINGLETON_OPERATIONS:
                folder_settings = dependencies.singleton(MEDIA_FOLDER_SETTINGS_ID)
            else:
                raise DirectorManagedMediaPrivacyError()
        except DirectorManagedMediaPrivacyError:
            raise
        except Exception:
            raise DirectorManagedMediaPrivacyError() from None
        if self._operation_id == MEDIA_SOURCE_PREVIEW:
            return self._invoke_preview(
                value,
                references,
                folder_settings=folder_settings,
                dependencies=dependencies,
            )
        return self._service.invoke(
            self._operation_id,
            value,
            references,
            project_records=project_records,
            folder_settings=folder_settings,
        )

    async def _invoke_preview(
        self,
        value: object,
        references: Mapping[str, object],
        *,
        folder_settings: object,
        dependencies: object,
    ) -> ProtectedOperationAdapterResult:
        lease = await self._service.preview_artifact(
            value,
            references.get("source"),
            folder_settings=folder_settings,
            dependencies=dependencies,
        )
        return ProtectedOperationAdapterResult({"ready": True}, lease=lease)

    def project(self, payload: object, declaration: object) -> dict[str, object]:
        if getattr(declaration, "id", None) != self._operation_id:
            raise DirectorManagedMediaPrivacyError()
        source = _mapping(payload)
        names = tuple(item.path for item in getattr(declaration, "safe_projection", ()))
        result: dict[str, object] = {}
        for name in names:
            value = source.get(name)
            if name in {"deleted", "media_missing", "ok", "ready"}:
                result[name] = value is True
            elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                result[name] = value
            else:
                raise DirectorManagedMediaPrivacyError()
        return result

    def capture_external_operation(
        self,
        value: object,
        references: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> ExternalOperationCapture:
        if (
            self._operation_id not in {MEDIA_SOURCE_ATTACH, PROJECT_TAKES_ATTACH}
            or getattr(declaration, "id", None) != self._operation_id
            or not isinstance(invocation, ExternalOperationInvocation)
            or type(references) is not dict
        ):
            raise DirectorManagedMediaPrivacyError()
        try:
            if self._operation_id == MEDIA_SOURCE_ATTACH:
                folder_settings = dependencies.singleton(MEDIA_FOLDER_SETTINGS_ID)
                target = self._service.attach_source(
                    value,
                    references.get("source"),
                    folder_settings=folder_settings,
                )
            else:
                project_records = dependencies.record(
                    PROJECT_RESOURCE_ID,
                    PROJECT_RECORD_KIND,
                    "use",
                )
                target = self._service.attach_project_take(
                    value,
                    references.get("source"),
                    references.get("take"),
                    project_records=project_records,
                )
        except DirectorManagedMediaPrivacyError:
            raise
        except Exception:
            raise DirectorManagedMediaPrivacyError() from None
        return ExternalOperationCapture(
            {"externalTransactionId": invocation.transaction_id},
            target,
        )

    def classify_external_operation(
        self,
        capture_context: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> ExternalOperationClassification:
        self._require_external_context(capture_context, invocation, declaration)
        return ExternalOperationClassification(ExternalOperationDisposition.ABSENT)

    def prepare_external_operation(
        self,
        capture_context: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> dict[str, object]:
        return self._require_external_context(capture_context, invocation, declaration)

    def finalize_external_operation(
        self,
        prepared_context: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> ProtectedOperationAdapterResult:
        self._require_external_context(prepared_context, invocation, declaration)
        return ProtectedOperationAdapterResult({"ok": True})

    def rollback_external_operation(
        self,
        operation_context: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> bool:
        self._require_external_context(operation_context, invocation, declaration)
        return True

    def _require_external_context(
        self,
        value: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
    ) -> dict[str, object]:
        source = _mapping(value)
        if (
            self._operation_id not in {MEDIA_SOURCE_ATTACH, PROJECT_TAKES_ATTACH}
            or getattr(declaration, "id", None) != self._operation_id
            or not isinstance(invocation, ExternalOperationInvocation)
            or set(source) != {"externalTransactionId"}
            or source["externalTransactionId"] != invocation.transaction_id
        ):
            raise DirectorManagedMediaPrivacyError()
        return {"externalTransactionId": invocation.transaction_id}

    def bind_source_with_dependencies(
        self,
        resolved: object,
        declaration: object,
        dependencies: object,
    ):
        if self._operation_id not in {MEDIA_SOURCE_VIEW, MEDIA_SOURCE_PREVIEW} or getattr(declaration, "id", None) != self._operation_id:
            raise DirectorManagedMediaPrivacyError()
        try:
            folder_settings = dependencies.singleton(MEDIA_FOLDER_SETTINGS_ID)
        except Exception:
            raise DirectorManagedMediaPrivacyError() from None
        return self._service.bind_source(
            resolved,
            self._operation_id,
            folder_settings=folder_settings,
        )


def build_director_media_server_adapters(
    service: DirectorManagedMediaService,
) -> dict[str, object]:
    return {
        MEDIA_OPERATION_ADAPTER_IDS[operation_id]: DirectorMediaOperationAdapter(
            service,
            operation_id,
        )
        for operation_id in MEDIA_OPERATION_IDS
    }


def _attach_source_to_timeline(
    value: object,
    *,
    item_id: object,
    asset_type: str,
    path: Path,
    locator: MediaSourceLocator,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    if type(value) is not dict:
        raise DirectorManagedMediaPrivacyError()
    identifier = str(item_id or "").strip()
    if not identifier or len(identifier.encode("utf-8")) > 512:
        raise DirectorManagedMediaPrivacyError()
    timeline = normalize_video_timeline(deepcopy(value))
    field = {"Image": "image", "Video": "video", "Audio": "audio"}.get(asset_type)
    if field is None:
        raise DirectorManagedMediaPrivacyError()
    target: dict[str, object] | None = None
    if asset_type in {"Image", "Video"}:
        track = timeline.get("director_track")
        sections = track.get("sections") if isinstance(track, dict) else None
        if isinstance(sections, list):
            target = next(
                (
                    item
                    for item in sections
                    if isinstance(item, dict)
                    and item.get("item_id") == identifier
                    and item.get("type") == asset_type
                ),
                None,
            )
    else:
        tracks = timeline.get("audio_tracks")
        if isinstance(tracks, list):
            target = next(
                (
                    clip
                    for track in tracks
                    if isinstance(track, dict)
                    for clip in track.get("clips", [])
                    if isinstance(clip, dict) and clip.get("item_id") == identifier
                ),
                None,
            )
    if target is None:
        raise DirectorManagedMediaPrivacyError()
    assets = timeline.setdefault("assets", [])
    if not isinstance(assets, list):
        raise DirectorManagedMediaPrivacyError()
    used = {
        str(item.get("asset_id"))
        for item in assets
        if isinstance(item, dict) and item.get("asset_id") is not None
    }
    index = 1
    while f"asset_imported_{index:03d}" in used:
        index += 1
    asset_id = f"asset_imported_{index:03d}"
    allowed_metadata = {
        key: deepcopy(item)
        for key, item in metadata.items()
        if key in _SAFE_METADATA_FIELDS
    }
    asset = {
        "asset_id": asset_id,
        "type": asset_type,
        "source_kind": "FilePath",
        "path": str(path),
        "name": path.name,
        "mime_type": locator.content_type,
        "size_bytes": locator.size,
        "metadata": allowed_metadata,
    }
    assets.append(asset)
    target[field] = {"asset_id": asset_id}
    if "name" in target and not target.get("name"):
        target["name"] = path.name
    return normalize_video_timeline(timeline)


def migrate_legacy_media_folder_settings(
    *,
    config_dir: str | os.PathLike[str],
    singleton_handle: object,
    authorization: object,
) -> bool:
    """Move all remaining legacy folder files into one verified singleton.

    A first migration captures the three file locations under one exclusive
    legacy-writer lock.  A retry after partial source retirement validates each
    remaining file against the corresponding field of the already verified
    singleton before deleting anything else.
    """

    try:
        root = _absolute_directory(config_dir, create=True)
        with _CONFIG_WRITE_LOCK:
            directory_fd = _open_absolute_directory_fd(root, create=False)
            try:
                opened_root = os.fstat(directory_fd)
                with _exclusive_private_file_lock(
                    directory_fd,
                    _CONFIG_LOCK_FILE,
                ):
                    sources = _read_legacy_folder_sources(directory_fd)
                    if not sources:
                        return False
                    target = _legacy_folder_settings_target(sources)
                    current, revision = _read_direct_folder_singleton(
                        singleton_handle,
                        authorization,
                    )
                    if current is None:
                        receipt = _replace_direct_folder_singleton(
                            singleton_handle,
                            target,
                            0,
                            authorization,
                        )
                        current, revision = _read_direct_folder_singleton(
                            singleton_handle,
                            authorization,
                        )
                        if (
                            current != target
                            or revision != receipt
                            or receipt != 1
                        ):
                            raise DirectorManagedMediaPrivacyError()
                    else:
                        folders = current["folders"]
                        target_folders = target["folders"]
                        assert isinstance(folders, dict)
                        assert isinstance(target_folders, dict)
                        if any(
                            folders[source.media_type]
                            != target_folders[source.media_type]
                            for source in sources
                        ):
                            raise DirectorManagedMediaPrivacyError()
                        readback, readback_revision = _read_direct_folder_singleton(
                            singleton_handle,
                            authorization,
                        )
                        if readback != current or readback_revision != revision:
                            raise DirectorManagedMediaPrivacyError()
                    _verify_absolute_directory(
                        root,
                        (opened_root.st_dev, opened_root.st_ino),
                    )
                    for source in sources:
                        if _read_legacy_folder_source(
                            directory_fd,
                            source.media_type,
                            source.revision.name,
                        ) != source:
                            raise DirectorManagedMediaPrivacyError()
                        os.unlink(source.revision.name, dir_fd=directory_fd)
                    os.fsync(directory_fd)
                    return True
            finally:
                os.close(directory_fd)
    except DirectorManagedMediaPrivacyError:
        raise
    except BaseException as error:
        if not isinstance(error, Exception):
            raise
        raise DirectorManagedMediaPrivacyError() from None


def _read_direct_folder_singleton(
    singleton_handle: object,
    authorization: object,
) -> tuple[dict[str, object] | None, int]:
    status_method = getattr(singleton_handle, "status", None)
    reveal_method = getattr(singleton_handle, "reveal_field", None)
    if not callable(status_method) or not callable(reveal_method):
        raise DirectorManagedMediaPrivacyError()
    status = status_method(MEDIA_FOLDER_SETTINGS_ID)
    exists = getattr(status, "exists", None)
    revision = getattr(status, "revision", None)
    if type(exists) is not bool or type(revision) is not int or revision < 0:
        raise DirectorManagedMediaPrivacyError()
    if not exists:
        if revision != 0:
            raise DirectorManagedMediaPrivacyError()
        return None, 0
    revealed = reveal_method(MEDIA_FOLDER_SETTINGS_ID, authorization)
    revealed_revision = getattr(revealed, "revision", None)
    if type(revealed_revision) is not int or revealed_revision < 1:
        raise DirectorManagedMediaPrivacyError()
    return (
        normalize_media_folder_settings(getattr(revealed, "value", None)),
        revealed_revision,
    )


def _replace_direct_folder_singleton(
    singleton_handle: object,
    value: object,
    expected_revision: int,
    authorization: object,
) -> int:
    replace_method = getattr(singleton_handle, "replace_field", None)
    if not callable(replace_method):
        raise DirectorManagedMediaPrivacyError()
    receipt = replace_method(
        MEDIA_FOLDER_SETTINGS_ID,
        normalize_media_folder_settings(value),
        expected_revision,
        authorization,
    )
    revision = getattr(receipt, "revision", None)
    if type(revision) is not int or revision != expected_revision + 1:
        raise DirectorManagedMediaPrivacyError()
    return revision


def _read_legacy_folder_sources(
    directory_fd: int,
) -> tuple[_LegacyFolderSource, ...]:
    result = []
    for media_type, definition in MEDIA_DEFINITIONS.items():
        name = str(definition["config_name"])
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        result.append(_read_legacy_folder_source(directory_fd, media_type, name))
    return tuple(result)


def _read_legacy_folder_source(
    directory_fd: int,
    media_type: str,
    name: str,
) -> _LegacyFolderSource:
    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_LEGACY_FOLDER_CONFIG_BYTES:
        raise DirectorManagedMediaPrivacyError()
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size > MAX_LEGACY_FOLDER_CONFIG_BYTES
        ):
            raise DirectorManagedMediaPrivacyError()
        with os.fdopen(os.dup(descriptor), "rb") as source:
            encoded = source.read(MAX_LEGACY_FOLDER_CONFIG_BYTES + 1)
        if (
            len(encoded) > MAX_LEGACY_FOLDER_CONFIG_BYTES
            or _stat_revision(os.fstat(descriptor)) != _stat_revision(opened)
        ):
            raise DirectorManagedMediaPrivacyError()
        try:
            payload = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
            )
        except (UnicodeError, json.JSONDecodeError, ValueError):
            raise DirectorManagedMediaPrivacyError() from None
        folders = _legacy_folder_payload(payload)
        return _LegacyFolderSource(
            _media_type(media_type),
            BoundFileRevision(
                name,
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            ),
            hashlib.sha256(encoded).digest(),
            tuple(folders),
        )
    finally:
        os.close(descriptor)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _legacy_folder_payload(value: object) -> list[MediaFolder]:
    if (
        type(value) is not dict
        or set(value) != {"folders", "schema", "version"}
        or value.get("schema") != MEDIA_FOLDER_CONFIG_SCHEMA
        or type(value.get("version")) is not int
        or value.get("version") != MEDIA_FOLDER_CONFIG_VERSION
        or type(value.get("folders")) is not list
    ):
        raise DirectorManagedMediaPrivacyError()
    result = []
    for item in value["folders"]:
        if (
            type(item) is not dict
            or set(item) != {"alias", "enabled", "path"}
            or not isinstance(item.get("alias"), str)
            or not isinstance(item.get("path"), str)
            or type(item.get("enabled")) is not bool
        ):
            raise DirectorManagedMediaPrivacyError()
        result.append(
            MediaFolder(
                _safe_alias(item["alias"]),
                _absolute_directory(
                    item["path"],
                    create=False,
                    require_exists=False,
                ),
                item["enabled"],
            )
        )
    return result


def _legacy_folder_settings_target(
    sources: Sequence[_LegacyFolderSource],
) -> dict[str, object]:
    by_type = {source.media_type: source for source in sources}
    value = {
        "schema": MEDIA_FOLDER_SETTINGS_SCHEMA,
        "version": 1,
        "folders": {
            media_type: [
                {
                    "alias": folder.alias,
                    "path": str(folder.path),
                    "enabled": folder.enabled,
                }
                for folder in (
                    by_type[media_type].folders
                    if media_type in by_type
                    else ()
                )
            ]
            for media_type in MEDIA_DEFINITIONS
        },
    }
    try:
        return normalize_media_folder_settings(value)
    except Exception:
        raise DirectorManagedMediaPrivacyError() from None


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise DirectorManagedMediaPrivacyError()
    return dict(value)


def _resolved_value(references: Mapping[str, object], name: str) -> object:
    value = references.get(name)
    if value is None or not hasattr(value, "value"):
        raise DirectorManagedMediaPrivacyError()
    return value.value


def _media_type(value: object) -> str:
    kind = str(value or "").strip().lower()
    if kind not in MEDIA_DEFINITIONS:
        raise DirectorManagedMediaPrivacyError()
    return kind


def _folder(value: MediaFolder) -> MediaFolder:
    if not isinstance(value, MediaFolder):
        raise DirectorManagedMediaPrivacyError()
    return MediaFolder(_safe_alias(value.alias), _absolute_directory(value.path, create=False, require_exists=False), bool(value.enabled))


def _safe_alias(value: object) -> str:
    alias = str(value or "").strip()
    if _ALIAS.fullmatch(alias) is None:
        raise DirectorManagedMediaPrivacyError()
    return alias


def _alias_for(path: Path, folders: Sequence[MediaFolder]) -> str:
    base = re.sub(r"[^A-Za-z0-9_. -]+", "_", path.name).strip(" ._-") or "folder"
    aliases = {item.alias for item in folders}
    for index in range(1, 1000):
        suffix = "" if index == 1 else f" {index}"
        candidate = _safe_alias(f"{base[:80-len(suffix)]}{suffix}")
        if candidate not in aliases:
            return candidate
    raise DirectorManagedMediaPrivacyError()


def _absolute_directory(
    value: object,
    *,
    create: bool,
    require_exists: bool = True,
) -> Path:
    try:
        raw = Path(os.path.abspath(os.path.expanduser(os.fspath(value))))
    except (TypeError, ValueError):
        raise DirectorManagedMediaPrivacyError() from None
    descriptor: int | None = None
    try:
        descriptor = _open_absolute_directory_fd(raw, create=create)
    except FileNotFoundError:
        if require_exists:
            raise DirectorManagedMediaPrivacyError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return raw


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _inside(root: Path, candidate: Path, *, require_exists: bool) -> Path:
    root = Path(os.path.abspath(root))
    candidate = Path(os.path.abspath(candidate))
    try:
        if os.path.commonpath((str(root), str(candidate))) != str(root):
            raise DirectorManagedMediaPrivacyError()
    except ValueError:
        raise DirectorManagedMediaPrivacyError() from None
    if require_exists and not candidate.exists():
        raise DirectorManagedMediaPrivacyError()
    return candidate


def _source_locator_from_stat(
    media_type: str,
    root: Path,
    relative: tuple[str, ...],
    info: os.stat_result,
) -> MediaSourceLocator:
    if not relative or any(part in {"", ".", ".."} for part in relative):
        raise DirectorManagedMediaPrivacyError()
    suffix = Path(relative[-1]).suffix.lower()
    if suffix not in MEDIA_DEFINITIONS[media_type]["extensions"]:
        raise DirectorManagedMediaPrivacyError()
    if not stat.S_ISREG(info.st_mode):
        raise DirectorManagedMediaPrivacyError()
    return MediaSourceLocator(
        media_type,
        root,
        relative,
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        mimetypes.guess_type(relative[-1])[0] or "application/octet-stream",
    )


def _scan_sources(
    root: Path,
    media_type: str,
    *,
    recursive: bool,
    metadata_reader: Callable[[object, str], Mapping[str, object]] | None = None,
    expected_root_binding: tuple[int, int] | None = None,
) -> list[tuple[MediaSourceLocator, Path, dict[str, object]]]:
    media_type = _media_type(media_type)
    extensions = MEDIA_DEFINITIONS[media_type]["extensions"]
    root_fd: int | None = None
    binding: tuple[int, int] | None = None
    values: list[tuple[MediaSourceLocator, Path, dict[str, object]]] = []
    try:
        try:
            root_fd = _open_absolute_directory_fd(root, create=False)
        except FileNotFoundError:
            return []
        opened_root = os.fstat(root_fd)
        binding = (opened_root.st_dev, opened_root.st_ino)
        if expected_root_binding is not None and binding != expected_root_binding:
            raise DirectorManagedMediaPrivacyError()

        def walk(directory_fd: int, parts: tuple[str, ...]) -> None:
            for name in sorted(os.listdir(directory_fd)):
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISDIR(before.st_mode):
                    if not recursive:
                        continue
                    child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
                    try:
                        opened = os.fstat(child_fd)
                        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                            raise DirectorManagedMediaPrivacyError()
                        walk(child_fd, (*parts, name))
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(before.st_mode) or Path(name).suffix.lower() not in extensions:
                    continue
                file_fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
                try:
                    opened = os.fstat(file_fd)
                    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise DirectorManagedMediaPrivacyError()
                    relative = (*parts, name)
                    locator = _source_locator_from_stat(media_type, root, relative, opened)
                    metadata: dict[str, object] = {}
                    if metadata_reader is not None:
                        with os.fdopen(os.dup(file_fd), "rb") as source:
                            metadata = _safe_metadata(metadata_reader(source, media_type))
                    after = os.fstat(file_fd)
                    if _stat_revision(after) != _stat_revision(opened):
                        raise DirectorManagedMediaPrivacyError()
                    values.append((locator, root.joinpath(*relative), metadata))
                finally:
                    os.close(file_fd)

        walk(root_fd, ())
        _verify_absolute_directory(root, binding)
        return values
    except DirectorManagedMediaPrivacyError:
        raise
    except (OSError, TypeError, ValueError):
        raise DirectorManagedMediaPrivacyError() from None
    finally:
        if root_fd is not None:
            os.close(root_fd)


def _source_payload(
    locator: MediaSourceLocator,
    path: Path,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    return {
        "filename": "/".join(locator.relative_parts),
        "path": str(path),
        "name": path.name,
        "mtime": locator.mtime_ns / 1_000_000_000,
        "size": locator.size,
        "mime_type": locator.content_type,
        **dict(metadata),
    }


def _revalidate_source(locator: MediaSourceLocator, allowed_roots: Sequence[Path]) -> Path:
    if locator.media_type not in MEDIA_DEFINITIONS or not any(
        _contains(root, locator.root) for root in allowed_roots
    ):
        raise DirectorManagedMediaPrivacyError()
    path = locator.root.joinpath(*locator.relative_parts)
    with _open_relative_file(locator.root, locator.relative_parts) as (_root_fd, _parent_fd, file_fd):
        current = os.fstat(file_fd)
        if _stat_revision(current) != (
            locator.device,
            locator.inode,
            locator.mtime_ns,
            locator.size,
        ):
            raise DirectorManagedMediaPrivacyError()
    return path


def _contains(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath((str(root), str(candidate))) == str(root)
    except ValueError:
        return False


def _stat_revision(value: os.stat_result) -> tuple[int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_mtime_ns, value.st_size


def _safe_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(key not in _SAFE_METADATA_FIELDS for key in value):
        raise DirectorManagedMediaPrivacyError()
    result: dict[str, object] = {}
    for key, item in value.items():
        if item is None and key == "duration_seconds":
            result[key] = None
        elif isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 2_147_483_647:
            result[key] = item
        elif key == "duration_seconds" and isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) and item >= 0:
            result[key] = float(item)
        else:
            raise DirectorManagedMediaPrivacyError()
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError):
        raise DirectorManagedMediaPrivacyError() from None
    return result


def _open_absolute_directory_fd(path: Path, *, create: bool) -> int:
    absolute = Path(os.path.abspath(path))
    if not absolute.parts or absolute.parts[0] != os.path.sep:
        raise DirectorManagedMediaPrivacyError()
    current_fd = os.open(os.path.sep, _DIRECTORY_FLAGS)
    try:
        for part in absolute.parts[1:]:
            try:
                before = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=current_fd)
                before = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise DirectorManagedMediaPrivacyError()
            next_fd, opened = _open_and_fstat_at(
                part,
                _DIRECTORY_FLAGS,
                dir_fd=current_fd,
            )
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                os.close(next_fd)
                raise DirectorManagedMediaPrivacyError()
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_and_fstat_at(
    name: str,
    flags: int,
    *,
    dir_fd: int,
) -> tuple[int, os.stat_result]:
    descriptor = os.open(name, flags, dir_fd=dir_fd)
    try:
        return descriptor, os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _exclusive_private_file_lock(directory_fd: int, name: str):
    descriptor = os.open(
        name,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=directory_fd,
    )
    locked = False
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise DirectorManagedMediaPrivacyError()
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise DirectorManagedMediaPrivacyError()
        yield descriptor
    except DirectorManagedMediaPrivacyError:
        raise
    except OSError:
        raise DirectorManagedMediaPrivacyError() from None
    finally:
        if locked:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def _verify_absolute_directory(path: Path, expected: tuple[int, int]) -> None:
    descriptor: int | None = None
    try:
        descriptor = _open_absolute_directory_fd(path, create=False)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != expected:
            raise DirectorManagedMediaPrivacyError()
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _directory_binding(
    path: Path,
    *,
    missing_ok: bool = False,
) -> tuple[int, int] | None:
    descriptor: int | None = None
    try:
        descriptor = _open_absolute_directory_fd(path, create=False)
        opened = os.fstat(descriptor)
        return opened.st_dev, opened.st_ino
    except FileNotFoundError:
        if missing_ok:
            return None
        raise DirectorManagedMediaPrivacyError() from None
    except DirectorManagedMediaPrivacyError:
        raise
    except OSError:
        raise DirectorManagedMediaPrivacyError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def _open_relative_parent(
    root: Path,
    relative_parts: Sequence[str],
    *,
    expected_root_binding: tuple[int, int] | None = None,
):
    if not relative_parts or any(part in {"", ".", ".."} or os.path.sep in part for part in relative_parts):
        raise DirectorManagedMediaPrivacyError()
    root_fd = _open_absolute_directory_fd(root, create=False)
    parent_fd: int | None = None
    try:
        opened_root = os.fstat(root_fd)
        binding = (opened_root.st_dev, opened_root.st_ino)
        if expected_root_binding is not None and binding != expected_root_binding:
            raise DirectorManagedMediaPrivacyError()
        parent_fd = os.dup(root_fd)
        for part in relative_parts[:-1]:
            before = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise DirectorManagedMediaPrivacyError()
            next_fd, opened = _open_and_fstat_at(
                part,
                _DIRECTORY_FLAGS,
                dir_fd=parent_fd,
            )
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                os.close(next_fd)
                raise DirectorManagedMediaPrivacyError()
            os.close(parent_fd)
            parent_fd = next_fd
        yield root_fd, parent_fd
        _verify_absolute_directory(root, binding)
    except DirectorManagedMediaPrivacyError:
        raise
    except OSError:
        raise DirectorManagedMediaPrivacyError() from None
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        os.close(root_fd)


@contextmanager
def _open_relative_file(root: Path, relative_parts: Sequence[str]):
    with _open_relative_parent(root, relative_parts) as (root_fd, parent_fd):
        name = relative_parts[-1]
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise DirectorManagedMediaPrivacyError()
        file_fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
        try:
            opened = os.fstat(file_fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise DirectorManagedMediaPrivacyError()
            yield root_fd, parent_fd, file_fd
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _stat_revision(current) != _stat_revision(opened):
                raise DirectorManagedMediaPrivacyError()
        finally:
            os.close(file_fd)


def _regular_file_exists(root: Path, name: str) -> bool:
    descriptor = _open_absolute_directory_fd(root, create=False)
    try:
        try:
            value = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(value.st_mode):
            raise DirectorManagedMediaPrivacyError()
        return True
    finally:
        os.close(descriptor)


def _read_json_at_with_revision(
    parent_fd: int,
    name: str,
) -> tuple[dict[str, object], BoundFileRevision]:
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise DirectorManagedMediaPrivacyError()
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise DirectorManagedMediaPrivacyError()
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as source:
            value = json.load(source)
        if not isinstance(value, dict) or _stat_revision(os.fstat(descriptor)) != _stat_revision(opened):
            raise DirectorManagedMediaPrivacyError()
        return value, BoundFileRevision(
            name,
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
    except json.JSONDecodeError:
        raise DirectorManagedMediaPrivacyError() from None
    finally:
        os.close(descriptor)


def _read_json_at(parent_fd: int, name: str) -> dict[str, object]:
    return _read_json_at_with_revision(parent_fd, name)[0]


def _read_json_file(root: Path, name: str) -> dict[str, object]:
    descriptor = _open_absolute_directory_fd(root, create=False)
    try:
        opened = os.fstat(descriptor)
        value = _read_json_at(descriptor, name)
        _verify_absolute_directory(root, (opened.st_dev, opened.st_ino))
        return value
    finally:
        os.close(descriptor)


def _replace_json_file(root: Path, name: str, value: object) -> None:
    with _CONFIG_WRITE_LOCK:
        _replace_json_file_locked(root, name, value)


def _replace_json_file_locked(root: Path, name: str, value: object) -> None:
    try:
        encoded = json.dumps(value, indent=2, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise DirectorManagedMediaPrivacyError() from None
    directory_fd = _open_absolute_directory_fd(root, create=True)
    try:
        with _exclusive_private_file_lock(directory_fd, _CONFIG_LOCK_FILE):
            _replace_json_file_under_lock(
                root,
                directory_fd,
                name,
                value,
                encoded,
            )
    finally:
        os.close(directory_fd)


def _replace_json_file_under_lock(
    root: Path,
    directory_fd: int,
    name: str,
    value: object,
    encoded: bytes,
) -> None:
    expected_target: tuple[int, int] | None = None
    temporary = f".{name}.{os.getpid()}.{id(value)}.tmp"
    temp_fd: int | None = None
    try:
        opened_root = os.fstat(directory_fd)
        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode):
                raise DirectorManagedMediaPrivacyError()
            expected_target = (current.st_dev, current.st_ino)
        except FileNotFoundError:
            pass
        temp_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(temp_fd, "wb", closefd=False) as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        temp_stat = os.fstat(temp_fd)
        if not stat.S_ISREG(temp_stat.st_mode):
            raise DirectorManagedMediaPrivacyError()
        _verify_absolute_directory(root, (opened_root.st_dev, opened_root.st_ino))
        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            current_target = (current.st_dev, current.st_ino)
        except FileNotFoundError:
            current_target = None
        if current_target != expected_target:
            raise DirectorManagedMediaPrivacyError()
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = ""
    except DirectorManagedMediaPrivacyError:
        raise
    except OSError:
        raise DirectorManagedMediaPrivacyError() from None
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _delete_take_files(root: Path, locator: ProjectTakeLocator) -> tuple[bool, int]:
    if not locator.sidecars:
        raise DirectorManagedMediaPrivacyError()
    expected_root = (locator.root_device, locator.root_inode)
    with _open_relative_parent(
        root,
        locator.source.relative_parts,
        expected_root_binding=expected_root,
    ) as (_root_fd, parent_fd):
        with _exclusive_private_file_lock(parent_fd, _TAKE_DELETE_LOCK_FILE):
            return _delete_take_files_under_lock(
                root,
                locator,
                parent_fd,
                expected_root,
            )


def _delete_take_files_under_lock(
    root: Path,
    locator: ProjectTakeLocator,
    parent_fd: int,
    expected_root: tuple[int, int],
) -> tuple[bool, int]:
    media_name = locator.source.relative_parts[-1]
    opened: list[tuple[str, int, os.stat_result]] = []
    media_missing = False
    try:
        try:
            media_before = os.stat(media_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            media_missing = True
        else:
            if not stat.S_ISREG(media_before.st_mode):
                raise DirectorManagedMediaPrivacyError()
            media_fd, media_current = _open_and_fstat_at(
                media_name,
                _FILE_FLAGS,
                dir_fd=parent_fd,
            )
            if (
                (media_current.st_dev, media_current.st_ino)
                != (media_before.st_dev, media_before.st_ino)
                or _stat_revision(media_current) != (
                    locator.source.device,
                    locator.source.inode,
                    locator.source.mtime_ns,
                    locator.source.size,
                )
            ):
                os.close(media_fd)
                raise DirectorManagedMediaPrivacyError()
            opened.append((media_name, media_fd, media_current))

        for sidecar in locator.sidecars:
            try:
                before = os.stat(sidecar.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                raise DirectorManagedMediaPrivacyError() from None
            if not stat.S_ISREG(before.st_mode):
                raise DirectorManagedMediaPrivacyError()
            descriptor, current = _open_and_fstat_at(
                sidecar.name,
                _FILE_FLAGS,
                dir_fd=parent_fd,
            )
            if (
                (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
                or _stat_revision(current) != (
                    sidecar.device,
                    sidecar.inode,
                    sidecar.mtime_ns,
                    sidecar.size,
                )
            ):
                os.close(descriptor)
                raise DirectorManagedMediaPrivacyError()
            opened.append((sidecar.name, descriptor, current))

        _verify_absolute_directory(root, expected_root)
        for name, _descriptor, expected in opened:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _stat_revision(current) != _stat_revision(expected):
                raise DirectorManagedMediaPrivacyError()
        deleted = _quarantine_and_delete_opened_files(
            root,
            expected_root,
            parent_fd,
            opened,
        )
        return media_missing, deleted
    finally:
        for _name, descriptor, _expected in opened:
            os.close(descriptor)


def _quarantine_and_delete_opened_files(
    root: Path,
    expected_root: tuple[int, int],
    parent_fd: int,
    opened: Sequence[tuple[str, int, os.stat_result]],
) -> int:
    quarantine_name, quarantine_fd = _create_private_delete_quarantine(parent_fd)
    quarantined: list[tuple[str, str, os.stat_result]] = []
    try:
        for index, (name, _descriptor, expected) in enumerate(opened):
            quarantine = f"leaf-{index}-{secrets.token_urlsafe(18)}"
            os.rename(
                name,
                quarantine,
                src_dir_fd=parent_fd,
                dst_dir_fd=quarantine_fd,
            )
            quarantined.append((name, quarantine, expected))
            moved = os.stat(
                quarantine,
                dir_fd=quarantine_fd,
                follow_symlinks=False,
            )
            if _stat_revision(moved) != _stat_revision(expected):
                raise DirectorManagedMediaPrivacyError()

        _verify_absolute_directory(root, expected_root)
        for _name, quarantine, expected in quarantined:
            current = os.stat(
                quarantine,
                dir_fd=quarantine_fd,
                follow_symlinks=False,
            )
            if _stat_revision(current) != _stat_revision(expected):
                raise DirectorManagedMediaPrivacyError()
        for _name, quarantine, _expected in quarantined:
            os.unlink(quarantine, dir_fd=quarantine_fd)
        return len(quarantined)
    except BaseException:
        _restore_delete_quarantine(parent_fd, quarantine_fd, quarantined)
        raise
    finally:
        os.close(quarantine_fd)
        try:
            os.rmdir(quarantine_name, dir_fd=parent_fd)
        except OSError:
            pass


def _create_private_delete_quarantine(parent_fd: int) -> tuple[str, int]:
    for _attempt in range(8):
        name = f".helto-delete-{secrets.token_urlsafe(18)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        descriptor: int | None = None
        try:
            descriptor, opened = _open_and_fstat_at(
                name,
                _DIRECTORY_FLAGS,
                dir_fd=parent_fd,
            )
            if (
                not stat.S_ISDIR(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o700
            ):
                raise DirectorManagedMediaPrivacyError()
            return name, descriptor
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
    raise DirectorManagedMediaPrivacyError()


def _restore_delete_quarantine(
    parent_fd: int,
    quarantine_fd: int,
    quarantined: Sequence[tuple[str, str, os.stat_result]],
) -> None:
    for name, quarantine, _expected in reversed(quarantined):
        try:
            os.link(
                quarantine,
                name,
                src_dir_fd=quarantine_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except OSError:
            continue
        try:
            os.unlink(quarantine, dir_fd=quarantine_fd)
        except OSError:
            pass


def _project_take_locator(
    project_record_id: str,
    shot_id: str,
    root: Path,
    path: Path,
    source: MediaSourceLocator,
    root_binding: tuple[int, int],
) -> ProjectTakeLocator | None:
    if source.root != root or path != root.joinpath(*source.relative_parts):
        return None
    first_name = f"{Path(source.relative_parts[-1]).stem}.helto_take.json"
    second_name = f"{source.relative_parts[-1]}.helto_take.json"
    sidecar_names = (first_name, second_name)
    validated: list[BoundFileRevision] = []
    take_id = ""
    try:
        with _open_relative_parent(
            root,
            source.relative_parts,
            expected_root_binding=root_binding,
        ) as (_root_fd, parent_fd):
            for name in sidecar_names:
                try:
                    payload, revision = _read_json_at_with_revision(parent_fd, name)
                except FileNotFoundError:
                    continue
                except DirectorManagedMediaPrivacyError:
                    continue
                candidate_take_id = _validated_take_id(payload, shot_id)
                if not candidate_take_id or (take_id and candidate_take_id != take_id):
                    continue
                take_id = candidate_take_id
                validated.append(revision)
        if not validated or not take_id:
            return None
    except (DirectorManagedMediaPrivacyError, OSError, TypeError, ValueError):
        return None
    return ProjectTakeLocator(
        project_record_id,
        shot_id,
        source,
        tuple(validated),
        take_id,
        root_binding[0],
        root_binding[1],
    )


def _validated_take_id(payload: Mapping[str, object], shot_id: str) -> str:
    registration = payload.get("registration")
    if not isinstance(registration, Mapping):
        return ""
    raw_shot_ids = registration.get("shot_ids", ())
    if not isinstance(raw_shot_ids, (list, tuple)):
        return ""
    shot_ids = {str(item) for item in raw_shot_ids}
    if registration.get("shot_id") is not None:
        shot_ids.add(str(registration["shot_id"]))
    if shot_id not in shot_ids:
        return ""
    take = registration.get("take")
    return str(take.get("take_id") or "") if isinstance(take, Mapping) else ""


def _safe_component(value: object) -> str:
    text = str(value or "").strip()
    sanitized = _SAFE_PART.sub("_", text).strip("._-")[:96]
    return sanitized if sanitized == text else ""


def _prune_empty(
    root: Path,
    start: Path,
    *,
    expected_root_binding: tuple[int, int] | None = None,
) -> None:
    try:
        relative = start.relative_to(root).parts
    except ValueError:
        raise DirectorManagedMediaPrivacyError() from None
    while relative:
        root_fd = _open_absolute_directory_fd(root, create=False)
        parent_fd: int | None = None
        try:
            opened_root = os.fstat(root_fd)
            binding = (opened_root.st_dev, opened_root.st_ino)
            if expected_root_binding is not None and binding != expected_root_binding:
                raise DirectorManagedMediaPrivacyError()
            parent_fd = os.dup(root_fd)
            for part in relative[:-1]:
                before = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISDIR(before.st_mode):
                    raise DirectorManagedMediaPrivacyError()
                next_fd, opened = _open_and_fstat_at(
                    part,
                    _DIRECTORY_FLAGS,
                    dir_fd=parent_fd,
                )
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    os.close(next_fd)
                    raise DirectorManagedMediaPrivacyError()
                os.close(parent_fd)
                parent_fd = next_fd
            try:
                os.rmdir(relative[-1], dir_fd=parent_fd)
            except OSError:
                return
            _verify_absolute_directory(root, binding)
        finally:
            if parent_fd is not None:
                os.close(parent_fd)
            os.close(root_fd)
        relative = relative[:-1]


__all__ = [
    "AUDIO_EXTENSIONS",
    "DirectorManagedMediaPrivacyError",
    "DirectorManagedMediaService",
    "DirectorMediaOperationAdapter",
    "IMAGE_EXTENSIONS",
    "MAX_OPERATION_REFERENCES",
    "MAX_PROJECT_TAKE_REFERENCES",
    "MEDIA_FOLDER_REFERENCE_KIND",
    "MEDIA_FOLDERS_ADD",
    "MEDIA_FOLDERS_LIST",
    "MEDIA_FOLDERS_REMOVE",
    "MEDIA_ITEMS_LIST",
    "MEDIA_OPERATION_ADAPTER_IDS",
    "MEDIA_OPERATION_IDS",
    "MEDIA_OPERATION_RESOURCE_ID",
    "MEDIA_PRIVACY_ACTIVATION_GAPS",
    "MEDIA_PROTECTED_OPERATIONS",
    "MEDIA_SOURCE_PREVIEW",
    "MEDIA_SOURCE_REFERENCE_KIND",
    "MEDIA_SOURCE_VIEW",
    "MediaFolder",
    "MediaFolderLocator",
    "MediaSourceLocator",
    "PROJECT_TAKES_DELETE",
    "PROJECT_TAKES_LIST",
    "PROJECT_TAKE_REFERENCE_KIND",
    "ProjectTakeLocator",
    "VIDEO_EXTENSIONS",
    "build_director_media_privacy_profile",
    "build_director_media_server_adapters",
]
