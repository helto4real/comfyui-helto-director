"""Managed artifact composition for Director timeline media caches."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import io
import json
import math
import os
import stat
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol

from helto_privacy import (
    AdapterSlot,
    ArtifactDeclaration,
    ArtifactRetention,
    PrivacyProfile,
    ProfileResource,
    ResourceKind,
    generate_artifact_owner_id,
    run_blocking_adapter,
)

from ..media_domain import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    legacy_cache_root,
)

from .managed_privacy import (
    DIRECTOR_DISTRIBUTION,
    DIRECTOR_PROFILE_ID,
    GLOBAL_SCOPE_ID,
    build_director_timeline_privacy_profile,
)


_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd


MEDIA_ARTIFACT_RESOURCE_ID = "timeline-media-cache"
THUMBNAIL_ARTIFACT_ADAPTER_ID = "director-thumbnail-artifact"
WAVEFORM_ARTIFACT_ADAPTER_ID = "director-waveform-artifact"
THUMBNAIL_ARTIFACT_KIND = "thumbnail"
WAVEFORM_ARTIFACT_KIND = "waveform"
THUMBNAIL_ARTIFACT_PURPOSE = "timeline-thumbnail-cache"
WAVEFORM_ARTIFACT_PURPOSE = "timeline-waveform-cache"

DEFAULT_THUMBNAIL_SIZE = 320
DEFAULT_WAVEFORM_PEAKS = 96
MIN_WAVEFORM_PEAKS = 16
MAX_WAVEFORM_PEAKS = 512
MAX_WAVEFORM_DURATION_SECONDS = 31_536_000
MAX_WAVEFORM_SAMPLE_RATE = 768_000
MAX_WAVEFORM_CHANNELS = 64

THUMBNAIL_LEGACY_DERIVATIVES = (
    "thumbnails/*.webp",
    "thumbnails/*.webp.tmp",
    "thumbnails/.*.webp.*.tmp",
    "thumbnails/*.webp.enc",
    "thumbnails/*.webp.enc.tmp",
    "thumbnails/.*.webp.enc.*.tmp",
)
WAVEFORM_LEGACY_DERIVATIVES = (
    "waveforms/*.json",
    "waveforms/*.json.tmp",
    "waveforms/.*.json.*.tmp",
    "waveforms/*.json.enc",
    "waveforms/*.json.enc.tmp",
    "waveforms/.*.json.enc.*.tmp",
)

MEDIA_ARTIFACT_RESOURCE = ProfileResource(
    MEDIA_ARTIFACT_RESOURCE_ID,
    ResourceKind.ARTIFACT,
    (THUMBNAIL_ARTIFACT_ADAPTER_ID, WAVEFORM_ARTIFACT_ADAPTER_ID),
)
THUMBNAIL_ARTIFACT_ADAPTER_SLOT = AdapterSlot(
    THUMBNAIL_ARTIFACT_ADAPTER_ID,
    ResourceKind.ARTIFACT,
    MEDIA_ARTIFACT_RESOURCE_ID,
)
WAVEFORM_ARTIFACT_ADAPTER_SLOT = AdapterSlot(
    WAVEFORM_ARTIFACT_ADAPTER_ID,
    ResourceKind.ARTIFACT,
    MEDIA_ARTIFACT_RESOURCE_ID,
)
THUMBNAIL_ARTIFACT_DECLARATION = ArtifactDeclaration(
    THUMBNAIL_ARTIFACT_KIND,
    MEDIA_ARTIFACT_RESOURCE_ID,
    GLOBAL_SCOPE_ID,
    THUMBNAIL_ARTIFACT_PURPOSE,
    THUMBNAIL_ARTIFACT_ADAPTER_ID,
    1,
    ArtifactRetention.REGENERABLE_CACHE,
    ("preview",),
    media_type="image/webp",
)
WAVEFORM_ARTIFACT_DECLARATION = ArtifactDeclaration(
    WAVEFORM_ARTIFACT_KIND,
    MEDIA_ARTIFACT_RESOURCE_ID,
    GLOBAL_SCOPE_ID,
    WAVEFORM_ARTIFACT_PURPOSE,
    WAVEFORM_ARTIFACT_ADAPTER_ID,
    1,
    ArtifactRetention.REGENERABLE_CACHE,
    ("preview",),
    media_type="application/json",
)
MEDIA_ARTIFACT_DECLARATIONS = (
    THUMBNAIL_ARTIFACT_DECLARATION,
    WAVEFORM_ARTIFACT_DECLARATION,
)


@dataclass(frozen=True, slots=True)
class DirectorMediaArtifactContract:
    """Consumer-owned facts intentionally outside the generic artifact schema."""

    declaration: ArtifactDeclaration
    owner_policy: str
    legacy_derivatives: tuple[str, ...]
    requires_allowed_root_fd_validation: bool = True


MEDIA_ARTIFACT_CONTRACTS = (
    DirectorMediaArtifactContract(
        THUMBNAIL_ARTIFACT_DECLARATION,
        "source-and-normalized-parameters",
        THUMBNAIL_LEGACY_DERIVATIVES,
    ),
    DirectorMediaArtifactContract(
        WAVEFORM_ARTIFACT_DECLARATION,
        "source-and-normalized-parameters",
        WAVEFORM_LEGACY_DERIVATIVES,
    ),
)


def build_director_media_artifact_privacy_profile(
    base_profile: PrivacyProfile | None = None,
) -> PrivacyProfile:
    """Compose D4 declarations into the D2 profile.

    Activation gap: this function must not be installed by a live bootstrap
    until the complete Director profile, route/call-site cutover, and explicit
    public-mode behavior have been designed and activated atomically.
    """

    base = base_profile or build_director_timeline_privacy_profile()
    if base.id != DIRECTOR_PROFILE_ID or base.distribution != DIRECTOR_DISTRIBUTION:
        raise ValueError("Director media artifacts require the Director profile.")
    return replace(
        base,
        resources=(*base.resources, MEDIA_ARTIFACT_RESOURCE),
        server_adapters=(
            *base.server_adapters,
            THUMBNAIL_ARTIFACT_ADAPTER_SLOT,
            WAVEFORM_ARTIFACT_ADAPTER_SLOT,
        ),
        artifacts=(*base.artifacts, *MEDIA_ARTIFACT_DECLARATIONS),
    )


class DirectorManagedMediaArtifactError(RuntimeError):
    """Product-data-free error for managed media artifact failures."""

    def __init__(self, message: str = "Director media artifact operation failed.") -> None:
        super().__init__(message)


class DirectorMediaArtifactCodecAdapter:
    """Byte codec and exhaustive retirement of one legacy cache family."""

    def __init__(
        self,
        artifact_kind: str,
        legacy_derivatives: tuple[str, ...],
        *,
        cache_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.artifact_kind = artifact_kind
        self.legacy_derivatives = legacy_derivatives
        self._cache_root = Path(cache_root) if cache_root is not None else None

    @property
    def cache_root(self) -> Path:
        if self._cache_root is not None:
            return self._cache_root
        return legacy_cache_root()

    def encode(self, value: object) -> bytes:
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("Director media artifacts must be encoded bytes.")
        return bytes(value)

    def decode(self, value: bytes) -> bytes:
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("Director media artifacts must decode to bytes.")
        return bytes(value)

    def purge_plaintext_derivatives(self, artifact_kind: str) -> None:
        if artifact_kind != self.artifact_kind:
            raise ValueError("Unknown Director media artifact kind.")
        _purge_legacy_derivatives(self.cache_root, self.legacy_derivatives)

    def prepare_mode_transition(self, *_args: object) -> None:
        return None

    def commit_mode_transition(self, *_args: object) -> None:
        return None

    def rollback_mode_transition(self, *_args: object) -> None:
        return None


def build_director_media_artifact_server_adapters(
    *,
    cache_root: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    return {
        THUMBNAIL_ARTIFACT_ADAPTER_ID: DirectorMediaArtifactCodecAdapter(
            THUMBNAIL_ARTIFACT_KIND,
            THUMBNAIL_LEGACY_DERIVATIVES,
            cache_root=cache_root,
        ),
        WAVEFORM_ARTIFACT_ADAPTER_ID: DirectorMediaArtifactCodecAdapter(
            WAVEFORM_ARTIFACT_KIND,
            WAVEFORM_LEGACY_DERIVATIVES,
            cache_root=cache_root,
        ),
    }


class _ArtifactHandle(Protocol):
    async def write(self, artifact_kind: str, owner_id: str, value: object): ...

    async def read(self, artifact_kind: str, reference: object) -> object: ...

    async def retire(self, artifact_kind: str, reference: object) -> int: ...

    async def sweep(self): ...

    async def lease(
        self,
        artifact_kind: str,
        reference: object,
        operation: str,
        authorization: object,
    ): ...


ThumbnailEncoder = Callable[[BinaryIO, str, int], bytes]
WaveformEncoder = Callable[[BinaryIO, str, int], Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class _SourceRevision:
    device: int
    inode: int
    modified_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class _ArtifactEntry:
    owner_id: str
    reference: object
    source_revision: _SourceRevision


class DirectorManagedMediaArtifacts:
    """Product compositor over shared storage, retirement, and lease handles.

    Source authorization, descriptor-safe stat checks, parameter normalization,
    media decoding, and WebP/peak encoding remain Director-owned.  The supplied
    shared handle remains the sole owner of persistence, encryption, cleanup
    ledgers, and opaque browser leases.

    This class intentionally has no privacy/public switch. The shared artifact
    handle resolves the authoritative storage mode; request parameters and
    cache keys cannot override it.
    """

    def __init__(
        self,
        handle: _ArtifactHandle,
        *,
        authorized_roots: Callable[[], tuple[str, ...] | list[str]],
        thumbnail_encoder: ThumbnailEncoder | None = None,
        waveform_encoder: WaveformEncoder | None = None,
    ) -> None:
        required = ("write", "read", "retire", "lease")
        if any(not callable(getattr(handle, method, None)) for method in required):
            raise TypeError("A shared Director artifact handle is required.")
        if not callable(authorized_roots):
            raise TypeError("Director allowed-root adapter is required.")
        self.handle = handle
        self._authorized_roots = authorized_roots
        self._thumbnail_encoder = thumbnail_encoder or _encode_thumbnail
        self._waveform_encoder = waveform_encoder or _encode_waveform
        self._entries: dict[str, _ArtifactEntry] = {}
        self._inflight: dict[
            tuple[str, _SourceRevision],
            asyncio.Task[tuple[object, bytes]],
        ] = {}
        self._inflight_lock = asyncio.Lock()
        self._publish_locks: dict[str, asyncio.Lock] = {}

    @property
    def cache_entry_count(self) -> int:
        return len(self._entries)

    async def thumbnail(
        self,
        source_path: str | os.PathLike[str],
        max_size: object = DEFAULT_THUMBNAIL_SIZE,
    ) -> tuple[object, bytes]:
        normalized_size = _clamp_int(max_size, 64, 1024, DEFAULT_THUMBNAIL_SIZE)
        return await self._artifact(
            THUMBNAIL_ARTIFACT_KIND,
            source_path,
            {"max_size": normalized_size},
        )

    async def waveform(
        self,
        source_path: str | os.PathLike[str],
        peaks: object = DEFAULT_WAVEFORM_PEAKS,
    ) -> tuple[object, dict[str, object]]:
        normalized_peaks = _clamp_int(
            peaks,
            MIN_WAVEFORM_PEAKS,
            MAX_WAVEFORM_PEAKS,
            DEFAULT_WAVEFORM_PEAKS,
        )
        reference, payload = await self._artifact(
            WAVEFORM_ARTIFACT_KIND,
            source_path,
            {"peaks": normalized_peaks},
        )
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DirectorManagedMediaArtifactError() from None
        if not isinstance(decoded, dict):
            raise DirectorManagedMediaArtifactError()
        return reference, decoded

    async def thumbnail_lease(
        self,
        source_path: str | os.PathLike[str],
        authorization: object,
        max_size: object = DEFAULT_THUMBNAIL_SIZE,
    ):
        reference, _payload = await self.thumbnail(source_path, max_size)
        return await self.handle.lease(
            THUMBNAIL_ARTIFACT_KIND,
            reference,
            "preview",
            authorization,
        )

    async def waveform_lease(
        self,
        source_path: str | os.PathLike[str],
        authorization: object,
        peaks: object = DEFAULT_WAVEFORM_PEAKS,
    ):
        reference, _payload = await self.waveform(source_path, peaks)
        return await self.handle.lease(
            WAVEFORM_ARTIFACT_KIND,
            reference,
            "preview",
            authorization,
        )

    async def startup_recover(self):
        self._entries.clear()
        self._publish_locks.clear()
        if not callable(getattr(self.handle, "sweep", None)):
            raise DirectorManagedMediaArtifactError()
        return await self.handle.sweep()

    async def _artifact(
        self,
        artifact_kind: str,
        source_path: str | os.PathLike[str],
        parameters: Mapping[str, object],
    ) -> tuple[object, bytes]:
        roots = tuple(self._authorized_roots())
        resolved_path, revision = await run_blocking_adapter(
            _authorized_source_revision,
            os.fspath(source_path),
            roots,
        )
        cache_key = normalized_media_parameter_key(
            artifact_kind,
            resolved_path,
            parameters,
        )
        current = self._entries.get(cache_key)
        if current is not None and current.source_revision == revision:
            try:
                value = await self.handle.read(artifact_kind, current.reference)
            except Exception:
                pass
            else:
                return current.reference, _require_bytes(value)

        inflight_key = (cache_key, revision)
        async with self._inflight_lock:
            task = self._inflight.get(inflight_key)
            if task is None:
                task = asyncio.create_task(
                    self._regenerate(
                        artifact_kind,
                        resolved_path,
                        roots,
                        revision,
                        parameters,
                        cache_key,
                    )
                )
                self._inflight[inflight_key] = task
                task.add_done_callback(
                    lambda completed, key=inflight_key: self._discard_inflight(
                        key,
                        completed,
                    )
                )
        return await asyncio.shield(task)

    def _discard_inflight(
        self,
        inflight_key: tuple[str, _SourceRevision],
        task: asyncio.Task[tuple[object, bytes]],
    ) -> None:
        if self._inflight.get(inflight_key) is task:
            self._inflight.pop(inflight_key, None)
        if not task.cancelled():
            task.exception()

    async def _regenerate(
        self,
        artifact_kind: str,
        resolved_path: str,
        roots: tuple[str, ...],
        expected_revision: _SourceRevision,
        parameters: Mapping[str, object],
        cache_key: str,
    ) -> tuple[object, bytes]:
        current = self._entries.get(cache_key)
        if current is not None and current.source_revision == expected_revision:
            try:
                value = await self.handle.read(artifact_kind, current.reference)
            except Exception:
                pass
            else:
                return current.reference, _require_bytes(value)

        if artifact_kind == THUMBNAIL_ARTIFACT_KIND:
            encoded_revision, payload = await run_blocking_adapter(
                _encode_authorized_thumbnail,
                resolved_path,
                roots,
                expected_revision,
                int(parameters["max_size"]),
                self._thumbnail_encoder,
            )
        elif artifact_kind == WAVEFORM_ARTIFACT_KIND:
            encoded_revision, waveform = await run_blocking_adapter(
                _encode_authorized_waveform,
                resolved_path,
                roots,
                expected_revision,
                int(parameters["peaks"]),
                self._waveform_encoder,
            )
            payload = _serialize_waveform_payload(waveform, int(parameters["peaks"]))
        else:
            raise ValueError("Unknown Director media artifact kind.")

        current = self._entries.get(cache_key)
        owner_id = current.owner_id if current is not None else generate_artifact_owner_id()
        reference = await self.handle.write(artifact_kind, owner_id, payload)
        try:
            revealed = _require_bytes(await self.handle.read(artifact_kind, reference))
            if revealed != payload:
                raise DirectorManagedMediaArtifactError()
        except BaseException:
            await self._retire_after_failed_write(artifact_kind, reference)
            raise

        publish_lock = self._publish_locks.setdefault(cache_key, asyncio.Lock())
        retry = False
        async with publish_lock:
            try:
                _current_path, current_revision = await run_blocking_adapter(
                    _authorized_source_revision,
                    resolved_path,
                    roots,
                )
            except BaseException:
                await self._retire_after_failed_write(artifact_kind, reference)
                raise

            latest = self._entries.get(cache_key)
            if current_revision != encoded_revision:
                try:
                    await self.handle.retire(artifact_kind, reference)
                except Exception:
                    raise DirectorManagedMediaArtifactError() from None
                if latest is not None and latest.source_revision == current_revision:
                    try:
                        latest_value = _require_bytes(
                            await self.handle.read(artifact_kind, latest.reference)
                        )
                    except Exception:
                        retry = True
                    else:
                        return latest.reference, latest_value
                else:
                    retry = True
            else:
                try:
                    if latest is not None and latest.reference != reference:
                        await self.handle.retire(artifact_kind, latest.reference)
                except BaseException:
                    await self._retire_after_failed_write(artifact_kind, reference)
                    raise
                self._entries[cache_key] = _ArtifactEntry(
                    owner_id,
                    reference,
                    encoded_revision,
                )
                return reference, revealed

        if retry:
            return await self._artifact(
                artifact_kind,
                resolved_path,
                parameters,
            )
        raise DirectorManagedMediaArtifactError()

    async def _retire_after_failed_write(
        self,
        artifact_kind: str,
        reference: object,
    ) -> None:
        try:
            await self.handle.retire(artifact_kind, reference)
        except Exception:
            # Shared cleanup ledgers retain failed retirement for sweep.
            pass


def normalized_media_parameter_key(
    artifact_kind: str,
    source_path: str | os.PathLike[str],
    parameters: Mapping[str, object],
) -> str:
    """Return a stable internal source/parameter key with no mode dimension."""

    if artifact_kind == THUMBNAIL_ARTIFACT_KIND:
        normalized = {
            "max_size": _clamp_int(
                parameters.get("max_size"),
                64,
                1024,
                DEFAULT_THUMBNAIL_SIZE,
            )
        }
    elif artifact_kind == WAVEFORM_ARTIFACT_KIND:
        normalized = {
            "peaks": _clamp_int(
                parameters.get("peaks"),
                MIN_WAVEFORM_PEAKS,
                MAX_WAVEFORM_PEAKS,
                DEFAULT_WAVEFORM_PEAKS,
            )
        }
    else:
        raise ValueError("Unknown Director media artifact kind.")
    payload = {
        "kind": artifact_kind,
        "source": os.path.abspath(os.path.expanduser(source_path)),
        "parameters": normalized,
    }
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _require_bytes(value: object) -> bytes:
    if not isinstance(value, (bytes, bytearray)):
        raise DirectorManagedMediaArtifactError()
    return bytes(value)


def _normalized_waveform_payload(
    value: Mapping[str, object],
    expected_peaks: int,
) -> dict[str, object]:
    duration = value.get("duration_seconds")
    if duration is not None and (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not 0 <= duration <= MAX_WAVEFORM_DURATION_SECONDS
    ):
        raise DirectorManagedMediaArtifactError()
    sample_rate = value.get("sample_rate")
    if sample_rate is not None and (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, int)
        or not 1 <= sample_rate <= MAX_WAVEFORM_SAMPLE_RATE
    ):
        raise DirectorManagedMediaArtifactError()
    channels = value.get("channels", 0)
    if (
        isinstance(channels, bool)
        or not isinstance(channels, int)
        or not 0 <= channels <= MAX_WAVEFORM_CHANNELS
    ):
        raise DirectorManagedMediaArtifactError()
    peaks = value.get("peaks")
    if not isinstance(peaks, list) or len(peaks) != expected_peaks:
        raise DirectorManagedMediaArtifactError()
    normalized_peaks: list[float] = []
    for peak in peaks:
        if (
            isinstance(peak, bool)
            or not isinstance(peak, (int, float))
            or not 0 <= peak <= 1
        ):
            raise DirectorManagedMediaArtifactError()
        normalized_peaks.append(float(peak))
    return {
        "duration_seconds": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "peaks": normalized_peaks,
    }


def _serialize_waveform_payload(
    value: Mapping[str, object],
    expected_peaks: int,
) -> bytes:
    normalized = _normalized_waveform_payload(value, expected_peaks)
    try:
        return json.dumps(
            normalized,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (OverflowError, TypeError, ValueError):
        raise DirectorManagedMediaArtifactError() from None


def _clamp_int(value: object, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (OverflowError, TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _revision(source_stat: os.stat_result) -> _SourceRevision:
    return _SourceRevision(
        source_stat.st_dev,
        source_stat.st_ino,
        source_stat.st_mtime_ns,
        source_stat.st_size,
    )


def _purge_legacy_derivatives(
    cache_root: Path,
    legacy_derivatives: tuple[str, ...],
) -> None:
    """Delete only preflighted regular derivatives below one real cache dir."""

    directories = {pattern.partition("/")[0] for pattern in legacy_derivatives}
    if len(directories) != 1 or "" in directories:
        raise DirectorManagedMediaArtifactError()
    directory_name = directories.pop()
    name_patterns = tuple(
        pattern.partition("/")[2] for pattern in legacy_derivatives
    )
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    root_fd: int | None = None
    directory_fd: int | None = None
    opened: list[tuple[str, int, os.stat_result]] = []
    try:
        try:
            root_fd, root_binding = _open_absolute_directory(cache_root)
        except FileNotFoundError:
            return
        try:
            before_directory = os.stat(
                directory_name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(before_directory.st_mode):
                raise DirectorManagedMediaArtifactError()
            directory_fd = os.open(directory_name, directory_flags, dir_fd=root_fd)
        except FileNotFoundError:
            return
        opened_directory = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(opened_directory.st_mode)
            or (before_directory.st_dev, before_directory.st_ino)
            != (opened_directory.st_dev, opened_directory.st_ino)
        ):
            raise DirectorManagedMediaArtifactError()
        names = sorted(
            name
            for name in os.listdir(directory_fd)
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in name_patterns)
        )
        for name in names:
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise DirectorManagedMediaArtifactError()
            file_fd = os.open(name, file_flags, dir_fd=directory_fd)
            opened_stat = os.fstat(file_fd)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or (before.st_dev, before.st_ino)
                != (opened_stat.st_dev, opened_stat.st_ino)
            ):
                os.close(file_fd)
                raise DirectorManagedMediaArtifactError()
            opened.append((name, file_fd, opened_stat))
        for name, _file_fd, opened_stat in opened:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino)
                != (opened_stat.st_dev, opened_stat.st_ino)
            ):
                raise DirectorManagedMediaArtifactError()
        _verify_absolute_directory(cache_root, root_binding)
        current_directory = os.stat(
            directory_name,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(current_directory.st_mode)
            or (current_directory.st_dev, current_directory.st_ino)
            != (opened_directory.st_dev, opened_directory.st_ino)
        ):
            raise DirectorManagedMediaArtifactError()
        for name, _file_fd, _opened_stat in opened:
            os.unlink(name, dir_fd=directory_fd)
    except DirectorManagedMediaArtifactError:
        raise
    except (OSError, TypeError, ValueError):
        raise DirectorManagedMediaArtifactError() from None
    finally:
        for _name, file_fd, _opened_stat in opened:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)
        if root_fd is not None:
            os.close(root_fd)


def _authorized_source_revision(
    source_path: str,
    authorized_roots: tuple[str, ...],
) -> tuple[str, _SourceRevision]:
    with _open_authorized_media(source_path, authorized_roots) as (
        source,
        resolved_path,
        revision,
    ):
        if _revision(os.fstat(source.fileno())) != revision:
            raise DirectorManagedMediaArtifactError()
        return resolved_path, revision


def _encode_authorized_thumbnail(
    source_path: str,
    authorized_roots: tuple[str, ...],
    expected_revision: _SourceRevision,
    max_size: int,
    encoder: ThumbnailEncoder,
) -> tuple[_SourceRevision, bytes]:
    with _open_authorized_media(source_path, authorized_roots) as (
        source,
        _resolved_path,
        revision,
    ):
        if revision != expected_revision:
            raise DirectorManagedMediaArtifactError()
        payload = _require_bytes(encoder(source, Path(source_path).suffix.lower(), max_size))
        if _revision(os.fstat(source.fileno())) != revision:
            raise DirectorManagedMediaArtifactError()
        return revision, payload


def _encode_authorized_waveform(
    source_path: str,
    authorized_roots: tuple[str, ...],
    expected_revision: _SourceRevision,
    peaks: int,
    encoder: WaveformEncoder,
) -> tuple[_SourceRevision, Mapping[str, object]]:
    with _open_authorized_media(source_path, authorized_roots) as (
        source,
        _resolved_path,
        revision,
    ):
        if revision != expected_revision:
            raise DirectorManagedMediaArtifactError()
        payload = encoder(source, Path(source_path).suffix.lower(), peaks)
        if not isinstance(payload, Mapping):
            raise DirectorManagedMediaArtifactError()
        if _revision(os.fstat(source.fileno())) != revision:
            raise DirectorManagedMediaArtifactError()
        return revision, dict(payload)


@contextmanager
def _open_authorized_media(
    source_path: str,
    authorized_roots: tuple[str, ...],
) -> Iterator[tuple[BinaryIO, str, _SourceRevision]]:
    """Open a regular root-bound source without reopening a validated path."""

    if (
        not _OPEN_SUPPORTS_DIR_FD
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise DirectorManagedMediaArtifactError(
            "Secure Director media source access is unavailable."
        )
    resolved_path = os.path.abspath(os.path.expanduser(source_path))
    root_path, relative_parts = _resolved_root_binding(
        resolved_path,
        authorized_roots,
    )
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    directory_fds: list[int] = []
    file_fd: int | None = None
    try:
        current_fd, _root_binding = _open_absolute_directory(root_path)
        directory_fds.append(current_fd)
        for part in relative_parts[:-1]:
            before_directory = os.stat(
                part,
                dir_fd=current_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(before_directory.st_mode):
                raise DirectorManagedMediaArtifactError()
            next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            opened_directory = os.fstat(next_fd)
            if (
                not stat.S_ISDIR(opened_directory.st_mode)
                or (before_directory.st_dev, before_directory.st_ino)
                != (opened_directory.st_dev, opened_directory.st_ino)
            ):
                os.close(next_fd)
                raise DirectorManagedMediaArtifactError()
            current_fd = next_fd
            directory_fds.append(current_fd)
        before_file = os.stat(
            relative_parts[-1],
            dir_fd=current_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(before_file.st_mode):
            raise DirectorManagedMediaArtifactError()
        file_fd = os.open(relative_parts[-1], file_flags, dir_fd=current_fd)
        source_stat = os.fstat(file_fd)
        if (
            not stat.S_ISREG(source_stat.st_mode)
            or (before_file.st_dev, before_file.st_ino)
            != (source_stat.st_dev, source_stat.st_ino)
        ):
            raise DirectorManagedMediaArtifactError()
        with os.fdopen(file_fd, "rb") as source:
            file_fd = None
            source_revision = _revision(source_stat)
            yield source, resolved_path, source_revision
            if _revision(os.fstat(source.fileno())) != source_revision:
                raise DirectorManagedMediaArtifactError()
            _verify_absolute_regular_file(resolved_path, source_revision)
    except DirectorManagedMediaArtifactError:
        raise
    except (OSError, ValueError):
        raise DirectorManagedMediaArtifactError() from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _resolved_root_binding(
    resolved_path: str,
    authorized_roots: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    candidates: list[tuple[int, str, tuple[str, ...]]] = []
    for root in authorized_roots:
        root_path = os.path.abspath(os.path.expanduser(os.fspath(root)))
        try:
            if os.path.commonpath((resolved_path, root_path)) != root_path:
                continue
        except ValueError:
            continue
        relative_parts = Path(os.path.relpath(resolved_path, root_path)).parts
        if not relative_parts or any(part in {"", ".", ".."} for part in relative_parts):
            continue
        candidates.append((len(root_path), root_path, relative_parts))
    if not candidates:
        raise DirectorManagedMediaArtifactError()
    _length, root_path, relative_parts = max(candidates, key=lambda item: item[0])
    return root_path, relative_parts


DirectoryBinding = tuple[tuple[str, int, int], ...]


def _open_absolute_directory(
    directory_path: str | os.PathLike[str],
) -> tuple[int, DirectoryBinding]:
    """Walk one absolute directory from `/` without following any component."""

    absolute = os.path.abspath(os.path.expanduser(os.fspath(directory_path)))
    parts = Path(absolute).parts
    if not parts or parts[0] != os.path.sep:
        raise DirectorManagedMediaArtifactError()
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    current_fd = os.open(os.path.sep, flags)
    root_stat = os.fstat(current_fd)
    binding: list[tuple[str, int, int]] = [
        (os.path.sep, root_stat.st_dev, root_stat.st_ino)
    ]
    try:
        for part in parts[1:]:
            before = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise DirectorManagedMediaArtifactError()
            next_fd = os.open(part, flags, dir_fd=current_fd)
            opened = os.fstat(next_fd)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                os.close(next_fd)
                raise DirectorManagedMediaArtifactError()
            os.close(current_fd)
            current_fd = next_fd
            binding.append((part, opened.st_dev, opened.st_ino))
        return current_fd, tuple(binding)
    except BaseException:
        os.close(current_fd)
        raise


def _verify_absolute_directory(
    directory_path: str | os.PathLike[str],
    expected: DirectoryBinding,
) -> None:
    current_fd, current = _open_absolute_directory(directory_path)
    try:
        if current != expected:
            raise DirectorManagedMediaArtifactError()
    finally:
        os.close(current_fd)


def _verify_absolute_regular_file(
    source_path: str | os.PathLike[str],
    expected: _SourceRevision,
) -> None:
    absolute = os.path.abspath(os.path.expanduser(os.fspath(source_path)))
    parent_fd, _binding = _open_absolute_directory(Path(absolute).parent)
    file_fd: int | None = None
    try:
        filename = Path(absolute).name
        before = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise DirectorManagedMediaArtifactError()
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        file_fd = os.open(filename, flags, dir_fd=parent_fd)
        opened = os.fstat(file_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or _revision(opened) != expected
        ):
            raise DirectorManagedMediaArtifactError()
    except DirectorManagedMediaArtifactError:
        raise
    except (OSError, TypeError, ValueError):
        raise DirectorManagedMediaArtifactError() from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def _encode_thumbnail(source: BinaryIO, suffix: str, max_size: int) -> bytes:
    from PIL import Image, ImageOps

    if suffix in IMAGE_EXTENSIONS:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            rendered = image.copy()
    elif suffix in VIDEO_EXTENSIONS:
        import av

        with av.open(source) as container:
            rendered = None
            for frame in container.decode(video=0):
                rendered = frame.to_image().convert("RGB")
                rendered.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                break
        if rendered is None:
            raise DirectorManagedMediaArtifactError()
    else:
        raise DirectorManagedMediaArtifactError()
    buffer = io.BytesIO()
    rendered.save(buffer, "WEBP", quality=90, method=4)
    return buffer.getvalue()


def _encode_waveform(source: BinaryIO, suffix: str, peaks: int) -> Mapping[str, object]:
    import av

    if suffix not in AUDIO_EXTENSIONS:
        raise DirectorManagedMediaArtifactError()
    samples: list[float] = []
    sample_rate = None
    channels = 0
    duration_seconds = None
    with av.open(source) as container:
        if container.duration is not None:
            duration_seconds = max(0.0, float(container.duration / av.time_base))
        for frame in container.decode(audio=0):
            sample_rate = sample_rate or frame.sample_rate
            channels = max(channels, len(frame.layout.channels))
            array = frame.to_ndarray()
            values = abs(array).max(axis=0) if array.ndim == 2 else abs(array)
            samples.extend(float(value) for value in values)
    return {
        "duration_seconds": duration_seconds,
        "sample_rate": sample_rate,
        "channels": channels,
        "peaks": _bin_peaks(samples, peaks),
    }


def _bin_peaks(samples: list[float], peaks: int) -> list[float]:
    if not samples:
        return [0.0 for _ in range(peaks)]
    max_value = max(max(samples), 1.0)
    chunk_size = max(1, math.ceil(len(samples) / peaks))
    return [
        round(max(samples[index * chunk_size:(index + 1) * chunk_size] or [0.0]) / max_value, 4)
        for index in range(peaks)
    ]
