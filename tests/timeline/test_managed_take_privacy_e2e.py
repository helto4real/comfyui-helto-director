from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
import torch

import helto_privacy.artifacts as shared_artifacts
import helto_privacy.associations as shared_associations
import helto_privacy.envelope as shared_envelope
import helto_privacy.external_operation_state as shared_external_operations
import helto_privacy.guard as shared_guard
import helto_privacy.keystore as shared_keystore
import helto_privacy.mode_state as shared_mode_state
import helto_privacy.opaque_references as shared_references
import helto_privacy.runtime as shared_runtime
import helto_privacy.subject_mode as shared_subject_mode
import helto_privacy.suite_runtime as shared_suite_runtime
from helto_privacy import (
    ArtifactError,
    ArtifactReference,
    ExternalOperationError,
    ProtectedOperationError,
    apply_external_operation,
    install,
    lock_keystore,
    prepare_external_operation,
    resume_external_operation,
    rollback_external_operation,
)
from helto_privacy.associations import AssociationError, claim_operation_association
from helto_privacy.guard import authorize_privacy_request
from helto_privacy.mode import EffectivePrivacyMode
from helto_privacy.opaque_references import (
    OpaqueReferenceError,
    revoke_operation_references,
)
from helto_privacy.subject_mode import (
    consume_subject_mode_reference,
    prepare_subject_mode_reference,
)

from shared.timeline.defaults import create_default_video_timeline
from shared.timeline.managed_privacy import (
    GLOBAL_MODE_ADAPTER_ID,
    TIMELINE_FIELD_ID,
    build_director_timeline_server_adapters,
)
from shared.timeline.managed_durable_state import (
    CAPTURE_INDEX_ID,
    DURABLE_STATE_RESOURCE_ID,
    DURABLE_STATE_STORE_ADAPTER_ID,
    build_director_durable_state_server_adapters,
)
from shared.timeline.managed_segment_spills import (
    SEGMENT_ARTIFACT_RESOURCE_ID,
    SEGMENT_SPILL_ARTIFACT_KIND,
    DirectorManagedSegmentSpillError,
    DirectorManagedSegmentSpillSession,
    DirectorSegmentTensorPayloadAdapter,
    build_director_segment_spill_server_adapters,
    build_director_take_segment_privacy_profile,
)
from shared.timeline.managed_take_privacy import (
    ASSOCIATE_CAPTURE_OPERATION_ID,
    CAPTURE_TAKE_OPERATION_ID,
    TAKE_OPERATION_ADAPTER_ID,
    TAKE_OPERATION_RESOURCE_ID,
    TAKE_SIDECAR_ARTIFACT_KIND,
    TAKE_SIDECAR_RESOURCE_ID,
    TAKE_CAPTURE_SUBJECT_MODE_BINDING_ID,
    DirectorManagedTakeService,
    build_director_take_server_adapters,
)


CANARY = "SYNTHETIC_D6_SIDECAR_CANARY"
PASSWORD = "synthetic D6 integration password"
EXPECTED_D6_SEGMENT_PROFILE_FINGERPRINT = (
    "30ed0f60ce9d456ac6e04a4183dcf326ae5d66e08cac01c6695f82551fd23b2c"
)


class _Request:
    def __init__(self, token: str) -> None:
        self.headers = {"X-Helto-Privacy-Token": token}
        self.cookies = {}


class _ModeAdapter:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.revision = 0

    def read_declared_mode(self, _scope_id):
        return self.mode

    def write_declared_mode(self, _scope_id, mode):
        self.mode = mode

    def read_mode_source(self, _scope_id):
        return {"revision": self.revision, "declared": self.mode}

    def compare_and_set_mode_source(
        self,
        _scope_id,
        expected_revision,
        expected_declared,
        target_declared,
    ):
        if (expected_revision, expected_declared) != (self.revision, self.mode):
            raise RuntimeError("synthetic mode conflict")
        self.revision += 1
        self.mode = target_declared
        return self.read_mode_source(_scope_id)

    def classify_mode_source(self, _scope_id, prior, target):
        current = self.read_mode_source(_scope_id)
        if current == prior:
            return "prior"
        if current == target:
            return "target"
        return "diverged"

    def rollback_mode_source(self, _scope_id, target, prior):
        if self.read_mode_source(_scope_id) != target:
            raise RuntimeError("synthetic mode rollback conflict")
        self.revision += 1
        self.mode = prior["declared"]
        return self.read_mode_source(_scope_id)


