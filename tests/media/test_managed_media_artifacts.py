from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass

import pytest

import shared.timeline.managed_media_artifacts as managed_media_artifacts
from shared.timeline.managed_media_artifacts import (
    MEDIA_ARTIFACT_CONTRACTS,
    MEDIA_ARTIFACT_RESOURCE_ID,
    THUMBNAIL_ARTIFACT_ADAPTER_ID,
    THUMBNAIL_ARTIFACT_KIND,
    THUMBNAIL_LEGACY_DERIVATIVES,
    WAVEFORM_ARTIFACT_ADAPTER_ID,
    WAVEFORM_ARTIFACT_KIND,
    WAVEFORM_LEGACY_DERIVATIVES,
    DirectorManagedMediaArtifactError,
    DirectorManagedMediaArtifacts,
    build_director_media_artifact_privacy_profile,
    build_director_media_artifact_server_adapters,
    normalized_media_parameter_key,
)
from shared.timeline.managed_privacy import (
    DIRECTOR_DISTRIBUTION,
    DIRECTOR_PROFILE_ID,
    GLOBAL_SCOPE_ID,
    build_director_timeline_server_adapters,
)


@dataclass(frozen=True)
class _Reference:
    id: int


class _Handle:
    def __init__(self) -> None:
        self.values: dict[_Reference, bytes] = {}
        self.write_calls: list[tuple[str, str, bytes]] = []
        self.retire_calls: list[tuple[str, _Reference]] = []
        self.lease_calls: list[tuple[str, _Reference, str, object]] = []
        self.sweeps = 0

    async def write(self, artifact_kind, owner_id, value):
        reference = _Reference(len(self.write_calls) + 1)
        payload = bytes(value)
        self.write_calls.append((artifact_kind, owner_id, payload))
        self.values[reference] = payload
        return reference

    async def read(self, _artifact_kind, reference):
        return self.values[reference]

    async def retire(self, artifact_kind, reference):
        self.retire_calls.append((artifact_kind, reference))
        return int(self.values.pop(reference, None) is not None)

    async def sweep(self):
        self.sweeps += 1
        return {"swept": self.sweeps}

    async def lease(self, artifact_kind, reference, operation, authorization):
        self.lease_calls.append((artifact_kind, reference, operation, authorization))
        return {"url": "/helto_privacy/artifacts/opaque", "operation": operation}


class _BlockedV1WriteHandle(_Handle):
    def __init__(self) -> None:
        super().__init__()
        self.v1_write_started = asyncio.Event()
        self.release_v1_write = asyncio.Event()

    async def write(self, artifact_kind, owner_id, value):
        reference = await super().write(artifact_kind, owner_id, value)
        if b"SYNTHETIC_VERSION_ONE" in bytes(value):
            self.v1_write_started.set()
            await asyncio.wait_for(self.release_v1_write.wait(), timeout=5)
        return reference


