from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import torch

from helto_privacy import (
    ArtifactDecodedOutput,
    ArtifactPayloadMode,
    ArtifactRetention,
    ResourceKind,
)

from shared.segmented_executor import (
    stitch_spilled_segment_images,
    trim_visible_segment_images,
)
import shared.timeline.managed_segment_spills as managed_segment_spills
from shared.timeline.managed_media_artifacts import (
    build_director_media_artifact_privacy_profile,
)
from shared.timeline.managed_library_records import build_director_library_privacy_profile
from shared.timeline.managed_media_privacy import build_director_media_privacy_profile
from shared.timeline.managed_privacy import build_director_timeline_privacy_profile
from shared.timeline.managed_segment_spills import (
    SEGMENT_ARTIFACT_RESOURCE_ID,
    SEGMENT_SPILL_ADAPTER_ID,
    SEGMENT_SPILL_ACTIVATION_GAPS,
    SEGMENT_SPILL_ARTIFACT_DECLARATION,
    SEGMENT_SPILL_ARTIFACT_KIND,
    SEGMENT_SPILL_FORMAT_VERSION,
    SEGMENT_SPILL_LEGACY_DERIVATIVES,
    SEGMENT_SPILL_MEDIA_TYPE,
    SEGMENT_SPILL_PLAINTEXT_DERIVATIVE_INVENTORY,
    SEGMENT_SPILL_PURPOSE,
    DIRECTOR_MAX_LONG_EDGE,
    DIRECTOR_MAX_SHORT_EDGE,
    MAX_SEGMENT_FRAMES,
    MAX_SEGMENT_OWNER_PLAINTEXT_BYTES,
    MAX_SEGMENT_SPILL_INPUT_BYTES,
    MAX_SEGMENT_TENSOR_BYTES,
    MAX_SEGMENT_TENSOR_NUMEL,
    MAX_STITCH_OUTPUT_BYTES,
    MAX_STITCH_WORKING_SET_BYTES,
    MAX_STITCH_FRAME_COUNT,
    SEGMENT_TENSOR_HEADER,
    SEGMENT_TENSOR_HEADER_BYTES,
    SEGMENT_TENSOR_MAGIC,
    SEGMENT_TENSOR_RANK,
    STITCH_WORKING_OVERHEAD_BYTES,
    ManagedSegmentSpillRecord,
    DirectorManagedSegmentSpillError,
    DirectorManagedSegmentSpillSession,
    DirectorManagedSegmentSpillStore,
    DirectorSegmentTensorPayloadAdapter,
    build_director_segment_spill_privacy_profile,
    build_director_take_segment_privacy_profile,
    validate_segment_stitch_descriptors,
    validate_segment_tensor_descriptor,
)
from shared.timeline.managed_take_privacy import (
    CAPTURE_TAKE_OPERATION_ID,
    TAKE_OPERATION_RESOURCE_ID,
)


class _Abort(BaseException):
    pass


class _MemorySink:
    def __init__(self, max_chunk_bytes: int = 64) -> None:
        self.max_chunk_bytes = max_chunk_bytes
        self.payload = bytearray()
        self.writes = []

    def write(self, value) -> int:
        view = memoryview(value).cast("B")
        assert 0 < len(view) <= self.max_chunk_bytes
        self.writes.append((type(value), len(view)))
        self.payload.extend(view)
        return len(view)


class _MemorySource:
    def __init__(self, payload: object, max_chunk_bytes: int = 64) -> None:
        self.max_chunk_bytes = max_chunk_bytes
        self.payload = memoryview(payload).cast("B")
        self.offset = 0
        self.read_sizes = []

    def readinto(self, destination) -> int:
        view = memoryview(destination).cast("B")
        assert 0 < len(view) <= self.max_chunk_bytes
        self.read_sizes.append(len(view))
        count = min(len(view), len(self.payload) - self.offset)
        if count:
            view[:count] = self.payload[self.offset : self.offset + count]
            self.offset += count
        return count


def _encode(codec, tensor, *, chunk_bytes=64):
    sink = _MemorySink(chunk_bytes)
    codec.encode_to(tensor, sink)
    return sink