class _BoundedFileSource:
    max_chunk_bytes = 1_024 * 1_024

    def __init__(self, handle) -> None:
        self.handle = handle

    def readinto(self, destination) -> int:
        return self.handle.readinto(destination)


def _timeline() -> dict:
    value = create_default_video_timeline()
    value["project"]["duration_seconds"] = 2.0
    value["director_track"]["sections"] = [{
        "item_id": "section_001",
        "type": "Text",
        "start_time": 0.0,
        "end_time": 2.0,
        "prompt": CANARY,
    }]
    value["sequence"]["shots"] = [{
        "shot_id": "shot_001",
        "type": "Generated",
        "start_time": 0.0,
        "end_time": 2.0,
        "section_ids": ["section_001"],
    }]
    return value


def _registration() -> dict:
    return {
        "shot_id": "shot_001",
        "shot_ids": ["shot_001"],
        "asset": {
            "asset_id": "asset_synthetic",
            "type": "Video",
            "path": f"/{CANARY}/output.mp4",
            "name": f"{CANARY}.mp4",
        },
        "take": {
            "take_id": "take_synthetic",
            "status": "Candidate",
            "metadata": {"private": CANARY},
        },
    }


def _install_d6(
    tmp_path: Path,
    monkeypatch,
    *,
    mode: str = "private",
):
    state = tmp_path / "shared-state"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv(shared_keystore.KEYSTORE_ENV, str(state / "keystore.json"))
    monkeypatch.setenv(shared_keystore.SESSION_DIR_ENV, str(state / "session"))
    monkeypatch.setenv(shared_mode_state.MODE_STATE_ENV, str(state / "modes.json"))
    monkeypatch.setenv(
        shared_external_operations.EXTERNAL_OPERATION_STATE_ENV,
        str(state / "external-operations.json"),
    )
    monkeypatch.setenv(shared_artifacts.ARTIFACT_ROOT_ENV, str(artifact_root))
    monkeypatch.setattr(shared_runtime, "_INSTALLATIONS", {})
    monkeypatch.setattr(shared_runtime, "register_helto_privacy_ui", lambda **_kwargs: True)
    monkeypatch.setattr(shared_suite_runtime, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_keystore, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_envelope, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_guard, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_artifacts, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_keystore, "SCRYPT_N", 2**12)
    shared_artifacts.reset_artifact_runtime_for_tests()
    shared_associations.clear_associations_for_tests()
    shared_references.clear_opaque_references_for_tests()
    shared_subject_mode.invalidate_subject_mode_session("d6-test-reset")

    profile = build_director_take_segment_privacy_profile()
    assert profile.fingerprint == EXPECTED_D6_SEGMENT_PROFILE_FINGERPRINT
    adapters = build_director_timeline_server_adapters()
    adapters[GLOBAL_MODE_ADAPTER_ID] = _ModeAdapter(mode)
    adapters.update(build_director_take_server_adapters())
    singleton_path = state / "director-singletons.sqlite3"
    adapters.update(build_director_durable_state_server_adapters(singleton_path))
    adapters.update(build_director_segment_spill_server_adapters())
    pack = install(profile, adapters)
    token = shared_keystore.initialize_keystore(PASSWORD)["token"]
    return pack, token, artifact_root, singleton_path


def _subject_lease(pack, token: str, effective: EffectivePrivacyMode):
    binding = next(
        item for item in pack.profile.subject_mode_bindings
        if item.id == TAKE_CAPTURE_SUBJECT_MODE_BINDING_ID
    )
    authorization = authorize_privacy_request(
        _Request(token),
        "subject-mode.prepare",
        pack_id=pack.profile.id,
    )
    prepared = prepare_subject_mode_reference(
        profile=pack.profile,
        binding=binding,
        subject_id="director-node-1",
        effective=effective,
        authorization=authorization,
        installation=pack._installation,
    )
    return consume_subject_mode_reference(
        prepared.reference,
        profile=pack.profile,
        binding=binding,
        subject_id="director-node-1",
    )