def test_d4_composes_exact_artifact_contract_into_inactive_d2_profile():
    profile = build_director_media_artifact_privacy_profile()

    assert profile.id == DIRECTOR_PROFILE_ID == "helto.director"
    assert profile.distribution == DIRECTOR_DISTRIBUTION == "comfyui-helto-director"
    resource = {item.id: item for item in profile.resources}[MEDIA_ARTIFACT_RESOURCE_ID]
    assert resource.kind.value == "artifact"
    assert resource.adapter_slots == (
        THUMBNAIL_ARTIFACT_ADAPTER_ID,
        WAVEFORM_ARTIFACT_ADAPTER_ID,
    )
    declarations = {item.id: item for item in profile.artifacts}
    assert set(declarations) == {THUMBNAIL_ARTIFACT_KIND, WAVEFORM_ARTIFACT_KIND}

    thumbnail = declarations[THUMBNAIL_ARTIFACT_KIND]
    assert thumbnail.scope_id == GLOBAL_SCOPE_ID == "director-global"
    assert thumbnail.purpose == "timeline-thumbnail-cache"
    assert thumbnail.payload_adapter == THUMBNAIL_ARTIFACT_ADAPTER_ID
    assert thumbnail.format_version == 1
    assert thumbnail.retention.value == "regenerable-cache"
    assert thumbnail.operations == ("preview",)
    assert thumbnail.media_type == "image/webp"

    waveform = declarations[WAVEFORM_ARTIFACT_KIND]
    assert waveform.scope_id == GLOBAL_SCOPE_ID
    assert waveform.purpose == "timeline-waveform-cache"
    assert waveform.payload_adapter == WAVEFORM_ARTIFACT_ADAPTER_ID
    assert waveform.format_version == 1
    assert waveform.retention.value == "regenerable-cache"
    assert waveform.operations == ("preview",)
    assert waveform.media_type == "application/json"
    assert profile.legacy_bindings == ()
    assert profile.protected_operations == ()

    contracts = {item.declaration.id: item for item in MEDIA_ARTIFACT_CONTRACTS}
    assert contracts[THUMBNAIL_ARTIFACT_KIND].requires_allowed_root_fd_validation is True
    assert contracts[WAVEFORM_ARTIFACT_KIND].requires_allowed_root_fd_validation is True


def test_d4_adapter_fragment_completes_composed_profile_contract():
    profile = build_director_media_artifact_privacy_profile()
    adapters = {
        **build_director_timeline_server_adapters(),
        **build_director_media_artifact_server_adapters(),
    }

    assert set(adapters) == {slot.id for slot in profile.server_adapters}
    for adapter_id, methods in profile.server_adapter_contracts.items():
        assert all(callable(getattr(adapters[adapter_id], method, None)) for method in methods)


