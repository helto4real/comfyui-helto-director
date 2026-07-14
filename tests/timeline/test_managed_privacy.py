from __future__ import annotations

import copy
import json
import multiprocessing
from pathlib import Path

import pytest

import helto_privacy.envelope as shared_envelope
import helto_privacy.execution as shared_execution
import helto_privacy.guard as shared_guard
import helto_privacy.keystore as shared_keystore
import helto_privacy.migration as shared_migration
import helto_privacy.runtime as shared_runtime
import helto_privacy.suite_runtime as shared_suite_runtime
from helto_privacy import (
    DIRECTOR_V1_JSON_KEY_IMPORT_ID,
    EnvelopeDisposition,
    ExecutionError,
    LegacyKeyFormat,
    PrivacyEnvelopeCodec,
    install,
    lock_keystore,
    unlock_keystore,
)
from helto_privacy.guard import authorize_privacy_request

from shared.timeline.defaults import create_default_video_timeline
from shared.timeline.global_settings import (
    MODE_SOURCE_REVISION_KEY,
    load_global_settings,
    save_global_settings,
    settings_path,
)
from shared.timeline.normalize import normalize_video_timeline
from shared.timeline.validate import validate_video_timeline
from shared.timeline.managed_privacy import (
    DIRECTOR_NODE_TYPE,
    DIRECTOR_PROFILE_ID,
    DIRECTOR_TIMELINE_SCHEMA,
    GLOBAL_MODE_ADAPTER_ID,
    GLOBAL_SCOPE_ID,
    TIMELINE_BROWSER_ADAPTER_ID,
    TIMELINE_DISPATCH_ADAPTER_ID,
    TIMELINE_EXECUTION_INPUT,
    TIMELINE_EXECUTION_PROJECTION_ID,
    TIMELINE_EXECUTION_RESOURCE_ID,
    TIMELINE_FIELD_ID,
    TIMELINE_PROJECTION_ADAPTER_ID,
    TIMELINE_RESOURCE_ID,
    TIMELINE_STATE_ADAPTER_ID,
    TIMELINE_SUBJECT_INPUT,
    TIMELINE_SUBJECT_MODE_BINDING_ID,
    TIMELINE_WIDGET_NAME,
    DirectorGlobalModeAdapter,
    DirectorTimelineExecutionDispatchAdapter,
    DirectorTimelineExecutionProjectionAdapter,
    DirectorTimelineStateAdapter,
    build_director_timeline_privacy_profile,
    build_director_timeline_server_adapters,
)
from tests.synthetic_legacy import director_legacy_fixture


class Request:
    def __init__(self, token: str) -> None:
        self.headers = {"X-Helto-Privacy-Token": token}
        self.cookies = {}


def _competing_mode_source_cas(
    base_dir: str,
    barrier,
    outcomes,
) -> None:
    adapter = DirectorGlobalModeAdapter(base_dir=base_dir)
    prior = adapter.read_mode_source(GLOBAL_SCOPE_ID)
    barrier.wait()
    try:
        target = adapter.compare_and_set_mode_source(
            GLOBAL_SCOPE_ID,
            prior["revision"],
            prior["declared"],
            "public",
        )
    except Exception:
        outcomes.put("conflict")
    else:
        outcomes.put((target["revision"], target["declared"]))


def _authorization(pack, token: str, operation: str):
    return authorize_privacy_request(Request(token), operation, pack_id=pack.profile.id)


def _installed_pack(tmp_path, monkeypatch):
    monkeypatch.setenv(shared_migration.MIGRATION_STATE_ENV, str(tmp_path / "migration.json"))
    monkeypatch.setenv(shared_keystore.KEYSTORE_ENV, str(tmp_path / "keystore.json"))
    monkeypatch.setenv(shared_keystore.SESSION_DIR_ENV, str(tmp_path / "session"))
    monkeypatch.setattr(shared_runtime, "_INSTALLATIONS", {})
    monkeypatch.setattr(shared_runtime, "register_helto_privacy_ui", lambda **_kwargs: True)
    monkeypatch.setattr(shared_suite_runtime, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_keystore, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_envelope, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_guard, "require_active_process_suite", lambda: None)
    monkeypatch.setattr(shared_keystore, "SCRYPT_N", 2**12)
    shared_migration.reset_migration_runtime_for_tests()
    shared_execution.invalidate_execution_session("director-d2-test-reset")
    pack = install(
        build_director_timeline_privacy_profile(),
        build_director_timeline_server_adapters(),
    )
    token = shared_keystore.initialize_keystore("synthetic Director D2 password")["token"]
    return pack, token