def _decode(codec, payload, *, chunk_bytes=64):
    source = _MemorySource(payload, chunk_bytes)
    return codec.decode_from(source), source


class _FakeRun:
    def __init__(self, handle) -> None:
        self.handle = handle

    async def write(self, artifact_kind: str, value: object) -> object:
        self.handle.write_calls += 1
        if self.handle.write_failure is not None:
            raise self.handle.write_failure
        assert artifact_kind == SEGMENT_SPILL_ARTIFACT_KIND
        reference = f"opaque-{self.handle.write_calls}"
        self.handle.payloads[reference] = _encode(
            self.handle.codec,
            value,
        ).payload
        return reference

    async def close(self) -> int:
        self.handle.close_calls += 1
        if self.handle.close_failure is not None:
            raise self.handle.close_failure
        count = len(self.handle.payloads)
        self.handle.payloads.clear()
        return count


class _FakeHandle:
    def __init__(self) -> None:
        self.codec = DirectorSegmentTensorPayloadAdapter()
        self.payloads: dict[object, bytearray] = {}
        self.write_calls = 0
        self.read_calls = 0
        self.close_calls = 0
        self.write_failure: BaseException | None = None
        self.read_failure: BaseException | None = None
        self.close_failure: BaseException | None = None

    def run(self):
        return _FakeRun(self)

    async def read(self, artifact_kind: str, reference: object) -> object:
        self.read_calls += 1
        if self.read_failure is not None:
            raise self.read_failure
        assert artifact_kind == SEGMENT_SPILL_ARTIFACT_KIND
        return _decode(self.codec, self.payloads[reference])[0]


class _ExistingStore:
    def read_segment(self, record):
        return record["tensor"]


def _cpu_tensor(start: int, frames: int, shape=(2, 2, 3)) -> torch.Tensor:
    count = frames
    for dimension in shape:
        count *= dimension
    return torch.arange(
        start,
        start + count,
        dtype=torch.float32,
        device="cpu",
    ).reshape(frames, *shape)


def test_segment_declaration_and_combined_fragment_are_exact_and_composable():
    base = build_director_media_artifact_privacy_profile()
    spill_profile = build_director_segment_spill_privacy_profile(base)
    combined = build_director_take_segment_privacy_profile(base)

    resource = next(
        item for item in spill_profile.resources
        if item.id == SEGMENT_ARTIFACT_RESOURCE_ID
    )
    assert resource.kind is ResourceKind.ARTIFACT
    assert resource.adapter_slots == (SEGMENT_SPILL_ADAPTER_ID,)
    assert SEGMENT_SPILL_ARTIFACT_DECLARATION in spill_profile.artifacts
    assert SEGMENT_SPILL_ARTIFACT_DECLARATION.id == "timeline-segment-spill"
    assert SEGMENT_SPILL_ARTIFACT_DECLARATION.purpose == SEGMENT_SPILL_PURPOSE
    assert SEGMENT_SPILL_ARTIFACT_DECLARATION.format_version == SEGMENT_SPILL_FORMAT_VERSION
    assert SEGMENT_SPILL_ARTIFACT_DECLARATION.retention is ArtifactRetention.RUN_SCOPED_SPILL
    assert SEGMENT_SPILL_ARTIFACT_DECLARATION.operations == ()
    assert SEGMENT_SPILL_ARTIFACT_DECLARATION.media_type == SEGMENT_SPILL_MEDIA_TYPE
    assert SEGMENT_SPILL_ARTIFACT_DECLARATION.payload_mode is ArtifactPayloadMode.STREAM_V1
    stream = SEGMENT_SPILL_ARTIFACT_DECLARATION.stream_contract
    assert stream is not None
    assert stream.codec_schema == "helto.director.timeline-segment-raw-tensor"
    assert stream.codec_version == SEGMENT_SPILL_FORMAT_VERSION
    assert stream.max_plaintext_bytes == MAX_SEGMENT_SPILL_INPUT_BYTES
    assert stream.decoded_output is ArtifactDecodedOutput.MATERIALIZED
    assert stream.max_materialized_output_bytes == MAX_SEGMENT_TENSOR_BYTES
    assert stream.max_owner_plaintext_bytes == MAX_SEGMENT_OWNER_PLAINTEXT_BYTES
    assert {item.id for item in combined.resources}.issuperset(
        {SEGMENT_ARTIFACT_RESOURCE_ID, TAKE_OPERATION_RESOURCE_ID}
    )
    assert any(
        operation.id == CAPTURE_TAKE_OPERATION_ID
        for operation in combined.protected_operations
    )


