from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import shared.timeline.managed_take_privacy as managed_take

from helto_privacy import (
    ArtifactOperationDependency,
    ArtifactReference,
    ArtifactRetention,
    ExternalOperationDisposition,
    ExternalOperationInvocation,
    OpaqueReferenceKind,
    SafePayloadKind,
    ResourceKind,
    SafeDiagnosticKind,
    SensitiveFieldClass,
    SingletonOperationDependency,
)

from shared.timeline.defaults import create_default_video_timeline
from shared.timeline.managed_media_artifacts import (
    build_director_media_artifact_privacy_profile,
)
from shared.timeline.managed_library_records import (
    build_director_library_privacy_profile,
)
from shared.timeline.managed_privacy import build_director_timeline_privacy_profile
from shared.timeline.managed_durable_state import (
    CAPTURE_INDEX_ID,
    DURABLE_STATE_RESOURCE_ID,
)
from shared.timeline.managed_take_privacy import (
    ASSOCIATE_CAPTURE_OPERATION,
    ASSOCIATE_CAPTURE_OPERATION_ID,
    CAPTURE_TAKE_OPERATION,
    CAPTURE_TAKE_OPERATION_ID,
    TAKE_OPERATION_ADAPTER_ID,
    TAKE_OPERATION_RESOURCE_ID,
    TAKE_SIDECAR_RESOURCE_ID,
    TAKE_SIDECAR_ADAPTER_ID,
    TAKE_SIDECAR_ARTIFACT_DECLARATION,
    TAKE_SAFE_PAYLOAD_PROJECTION,
    CAPTURE_ASSET_REFERENCE_KIND,
    CAPTURE_TAKE_REFERENCE_KIND,
    TAKE_OPAQUE_REFERENCE_RETIREMENT_PLAN,
    TAKE_PLAINTEXT_DERIVATIVE_INVENTORY,
    TAKE_PRIVACY_ACTIVATION_GAPS,
    TAKE_SAFE_OPERATION_LEAVES,
    TAKE_SAFE_DIAGNOSTIC_LEAVES,
    TAKE_SAFE_SIDECAR_LEAVES,
    TAKE_SAFE_UI_LEAVES,
    DirectorManagedTakePrivacyError,
    DirectorManagedTakeService,
    DirectorTakeSidecarPayloadAdapter,
    CapturedAssetLocator,
    CapturedTakeLocator,
    DirectorTakeOperationProjectionAdapter,
    associate_take_output_candidate,
    build_director_take_privacy_profile,
    normalize_take_registration_candidate,
    project_safe_take_sidecar_candidate,
    project_safe_take_ui_candidate,
)
from shared.timeline.take_registration import (
    prepare_take_registration,
    register_generated_take,
)


CANARY = "SYNTHETIC_TAKE_PRIVATE_CANARY"
EXPECTED_D6_PROFILE_FINGERPRINT = (
    "4e29c0e7f2b1b4c502237ae127ef51001d749f8ae7230ad0e2d03698c6686891"
)


def _timeline_with_shot() -> dict:
    timeline = create_default_video_timeline()
    timeline["project"]["duration_seconds"] = 2.0
    timeline["director_track"]["sections"] = [
        {
            "item_id": "section_001",
            "type": "Text",
            "start_time": 0.0,
            "end_time": 2.0,
            "prompt": "synthetic prompt",
        }
    ]
    timeline["sequence"]["shots"] = [
        {
            "shot_id": "shot_001",
            "type": "Generated",
            "start_time": 0.0,
            "end_time": 2.0,
            "section_ids": ["section_001"],
        }
    ]
    return timeline


def _registration() -> dict:
    return {
        "shot_id": "shot_001",
        "shot_ids": ["shot_001"],
        "asset": {
            "asset_id": "asset_synthetic",
            "type": "Video",
            "path": "/synthetic/output.mp4",
            "name": "output.mp4",
            "metadata": {"frame_count": 5},
        },
        "take": {
            "take_id": "take_synthetic",
            "status": "Candidate",
            "metadata": {"private_note": CANARY},
        },
    }