def test_d2_profile_declares_only_timeline_workflow_and_semantic_execution():
    profile = build_director_timeline_privacy_profile()

    assert profile.id == DIRECTOR_PROFILE_ID == "helto.director"
    assert {item.id: item.kind.value for item in profile.resources} == {
        "director-global-mode": "mode",
        "timeline": "workflow",
        "timeline-render": "execution",
    }
    assert profile.protected_operations == ()
    assert profile.legacy_bindings == ()
    assert len(profile.protected_fields) == 1
    field = profile.protected_fields[0]
    assert field.id == TIMELINE_FIELD_ID
    assert field.workflow_resource_id == TIMELINE_RESOURCE_ID
    assert field.scope_id == GLOBAL_SCOPE_ID
    assert field.node_types == (DIRECTOR_NODE_TYPE,)
    assert field.location.name == TIMELINE_WIDGET_NAME
    assert field.current_schema == DIRECTOR_TIMELINE_SCHEMA
    assert field.execution is True
    assert field.state_authority.value == "external-browser-workflow"
    assert field.external_transition_policy.contract_payload() == {
        "ownerIdentity": "graph-node-field-v1",
        "maxOwners": 1024,
        "maxOriginalBytesPerOwner": 2 * 1024 * 1024,
        "maxTargetBytesPerOwner": 2 * 1024 * 1024,
        "maxTotalBytes": 32 * 1024 * 1024,
        "leaseSeconds": 300,
    }
    assert field.legacy_reader_ids == ()
    assert field.browser_adapter == TIMELINE_BROWSER_ADAPTER_ID

    assert len(profile.subject_mode_bindings) == 1
    binding = profile.subject_mode_bindings[0]
    assert binding.id == TIMELINE_SUBJECT_MODE_BINDING_ID
    assert binding.input_name == TIMELINE_SUBJECT_INPUT
    projection = profile.execution_projections[0]
    assert projection.id == TIMELINE_EXECUTION_PROJECTION_ID
    assert projection.execution_resource_id == TIMELINE_EXECUTION_RESOURCE_ID
    assert projection.input_name == TIMELINE_EXECUTION_INPUT
    assert projection.subject_mode_binding_id == binding.id

    assert len(profile.legacy_key_imports) == 1
    key_import = profile.legacy_key_imports[0]
    assert key_import.import_id == DIRECTOR_V1_JSON_KEY_IMPORT_ID
    assert key_import.location_id == TIMELINE_FIELD_ID
    assert key_import.location_kind.value == "workflow-field"
    assert key_import.source_format.value == "json"


def test_d2_server_adapter_set_is_exact_and_contract_complete():
    profile = build_director_timeline_privacy_profile()
    adapters = build_director_timeline_server_adapters()

    assert set(adapters) == {slot.id for slot in profile.server_adapters}
    for adapter_id, methods in profile.server_adapter_contracts.items():
        assert all(callable(getattr(adapters[adapter_id], method, None)) for method in methods)


def test_global_mode_adapter_preserves_existing_private_default_and_writes_boolean():
    stored = {"privacy": {}}
    writes = []
    adapter = DirectorGlobalModeAdapter(
        loader=lambda: copy.deepcopy(stored),
        saver=lambda value: writes.append(copy.deepcopy(value)),
    )

    assert adapter.read_declared_mode(GLOBAL_SCOPE_ID) == "private"
    adapter.write_declared_mode(GLOBAL_SCOPE_ID, "public")
    assert writes[-1]["privacy"]["mode"] is False
    with pytest.raises(ValueError):
        adapter.write_declared_mode(GLOBAL_SCOPE_ID, "inherit")