def test_d6_composes_before_or_after_d3_d4_d5_without_duplicate_fragments():
    d2 = build_director_timeline_privacy_profile()
    d6_first = build_director_take_segment_privacy_profile(d2)
    d3_after = build_director_library_privacy_profile(d6_first)
    d4_after = build_director_media_artifact_privacy_profile(d3_after)
    complete = build_director_media_privacy_profile(d4_after)

    d5_first = build_director_media_privacy_profile()
    d6_after = build_director_take_segment_privacy_profile(d5_first)
    for profile in (complete, d6_after, d5_first):
        assert sum(
            item.id == TAKE_OPERATION_RESOURCE_ID for item in profile.resources
        ) == 1
        assert sum(
            item.id == SEGMENT_ARTIFACT_RESOURCE_ID for item in profile.resources
        ) == 1
        assert sum(
            item.id == SEGMENT_SPILL_ARTIFACT_KIND for item in profile.artifacts
        ) == 1


def test_tensor_payload_codec_round_trips_cpu_and_rejects_unsafe_shapes():
    codec = DirectorSegmentTensorPayloadAdapter()
    tensor = _cpu_tensor(0, 3)
    encoded = _encode(codec, tensor)
    decoded, source = _decode(codec, encoded.payload)

    assert decoded.device.type == "cpu"
    assert decoded.is_contiguous()
    assert torch.equal(decoded, tensor)
    assert encoded.writes[0] == (bytes, SEGMENT_TENSOR_HEADER_BYTES)
    assert all(kind is memoryview for kind, _size in encoded.writes[1:])
    assert max(size for _kind, size in encoded.writes) <= encoded.max_chunk_bytes
    assert max(source.read_sizes) <= source.max_chunk_bytes

    with pytest.raises(DirectorManagedSegmentSpillError):
        _encode(codec, torch.zeros((2, 3, 4), dtype=torch.float32, device="cpu"))
    with pytest.raises(DirectorManagedSegmentSpillError):
        _encode(codec, torch.zeros((0, 2, 2, 3), dtype=torch.float32, device="cpu"))
    with pytest.raises(DirectorManagedSegmentSpillError):
        _encode(codec, torch.zeros((1, 2, 2, 3), dtype=torch.int64, device="cpu"))
    non_finite = torch.zeros((1, 2, 2, 3), dtype=torch.float32, device="cpu")
    non_finite[0, 0, 0, 0] = float("nan")
    with pytest.raises(DirectorManagedSegmentSpillError):
        _encode(codec, non_finite)


def test_tensor_descriptor_accepts_near_capacity_and_rejects_theoretical_planner_maximum():
    default_shape = (121, 768, 1_376, 3)
    default = validate_segment_tensor_descriptor(default_shape, "torch.float32")
    assert default.numel == 383_606_784
    assert default.byte_count == 1_534_427_136

    near_capacity = validate_segment_tensor_descriptor(
        (93, 1_280, 2_992, 3),
        "torch.float16",
    )
    assert near_capacity.byte_count == 2_137_006_080
    assert near_capacity.byte_count < MAX_SEGMENT_TENSOR_BYTES
    with pytest.raises(DirectorManagedSegmentSpillError):
        validate_segment_tensor_descriptor(
            (94, 1_280, 2_992, 3),
            "torch.float16",
        )

    maximum_shape = (
        MAX_SEGMENT_FRAMES,
        DIRECTOR_MAX_SHORT_EDGE,
        DIRECTOR_MAX_LONG_EDGE,
        3,
    )
    assert MAX_SEGMENT_FRAMES == 864_001
    assert DIRECTOR_MAX_LONG_EDGE == 2_992
    assert MAX_SEGMENT_TENSOR_NUMEL == MAX_SEGMENT_TENSOR_BYTES // 2
    with pytest.raises(DirectorManagedSegmentSpillError):
        validate_segment_tensor_descriptor(maximum_shape, "torch.float16")