def test_take_fragment_composes_onto_an_arbitrary_director_base():
    d2 = build_director_timeline_privacy_profile()
    d3 = build_director_library_privacy_profile(d2)
    d4 = build_director_media_artifact_privacy_profile(d2)
    d3_d4 = build_director_media_artifact_privacy_profile(d3)
    for arbitrary_base in (d2, d3, d4, d3_d4):
        composed = build_director_take_privacy_profile(arbitrary_base)
        assert TAKE_OPERATION_RESOURCE_ID in {
            resource.id for resource in composed.resources
        }

    base = d3_d4
    profile = build_director_take_privacy_profile(base)
    assert build_director_take_privacy_profile().fingerprint == (
        EXPECTED_D6_PROFILE_FINGERPRINT
    )

    assert {resource.id for resource in profile.resources} == {
        *(resource.id for resource in base.resources),
        DURABLE_STATE_RESOURCE_ID,
        TAKE_OPERATION_RESOURCE_ID,
        TAKE_SIDECAR_RESOURCE_ID,
    }
    assert next(
        resource for resource in profile.resources
        if resource.id == TAKE_OPERATION_RESOURCE_ID
    ).kind is ResourceKind.OPERATION
    assert {
        "capture_external_operation",
        "classify_external_operation",
        "prepare_external_operation",
        "finalize_external_operation",
        "rollback_external_operation",
        "project",
        "project_safe_payload",
    }.issubset(profile.server_adapter_contracts[TAKE_OPERATION_ADAPTER_ID])
    assert next(
        operation for operation in profile.protected_operations
        if operation.id == CAPTURE_TAKE_OPERATION_ID
    ) == CAPTURE_TAKE_OPERATION

    safe = {field.path: field.kind for field in CAPTURE_TAKE_OPERATION.safe_projection}
    assert tuple(sorted(safe)) == tuple(sorted(TAKE_SAFE_DIAGNOSTIC_LEAVES))
    assert safe == {
        "accepted": SafeDiagnosticKind.BOOLEAN,
        "asset_count": SafeDiagnosticKind.COUNT,
        "has_preview": SafeDiagnosticKind.BOOLEAN,
        "has_sidecar": SafeDiagnosticKind.BOOLEAN,
        "ok": SafeDiagnosticKind.BOOLEAN,
        "take_count": SafeDiagnosticKind.COUNT,
    }
    sensitive = {
        field.path: field.field_class
        for field in CAPTURE_TAKE_OPERATION.sensitive_fields
    }
    assert sensitive["*"] is SensitiveFieldClass.CONSUMER_DERIVED
    assert sensitive["media.path"] is SensitiveFieldClass.PATH_OR_NAME
    assert sensitive["runtime_context"] is SensitiveFieldClass.DEBUG
    assert sensitive["registration"] is SensitiveFieldClass.CONSUMER_DERIVED


def test_take_candidate_preserves_current_normalization_and_association():
    registration = _registration()
    expected_normalized = prepare_take_registration(
        deepcopy(registration),
        generated_asset_path="/ignored/because/asset/path/exists.mp4",
        accept=True,
        update_clip_instance=False,
    )
    actual_normalized = normalize_take_registration_candidate(
        deepcopy(registration),
        generated_asset_path="/ignored/because/asset/path/exists.mp4",
        accept=True,
        update_clip_instance=False,
    )
    assert actual_normalized == expected_normalized

    timeline = _timeline_with_shot()
    expected = register_generated_take(deepcopy(timeline), expected_normalized)
    actual = associate_take_output_candidate(deepcopy(timeline), actual_normalized)
    assert actual == expected
    assert actual["timeline"]["assets"][0]["path"] == "/synthetic/output.mp4"
    assert actual["timeline"]["sequence"]["shots"][0]["takes"][0][
        "metadata"
    ]["private_note"] == CANARY