def test_global_mode_source_is_revisioned_classified_and_idempotently_rolled_back(tmp_path):
    adapter = DirectorGlobalModeAdapter(base_dir=tmp_path)
    prior = adapter.read_mode_source(GLOBAL_SCOPE_ID)
    assert prior == {"revision": 0, "declared": "private"}

    target = adapter.compare_and_set_mode_source(
        GLOBAL_SCOPE_ID,
        prior["revision"],
        prior["declared"],
        "public",
    )
    assert target == {"revision": 1, "declared": "public"}
    assert adapter.classify_mode_source(GLOBAL_SCOPE_ID, prior, target) == "target"
    assert load_global_settings(tmp_path)["privacy"]["mode"] is False
    raw = json.loads(settings_path(tmp_path).read_text(encoding="utf-8"))
    assert raw[MODE_SOURCE_REVISION_KEY] == 1
    assert MODE_SOURCE_REVISION_KEY not in load_global_settings(tmp_path)

    with pytest.raises(ValueError, match="changed concurrently"):
        adapter.compare_and_set_mode_source(
            GLOBAL_SCOPE_ID,
            prior["revision"],
            prior["declared"],
            "private",
        )

    restored = adapter.rollback_mode_source(GLOBAL_SCOPE_ID, target, prior)
    assert restored == {"revision": 2, "declared": "private"}
    assert adapter.rollback_mode_source(GLOBAL_SCOPE_ID, target, prior) == restored
    assert adapter.classify_mode_source(GLOBAL_SCOPE_ID, prior, target) == "diverged"
    assert adapter.read_declared_mode(GLOBAL_SCOPE_ID) == "private"


def test_normal_global_settings_writes_advance_the_same_mode_source_revision(tmp_path):
    adapter = DirectorGlobalModeAdapter(base_dir=tmp_path)
    assert adapter.read_mode_source(GLOBAL_SCOPE_ID) == {
        "revision": 0,
        "declared": "private",
    }

    save_global_settings({"privacy": {"mode": False}}, tmp_path)
    assert adapter.read_mode_source(GLOBAL_SCOPE_ID) == {
        "revision": 1,
        "declared": "public",
    }
    save_global_settings({"privacy": {"mode": False}}, tmp_path)
    assert adapter.read_mode_source(GLOBAL_SCOPE_ID) == {
        "revision": 1,
        "declared": "public",
    }
    save_global_settings({"privacy": {"mode": True}}, tmp_path)
    assert adapter.read_mode_source(GLOBAL_SCOPE_ID) == {
        "revision": 2,
        "declared": "private",
    }