def _operation_authorization(pack, token: str):
    return authorize_privacy_request(
        _Request(token),
        ASSOCIATE_CAPTURE_OPERATION_ID,
        pack_id=pack.profile.id,
    )


def _claim_capture(pack, token: str, *, preview: object = None):
    timeline = _timeline()
    capture = asyncio.run(DirectorManagedTakeService(
        operations=pack.operations(TAKE_OPERATION_RESOURCE_ID),
    ).capture(
        timeline,
        _registration(),
        subject_mode=_subject_lease(pack, token, EffectivePrivacyMode.PRIVATE),
        sidecar={
            "schema_version": 1,
            "registration": _registration(),
            "media": {
                "duration_seconds": 1.5,
                "path": f"/{CANARY}/output.mp4",
            },
        },
        preview=preview,
    ))
    authorization = authorize_privacy_request(
        _Request(token),
        CAPTURE_TAKE_OPERATION_ID,
        pack_id=pack.profile.id,
    )
    claimed = claim_operation_association(
        installation=pack._installation,
        profile=pack.profile,
        association_id=capture.association.id,
        authorization=authorization,
    )
    identifiers = [item["id"] for item in claimed.references]
    return timeline, capture, claimed, {
        "asset": identifiers[0],
        "take": identifiers[1],
    }