def test_sidecar_and_ui_candidates_copy_only_explicit_coarse_leaves():
    sidecar = {
        "schema_version": 1,
        "type": CANARY,
        "privacy": {"privacy_mode": True, "redacted_fields": [CANARY]},
        "registration": {
            "shot_id": CANARY,
            "shot_ids": [CANARY, "shot_002"],
            "asset": {"name": CANARY, "path": f"/{CANARY}.mp4"},
        },
        "media": {
            "filename": f"{CANARY}.mp4",
            "path": f"/{CANARY}.mp4",
            "frame_count": 5,
            "duration_seconds": 1.25,
            "width": 8,
            "height": 6,
            "size_bytes": 144,
        },
    }
    projected_sidecar = project_safe_take_sidecar_candidate(sidecar)
    assert projected_sidecar == {
        "schema_version": 1,
        "privacy": {"private": True},
        "registration": {"shot_count": 2},
        "media": {
            "frame_count": 5,
            "duration_milliseconds": 1250,
            "width": 8,
            "height": 6,
            "size_bytes": 144,
        },
    }
    assert _leaf_paths(projected_sidecar) == set(TAKE_SAFE_SIDECAR_LEAVES)

    result = {
        "asset_id": CANARY,
        "take_id": CANARY,
        "accepted": True,
        "debug_info": CANARY,
    }
    projected_ui = project_safe_take_ui_candidate(
        result,
        sidecar=sidecar,
        preview={"filename": CANARY},
        private=True,
    )
    assert set(projected_ui) == set(TAKE_SAFE_UI_LEAVES)
    assert projected_ui == {
        "ok": True,
        "accepted": True,
        "has_sidecar": True,
        "has_preview": True,
        "asset_count": 1,
        "take_count": 1,
        "duration_seconds": 1.25,
        "status": "Unavailable",
        "private": True,
    }
    assert CANARY not in json.dumps(projected_sidecar)
    assert CANARY not in json.dumps(projected_ui)


def test_take_operation_adapter_uses_the_same_canary_free_projection():
    adapter = DirectorTakeOperationProjectionAdapter()
    projected = adapter.project(
        {
            "result": {
                "asset_id": CANARY,
                "take_id": CANARY,
                "accepted": False,
            },
            "sidecar": {"path": CANARY},
            "preview": None,
            "runtime_context": CANARY,
        },
        CAPTURE_TAKE_OPERATION,
    )
    assert projected == {
        "ok": True,
        "accepted": False,
        "has_sidecar": True,
        "has_preview": False,
        "asset_count": 1,
        "take_count": 1,
    }
    assert CANARY not in json.dumps(projected)


@pytest.mark.parametrize(
    "shot_ids",
    (None, CANARY, 4, {"shot": CANARY}, object()),
)
def test_sidecar_projection_treats_arbitrary_shot_id_containers_as_empty(
    shot_ids,
):
    projected = project_safe_take_sidecar_candidate(
        {"registration": {"shot_ids": shot_ids}}
    )
    assert projected["registration"]["shot_count"] == 0


def test_sidecar_projection_bounds_shot_id_count():
    projected = project_safe_take_sidecar_candidate(
        {"registration": {"shot_ids": ["shot"] * 5_000}}
    )
    assert projected["registration"]["shot_count"] == 4_096


@pytest.mark.parametrize(
    ("duration", "expected"),
    (
        (float("nan"), 0),
        (float("inf"), 0),
        (float("-inf"), 0),
        (-1, 0),
        (10**500, 2_147_483_647),
        (CANARY, 0),
        (object(), 0),
    ),
)
def test_sidecar_projection_sanitizes_non_finite_and_unbounded_duration(
    duration,
    expected,
):
    projected = project_safe_take_sidecar_candidate(
        {"media": {"duration_seconds": duration}}
    )
    assert projected["media"]["duration_milliseconds"] == expected