def test_legacy_derivative_inventory_and_purge_cover_webp_json_tmp_and_enc(tmp_path):
    assert THUMBNAIL_LEGACY_DERIVATIVES == (
        "thumbnails/*.webp",
        "thumbnails/*.webp.tmp",
        "thumbnails/.*.webp.*.tmp",
        "thumbnails/*.webp.enc",
        "thumbnails/*.webp.enc.tmp",
        "thumbnails/.*.webp.enc.*.tmp",
    )
    assert WAVEFORM_LEGACY_DERIVATIVES == (
        "waveforms/*.json",
        "waveforms/*.json.tmp",
        "waveforms/.*.json.*.tmp",
        "waveforms/*.json.enc",
        "waveforms/*.json.enc.tmp",
        "waveforms/.*.json.enc.*.tmp",
    )
    paths = [
        tmp_path / "thumbnails/a.webp",
        tmp_path / "thumbnails/a.webp.tmp",
        tmp_path / "thumbnails/.a.webp.unique.tmp",
        tmp_path / "thumbnails/a.webp.enc",
        tmp_path / "thumbnails/a.webp.enc.tmp",
        tmp_path / "thumbnails/.a.webp.enc.unique.tmp",
        tmp_path / "waveforms/a.json",
        tmp_path / "waveforms/a.json.tmp",
        tmp_path / "waveforms/.a.json.unique.tmp",
        tmp_path / "waveforms/a.json.enc",
        tmp_path / "waveforms/a.json.enc.tmp",
        tmp_path / "waveforms/.a.json.enc.unique.tmp",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic-cache")
    unrelated = tmp_path / "thumbnails/keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    adapters = build_director_media_artifact_server_adapters(cache_root=tmp_path)
    adapters[THUMBNAIL_ARTIFACT_ADAPTER_ID].purge_plaintext_derivatives(
        THUMBNAIL_ARTIFACT_KIND
    )
    adapters[WAVEFORM_ARTIFACT_ADAPTER_ID].purge_plaintext_derivatives(
        WAVEFORM_ARTIFACT_KIND
    )

    assert all(not path.exists() for path in paths)
    assert unrelated.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("artifact_kind", [THUMBNAIL_ARTIFACT_KIND, WAVEFORM_ARTIFACT_KIND])
def test_legacy_purge_rejects_symlinked_cache_directory_without_touching_target(
    tmp_path,
    artifact_kind,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    suffix = "webp" if artifact_kind == THUMBNAIL_ARTIFACT_KIND else "json"
    target = outside / f"keep.{suffix}"
    target.write_bytes(b"OUTSIDE_CANARY")
    cache = tmp_path / "cache"
    cache.mkdir()
    directory = "thumbnails" if artifact_kind == THUMBNAIL_ARTIFACT_KIND else "waveforms"
    try:
        (cache / directory).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    adapter_id = (
        THUMBNAIL_ARTIFACT_ADAPTER_ID
        if artifact_kind == THUMBNAIL_ARTIFACT_KIND
        else WAVEFORM_ARTIFACT_ADAPTER_ID
    )
    adapter = build_director_media_artifact_server_adapters(cache_root=cache)[adapter_id]

    with pytest.raises(
        DirectorManagedMediaArtifactError,
        match="Director media artifact operation failed",
    ) as error:
        adapter.purge_plaintext_derivatives(artifact_kind)

    assert str(outside) not in str(error.value)
    assert target.read_bytes() == b"OUTSIDE_CANARY"


def test_legacy_purge_rejects_symlinked_derivative_without_touching_target(tmp_path):
    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"OUTSIDE_CANARY")
    cache = tmp_path / "cache"
    thumbnails = cache / "thumbnails"
    thumbnails.mkdir(parents=True)
    try:
        (thumbnails / "linked.webp").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")
    adapter = build_director_media_artifact_server_adapters(cache_root=cache)[
        THUMBNAIL_ARTIFACT_ADAPTER_ID
    ]

    with pytest.raises(
        DirectorManagedMediaArtifactError,
        match="Director media artifact operation failed",
    ) as error:
        adapter.purge_plaintext_derivatives(THUMBNAIL_ARTIFACT_KIND)

    assert str(outside) not in str(error.value)
    assert outside.read_bytes() == b"OUTSIDE_CANARY"
    assert (thumbnails / "linked.webp").is_symlink()


def test_legacy_purge_ancestor_swap_fails_without_unlinking_outside(
    tmp_path,
    monkeypatch,
):
    anchor = tmp_path / "anchor"
    cache = anchor / "cache"
    thumbnails = cache / "thumbnails"
    thumbnails.mkdir(parents=True)
    inside = thumbnails / "inside.webp"
    inside.write_bytes(b"INSIDE_CANARY")
    detached = tmp_path / "detached-anchor"
    outside_anchor = tmp_path / "outside-anchor"
    outside = outside_anchor / "cache/thumbnails/outside.webp"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"OUTSIDE_CANARY")
    original_open = managed_media_artifacts.os.open
    swapped = False

    def adversarial_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "cache" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            anchor.rename(detached)
            outside_anchor.rename(anchor)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(managed_media_artifacts.os, "open", adversarial_open)
    adapter = build_director_media_artifact_server_adapters(cache_root=cache)[
        THUMBNAIL_ARTIFACT_ADAPTER_ID
    ]

    with pytest.raises(DirectorManagedMediaArtifactError):
        adapter.purge_plaintext_derivatives(THUMBNAIL_ARTIFACT_KIND)

    assert swapped is True
    assert (anchor / "cache/thumbnails/outside.webp").read_bytes() == b"OUTSIDE_CANARY"
    assert (detached / "cache/thumbnails/inside.webp").read_bytes() == b"INSIDE_CANARY"


def test_normalized_parameter_key_has_no_mode_dimension(tmp_path):
    source = tmp_path / "synthetic.png"
    source.write_bytes(b"synthetic")

    public_shape = normalized_media_parameter_key(
        THUMBNAIL_ARTIFACT_KIND,
        source,
        {"max_size": 320, "privacy": False},
    )
    private_shape = normalized_media_parameter_key(
        THUMBNAIL_ARTIFACT_KIND,
        source,
        {"max_size": 320, "privacy": True},
    )

    assert public_shape == private_shape
    assert public_shape != normalized_media_parameter_key(
        THUMBNAIL_ARTIFACT_KIND,
        source,
        {"max_size": 640},
    )