@pytest.mark.parametrize(
    "shape",
    (
        (MAX_SEGMENT_FRAMES + 1, 1_280, 2_992, 3),
        (MAX_SEGMENT_FRAMES, 1_281, 2_992, 3),
        (MAX_SEGMENT_FRAMES, 1_280, 2_993, 3),
        (MAX_SEGMENT_FRAMES, 1_280, 2_992, 4),
    ),
)
def test_tensor_descriptor_rejects_just_over_declared_planner_limits(shape):
    with pytest.raises(DirectorManagedSegmentSpillError):
        validate_segment_tensor_descriptor(shape, "torch.float32")


def test_tensor_payload_decoder_validates_strict_header_byte_count_and_exact_eof():
    codec = DirectorSegmentTensorPayloadAdapter()
    tensor = _cpu_tensor(0, 1)
    valid = _encode(codec, tensor).payload
    header = list(SEGMENT_TENSOR_HEADER.unpack(valid[:SEGMENT_TENSOR_HEADER_BYTES]))
    assert header[0] == SEGMENT_TENSOR_MAGIC
    assert header[3] == SEGMENT_TENSOR_RANK
    assert header[-1] == tensor.numel() * tensor.element_size()

    invalid_headers = []
    for index, replacement in (
        (0, b"BADMAGIC"),
        (1, SEGMENT_SPILL_FORMAT_VERSION + 1),
        (2, 99),
        (3, SEGMENT_TENSOR_RANK - 1),
        (4, 1),
        (8, 4),
        (9, header[-1] + 1),
    ):
        candidate = list(header)
        candidate[index] = replacement
        invalid_headers.append(
            bytearray(SEGMENT_TENSOR_HEADER.pack(*candidate))
            + valid[SEGMENT_TENSOR_HEADER_BYTES:]
        )
    invalid_payloads = (
        *invalid_headers,
        valid[: SEGMENT_TENSOR_HEADER_BYTES - 1],
        valid[:-1],
        valid + b"x",
    )
    for invalid in invalid_payloads:
        with pytest.raises(DirectorManagedSegmentSpillError):
            _decode(codec, invalid)


def test_tensor_codec_rejects_oversize_header_before_tensor_allocation(monkeypatch):
    codec = DirectorSegmentTensorPayloadAdapter()
    oversized = SEGMENT_TENSOR_HEADER.pack(
        SEGMENT_TENSOR_MAGIC,
        SEGMENT_SPILL_FORMAT_VERSION,
        1,
        SEGMENT_TENSOR_RANK,
        0,
        94,
        1_280,
        2_992,
        3,
        94 * 1_280 * 2_992 * 3 * 2,
    )
    monkeypatch.setattr(
        managed_segment_spills.torch,
        "empty",
        lambda *_args, **_kwargs: pytest.fail("oversize header allocated a tensor"),
    )
    with pytest.raises(DirectorManagedSegmentSpillError):
        _decode(codec, oversized)


def test_tensor_codec_finite_checks_are_chunk_bounded_without_whole_mask(monkeypatch):
    codec = DirectorSegmentTensorPayloadAdapter()
    tensor = _cpu_tensor(0, 3)
    original = torch.isfinite
    observed = []

    def bounded(value):
        observed.append(value.numel())
        assert value.numel() <= 16
        return original(value)

    monkeypatch.setattr(managed_segment_spills.torch, "isfinite", bounded)
    encoded = _encode(codec, tensor, chunk_bytes=64)
    monkeypatch.setattr(managed_segment_spills, "FINITE_CHECK_CHUNK_BYTES", 64)
    decoded, _source = _decode(codec, encoded.payload, chunk_bytes=64)
    assert torch.equal(decoded, tensor)
    assert observed
    assert max(observed) <= 16