def test_sidecar_and_ui_projection_are_total_for_hostile_values():
    class ExplosiveDict(dict):
        def get(self, *_args, **_kwargs):
            raise AssertionError("must not inspect arbitrary mapping subclasses")

    class ExplosiveValue:
        def __bool__(self):
            raise AssertionError("must not coerce arbitrary values")

        def __int__(self):
            raise AssertionError("must not convert arbitrary values")

        def __float__(self):
            raise AssertionError("must not convert arbitrary values")

    explosive = ExplosiveValue()
    assert project_safe_take_sidecar_candidate(ExplosiveDict()) == {
        "schema_version": 0,
        "privacy": {"private": False},
        "registration": {"shot_count": 0},
        "media": {
            "frame_count": 0,
            "duration_milliseconds": 0,
            "width": 0,
            "height": 0,
            "size_bytes": 0,
        },
    }
    projected = project_safe_take_sidecar_candidate(
        {
            "schema_version": explosive,
            "privacy": ExplosiveDict(),
            "registration": {"shot_ids": [explosive, CANARY]},
            "media": {
                "frame_count": explosive,
                "duration_seconds": explosive,
                "width": float("inf"),
                "height": float("nan"),
                "size_bytes": 10**500,
            },
        }
    )
    assert projected["registration"]["shot_count"] == 1
    assert projected["media"] == {
        "frame_count": 0,
        "duration_milliseconds": 0,
        "width": 0,
        "height": 0,
        "size_bytes": 2_147_483_647,
    }
    assert project_safe_take_ui_candidate(
        ExplosiveDict(),
        private=explosive,
    )["private"] is False


def test_take_operation_rejects_unknown_declaration_with_sanitized_error():
    with pytest.raises(DirectorManagedTakePrivacyError) as error:
        DirectorTakeOperationProjectionAdapter().project({}, object())
    assert str(error.value) == "Director safe take projection failed."
    assert CANARY not in str(error.value)


class _SidecarHandle:
    def __init__(self):
        self.reference = ArtifactReference("hp-art-" + "A" * 32)
        self.values = {}
        self.retired = []
        self.released = []

    async def write(self, owner_id, value):
        assert owner_id.startswith("hp-owner-")
        self.values[self.reference] = deepcopy(value)
        return self.reference

    async def read(self, kind, reference):
        assert kind == TAKE_SIDECAR_ARTIFACT_DECLARATION.id
        return deepcopy(self.values[reference])

    async def retire(self, reference):
        self.retired.append(reference)
        self.values.pop(reference, None)
        return 1

    async def release_owner(self, owner_id):
        self.released.append(owner_id)
        count = len(self.values)
        self.values.clear()
        return count


class _TakeDependencies:
    def __init__(self, sidecars):
        self.sidecars = sidecars
        self.lookups = []
        self.capture_index = _CaptureIndexCapability()

    def artifact(self, artifact_kind):
        self.lookups.append(artifact_kind)
        if artifact_kind != TAKE_SIDECAR_ARTIFACT_DECLARATION.id:
            raise AssertionError("undeclared artifact dependency")
        return self.sidecars

    def record(self, *_args):
        raise AssertionError("record authority must not be available")

    def singleton(self, singleton_id):
        self.lookups.append(singleton_id)
        assert singleton_id == CAPTURE_INDEX_ID
        return self.capture_index


class _OperationHandle:
    def __init__(self, failure=None):
        self.failure = failure
        self.adapter_result = None

    def defer(self, operation_id, adapter_result, *, subject_mode):
        assert operation_id == CAPTURE_TAKE_OPERATION_ID
        assert subject_mode == "synthetic-subject-mode"
        if self.failure is not None:
            raise self.failure
        self.adapter_result = adapter_result
        return object()


class _CaptureIndexCapability:
    def __init__(self):
        self.revision = 0
        self.value = None

    def status(self):
        return SimpleNamespace(exists=self.value is not None, revision=self.revision)

    def reveal(self):
        assert self.value is not None
        return SimpleNamespace(revision=self.revision, value=deepcopy(self.value))

    def replace(self, value, expected_revision):
        assert expected_revision == self.revision
        self.revision += 1
        self.value = deepcopy(value)
        return SimpleNamespace(revision=self.revision)