def test_global_mode_source_rejects_corrupt_or_non_monotonic_state(tmp_path):
    path = settings_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    adapter = DirectorGlobalModeAdapter(base_dir=tmp_path)
    with pytest.raises(ValueError, match="MODE_SOURCE_INVALID"):
        adapter.read_mode_source(GLOBAL_SCOPE_ID)

    path.write_text(
        json.dumps({
            "privacy": {"mode": True},
            MODE_SOURCE_REVISION_KEY: -1,
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MODE_SOURCE_INVALID"):
        adapter.read_mode_source(GLOBAL_SCOPE_ID)


def test_global_mode_source_compare_and_set_has_one_cross_process_winner(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_competing_mode_source_cas,
            args=(str(tmp_path), barrier, outcomes),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    barrier.wait()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    observed = [outcomes.get(timeout=1) for _ in processes]
    assert set(observed) == {
        "conflict",
        (1, "public"),
    }
    assert DirectorGlobalModeAdapter(base_dir=Path(tmp_path)).read_mode_source(
        GLOBAL_SCOPE_ID
    ) == {"revision": 1, "declared": "public"}


def test_timeline_state_normalizes_plaintext_and_never_defaults_protected_state():
    profile = build_director_timeline_privacy_profile()
    declaration = profile.protected_fields[0]
    adapter = DirectorTimelineStateAdapter()
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 9.5

    normalized = adapter.normalize(json.dumps(timeline), declaration)
    assert normalized["timeline"]["project"]["duration_seconds"] == 9.5

    envelope = {
        "version": 1,
        "schema": DIRECTOR_TIMELINE_SCHEMA,
        "encrypted": True,
        "algorithm": "AES-256-GCM",
        "keyId": "synthetic",
        "nonce": "synthetic",
        "ciphertext": "SYNTHETIC_LOCKED_TIMELINE",
    }
    with pytest.raises(ValueError, match="cannot execute as plaintext"):
        adapter.normalize(envelope, declaration)
    with pytest.raises(ValueError, match="cannot execute as plaintext"):
        adapter.normalize({"timeline": envelope}, declaration)
    with pytest.raises(ValueError, match="plaintext is unavailable"):
        adapter.normalize("", declaration)

    target = {TIMELINE_WIDGET_NAME: json.dumps(envelope, separators=(",", ":"))}
    original = target[TIMELINE_WIDGET_NAME]
    adapter.clear_plaintext(target, declaration)
    assert target[TIMELINE_WIDGET_NAME] == ""
    assert original.endswith('"SYNTHETIC_LOCKED_TIMELINE"}')


def test_timeline_transition_codec_round_trips_exact_private_and_public_bytes(
    tmp_path,
    monkeypatch,
):
    _installed_pack(tmp_path, monkeypatch)
    adapter = DirectorTimelineStateAdapter()
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 12.5
    normalized = {"timeline": normalize_video_timeline(timeline)}
    envelope = PrivacyEnvelopeCodec(DIRECTOR_TIMELINE_SCHEMA).encrypt_state(
        normalized
    )
    private_exact = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert adapter.classify_mode_transition_representation(private_exact, None) == (
        "private"
    )
    decoded_private = adapter.decode_mode_transition_representation(
        private_exact,
        None,
    )
    assert adapter.normalize_mode_transition_value(decoded_private, None) == normalized

    public_exact = adapter.encode_public_mode_transition(decoded_private, None)
    assert public_exact == json.dumps(
        normalized["timeline"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert adapter.classify_mode_transition_representation(public_exact, None) == (
        "public"
    )
    decoded_public = adapter.decode_mode_transition_representation(public_exact, None)
    assert adapter.normalize_mode_transition_value(decoded_public, None) == normalized


@pytest.mark.parametrize(
    "exact",
    (
        b"",
        b"   ",
        b"{not-json",
        b"\xff",
        b"{}",
        b"[]",
        b'{"schema_version":"2.0","type":"VIDEO_TIMELINE"}',
        b'{"project":{},"schema_version":"2.0","type":"WRONG"}',
        (
            b'{"project":{},"schema_version":"2.0",'
            b'"type":"VIDEO_TIMELINE","ui_state":{}}'
        ),
        b'{"project":null,"type":"NOT_A_TIMELINE"}',
        b'{"timeline":{"project":{},"type":"VIDEO_TIMELINE"}}',
        b'{"project":{},"type":NaN}',
        b'{"project":{},"type":"VIDEO_TIMELINE","type":"VIDEO_TIMELINE"}',
        (
            b'{"algorithm":"AES-256-GCM","encrypted":true,'
            b'"keyId":"synthetic","nonce":"synthetic",'
            b'"schema":"wrong.schema","ciphertext":"synthetic","version":1}'
        ),
        (
            b'{"algorithm":"AES-256-GCM","encrypted":true,'
            b'"keyId":"synthetic","nonce":"synthetic",'
            b'"schema":"helto.timeline-director","version":1}'
        ),
        (
            b'{"algorithm":"AES-256-GCM","ciphertext":"synthetic",'
            b'"encrypted":true,"keyId":"synthetic","nonce":"synthetic",'
            b'"schema":"helto.timeline-director","version":1}'
        ),
        (
            b'{"algorithm":"AES-256-GCM",'
            b'"ciphertext":"//////////////////////",'
            b'"encrypted":true,"keyId":"synthetic",'
            b'"nonce":"////////////////",'
            b'"schema":"helto.timeline-director","version":1}'
        ),
    ),
)
def test_timeline_transition_codec_rejects_non_timeline_or_malformed_exact_bytes(
    exact,
):
    adapter = DirectorTimelineStateAdapter()
    with pytest.raises(
        ValueError,
        match="transition representation is invalid",
    ):
        adapter.classify_mode_transition_representation(exact, None)


def test_execution_projection_and_dispatch_use_existing_normalize_validate_build_path():
    profile = build_director_timeline_privacy_profile()
    declaration = profile.execution_projections[0]
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 4.0
    projection = DirectorTimelineExecutionProjectionAdapter()
    semantic = projection.project(
        {TIMELINE_FIELD_ID: {"timeline": timeline}},
        declaration,
    )
    assert semantic["project"]["duration_seconds"] == 4.0

    checkpoints = []
    cancellation = type("Cancellation", (), {"checkpoint": lambda self: checkpoints.append(True)})()
    dispatched = DirectorTimelineExecutionDispatchAdapter().dispatch(
        semantic,
        {
            "duration_seconds": 6.0,
            "frame_rate": 24.0,
            "aspect_ratio": "16:9",
            "orientation": "Landscape",
            "quality_preset": "High",
        },
        cancellation,
    )
    built_timeline, validation = dispatched
    expected = copy.deepcopy(semantic)
    expected["project"].update(
        {
            "duration_seconds": 6.0,
            "frame_rate": 24.0,
            "aspect_ratio": "16:9",
            "orientation": "Landscape",
            "quality_preset": "High",
        }
    )
    expected = normalize_video_timeline(expected)
    expected_validation = validate_video_timeline(expected)
    expected["validation"] = expected_validation
    assert built_timeline == expected
    assert validation == expected_validation
    assert built_timeline["project"]["duration_seconds"] == 6.0
    assert built_timeline["validation"] == validation
    assert checkpoints == [True, True]

    with pytest.raises(ValueError, match="context is incomplete"):
        DirectorTimelineExecutionDispatchAdapter().dispatch(
            semantic,
            {},
            cancellation,
        )


def test_current_director_envelope_survives_json_key_import_and_shared_read(
    tmp_path,
    monkeypatch,
):
    legacy_dir = tmp_path / "legacy"
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 13.0
    envelope, source = director_legacy_fixture(legacy_dir, {"timeline": timeline})

    pack, token = _installed_pack(tmp_path / "shared", monkeypatch)
    pack.migration.import_legacy_key_source(
        DIRECTOR_V1_JSON_KEY_IMPORT_ID,
        source,
        "synthetic Director D2 password",
        LegacyKeyFormat.JSON,
        _authorization(pack, token, "migration.key-import"),
    )

    token = shared_keystore.session_token()
    workflow = pack.workflow(TIMELINE_RESOURCE_ID)
    disposition = workflow.inspect_disposition(
        TIMELINE_FIELD_ID,
        envelope,
        _authorization(pack, token, "snapshot.disposition"),
    )
    assert disposition.disposition is EnvelopeDisposition.VERIFIED_CURRENT
    revealed = workflow.reveal(
        TIMELINE_FIELD_ID,
        envelope,
        _authorization(pack, token, "snapshot.reveal"),
    )
    assert revealed.value["timeline"]["project"]["duration_seconds"] == 13.0
    assert envelope["schema"] == DIRECTOR_TIMELINE_SCHEMA


def test_shared_execution_grants_block_locked_state_and_replay(
    tmp_path,
    monkeypatch,
):
    pack, token = _installed_pack(tmp_path, monkeypatch)
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 8.0
    protected = PrivacyEnvelopeCodec(DIRECTOR_TIMELINE_SCHEMA).encrypt_state(
        {"timeline": timeline}
    )
    execution = pack.execution(TIMELINE_EXECUTION_RESOURCE_ID)
    subject_id = "director-node-17"
    blocked = execution.prepare(
        TIMELINE_EXECUTION_PROJECTION_ID,
        {TIMELINE_FIELD_ID: protected},
        _authorization(pack, token, "execution.prepare"),
        subject_id=subject_id,
    )
    calls = []
    lock_keystore()
    with pytest.raises(ExecutionError):
        execution.dispatch(
            blocked.reference,
            {"dispatch": lambda value: calls.append(value) or value},
            subject_id=subject_id,
        )
    assert calls == []

    token = unlock_keystore("synthetic Director D2 password")["token"]
    fresh = execution.prepare(
        TIMELINE_EXECUTION_PROJECTION_ID,
        {TIMELINE_FIELD_ID: protected},
        _authorization(pack, token, "execution.prepare"),
        subject_id=subject_id,
    )
    result = execution.dispatch(
        fresh.reference,
        {"dispatch": lambda value: calls.append(value) or value},
        subject_id=subject_id,
    )
    assert result.value["project"]["duration_seconds"] == 8.0
    assert len(calls) == 1
    with pytest.raises(ExecutionError):
        execution.dispatch(
            fresh.reference,
            {"dispatch": lambda value: calls.append(value) or value},
            subject_id=subject_id,
        )
    assert len(calls) == 1