def test_thumbnail_generation_is_single_flight_and_lease_is_shared(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    source = root / "synthetic.png"
    source.write_bytes(b"SYNTHETIC_IMAGE_BYTES")
    handle = _Handle()
    encoder_calls = []

    def encode(stream, suffix, max_size):
        encoder_calls.append((suffix, max_size))
        time.sleep(0.03)
        return b"RIFF_SYNTHETIC_WEBP_" + stream.read()

    service = DirectorManagedMediaArtifacts(
        handle,
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=encode,
        waveform_encoder=lambda *_args: {"peaks": []},
    )

    async def exercise():
        first, second = await asyncio.gather(
            service.thumbnail(source, 320),
            service.thumbnail(source, 320),
        )
        lease = await service.thumbnail_lease(source, {"synthetic": "authorization"})
        return first, second, lease

    first, second, lease = asyncio.run(exercise())

    assert first == second
    assert first[1] == b"RIFF_SYNTHETIC_WEBP_SYNTHETIC_IMAGE_BYTES"
    assert encoder_calls == [(".png", 320)]
    assert len(handle.write_calls) == 1
    assert handle.lease_calls[0][0] == THUMBNAIL_ARTIFACT_KIND
    assert handle.lease_calls[0][2] == "preview"
    assert lease["url"] == "/helto_privacy/artifacts/opaque"


def test_single_flight_is_revision_bound_and_v2_never_joins_blocked_v1(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    source = root / "synthetic.png"
    source.write_bytes(b"SYNTHETIC_VERSION_ONE")
    handle = _Handle()
    first_started = threading.Event()
    release_first = threading.Event()

    def encode(stream, _suffix, _max_size):
        payload = stream.read()
        if payload == b"SYNTHETIC_VERSION_ONE":
            first_started.set()
            if not release_first.wait(timeout=5):
                raise AssertionError("synthetic v1 encoder was not released")
        return b"WEBP_" + payload

    service = DirectorManagedMediaArtifacts(
        handle,
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=encode,
        waveform_encoder=lambda *_args: {"peaks": []},
    )

    async def exercise():
        first = asyncio.create_task(service.thumbnail(source))
        for _attempt in range(200):
            if first_started.is_set():
                break
            await asyncio.sleep(0.005)
        assert first_started.is_set()
        source.write_bytes(b"SYNTHETIC_VERSION_TWO_IS_NEW")
        try:
            second = await asyncio.wait_for(service.thumbnail(source), timeout=2)
        finally:
            release_first.set()
        with pytest.raises(DirectorManagedMediaArtifactError):
            await first
        return second

    second_reference, second_payload = asyncio.run(exercise())

    assert second_payload == b"WEBP_SYNTHETIC_VERSION_TWO_IS_NEW"
    assert handle.values[second_reference] == second_payload
    assert b"SYNTHETIC_VERSION_ONE" not in second_payload
    assert len(handle.write_calls) == 1


def test_out_of_order_v1_write_cannot_replace_published_v2(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    source = root / "synthetic.png"
    source.write_bytes(b"SYNTHETIC_VERSION_ONE")
    handle = _BlockedV1WriteHandle()

    def encode(stream, _suffix, _max_size):
        return b"WEBP_" + stream.read()

    service = DirectorManagedMediaArtifacts(
        handle,
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=encode,
        waveform_encoder=lambda *_args: {"peaks": []},
    )

    async def exercise():
        first = asyncio.create_task(service.thumbnail(source))
        await asyncio.wait_for(handle.v1_write_started.wait(), timeout=2)
        source.write_bytes(b"SYNTHETIC_VERSION_TWO_IS_NEW")
        try:
            second = await asyncio.wait_for(service.thumbnail(source), timeout=2)
        finally:
            handle.release_v1_write.set()
        first_result = await asyncio.wait_for(first, timeout=2)
        third = await service.thumbnail(source)
        return first_result, second, third

    first, second, third = asyncio.run(exercise())

    assert first == second == third
    second_reference, second_payload = second
    assert second_payload == b"WEBP_SYNTHETIC_VERSION_TWO_IS_NEW"
    assert len(handle.write_calls) == 2
    stale_reference = _Reference(1)
    assert handle.retire_calls == [(THUMBNAIL_ARTIFACT_KIND, stale_reference)]
    assert handle.values == {second_reference: second_payload}
    assert service.cache_entry_count == 1


def test_waveform_regeneration_uses_product_json_encoding_and_shared_retirement(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    source = root / "synthetic.wav"
    source.write_bytes(b"SYNTHETIC_AUDIO_V1")
    handle = _Handle()

    def encode(stream, suffix, peaks):
        assert stream.read().startswith(b"SYNTHETIC_AUDIO")
        return {
            "duration_seconds": 1.5,
            "sample_rate": 8_000,
            "channels": 1,
            "peaks": [0.25] * peaks,
            "suffix": suffix,
        }

    service = DirectorManagedMediaArtifacts(
        handle,
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=lambda *_args: b"unused",
        waveform_encoder=encode,
    )

    first_reference, first = asyncio.run(service.waveform(source, 2))
    assert len(first["peaks"]) == 16
    assert set(first) == {"duration_seconds", "sample_rate", "channels", "peaks"}
    source.write_bytes(b"SYNTHETIC_AUDIO_VERSION_TWO")
    second_reference, second = asyncio.run(service.waveform(source, 2))

    assert set(second) == {"duration_seconds", "sample_rate", "channels", "peaks"}
    assert second_reference != first_reference
    assert handle.retire_calls == [(WAVEFORM_ARTIFACT_KIND, first_reference)]
    assert len(handle.write_calls) == 2


@pytest.mark.parametrize(
    "invalid_field,invalid_value",
    [
        ("duration_seconds", object()),
        ("duration_seconds", float("nan")),
        ("duration_seconds", 31_536_001),
        ("sample_rate", 0),
        ("sample_rate", 768_001),
        ("channels", -1),
        ("channels", 65),
        ("peaks", [1.1] * 16),
    ],
)
def test_waveform_payload_validation_fails_closed(
    tmp_path,
    invalid_field,
    invalid_value,
):
    root = tmp_path / "media"
    root.mkdir()
    source = root / "synthetic.wav"
    source.write_bytes(b"SYNTHETIC_AUDIO")
    handle = _Handle()
    payload = {
        "duration_seconds": 1.0,
        "sample_rate": 8_000,
        "channels": 1,
        "peaks": [0.25] * 16,
    }
    payload[invalid_field] = invalid_value
    service = DirectorManagedMediaArtifacts(
        handle,
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=lambda *_args: b"unused",
        waveform_encoder=lambda *_args: payload,
    )

    with pytest.raises(DirectorManagedMediaArtifactError):
        asyncio.run(service.waveform(source, 16))

    assert handle.write_calls == []


def test_waveform_json_serialization_failure_is_sanitized(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "media"
    root.mkdir()
    source = root / "synthetic.wav"
    source.write_bytes(b"SYNTHETIC_AUDIO")
    handle = _Handle()
    original_dumps = managed_media_artifacts.json.dumps

    def fail_waveform_dumps(value, *args, **kwargs):
        if set(value) == {"duration_seconds", "sample_rate", "channels", "peaks"}:
            raise TypeError("SYNTHETIC_SERIALIZATION_FAILURE")
        return original_dumps(value, *args, **kwargs)

    monkeypatch.setattr(managed_media_artifacts.json, "dumps", fail_waveform_dumps)
    service = DirectorManagedMediaArtifacts(
        handle,
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=lambda *_args: b"unused",
        waveform_encoder=lambda *_args: {
            "duration_seconds": 1.0,
            "sample_rate": 8_000,
            "channels": 1,
            "peaks": [0.25] * 16,
        },
    )

    with pytest.raises(
        DirectorManagedMediaArtifactError,
        match="Director media artifact operation failed",
    ) as error:
        asyncio.run(service.waveform(source, 16))

    assert "SYNTHETIC_SERIALIZATION_FAILURE" not in str(error.value)
    assert handle.write_calls == []


def test_allowed_root_and_fd_stat_changes_fail_closed_with_synthetic_bytes(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    source = root / "synthetic.png"
    source.write_bytes(b"BEFORE")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"OUTSIDE")
    handle = _Handle()

    service = DirectorManagedMediaArtifacts(
        handle,
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=lambda stream, _suffix, _size: stream.read(),
        waveform_encoder=lambda *_args: {"peaks": []},
    )
    with pytest.raises(DirectorManagedMediaArtifactError):
        asyncio.run(service.thumbnail(outside))

    try:
        (root / "linked.png").symlink_to(outside)
    except OSError:
        pass
    else:
        with pytest.raises(DirectorManagedMediaArtifactError):
            asyncio.run(service.thumbnail(root / "linked.png"))

    def mutate_during_encode(stream, _suffix, _size):
        payload = stream.read()
        source.write_bytes(b"CHANGED_AFTER_OPEN")
        return payload

    changing = DirectorManagedMediaArtifacts(
        handle,
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=mutate_during_encode,
        waveform_encoder=lambda *_args: {"peaks": []},
    )
    with pytest.raises(DirectorManagedMediaArtifactError):
        asyncio.run(changing.thumbnail(source))
    assert handle.write_calls == []


def test_allowed_root_ancestor_swap_fails_without_reading_outside(
    tmp_path,
    monkeypatch,
):
    anchor = tmp_path / "anchor"
    root = anchor / "allowed"
    root.mkdir(parents=True)
    source = root / "synthetic.png"
    source.write_bytes(b"INSIDE_CANARY")
    detached = tmp_path / "detached-anchor"
    outside_anchor = tmp_path / "outside-anchor"
    outside_source = outside_anchor / "allowed/synthetic.png"
    outside_source.parent.mkdir(parents=True)
    outside_source.write_bytes(b"OUTSIDE_CANARY")
    original_open = managed_media_artifacts.os.open
    swapped = False
    encoder_reads = []

    def adversarial_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "allowed" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            anchor.rename(detached)
            outside_anchor.rename(anchor)
        return original_open(path, flags, *args, **kwargs)

    def encode(stream, _suffix, _max_size):
        payload = stream.read()
        encoder_reads.append(payload)
        return payload

    monkeypatch.setattr(managed_media_artifacts.os, "open", adversarial_open)
    service = DirectorManagedMediaArtifacts(
        _Handle(),
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=encode,
        waveform_encoder=lambda *_args: {"peaks": []},
    )

    with pytest.raises(DirectorManagedMediaArtifactError):
        asyncio.run(service.thumbnail(source))

    assert swapped is True
    assert encoder_reads in ([], [b"INSIDE_CANARY"])
    assert b"OUTSIDE_CANARY" not in encoder_reads
    assert (anchor / "allowed/synthetic.png").read_bytes() == b"OUTSIDE_CANARY"


def test_startup_recovery_delegates_storage_cleanup_to_shared_handle(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    source = root / "synthetic.png"
    source.write_bytes(b"SYNTHETIC")
    handle = _Handle()
    service = DirectorManagedMediaArtifacts(
        handle,
        authorized_roots=lambda: (str(root),),
        thumbnail_encoder=lambda stream, _suffix, _size: stream.read(),
        waveform_encoder=lambda *_args: {"peaks": []},
    )
    asyncio.run(service.thumbnail(source))
    assert service.cache_entry_count == 1

    result = asyncio.run(service.startup_recover())

    assert result == {"swept": 1}
    assert service.cache_entry_count == 0