def test_tensor_codec_resource_errors_are_sanitized():
    codec = DirectorSegmentTensorPayloadAdapter()
    with pytest.raises(DirectorManagedSegmentSpillError) as error:
        _decode(codec, b"SYNTHETIC_PRIVATE_PAYLOAD")
    assert str(error.value) == "Director managed segment spill operation failed."
    assert "SYNTHETIC_PRIVATE_PAYLOAD" not in str(error.value)


def test_tensor_codec_does_not_swallow_stream_cancellation_base_exceptions():
    codec = DirectorSegmentTensorPayloadAdapter()
    tensor = _cpu_tensor(0, 1)
    encoded = _encode(codec, tensor)
    encode_abort = _Abort("synthetic encode cancellation")
    decode_abort = _Abort("synthetic decode cancellation")

    def abort_encode(_value):
        raise encode_abort

    def abort_decode(_value):
        raise decode_abort

    sink = _MemorySink()
    sink.write = abort_encode
    with pytest.raises(_Abort) as encode_error:
        codec.encode_to(tensor, sink)
    assert encode_error.value is encode_abort

    source = _MemorySource(encoded.payload)
    source.readinto = abort_decode
    with pytest.raises(_Abort) as decode_error:
        codec.decode_from(source)
    assert decode_error.value is decode_abort


def test_managed_session_writes_reads_stitches_and_closes_exactly_once():
    async def scenario():
        handle = _FakeHandle()
        session = DirectorManagedSegmentSpillSession(handle)
        first = _cpu_tensor(0, 2)
        second = _cpu_tensor(100, 3)
        async with session:
            records = [
                await session.write_segment(first),
                await session.write_segment(second),
            ]
            stitched = await session.stitch(records, final_frame_count=7)
        assert await session.close() == 0
        return handle, first, second, stitched

    handle, first, second, stitched = asyncio.run(scenario())
    assert stitched.device.type == "cpu"
    assert torch.equal(stitched[:2], first)
    assert torch.equal(stitched[2:5], second)
    assert torch.equal(stitched[5:], second[-1:].repeat((2, 1, 1, 1)))
    assert handle.write_calls == 2
    assert handle.read_calls == 2
    assert handle.close_calls == 1
    assert handle.payloads == {}


def test_synchronous_managed_store_preserves_runtime_stitch_contract_without_paths():
    handle = _FakeHandle()
    store = DirectorManagedSegmentSpillStore(handle, private=True)
    first = _cpu_tensor(0, 2)
    second = _cpu_tensor(100, 1)
    records = [
        store.write_segment({"id": "private-segment-one"}, first),
        store.write_segment({"id": "private-segment-two"}, second),
    ]

    stitched = stitch_spilled_segment_images(records, store, final_frame_count=4)
    summary = store.cleanup()

    assert torch.equal(stitched[:2], first)
    assert torch.equal(stitched[2:], second.repeat((2, 1, 1, 1)))
    assert summary == {
        "segment_storage": "managed_run_scoped_spill",
        "privacy_mode": True,
        "encrypted": True,
        "files_written": 2,
        "files_read": 2,
        "files_deleted": 2,
        "record_count": 2,
        "cleanup_pending": False,
    }
    assert "path" not in repr(records)
    assert "private-segment" not in repr(records)
    assert handle.payloads == {}


@pytest.mark.parametrize(
    ("failure_point", "failure"),
    (
        ("write", RuntimeError("synthetic write failure")),
        ("read", RuntimeError("synthetic read failure")),
        ("body", RuntimeError("synthetic body failure")),
        ("base", _Abort("synthetic interruption")),
    ),
)
def test_managed_session_closes_once_on_write_read_exception_and_base_exception(
    failure_point,
    failure,
):
    async def scenario():
        handle = _FakeHandle()
        if failure_point == "write":
            handle.write_failure = failure
        if failure_point == "read":
            handle.read_failure = failure
        session = DirectorManagedSegmentSpillSession(handle)
        try:
            async with session:
                record = await session.write_segment(_cpu_tensor(0, 1))
                if failure_point == "read":
                    await session.read_segment(record)
                elif failure_point in {"body", "base"}:
                    raise failure
        except BaseException as caught:
            assert caught is failure
        else:
            raise AssertionError("Expected synthetic spill failure.")
        assert await session.close() == 0
        return handle

    handle = asyncio.run(scenario())
    assert handle.close_calls == 1
    assert handle.payloads == {}