def test_take_service_defers_full_locators_without_mutating_product_timeline():
    async def scenario():
        timeline = _timeline_with_shot()
        original = deepcopy(timeline)
        sidecars = _SidecarHandle()
        operations = _OperationHandle()
        service = DirectorManagedTakeService(operations=operations)
        capture = await service.capture(
            timeline,
            _registration(),
            subject_mode="synthetic-subject-mode",
            accept=True,
            sidecar={
                "schema_version": 1,
                "registration": _registration(),
                "media": {"duration_seconds": 1.25, "path": f"/{CANARY}"},
            },
        )
        assert timeline == original
        assert repr(capture) == "DirectorDeferredTakeCapture()"
        assert CANARY not in json.dumps(capture.safe_ui)
        candidates = operations.adapter_result.references
        asset = candidates[0].value
        take = candidates[1].value
        assert isinstance(asset, CapturedAssetLocator)
        assert isinstance(take, CapturedTakeLocator)
        assert asset.value["asset"]["asset_id"] == "asset_synthetic"
        assert asset.value["take"]["take_id"] == "take_synthetic"
        assert take.value["asset"]["path"] == "/synthetic/output.mp4"
        assert take.protected_sidecar["media"]["path"] == f"/{CANARY}"
        adapter = DirectorTakeOperationProjectionAdapter()
        dependencies = _TakeDependencies(sidecars)
        invocation = ExternalOperationInvocation("hp-operation-" + "T" * 32)
        captured = adapter.capture_external_operation(
            {"timeline": timeline},
            {
                "asset": SimpleNamespace(value=asset),
                "take": SimpleNamespace(value=take),
            },
            invocation,
            ASSOCIATE_CAPTURE_OPERATION,
            dependencies,
        )
        assert captured.browser_value != timeline
        assert adapter.classify_external_operation(
            captured.context,
            invocation,
            ASSOCIATE_CAPTURE_OPERATION,
            dependencies,
        ).disposition is ExternalOperationDisposition.ABSENT
        prepared = await adapter.prepare_external_operation(
            captured.context,
            invocation,
            ASSOCIATE_CAPTURE_OPERATION,
            dependencies,
        )
        assert adapter.classify_external_operation(
            captured.context,
            invocation,
            ASSOCIATE_CAPTURE_OPERATION,
            dependencies,
        ).disposition is ExternalOperationDisposition.PREPARED
        result = adapter.finalize_external_operation(
            prepared,
            invocation,
            ASSOCIATE_CAPTURE_OPERATION,
            dependencies,
        )
        assert dependencies.lookups == [
            CAPTURE_INDEX_ID,
            CAPTURE_INDEX_ID,
            TAKE_SIDECAR_ARTIFACT_DECLARATION.id,
            CAPTURE_INDEX_ID,
            CAPTURE_INDEX_ID,
        ]
        assert result.payload == {
            name: capture.safe_ui[name]
            for name in TAKE_SAFE_DIAGNOSTIC_LEAVES
        }
        capture_entry = next(iter(dependencies.capture_index.value["captures"].values()))
        assert capture_entry["phase"] == "committed"
        assert capture_entry["externalTransactionId"] == invocation.transaction_id
        assert timeline == original
        reference = ArtifactReference(capture_entry["sidecarReference"]["id"])
        assert sidecars.values[reference]["media"]["path"] == f"/{CANARY}"
        return operations.adapter_result.safe_payload

    safe = asyncio.run(scenario())
    assert safe == {
        "accepted": True,
        "asset_count": 1,
        "duration_seconds": 1.25,
        "has_preview": False,
        "has_sidecar": True,
        "ok": True,
        "status": "Candidate",
        "take_count": 1,
    }


def test_take_service_defer_failure_never_writes_sidecar_or_mutates_timeline():
    async def scenario():
        timeline = _timeline_with_shot()
        original = deepcopy(timeline)
        sidecars = _SidecarHandle()
        service = DirectorManagedTakeService(
            operations=_OperationHandle(RuntimeError(CANARY)),
        )
        with pytest.raises(DirectorManagedTakePrivacyError) as error:
            await service.capture(
                timeline,
                _registration(),
                subject_mode="synthetic-subject-mode",
                sidecar={"private": CANARY},
            )
        assert CANARY not in str(error.value)
        assert timeline == original
        assert sidecars.values == {}
        assert sidecars.retired == []

    asyncio.run(scenario())


def test_take_safe_text_status_is_closed_and_never_forwards_canary_words():
    projected = project_safe_take_ui_candidate({
        "asset_id": "asset",
        "take_id": "take",
        "status": "SecretProject",
    })
    assert projected["status"] == "Unavailable"
    assert "SecretProject" not in json.dumps(projected)


