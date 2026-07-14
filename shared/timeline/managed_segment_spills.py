"""Managed run-scoped artifacts for Director segment tensors.

The model runtimes use the synchronous facade in this module, while it
keeps the generic encode/decode/shape/stitch seam and delegates all
persistence, encryption, retirement, and cleanup-ledger behavior to a supplied
shared artifact handle.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import math
import struct
import sys
from typing import Mapping, Protocol

import torch

from helto_privacy import (
    AdapterSlot,
    ArtifactDecodedOutput,
    ArtifactDeclaration,
    ArtifactPayloadMode,
    ArtifactRetention,
    ArtifactStreamContract,
    PrivacyProfile,
    ProfileResource,
    ResourceKind,
)

from .managed_privacy import (
    DIRECTOR_DISTRIBUTION,
    DIRECTOR_PROFILE_ID,
    GLOBAL_SCOPE_ID,
    build_director_timeline_privacy_profile,
)
from .managed_take_privacy import (
    TAKE_OPERATION_RESOURCE_ID,
    DirectorPlaintextDerivative,
    build_director_take_privacy_profile,
    has_complete_director_take_privacy_fragment,
)


SEGMENT_ARTIFACT_RESOURCE_ID = "director-segment-artifacts"
SEGMENT_SPILL_ADAPTER_ID = "director-segment-spill-payload"
SEGMENT_SPILL_ARTIFACT_KIND = "timeline-segment-spill"
SEGMENT_SPILL_PURPOSE = "timeline-segment-cache"
SEGMENT_SPILL_FORMAT_VERSION = 1
SEGMENT_SPILL_MEDIA_TYPE = "application/vnd.helto.raw-tensor"
SEGMENT_TENSOR_SCHEMA = "helto.director.timeline-segment-raw-tensor"
SEGMENT_TENSOR_MAGIC = b"HLTSEG01"
SEGMENT_TENSOR_HEADER = struct.Struct("<8sBBBB5Q")
SEGMENT_TENSOR_HEADER_BYTES = SEGMENT_TENSOR_HEADER.size
SEGMENT_TENSOR_RANK = 4
SEGMENT_TENSOR_RESERVED = 0

GIBIBYTE = 1_024 * 1_024 * 1_024
MEBIBYTE = 1_024 * 1_024
MAX_SEGMENT_TENSOR_BYTES = 2 * GIBIBYTE
MAX_SEGMENT_SPILL_INPUT_BYTES = MAX_SEGMENT_TENSOR_BYTES + SEGMENT_TENSOR_HEADER_BYTES
MAX_SEGMENT_OWNER_PLAINTEXT_BYTES = 8 * GIBIBYTE
MAX_STITCH_OUTPUT_BYTES = 2 * GIBIBYTE
STITCH_WORKING_OVERHEAD_BYTES = 128 * MEBIBYTE
MAX_STITCH_WORKING_SET_BYTES = 4 * GIBIBYTE + STITCH_WORKING_OVERHEAD_BYTES
FINITE_CHECK_CHUNK_BYTES = 8 * MEBIBYTE

# Director declares 3600 seconds at 240 fps, Native Resolution uses a 1280
# short edge, and the widest selectable aspect is 21:9.  Both planners round
# dimensions to their model divisor; WAN's divisor of 16 produces a 2992 long
# edge while LTX's divisor of 32 produces 2976.  LTX's 8n+1 alignment is the
# larger temporal alignment (WAN uses 4n+1). These planner limits remain useful
# metadata guards, but the stream contract deliberately rejects shapes above
# the explicit 2 GiB materialized tensor/output boundary.
DIRECTOR_MAX_DURATION_SECONDS = 3_600
DIRECTOR_MAX_FRAME_RATE = 240
DIRECTOR_MAX_REQUESTED_FRAMES = (
    DIRECTOR_MAX_DURATION_SECONDS * DIRECTOR_MAX_FRAME_RATE
)
PLANNER_MAX_TEMPORAL_STRIDE = 8
MAX_SEGMENT_FRAMES = (
    (DIRECTOR_MAX_REQUESTED_FRAMES - 1 + PLANNER_MAX_TEMPORAL_STRIDE - 1)
    // PLANNER_MAX_TEMPORAL_STRIDE
) * PLANNER_MAX_TEMPORAL_STRIDE + 1
DIRECTOR_MAX_SHORT_EDGE = 1_280
LTX_MAX_LONG_EDGE = 2_976
WAN_MAX_LONG_EDGE = 2_992
DIRECTOR_MAX_LONG_EDGE = max(LTX_MAX_LONG_EDGE, WAN_MAX_LONG_EDGE)
DIRECTOR_IMAGE_CHANNELS = 3
MAX_SEGMENT_SPILL_RECORDS = 14_400
MAX_STITCH_FRAME_COUNT = MAX_SEGMENT_FRAMES
MAX_SEGMENT_TENSOR_NUMEL = (
    MAX_SEGMENT_TENSOR_BYTES // 2
)
MAX_SUPPORTED_DTYPE_BYTES = 8
MAX_STITCH_AGGREGATE_NUMEL = MAX_SEGMENT_OWNER_PLAINTEXT_BYTES // 2
MAX_STITCH_AGGREGATE_BYTES = MAX_SEGMENT_OWNER_PLAINTEXT_BYTES

SEGMENT_SPILL_LEGACY_DERIVATIVES = (
    "segments/<run-id>/*.pt",
    "segments/<run-id>/*.pt.tmp",
    "segments/<run-id>/.*.pt.*.tmp",
    "segments/<run-id>/*.pt.enc",
    "segments/<run-id>/*.pt.enc.tmp",
    "segments/<run-id>/.*.pt.enc.*.tmp",
    "segment spill records containing local paths and run ids",
    "segment cleanup warnings containing ids or filesystem details",
)
SEGMENT_SPILL_PLAINTEXT_DERIVATIVE_INVENTORY = (
    DirectorPlaintextDerivative(
        "cache segments/<run-id>/*.pt and atomic temporary variants",
        "decoded IMAGE tensor batches in plaintext PyTorch archives",
        "replace with shared RUN_SCOPED_SPILL writes; never stage a local plaintext archive",
    ),
    DirectorPlaintextDerivative(
        "cache segments/<run-id>/*.pt.enc and local encryption envelopes",
        "product-owned ciphertext plus filesystem and cleanup-ledger metadata",
        "remove after shared artifact ownership and restart sweep are active",
    ),
    DirectorPlaintextDerivative(
        "segment_storage debug records and cleanup warnings",
        "run ids, segment ids, paths, shapes, dtypes and filesystem failures",
        "retain only coarse shared artifact counts/status through a separately "
        "declared safe projection",
    ),
)
SEGMENT_SPILL_ACTIVATION_GAPS = ()

SEGMENT_ARTIFACT_RESOURCE = ProfileResource(
    SEGMENT_ARTIFACT_RESOURCE_ID,
    ResourceKind.ARTIFACT,
    (SEGMENT_SPILL_ADAPTER_ID,),
)
SEGMENT_SPILL_ADAPTER_SLOT = AdapterSlot(
    SEGMENT_SPILL_ADAPTER_ID,
    ResourceKind.ARTIFACT,
    SEGMENT_ARTIFACT_RESOURCE_ID,
)
SEGMENT_SPILL_ARTIFACT_DECLARATION = ArtifactDeclaration(
    SEGMENT_SPILL_ARTIFACT_KIND,
    SEGMENT_ARTIFACT_RESOURCE_ID,
    GLOBAL_SCOPE_ID,
    SEGMENT_SPILL_PURPOSE,
    SEGMENT_SPILL_ADAPTER_ID,
    SEGMENT_SPILL_FORMAT_VERSION,
    ArtifactRetention.RUN_SCOPED_SPILL,
    (),
    media_type=SEGMENT_SPILL_MEDIA_TYPE,
    payload_mode=ArtifactPayloadMode.STREAM_V1,
    stream_contract=ArtifactStreamContract(
        SEGMENT_TENSOR_SCHEMA,
        SEGMENT_SPILL_FORMAT_VERSION,
        MAX_SEGMENT_SPILL_INPUT_BYTES,
        ArtifactDecodedOutput.MATERIALIZED,
        max_materialized_output_bytes=MAX_SEGMENT_TENSOR_BYTES,
        max_owner_plaintext_bytes=MAX_SEGMENT_OWNER_PLAINTEXT_BYTES,
    ),
)


def build_director_segment_spill_privacy_profile(
    base_profile: PrivacyProfile | None = None,
) -> PrivacyProfile:
    """Compose the spill fragment onto any valid Director base."""

    base = base_profile or build_director_timeline_privacy_profile()
    _require_director_base(base)
    if any(
        resource.id == SEGMENT_ARTIFACT_RESOURCE_ID for resource in base.resources
    ) or any(
        adapter.id == SEGMENT_SPILL_ADAPTER_ID for adapter in base.server_adapters
    ) or any(
        artifact.id == SEGMENT_SPILL_ARTIFACT_KIND for artifact in base.artifacts
    ):
        raise ValueError("Director segment-spill privacy fragment is already present.")
    return replace(
        base,
        resources=(*base.resources, SEGMENT_ARTIFACT_RESOURCE),
        server_adapters=(*base.server_adapters, SEGMENT_SPILL_ADAPTER_SLOT),
        artifacts=(*base.artifacts, SEGMENT_SPILL_ARTIFACT_DECLARATION),
    )


def build_director_take_segment_privacy_profile(
    base_profile: PrivacyProfile | None = None,
) -> PrivacyProfile:
    """Compose both D6 fragments without assuming a D3/D4 composition order."""

    base = base_profile or build_director_timeline_privacy_profile()
    if has_complete_director_take_privacy_fragment(base):
        with_take = base
    else:
        with_take = build_director_take_privacy_profile(base)
    if has_complete_director_segment_spill_fragment(with_take):
        return with_take
    return build_director_segment_spill_privacy_profile(with_take)


def has_complete_director_segment_spill_fragment(profile: PrivacyProfile) -> bool:
    present = any(
        item.id == SEGMENT_ARTIFACT_RESOURCE_ID for item in profile.resources
    )
    if not present:
        return False
    if not (
        SEGMENT_ARTIFACT_RESOURCE in profile.resources
        and SEGMENT_SPILL_ADAPTER_SLOT in profile.server_adapters
        and SEGMENT_SPILL_ARTIFACT_DECLARATION in profile.artifacts
    ):
        raise ValueError("Director segment-spill privacy fragment is incomplete.")
    return True


def build_director_segment_spill_server_adapters() -> dict[str, object]:
    return {SEGMENT_SPILL_ADAPTER_ID: DirectorSegmentTensorPayloadAdapter()}


class DirectorManagedSegmentSpillError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Director managed segment spill operation failed.")


@dataclass(frozen=True, slots=True)
class SegmentTensorDescriptor:
    shape: tuple[int, int, int, int]
    dtype: str
    numel: int
    byte_count: int


@dataclass(frozen=True, slots=True)
class SegmentStitchDescriptor:
    target_frame_count: int
    output_numel: int
    output_bytes: int
    aggregate_numel: int
    aggregate_bytes: int
    working_set_bytes: int


class DirectorSegmentTensorPayloadAdapter:
    """Strict CPU IMAGE-tensor codec for the shared artifact store."""

    def encode_to(self, value: object, sink: object) -> None:
        try:
            tensor = _normalized_image_tensor(value, require_finite=False)
            descriptor = validate_segment_tensor_descriptor(
                tuple(int(dimension) for dimension in tensor.shape),
                str(tensor.dtype),
            )
            chunk_bytes = _bounded_stream_chunk_bytes(sink)
            sink.write(_tensor_header(descriptor))
            flat = tensor.reshape(-1)
            raw = _tensor_byte_view(tensor)
            element_size = tensor.element_size()
            aligned_chunk_bytes = chunk_bytes - chunk_bytes % element_size
            if aligned_chunk_bytes < element_size:
                raise DirectorManagedSegmentSpillError()
            for offset in range(0, descriptor.byte_count, aligned_chunk_bytes):
                end = min(offset + aligned_chunk_bytes, descriptor.byte_count)
                _require_finite_slice(
                    flat,
                    offset // element_size,
                    end // element_size,
                )
                sink.write(raw[offset:end])
        except DirectorManagedSegmentSpillError:
            raise
        except Exception:
            raise DirectorManagedSegmentSpillError() from None

    def decode_from(self, source: object) -> torch.Tensor:
        try:
            chunk_bytes = _bounded_stream_chunk_bytes(source)
            header = bytearray(SEGMENT_TENSOR_HEADER_BYTES)
            _read_exact_into(source, header, chunk_bytes)
            descriptor = _descriptor_from_header(header)
            tensor = torch.empty(
                descriptor.shape,
                dtype=_torch_dtype(descriptor.dtype),
                device="cpu",
            )
            raw = _tensor_byte_view(tensor)
            _read_exact_into(source, raw, chunk_bytes)
            extra = bytearray(1)
            if source.readinto(extra) != 0:
                raise DirectorManagedSegmentSpillError()
            _require_finite_tensor(tensor)
            return tensor
        except DirectorManagedSegmentSpillError:
            raise
        except Exception:
            raise DirectorManagedSegmentSpillError() from None

    def purge_plaintext_derivatives(self, artifact_kind: str) -> None:
        if artifact_kind != SEGMENT_SPILL_ARTIFACT_KIND:
            raise ValueError("Unknown Director segment artifact kind.")
        # The adapter has no independent filesystem root. The complete profile binds
        # the explicit inventory above to shared retirement/restart sweep before
        # the old store is removed; it must not scan live cache roots here.

    def prepare_mode_transition(self, *_args: object) -> None:
        return None

    def commit_mode_transition(self, *_args: object) -> None:
        return None

    def rollback_mode_transition(self, *_args: object) -> None:
        return None


class _ArtifactRun(Protocol):
    async def write(self, artifact_kind: str, value: object) -> object: ...

    async def close(self) -> int: ...


class _ArtifactHandle(Protocol):
    def run(self) -> _ArtifactRun: ...

    async def read(self, artifact_kind: str, reference: object) -> object: ...


@dataclass(frozen=True, slots=True)
class ManagedSegmentSpillRecord:
    reference: object
    frame_count: int
    shape: tuple[int, ...]
    dtype: str
    _session_identity: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )


class DirectorManagedSegmentSpillSession:
    """Exactly-once shared run scope with sequential stitching semantics."""

    def __init__(self, handle: _ArtifactHandle) -> None:
        if not callable(getattr(handle, "run", None)) or not callable(
            getattr(handle, "read", None)
        ):
            raise TypeError("A shared Director artifact handle is required.")
        self._handle = handle
        self._run: _ArtifactRun | None = None
        self._closed = False
        self._session_identity = object()

    async def __aenter__(self) -> "DirectorManagedSegmentSpillSession":
        if self._run is not None or self._closed:
            raise DirectorManagedSegmentSpillError()
        run = self._handle.run()
        if not callable(getattr(run, "write", None)) or not callable(
            getattr(run, "close", None)
        ):
            raise TypeError("A shared Director artifact run is required.")
        self._run = run
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
        await self.close()
        return False

    async def write_segment(self, images: object) -> ManagedSegmentSpillRecord:
        run = self._require_run()
        tensor = _normalized_image_tensor(images)
        reference = await run.write(SEGMENT_SPILL_ARTIFACT_KIND, tensor)
        return ManagedSegmentSpillRecord(
            reference,
            int(tensor.shape[0]),
            tuple(int(dimension) for dimension in tensor.shape),
            str(tensor.dtype),
            self._session_identity,
        )

    async def read_segment(self, record: ManagedSegmentSpillRecord) -> torch.Tensor:
        self._require_run()
        _validate_spill_record(record)
        if record._session_identity is not self._session_identity:
            raise DirectorManagedSegmentSpillError()
        tensor = _normalized_image_tensor(
            await self._handle.read(
                SEGMENT_SPILL_ARTIFACT_KIND,
                record.reference,
            )
        )
        if (
            tuple(tensor.shape) != record.shape
            or int(tensor.shape[0]) != record.frame_count
            or str(tensor.dtype) != record.dtype
        ):
            raise DirectorManagedSegmentSpillError()
        return tensor

    async def stitch(
        self,
        records: list[ManagedSegmentSpillRecord] | tuple[ManagedSegmentSpillRecord, ...],
        *,
        final_frame_count: int,
    ) -> torch.Tensor:
        if type(records) not in {list, tuple}:
            raise DirectorManagedSegmentSpillError()
        if not records:
            _bounded_final_frame_count(final_frame_count, fallback=1)
            return torch.zeros((1, 1, 1, 3), dtype=torch.float32)
        stitch = validate_segment_stitch_descriptors(
            records,
            final_frame_count=final_frame_count,
        )
        first = await self.read_segment(records[0])
        target = stitch.target_frame_count
        output = torch.empty((target, *first.shape[1:]), dtype=first.dtype)
        cursor = _copy_segment_frames(output, first, 0)
        last_frame = first[-1:].clone()
        for record in records[1:]:
            if cursor >= target:
                break
            tensor = await self.read_segment(record)
            if tuple(tensor.shape[1:]) != tuple(output.shape[1:]):
                raise DirectorManagedSegmentSpillError()
            cursor = _copy_segment_frames(output, tensor, cursor)
            last_frame = tensor[-1:].clone()
        if cursor < target:
            output[cursor:target] = last_frame
        return output

    async def close(self) -> int:
        if self._closed and self._run is None:
            return 0
        self._closed = True
        run = self._run
        if run is None:
            return 0
        cleanup = asyncio.create_task(run.close())
        try:
            retired = await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(cleanup)
            except Exception:
                pass
            else:
                self._run = None
            raise
        except Exception:
            raise DirectorManagedSegmentSpillError() from None
        self._run = None
        return int(retired)

    def _require_run(self) -> _ArtifactRun:
        if self._run is None or self._closed:
            raise DirectorManagedSegmentSpillError()
        return self._run


class DirectorManagedSegmentSpillStore:
    """Synchronous runtime facade over one shared asynchronous spill session."""

    def __init__(self, handle: _ArtifactHandle, *, private: bool) -> None:
        self.privacy_mode = bool(private)
        self.records: list[dict[str, object]] = []
        self.files_written = 0
        self.files_read = 0
        self.files_deleted = 0
        self._runner = asyncio.Runner()
        self._session = DirectorManagedSegmentSpillSession(handle)
        self._runner.run(self._session.__aenter__())
        self._closed = False

    def write_segment(
        self,
        _segment: Mapping[str, object],
        images: object,
    ) -> dict[str, object]:
        if self._closed:
            raise DirectorManagedSegmentSpillError()
        managed = self._runner.run(self._session.write_segment(images))
        record = {
            "managed_record": managed,
            "frame_count": managed.frame_count,
        }
        self.records.append(record)
        self.files_written += 1
        return record

    def read_segment(self, record: object) -> torch.Tensor:
        if self._closed or not isinstance(record, Mapping):
            raise DirectorManagedSegmentSpillError()
        managed = record.get("managed_record")
        if type(managed) is not ManagedSegmentSpillRecord:
            raise DirectorManagedSegmentSpillError()
        tensor = self._runner.run(self._session.read_segment(managed))
        self.files_read += 1
        return tensor

    def cleanup(self) -> dict[str, object]:
        if not self._closed:
            self.files_deleted = self._runner.run(self._session.close())
            self._closed = True
            self._runner.close()
        return self.debug_summary()

    def debug_summary(self, *, include_paths: bool = False) -> dict[str, object]:
        if include_paths:
            raise DirectorManagedSegmentSpillError()
        return {
            "segment_storage": "managed_run_scoped_spill",
            "privacy_mode": self.privacy_mode,
            "encrypted": self.privacy_mode,
            "files_written": self.files_written,
            "files_read": self.files_read,
            "files_deleted": self.files_deleted,
            "record_count": len(self.records),
            "cleanup_pending": not self._closed,
        }


def _normalized_image_tensor(
    value: object,
    *,
    require_finite: bool = True,
) -> torch.Tensor:
    try:
        if not torch.is_tensor(value):
            raise DirectorManagedSegmentSpillError()
        if value.layout is not torch.strided or value.ndim != 4:
            raise DirectorManagedSegmentSpillError()
        shape = tuple(int(dimension) for dimension in value.shape)
        descriptor = validate_segment_tensor_descriptor(
            shape,
            str(value.dtype),
        )
        if descriptor.byte_count != math.prod(shape) * value.element_size():
            raise DirectorManagedSegmentSpillError()
    except DirectorManagedSegmentSpillError:
        raise
    except Exception:
        raise DirectorManagedSegmentSpillError() from None
    if len(shape) != 4:
        raise DirectorManagedSegmentSpillError()
    try:
        tensor = value.detach().cpu().contiguous()
    except Exception:
        raise DirectorManagedSegmentSpillError() from None
    if (
        tuple(int(dimension) for dimension in tensor.shape) != descriptor.shape
        or str(tensor.dtype) != descriptor.dtype
        or tensor.numel() != descriptor.numel
        or tensor.element_size() * tensor.numel() != descriptor.byte_count
    ):
        raise DirectorManagedSegmentSpillError()
    if require_finite:
        _require_finite_tensor(tensor)
    return tensor


def _validate_spill_record(record: object) -> None:
    if type(record) is not ManagedSegmentSpillRecord:
        raise DirectorManagedSegmentSpillError()
    if (
        type(record.shape) is not tuple
        or type(record.frame_count) is not int
        or type(record.dtype) is not str
    ):
        raise DirectorManagedSegmentSpillError()
    descriptor = validate_segment_tensor_descriptor(record.shape, record.dtype)
    if record.frame_count != descriptor.shape[0]:
        raise DirectorManagedSegmentSpillError()


def validate_segment_tensor_descriptor(
    shape: object,
    dtype: object,
) -> SegmentTensorDescriptor:
    """Validate planner-supported tensor metadata without allocating a tensor."""

    if (
        type(shape) is not tuple
        or len(shape) != 4
        or any(type(dimension) is not int or dimension <= 0 for dimension in shape)
        or type(dtype) is not str
    ):
        raise DirectorManagedSegmentSpillError()
    frames, height, width, channels = shape
    short_edge = min(height, width)
    long_edge = max(height, width)
    if (
        frames > MAX_SEGMENT_FRAMES
        or short_edge > DIRECTOR_MAX_SHORT_EDGE
        or long_edge > DIRECTOR_MAX_LONG_EDGE
        or channels != DIRECTOR_IMAGE_CHANNELS
    ):
        raise DirectorManagedSegmentSpillError()
    dtype_bytes = _dtype_bytes(dtype)
    numel = _checked_product(shape, MAX_SEGMENT_TENSOR_NUMEL)
    byte_count = _checked_multiply(
        numel,
        dtype_bytes,
        MAX_SEGMENT_TENSOR_BYTES,
    )
    return SegmentTensorDescriptor(shape, dtype, numel, byte_count)


def validate_segment_stitch_descriptors(
    records: object,
    *,
    final_frame_count: object,
) -> SegmentStitchDescriptor:
    """Validate aggregate/output bounds without reading or allocating tensors."""

    if (
        type(records) not in {list, tuple}
        or not records
        or len(records) > MAX_SEGMENT_SPILL_RECORDS
    ):
        raise DirectorManagedSegmentSpillError()
    descriptors: list[SegmentTensorDescriptor] = []
    for record in records:
        _validate_spill_record(record)
        descriptors.append(
            validate_segment_tensor_descriptor(record.shape, record.dtype)
        )
    first = descriptors[0]
    frame_shape = first.shape[1:]
    if any(
        descriptor.shape[1:] != frame_shape or descriptor.dtype != first.dtype
        for descriptor in descriptors[1:]
    ):
        raise DirectorManagedSegmentSpillError()
    aggregate_numel = _checked_sum(
        (descriptor.numel for descriptor in descriptors),
        MAX_STITCH_AGGREGATE_NUMEL,
    )
    aggregate_bytes = _checked_sum(
        (descriptor.byte_count for descriptor in descriptors),
        MAX_STITCH_AGGREGATE_BYTES,
    )
    target = _bounded_final_frame_count(
        final_frame_count,
        fallback=first.shape[0],
    )
    output_numel = _checked_product(
        (target, *frame_shape),
        MAX_STITCH_AGGREGATE_NUMEL,
    )
    output_bytes = _checked_multiply(
        output_numel,
        _dtype_bytes(first.dtype),
        MAX_STITCH_OUTPUT_BYTES,
    )
    working_set_bytes = _checked_sum(
        (
            output_bytes,
            max(descriptor.byte_count for descriptor in descriptors),
            STITCH_WORKING_OVERHEAD_BYTES,
        ),
        MAX_STITCH_WORKING_SET_BYTES,
    )
    return SegmentStitchDescriptor(
        target,
        output_numel,
        output_bytes,
        aggregate_numel,
        aggregate_bytes,
        working_set_bytes,
    )


def _bounded_final_frame_count(value: object, *, fallback: int) -> int:
    if value is None:
        target = fallback
    elif type(value) is int:
        target = fallback if value == 0 else value
    else:
        raise DirectorManagedSegmentSpillError()
    if target < 1 or target > MAX_STITCH_FRAME_COUNT:
        raise DirectorManagedSegmentSpillError()
    return target


def _dtype_bytes(dtype: str) -> int:
    sizes = {
        "torch.float16": 2,
        "torch.float32": 4,
        "torch.float64": 8,
        "torch.bfloat16": 2,
    }
    try:
        return sizes[dtype]
    except KeyError:
        raise DirectorManagedSegmentSpillError() from None


def _torch_dtype(dtype: str) -> torch.dtype:
    values = {
        "torch.float16": torch.float16,
        "torch.float32": torch.float32,
        "torch.float64": torch.float64,
        "torch.bfloat16": torch.bfloat16,
    }
    try:
        return values[dtype]
    except KeyError:
        raise DirectorManagedSegmentSpillError() from None


def _dtype_code(dtype: str) -> int:
    values = {
        "torch.float16": 1,
        "torch.float32": 2,
        "torch.float64": 3,
        "torch.bfloat16": 4,
    }
    try:
        return values[dtype]
    except KeyError:
        raise DirectorManagedSegmentSpillError() from None


def _dtype_name(code: object) -> str:
    values = {
        1: "torch.float16",
        2: "torch.float32",
        3: "torch.float64",
        4: "torch.bfloat16",
    }
    try:
        return values[code]
    except (KeyError, TypeError):
        raise DirectorManagedSegmentSpillError() from None


def _tensor_header(descriptor: SegmentTensorDescriptor) -> bytes:
    if sys.byteorder != "little":
        raise DirectorManagedSegmentSpillError()
    try:
        return SEGMENT_TENSOR_HEADER.pack(
            SEGMENT_TENSOR_MAGIC,
            SEGMENT_SPILL_FORMAT_VERSION,
            _dtype_code(descriptor.dtype),
            SEGMENT_TENSOR_RANK,
            SEGMENT_TENSOR_RESERVED,
            *descriptor.shape,
            descriptor.byte_count,
        )
    except (OverflowError, struct.error):
        raise DirectorManagedSegmentSpillError() from None


def _descriptor_from_header(value: object) -> SegmentTensorDescriptor:
    if sys.byteorder != "little":
        raise DirectorManagedSegmentSpillError()
    try:
        view = memoryview(value).cast("B")
        if len(view) != SEGMENT_TENSOR_HEADER_BYTES:
            raise DirectorManagedSegmentSpillError()
        (
            magic,
            version,
            dtype_code,
            rank,
            reserved,
            frames,
            height,
            width,
            channels,
            byte_count,
        ) = SEGMENT_TENSOR_HEADER.unpack(view)
    except DirectorManagedSegmentSpillError:
        raise
    except Exception:
        raise DirectorManagedSegmentSpillError() from None
    if (
        magic != SEGMENT_TENSOR_MAGIC
        or version != SEGMENT_SPILL_FORMAT_VERSION
        or rank != SEGMENT_TENSOR_RANK
        or reserved != SEGMENT_TENSOR_RESERVED
    ):
        raise DirectorManagedSegmentSpillError()
    descriptor = validate_segment_tensor_descriptor(
        (frames, height, width, channels),
        _dtype_name(dtype_code),
    )
    if byte_count != descriptor.byte_count:
        raise DirectorManagedSegmentSpillError()
    return descriptor


def _bounded_stream_chunk_bytes(stream: object) -> int:
    chunk_bytes = getattr(stream, "max_chunk_bytes", None)
    if (
        type(chunk_bytes) is not int
        or chunk_bytes < 1
        or not callable(getattr(stream, "write", None))
        and not callable(getattr(stream, "readinto", None))
    ):
        raise DirectorManagedSegmentSpillError()
    return chunk_bytes


def _tensor_byte_view(tensor: torch.Tensor) -> memoryview:
    try:
        if sys.byteorder != "little" or not tensor.is_contiguous():
            raise DirectorManagedSegmentSpillError()
        view = memoryview(tensor.view(torch.uint8).reshape(-1).numpy()).cast("B")
    except DirectorManagedSegmentSpillError:
        raise
    except Exception:
        raise DirectorManagedSegmentSpillError() from None
    if len(view) != tensor.numel() * tensor.element_size():
        raise DirectorManagedSegmentSpillError()
    return view


def _read_exact_into(source: object, destination: object, chunk_bytes: int) -> None:
    try:
        view = memoryview(destination).cast("B")
    except (TypeError, ValueError):
        raise DirectorManagedSegmentSpillError() from None
    offset = 0
    while offset < len(view):
        end = min(offset + chunk_bytes, len(view))
        count = source.readinto(view[offset:end])
        if type(count) is not int or count < 1 or count > end - offset:
            raise DirectorManagedSegmentSpillError()
        offset += count


def _require_finite_tensor(tensor: torch.Tensor) -> None:
    flat = tensor.reshape(-1)
    chunk_elements = max(1, FINITE_CHECK_CHUNK_BYTES // tensor.element_size())
    for offset in range(0, tensor.numel(), chunk_elements):
        _require_finite_slice(
            flat,
            offset,
            min(offset + chunk_elements, tensor.numel()),
        )


def _require_finite_slice(flat: torch.Tensor, start: int, end: int) -> None:
    try:
        if not bool(torch.isfinite(flat[start:end]).all().item()):
            raise DirectorManagedSegmentSpillError()
    except DirectorManagedSegmentSpillError:
        raise
    except Exception:
        raise DirectorManagedSegmentSpillError() from None


def _checked_product(values: object, maximum: int) -> int:
    if type(maximum) is not int or maximum < 1:
        raise DirectorManagedSegmentSpillError()
    result = 1
    try:
        iterator = iter(values)
    except TypeError:
        raise DirectorManagedSegmentSpillError() from None
    for value in iterator:
        if type(value) is not int or value < 1 or result > maximum // value:
            raise DirectorManagedSegmentSpillError()
        result *= value
    return result


def _checked_multiply(left: int, right: int, maximum: int) -> int:
    if (
        type(left) is not int
        or type(right) is not int
        or left < 0
        or right < 0
        or right != 0
        and left > maximum // right
    ):
        raise DirectorManagedSegmentSpillError()
    return left * right


def _checked_sum(values: object, maximum: int) -> int:
    total = 0
    try:
        iterator = iter(values)
    except TypeError:
        raise DirectorManagedSegmentSpillError() from None
    for value in iterator:
        if type(value) is not int or value < 0 or total > maximum - value:
            raise DirectorManagedSegmentSpillError()
        total += value
    return total


def _copy_segment_frames(
    output: torch.Tensor,
    tensor: torch.Tensor,
    cursor: int,
) -> int:
    if cursor >= output.shape[0]:
        return cursor
    count = min(int(tensor.shape[0]), int(output.shape[0]) - int(cursor))
    output[cursor : cursor + count] = tensor[:count]
    return cursor + count


def _require_director_base(base: PrivacyProfile) -> None:
    if base.id != DIRECTOR_PROFILE_ID or base.distribution != DIRECTOR_DISTRIBUTION:
        raise ValueError("Director segment spills require the Director profile.")
    if not any(scope.id == GLOBAL_SCOPE_ID for scope in base.scopes):
        raise ValueError("Director segment spills require the global Director scope.")