def test_managed_session_close_failure_is_sanitized_and_allows_explicit_retry():
    async def scenario():
        handle = _FakeHandle()
        failure = RuntimeError("synthetic close failure")
        handle.close_failure = failure
        session = DirectorManagedSegmentSpillSession(handle)
        try:
            async with session:
                await session.write_segment(_cpu_tensor(0, 1))
        except DirectorManagedSegmentSpillError as caught:
            assert "synthetic" not in str(caught)
        else:
            raise AssertionError("Expected synthetic close failure.")
        handle.close_failure = None
        assert await session.close() == 1
        assert await session.close() == 0
        return handle

    handle = asyncio.run(scenario())
    assert handle.close_calls == 2


@pytest.mark.parametrize("final_frame_count", (3, 6))
def test_managed_stitch_matches_wan_ltx_trim_clamp_and_pad_semantics(
    final_frame_count,
):
    async def scenario():
        handle = _FakeHandle()
        first = trim_visible_segment_images(
            _cpu_tensor(0, 4),
            {
                "trim_leading_frames": 1,
                "trim_trailing_frames": 1,
                "visible_frame_count": 2,
            },
        )
        second = trim_visible_segment_images(
            _cpu_tensor(100, 4),
            {
                "trim_leading_frames": 0,
                "trim_trailing_frames": 1,
                "visible_frame_count": 3,
            },
        )
        async with DirectorManagedSegmentSpillSession(handle) as session:
            records = [
                await session.write_segment(first),
                await session.write_segment(second),
            ]
            actual = await session.stitch(
                records,
                final_frame_count=final_frame_count,
            )
        expected = stitch_spilled_segment_images(
            [{"tensor": first}, {"tensor": second}],
            _ExistingStore(),
            final_frame_count=final_frame_count,
        )
        return actual, expected

    actual, expected = asyncio.run(scenario())
    assert torch.equal(actual, expected)


@pytest.mark.parametrize(
    "final_frame_count",
    (True, "8", -1, MAX_STITCH_FRAME_COUNT + 1),
)
def test_managed_stitch_rejects_invalid_or_unbounded_final_frame_count(
    final_frame_count,
):
    async def scenario():
        handle = _FakeHandle()
        async with DirectorManagedSegmentSpillSession(handle) as session:
            record = await session.write_segment(_cpu_tensor(0, 1))
            with pytest.raises(DirectorManagedSegmentSpillError):
                await session.stitch(
                    [record],
                    final_frame_count=final_frame_count,
                )
        return handle

    handle = asyncio.run(scenario())
    assert handle.close_calls == 1


def test_managed_stitch_enforces_record_and_aggregate_numel_limits(monkeypatch):
    async def scenario():
        handle = _FakeHandle()
        async with DirectorManagedSegmentSpillSession(handle) as session:
            records = [
                await session.write_segment(_cpu_tensor(0, 1)),
                await session.write_segment(_cpu_tensor(100, 1)),
            ]
            monkeypatch.setattr(
                managed_segment_spills,
                "MAX_SEGMENT_SPILL_RECORDS",
                1,
            )
            with pytest.raises(DirectorManagedSegmentSpillError):
                await session.stitch(records, final_frame_count=2)
            monkeypatch.setattr(
                managed_segment_spills,
                "MAX_SEGMENT_SPILL_RECORDS",
                4_096,
            )
            monkeypatch.setattr(
                managed_segment_spills,
                "MAX_STITCH_AGGREGATE_NUMEL",
                1,
            )
            with pytest.raises(DirectorManagedSegmentSpillError):
                await session.stitch(records, final_frame_count=2)
        return handle

    handle = asyncio.run(scenario())
    assert handle.close_calls == 1
    assert handle.payloads == {}