def test_take_profile_declares_typed_payload_references_and_durable_sidecar():
    profile = build_director_take_privacy_profile()
    assert TAKE_SIDECAR_ARTIFACT_DECLARATION.retention is ArtifactRetention.DURABLE_ADJUNCT
    assert TAKE_SIDECAR_ADAPTER_ID in profile.server_adapter_contracts
    assert TAKE_SAFE_PAYLOAD_PROJECTION in profile.safe_payload_projections
    assert {leaf.path: leaf.kind for leaf in TAKE_SAFE_PAYLOAD_PROJECTION.safe_leaves} == {
        "accepted": SafePayloadKind.BOOLEAN,
        "asset_count": SafePayloadKind.COUNT,
        "duration_seconds": SafePayloadKind.NUMBER,
        "has_preview": SafePayloadKind.BOOLEAN,
        "has_sidecar": SafePayloadKind.BOOLEAN,
        "ok": SafePayloadKind.BOOLEAN,
        "status": SafePayloadKind.SAFE_TEXT,
        "take_count": SafePayloadKind.COUNT,
    }
    assert set(profile.opaque_reference_kinds) >= {
        OpaqueReferenceKind(CAPTURE_ASSET_REFERENCE_KIND, TAKE_OPERATION_RESOURCE_ID, "director-global"),
        OpaqueReferenceKind(CAPTURE_TAKE_REFERENCE_KIND, TAKE_OPERATION_RESOURCE_ID, "director-global"),
    }
    assert CAPTURE_TAKE_OPERATION.deferred_ui is True
    assert CAPTURE_TAKE_OPERATION.subject_mode_binding_id == "take-capture-mode"
    assert tuple(item.minimum for item in CAPTURE_TAKE_OPERATION.reference_outputs) == (1, 1)
    associate = next(
        item for item in profile.protected_operations
        if item.id == ASSOCIATE_CAPTURE_OPERATION_ID
    )
    assert associate == ASSOCIATE_CAPTURE_OPERATION
    assert associate.route is None
    assert associate.artifact_dependencies == (
        ArtifactOperationDependency(
            TAKE_SIDECAR_ARTIFACT_DECLARATION.id,
            ("release-owner", "write"),
        ),
    )
    assert associate.singleton_dependencies == (
        SingletonOperationDependency(
            CAPTURE_INDEX_ID,
            ("replace", "reveal", "status"),
        ),
    )
    assert associate.external_operation_binding.field_id == "timeline-state"
    assert {item.name: item.revoke_on_success for item in associate.reference_inputs} == {
        "asset": True,
        "take": True,
    }
    codec = DirectorTakeSidecarPayloadAdapter()
    encoded = codec.encode({"private": CANARY})
    assert codec.decode(encoded) == {"private": CANARY}


def test_take_inventory_covers_sidecar_ui_debug_and_filename_derivatives():
    inventory = " ".join(item.location for item in TAKE_PLAINTEXT_DERIVATIVE_INVENTORY)
    assert ".helto_take.json" in inventory
    assert "ui.helto_take_capture_result" in inventory
    assert "DEBUG_INFO" in inventory
    assert "filenames" in inventory
    assert "asset_id and take_id string output" in inventory
    gaps = " ".join(TAKE_PRIVACY_ACTIVATION_GAPS)
    assert "activation-wiring" not in gaps
    assert "legacy-output-migration" not in gaps
    assert "deferred-association" not in gaps
    assert "safe-payload" not in gaps
    assert "sidecar-storage" not in gaps
    assert "opaque-reference" not in gaps
    plan = " ".join(TAKE_OPAQUE_REFERENCE_RETIREMENT_PLAN)
    assert "opaque reference" in plan
    assert "revoke" in plan
    assert "raw string" in plan


def test_take_dependency_wiring_has_no_runtime_pack_lookup_or_provider():
    source = Path(managed_take.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "bound_privacy_pack",
        "_installed_sidecar_handle",
        "sidecar_handle_provider",
    ):
        assert forbidden not in source


def _leaf_paths(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    paths: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        paths.update(_leaf_paths(child, path))
    return paths