def _exact(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _prepare_association(pack, token: str, timeline: dict, references: dict, marker: str):
    return asyncio.run(prepare_external_operation(
        pack._installation,
        ASSOCIATE_CAPTURE_OPERATION_ID,
        _operation_authorization(pack, token),
        request_id="hp-operation-request-" + marker * 24,
        owner_identity={
            "rootGraphId": "root",
            "graphId": "root",
            "nodeId": "director-node-1",
            "fieldId": TIMELINE_FIELD_ID,
        },
        original_exact=_exact(timeline),
        input_value={"timeline": copy.deepcopy(timeline)},
        references=references,
    ))


def _rollback_association(pack, token: str, prepared: dict):
    return asyncio.run(rollback_external_operation(
        pack._installation,
        ASSOCIATE_CAPTURE_OPERATION_ID,
        prepared["transactionId"],
        _operation_authorization(pack, token),
        resume_capability=prepared["resumeCapability"],
    ))


def _reveal_capture_index(pack, token: str):
    authorization = authorize_privacy_request(
        _Request(token),
        "singleton.reveal",
        pack_id=pack.profile.id,
    )
    return pack.singletons(DURABLE_STATE_RESOURCE_ID).reveal_field(
        CAPTURE_INDEX_ID,
        authorization,
    ).value


def test_d6_private_external_association_is_exact_encrypted_and_one_shot(
    tmp_path,
    monkeypatch,
):
    pack, token, artifact_root, singleton_path = _install_d6(tmp_path, monkeypatch)
    timeline, capture, claimed, references = _claim_capture(
        pack,
        token,
        preview={"path": f"/{CANARY}/preview.webp"},
    )
    original = copy.deepcopy(timeline)
    assert repr(capture) == "DirectorDeferredTakeCapture()"
    assert claimed.safe_payload == capture.safe_ui
    assert CANARY not in json.dumps(capture.safe_ui)
    assert timeline == original

    with pytest.raises(ExternalOperationError):
        _prepare_association(
            pack,
            token,
            timeline,
            {"asset": "hp-ref-" + "A" * 32, "take": "hp-ref-" + "B" * 32},
            "x",
        )
    assert not list(artifact_root.rglob("*.hpa"))

    retained = {}
    adapter = pack._installation.adapters[TAKE_OPERATION_ADAPTER_ID]
    finalize = adapter.finalize_external_operation

    def inspect_finalize(context, invocation, declaration, dependencies):
        retained["dependencies"] = dependencies
        return finalize(context, invocation, declaration, dependencies)

    monkeypatch.setattr(adapter, "finalize_external_operation", inspect_finalize)
    prepared = _prepare_association(pack, token, timeline, references, "a")
    assert prepared["phase"] == "prepared"
    assert prepared["browserValue"] != timeline
    completed = asyncio.run(apply_external_operation(
        pack._installation,
        ASSOCIATE_CAPTURE_OPERATION_ID,
        prepared["transactionId"],
        _operation_authorization(pack, token),
        resume_capability=prepared["resumeCapability"],
        current_exact=_exact(prepared["browserValue"]),
    ))
    assert completed["phase"] == "completed"
    assert completed["result"]["data"] == {
        "accepted": False,
        "asset_count": 1,
        "has_preview": True,
        "has_sidecar": True,
        "ok": True,
        "take_count": 1,
    }
    assert timeline == original
    with pytest.raises(ProtectedOperationError):
        retained["dependencies"].singleton(CAPTURE_INDEX_ID)

    index = _reveal_capture_index(pack, token)
    entry = next(iter(index["captures"].values()))
    assert entry["phase"] == "committed"
    assert entry["externalTransactionId"] == prepared["transactionId"]
    reference = ArtifactReference(entry["sidecarReference"]["id"])
    sidecar = asyncio.run(pack.artifacts(TAKE_SIDECAR_RESOURCE_ID).read(
        TAKE_SIDECAR_ARTIFACT_KIND,
        reference,
    ))
    assert CANARY in json.dumps(sidecar)
    protected = list(artifact_root.rglob("*.hpa"))
    assert len(protected) == 1
    assert CANARY.encode() not in protected[0].read_bytes()
    assert CANARY.encode() not in singleton_path.read_bytes()
    for path in (tmp_path / "shared-state").rglob("*journal*"):
        if path.is_file():
            assert CANARY.encode() not in path.read_bytes()

    with pytest.raises(ExternalOperationError):
        _prepare_association(pack, token, timeline, references, "b")
    revoke_authorization = authorize_privacy_request(
        _Request(token), "reference.revoke", pack_id=pack.profile.id
    )
    with pytest.raises(OpaqueReferenceError):
        revoke_operation_references(
            profile=pack.profile,
            authorization=revoke_authorization,
            reference_ids=tuple(references.values()),
        )


def test_d6_deferred_capture_is_invalidated_by_lock(tmp_path, monkeypatch):
    pack, token, artifact_root, _singleton_path = _install_d6(tmp_path, monkeypatch)
    service = DirectorManagedTakeService(
        operations=pack.operations(TAKE_OPERATION_RESOURCE_ID),
    )
    capture = asyncio.run(service.capture(
        _timeline(),
        _registration(),
        subject_mode=_subject_lease(pack, token, EffectivePrivacyMode.PRIVATE),
        sidecar={"registration": _registration(), "private": CANARY},
    ))
    authorization = authorize_privacy_request(
        _Request(token),
        CAPTURE_TAKE_OPERATION_ID,
        pack_id=pack.profile.id,
    )
    lock_keystore()
    with pytest.raises(AssociationError):
        claim_operation_association(
            installation=pack._installation,
            profile=pack.profile,
            association_id=capture.association.id,
            authorization=authorization,
        )
    assert not list(artifact_root.rglob("*.hpa"))
    assert not list(artifact_root.rglob("*.spill"))


def test_d6_finalize_projection_failure_recovers_as_exact_completed(
    tmp_path,
    monkeypatch,
):
    pack, token, artifact_root, _singleton_path = _install_d6(tmp_path, monkeypatch)
    timeline, _capture, _claimed, references = _claim_capture(pack, token)
    prepared = _prepare_association(pack, token, timeline, references, "c")
    adapter = pack._installation.adapters[TAKE_OPERATION_ADAPTER_ID]
    project = adapter.project
    failed = False

    def fail_once(value, declaration):
        nonlocal failed
        if declaration.id == ASSOCIATE_CAPTURE_OPERATION_ID and not failed:
            failed = True
            raise RuntimeError(CANARY)
        return project(value, declaration)

    monkeypatch.setattr(adapter, "project", fail_once)
    with pytest.raises(ExternalOperationError):
        asyncio.run(apply_external_operation(
            pack._installation,
            ASSOCIATE_CAPTURE_OPERATION_ID,
            prepared["transactionId"],
            _operation_authorization(pack, token),
            resume_capability=prepared["resumeCapability"],
            current_exact=_exact(prepared["browserValue"]),
        ))
    monkeypatch.setattr(adapter, "project", project)
    resumed = asyncio.run(resume_external_operation(
        pack._installation,
        ASSOCIATE_CAPTURE_OPERATION_ID,
        prepared["transactionId"],
        _operation_authorization(pack, token),
        resume_capability=prepared["resumeCapability"],
    ))
    assert resumed["phase"] == "rollback-required"
    completed = _rollback_association(pack, token, prepared)
    assert completed["phase"] == "completed"
    assert completed["result"]["data"]["ok"] is True
    assert len(list(artifact_root.rglob("*.hpa"))) == 1
    assert next(iter(_reveal_capture_index(pack, token)["captures"].values()))[
        "phase"
    ] == "committed"


def test_d6_failed_capture_index_prepare_rolls_back_orphan_and_releases_claims(
    tmp_path,
    monkeypatch,
):
    pack, token, artifact_root, _singleton_path = _install_d6(tmp_path, monkeypatch)
    timeline, _capture, _claimed, references = _claim_capture(pack, token)
    store = pack._installation.adapters[DURABLE_STATE_STORE_ADAPTER_ID]
    begin = store.begin_singleton_replace

    def fail_begin(*_args, **_kwargs):
        raise OSError(CANARY)

    monkeypatch.setattr(store, "begin_singleton_replace", fail_begin)
    with pytest.raises(ExternalOperationError):
        _prepare_association(pack, token, timeline, references, "d")
    monkeypatch.setattr(store, "begin_singleton_replace", begin)
    assert not list(artifact_root.rglob("*.hpa"))
    _revision, records = shared_external_operations.load_external_operation_state()
    journal = shared_external_operations.load_external_operation_journal(records[0])
    prepared = asyncio.run(resume_external_operation(
        pack._installation,
        ASSOCIATE_CAPTURE_OPERATION_ID,
        records[0].transaction_id,
        _operation_authorization(pack, token),
        resume_capability=journal["resumeCapability"],
    ))
    assert prepared["phase"] == "rollback-required"
    prepared["resumeCapability"] = journal["resumeCapability"]
    assert _rollback_association(pack, token, prepared)["phase"] == "rolled-back"
    assert not pack.singletons(DURABLE_STATE_RESOURCE_ID).status(CAPTURE_INDEX_ID).exists

    retried = _prepare_association(pack, token, timeline, references, "e")
    assert retried["phase"] == "prepared"
    assert _rollback_association(pack, token, retried)["phase"] == "rolled-back"


def test_d6_cancelled_capture_index_commit_cleans_sidecar_with_live_capability(
    tmp_path,
    monkeypatch,
):
    pack, token, artifact_root, _singleton_path = _install_d6(tmp_path, monkeypatch)
    timeline, _capture, _claimed, references = _claim_capture(pack, token)
    store = pack._installation.adapters[DURABLE_STATE_STORE_ADAPTER_ID]
    begin = store.begin_singleton_replace

    def cancel_begin(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(store, "begin_singleton_replace", cancel_begin)
    with pytest.raises(asyncio.CancelledError):
        _prepare_association(pack, token, timeline, references, "f")
    monkeypatch.setattr(store, "begin_singleton_replace", begin)
    assert not list(artifact_root.rglob("*.hpa"))
    _revision, records = shared_external_operations.load_external_operation_state()
    journal = shared_external_operations.load_external_operation_journal(records[0])
    prepared = {
        "transactionId": records[0].transaction_id,
        "resumeCapability": journal["resumeCapability"],
    }
    assert _rollback_association(pack, token, prepared)["phase"] == "rolled-back"


def _tensor() -> torch.Tensor:
    return torch.arange(24, dtype=torch.float32).reshape(2, 2, 2, 3)


@pytest.mark.parametrize(
    ("mode", "suffix"),
    (("private", ".hpa"), ("public", ".spill")),
)
def test_d6_real_spill_is_dual_mode_readable_and_removed_on_success(
    tmp_path,
    monkeypatch,
    mode,
    suffix,
):
    pack, _token, artifact_root, _committed = _install_d6(tmp_path, monkeypatch, mode=mode)
    handle = pack.artifacts(SEGMENT_ARTIFACT_RESOURCE_ID)
    tensor = _tensor()

    async def scenario():
        session = DirectorManagedSegmentSpillSession(handle)
        await session.__aenter__()
        record = await session.write_segment(tensor)
        stored = list(artifact_root.rglob(f"*{suffix}"))
        assert len(stored) == 1
        assert stored[0].stat().st_mode & 0o777 == 0o600
        if mode == "private":
            assert not list(artifact_root.rglob("*.spill"))
        else:
            with stored[0].open("rb") as stream:
                decoded = DirectorSegmentTensorPayloadAdapter().decode_from(
                    _BoundedFileSource(stream)
                )
            assert torch.equal(decoded, tensor)
        assert torch.equal(await session.read_segment(record), tensor)
        assert await session.close() == 1
        assert await session.close() == 0

    asyncio.run(scenario())
    assert not list(artifact_root.rglob("*.hpa"))
    assert not list(artifact_root.rglob("*.spill"))


def test_d6_spill_closes_on_cancellation_and_rejects_cross_session_record(
    tmp_path,
    monkeypatch,
):
    pack, _token, artifact_root, _committed = _install_d6(tmp_path, monkeypatch)
    handle = pack.artifacts(SEGMENT_ARTIFACT_RESOURCE_ID)

    async def scenario():
        first = DirectorManagedSegmentSpillSession(handle)
        second = DirectorManagedSegmentSpillSession(handle)
        await first.__aenter__()
        await second.__aenter__()
        record = await first.write_segment(_tensor())
        with pytest.raises(DirectorManagedSegmentSpillError):
            await second.read_segment(record)
        await second.close()
        await first.close()

        cancelled = DirectorManagedSegmentSpillSession(handle)
        with pytest.raises(asyncio.CancelledError):
            async with cancelled:
                await cancelled.write_segment(_tensor())
                raise asyncio.CancelledError()

    asyncio.run(scenario())
    assert not list(artifact_root.rglob("*.hpa"))
    assert not list(artifact_root.rglob("*.spill"))


def test_d6_public_cleanup_failure_blocks_then_explicit_retry_releases(
    tmp_path,
    monkeypatch,
    ):
    pack, _token, artifact_root, _committed = _install_d6(tmp_path, monkeypatch, mode="public")
    handle = pack.artifacts(SEGMENT_ARTIFACT_RESOURCE_ID)
    original_unlink = Path.unlink

    def fail_spill(path, *args, **kwargs):
        if path.suffix == ".spill":
            raise OSError(f"/{CANARY}/spill")
        return original_unlink(path, *args, **kwargs)

    context = type("Context", (), {
        "prior_mode": EffectivePrivacyMode.PUBLIC,
        "target_mode": EffectivePrivacyMode.PRIVATE,
    })()

    async def start_and_fail_cleanup():
        session = DirectorManagedSegmentSpillSession(handle)
        await session.__aenter__()
        await session.write_segment(_tensor())
        with pytest.raises(DirectorManagedSegmentSpillError) as error:
            await session.close()
        assert CANARY not in str(error.value)
        return session

    with monkeypatch.context() as blocked:
        blocked.setattr(Path, "unlink", fail_spill)
        session = asyncio.run(start_and_fail_cleanup())
        with pytest.raises(ArtifactError):
            shared_artifacts.prepare_artifact_mode_transition(
                pack._installation,
                "director-global",
                context,
            )
    ledger = json.loads((artifact_root / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["entries"][0]["cleanupPending"] is True
    assert asyncio.run(session.close()) == 1
    shared_artifacts.prepare_artifact_mode_transition(
        pack._installation,
        "director-global",
        context,
    )
    assert not list(artifact_root.rglob("*.spill"))


def test_d6_interrupted_spill_is_removed_by_shared_restart_sweep(tmp_path, monkeypatch):
    pack, _token, artifact_root, _committed = _install_d6(tmp_path, monkeypatch)
    session = DirectorManagedSegmentSpillSession(
        pack.artifacts(SEGMENT_ARTIFACT_RESOURCE_ID)
    )
    asyncio.run(session.__aenter__())
    asyncio.run(session.write_segment(_tensor()))
    assert list(artifact_root.rglob("*.hpa"))

    shared_artifacts.reset_artifact_runtime_for_tests()
    report = shared_artifacts.initialize_artifact_service(pack.profile)
    assert report is not None
    assert report.retired >= 1
    assert not list(artifact_root.rglob("*.hpa"))
    assert not list(artifact_root.rglob("*.spill"))