def test_stitch_descriptor_enforces_output_working_set_and_owner_quota_without_allocation():
    records = tuple(
        ManagedSegmentSpillRecord(
            f"opaque-{index}",
            93,
            (93, 1_280, 2_992, 3),
            "torch.float16",
        )
        for index in range(5)
    )
    plan = validate_segment_stitch_descriptors(
        records[:4],
        final_frame_count=93,
    )
    assert plan.output_bytes == 2_137_006_080
    assert plan.output_bytes <= MAX_STITCH_OUTPUT_BYTES
    assert plan.aggregate_bytes == 8_548_024_320
    assert plan.aggregate_bytes <= MAX_SEGMENT_OWNER_PLAINTEXT_BYTES
    assert plan.working_set_bytes == (
        plan.output_bytes * 2 + STITCH_WORKING_OVERHEAD_BYTES
    )
    assert plan.working_set_bytes <= MAX_STITCH_WORKING_SET_BYTES

    with pytest.raises(DirectorManagedSegmentSpillError):
        validate_segment_stitch_descriptors(records, final_frame_count=93)
    with pytest.raises(DirectorManagedSegmentSpillError):
        validate_segment_stitch_descriptors(
            records[:1],
            final_frame_count=94,
        )


def test_managed_stitch_rejects_malformed_record_before_shared_read():
    async def scenario():
        handle = _FakeHandle()
        async with DirectorManagedSegmentSpillSession(handle) as session:
            malformed = managed_segment_spills.ManagedSegmentSpillRecord(
                "opaque-malformed",
                1,
                (1, 2, 2, "3"),
                "torch.float32",
            )
            with pytest.raises(DirectorManagedSegmentSpillError):
                await session.stitch([malformed], final_frame_count=1)
        return handle

    handle = asyncio.run(scenario())
    assert handle.read_calls == 0
    assert handle.close_calls == 1


def test_spill_fragment_has_no_model_logic_live_imports_or_path_payloads():
    module_path = Path(
        "shared/timeline/managed_segment_spills.py"
    )
    source = module_path.read_text(encoding="utf-8")
    assert "shared.wan" not in source
    assert "shared.ltx" not in source
    assert "media_path" not in source
    assert "lease(" not in source
    for forbidden in (
        "io.BytesIO",
        "torch.save(",
        "torch.load(",
        ".getvalue(",
        "bytes(value)",
    ):
        assert forbidden not in source
    assert "def encode_to(" in source
    assert "def decode_from(" in source
    assert "torch.isfinite(tensor).all" not in source
    executor_source = Path("shared/segmented_executor.py").read_text(encoding="utf-8")
    assert "DirectorManagedSegmentSpillStore" in executor_source
    for live_path in (
        Path("shared/wan/runtime/segmented.py"),
        Path("shared/ltx/runtime/segmented.py"),
    ):
        live_source = live_path.read_text(encoding="utf-8")
        assert "managed_segment_spill_store" in live_source
        assert "SegmentSpillStore(" not in live_source


def test_spill_inventory_covers_plain_temp_encrypted_debug_and_cleanup_derivatives():
    patterns = " ".join(SEGMENT_SPILL_LEGACY_DERIVATIVES)
    inventory = " ".join(
        item.location for item in SEGMENT_SPILL_PLAINTEXT_DERIVATIVE_INVENTORY
    )
    assert "*.pt" in patterns
    assert ".tmp" in patterns
    assert ".pt.enc" in patterns
    assert "run ids" in patterns
    assert "cleanup warnings" in patterns
    assert "*.pt" in inventory
    assert "*.pt.enc" in inventory
    assert "debug" in inventory
    gaps = " ".join(SEGMENT_SPILL_ACTIVATION_GAPS)
    assert "streaming" not in gaps
    assert "activation-wiring" not in gaps
    assert "dual-mode" not in gaps
    assert "safe-payload" not in gaps
    assert "deferred-association" not in gaps
