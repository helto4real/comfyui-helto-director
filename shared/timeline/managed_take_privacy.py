"""D6 privacy declarations and product adapters for take capture.

Director bootstrap, the take-capture node, and the browser association path use
this module. Full registration and run metadata remain protected inputs; the
only outward projection is a closed set of booleans and counts derived by
Director-owned code.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
import math
import re
from typing import Any

from helto_privacy import (
    AdapterSlot,
    ArtifactDeclaration,
    ArtifactOperationDependency,
    ArtifactReference,
    ArtifactRetention,
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
    ResourceKind,
    SafeDiagnosticField,
    SafeDiagnosticKind,
    SafePayloadKind,
    SafePayloadLeaf,
    SafePayloadProjection,
    SensitiveFieldClass,
    SensitiveFieldDeclaration,
    SingletonOperationDependency,
    SubjectModeBinding,
    generate_artifact_owner_id,
)

from .managed_durable_state import (
    CAPTURE_INDEX_ID,
    CAPTURE_INDEX_SCHEMA,
    DURABLE_SINGLETON_IDS,
    build_director_durable_state_privacy_profile,
    normalize_capture_index,
)
from .managed_privacy import (
    DIRECTOR_DISTRIBUTION,
    DIRECTOR_PROFILE_ID,
    GLOBAL_SCOPE_ID,
    TIMELINE_BROWSER_ADAPTER_ID,
    TIMELINE_FIELD_ID,
    build_director_timeline_privacy_profile,
)
from .take_registration import prepare_take_registration, register_generated_take


TAKE_OPERATION_RESOURCE_ID = "director-take-operations"
TAKE_OPERATION_ADAPTER_ID = "director-take-operation"
CAPTURE_TAKE_OPERATION_ID = "capture-take"
ASSOCIATE_CAPTURE_OPERATION_ID = "associate-captured-take"
TAKE_SIDECAR_RESOURCE_ID = "director-take-sidecars"
TAKE_SIDECAR_ADAPTER_ID = "director-take-sidecar-payload"
TAKE_SIDECAR_ARTIFACT_KIND = "capture-take-sidecar"
TAKE_SIDECAR_PURPOSE = "capture-take-registration"
TAKE_SIDECAR_FORMAT_VERSION = 1
TAKE_SIDECAR_MEDIA_TYPE = "application/json"
TAKE_SAFE_PAYLOAD_PROJECTION_ID = "capture-take-safe-payload"
TAKE_SAFE_PAYLOAD_SCHEMA = "helto.director.capture-take-safe.v1"
TAKE_SAFE_PAYLOAD_PURPOSE = "capture-take-safe-ui"
TAKE_CAPTURE_NODE_TYPE = "HeltoTimelineTakeCapture"
TAKE_CAPTURE_SUBJECT_MODE_BINDING_ID = "take-capture-mode"
TAKE_CAPTURE_SUBJECT_MODE_INPUT = "privacy_mode_reference"
CAPTURE_ASSET_REFERENCE_KIND = "captured-asset"
CAPTURE_TAKE_REFERENCE_KIND = "captured-take"
MAX_TAKE_SIDECAR_BYTES = 4 * 1024 * 1024
MAX_SAFE_DIAGNOSTIC_COUNT = 2_147_483_647
MAX_SAFE_DURATION_SECONDS = 31_536_000.0
MAX_SAFE_SHOT_IDS = 4_096
_CAPTURE_ID = re.compile(r"^hp-owner-[A-Za-z0-9_-]{32}$")


TAKE_SAFE_OPERATION_LEAVES = (
    "accepted",
    "asset_count",
    "duration_seconds",
    "has_preview",
    "has_sidecar",
    "ok",
    "status",
    "take_count",
)
TAKE_SAFE_DIAGNOSTIC_LEAVES = tuple(
    item
    for item in TAKE_SAFE_OPERATION_LEAVES
    if item not in {"duration_seconds", "status"}
)
TAKE_SAFE_SIDECAR_LEAVES = (
    "media.duration_milliseconds",
    "media.frame_count",
    "media.height",
    "media.size_bytes",
    "media.width",
    "privacy.private",
    "registration.shot_count",
    "schema_version",
)
TAKE_SAFE_UI_LEAVES = TAKE_SAFE_OPERATION_LEAVES + ("private",)
TAKE_PRIVACY_ACTIVATION_GAPS = ()
TAKE_OPAQUE_REFERENCE_RETIREMENT_PLAN = (
    "declare asset and take opaque reference kinds on the capture-take operation",
    "return references instead of raw string identifiers and bind the routed "
    "associate-captured-take operation as the sole consumer",
    "revoke each reference on successful association; failed attempts release "
    "their claims for idempotent retry against the stable capture identity",
    "migrate saved workflows before removing the legacy asset_id and take_id "
    "string sockets and NodeOutput values",
)


TAKE_OPERATION_RESOURCE = ProfileResource(
    TAKE_OPERATION_RESOURCE_ID,
    ResourceKind.OPERATION,
    (TAKE_OPERATION_ADAPTER_ID,),
)
TAKE_SIDECAR_RESOURCE = ProfileResource(
    TAKE_SIDECAR_RESOURCE_ID,
    ResourceKind.ARTIFACT,
    (TAKE_SIDECAR_ADAPTER_ID,),
)
TAKE_OPERATION_ADAPTER_SLOT = AdapterSlot(
    TAKE_OPERATION_ADAPTER_ID,
    ResourceKind.OPERATION,
    TAKE_OPERATION_RESOURCE_ID,
)
TAKE_SIDECAR_ADAPTER_SLOT = AdapterSlot(
    TAKE_SIDECAR_ADAPTER_ID,
    ResourceKind.ARTIFACT,
    TAKE_SIDECAR_RESOURCE_ID,
)
TAKE_SIDECAR_ARTIFACT_DECLARATION = ArtifactDeclaration(
    TAKE_SIDECAR_ARTIFACT_KIND,
    TAKE_SIDECAR_RESOURCE_ID,
    GLOBAL_SCOPE_ID,
    TAKE_SIDECAR_PURPOSE,
    TAKE_SIDECAR_ADAPTER_ID,
    TAKE_SIDECAR_FORMAT_VERSION,
    ArtifactRetention.DURABLE_ADJUNCT,
    ("details",),
    media_type=TAKE_SIDECAR_MEDIA_TYPE,
)
TAKE_SAFE_PAYLOAD_PROJECTION = SafePayloadProjection(
    TAKE_SAFE_PAYLOAD_PROJECTION_ID,
    CAPTURE_TAKE_OPERATION_ID,
    TAKE_SAFE_PAYLOAD_SCHEMA,
    TAKE_SAFE_PAYLOAD_PURPOSE,
    (
        SafePayloadLeaf("accepted", SafePayloadKind.BOOLEAN),
        SafePayloadLeaf("asset_count", SafePayloadKind.COUNT),
        SafePayloadLeaf("duration_seconds", SafePayloadKind.NUMBER),
        SafePayloadLeaf("has_preview", SafePayloadKind.BOOLEAN),
        SafePayloadLeaf("has_sidecar", SafePayloadKind.BOOLEAN),
        SafePayloadLeaf("ok", SafePayloadKind.BOOLEAN),
        SafePayloadLeaf("status", SafePayloadKind.SAFE_TEXT),
        SafePayloadLeaf("take_count", SafePayloadKind.COUNT),
    ),
)
CAPTURE_TAKE_OPERATION = ProtectedOperation(
    CAPTURE_TAKE_OPERATION_ID,
    TAKE_OPERATION_RESOURCE_ID,
    TAKE_OPERATION_ADAPTER_ID,
    None,
    scope_id=GLOBAL_SCOPE_ID,
    subject_mode_binding_id=TAKE_CAPTURE_SUBJECT_MODE_BINDING_ID,
    sensitive_fields=(
        SensitiveFieldDeclaration("*", SensitiveFieldClass.CONSUMER_DERIVED),
        SensitiveFieldDeclaration(
            "debug_info",
            SensitiveFieldClass.DEBUG,
        ),
        SensitiveFieldDeclaration(
            "media.filename",
            SensitiveFieldClass.PATH_OR_NAME,
        ),
        SensitiveFieldDeclaration(
            "media.path",
            SensitiveFieldClass.PATH_OR_NAME,
        ),
        SensitiveFieldDeclaration(
            "registration",
            SensitiveFieldClass.CONSUMER_DERIVED,
        ),
        SensitiveFieldDeclaration(
            "runtime_context",
            SensitiveFieldClass.DEBUG,
        ),
        SensitiveFieldDeclaration(
            "sidecar",
            SensitiveFieldClass.CONSUMER_DERIVED,
        ),
        SensitiveFieldDeclaration(
            "ui",
            SensitiveFieldClass.CONSUMER_DERIVED,
        ),
    ),
    safe_projection=tuple(
        SafeDiagnosticField(
            leaf,
            SafeDiagnosticKind.COUNT if leaf.endswith("_count") else SafeDiagnosticKind.BOOLEAN,
        )
        for leaf in TAKE_SAFE_DIAGNOSTIC_LEAVES
    ),
    reference_outputs=(
        OperationReferenceOutput(CAPTURE_ASSET_REFERENCE_KIND),
        OperationReferenceOutput(CAPTURE_TAKE_REFERENCE_KIND),
    ),
    safe_payload_projection_id=TAKE_SAFE_PAYLOAD_PROJECTION_ID,
    deferred_ui=True,
)
ASSOCIATE_CAPTURE_OPERATION = ProtectedOperation(
    ASSOCIATE_CAPTURE_OPERATION_ID,
    TAKE_OPERATION_RESOURCE_ID,
    TAKE_OPERATION_ADAPTER_ID,
    None,
    scope_id=GLOBAL_SCOPE_ID,
    sensitive_fields=(
        SensitiveFieldDeclaration("*", SensitiveFieldClass.CONSUMER_DERIVED),
        SensitiveFieldDeclaration("timeline", SensitiveFieldClass.USER_AUTHORED),
    ),
    safe_projection=tuple(
        SafeDiagnosticField(
            leaf,
            SafeDiagnosticKind.COUNT if leaf.endswith("_count") else SafeDiagnosticKind.BOOLEAN,
        )
        for leaf in TAKE_SAFE_DIAGNOSTIC_LEAVES
    ),
    reference_inputs=(
        OperationReferenceInput(
            "asset",
            CAPTURE_ASSET_REFERENCE_KIND,
            revoke_on_success=True,
        ),
        OperationReferenceInput(
            "take",
            CAPTURE_TAKE_REFERENCE_KIND,
            revoke_on_success=True,
        ),
    ),
    artifact_dependencies=(
        ArtifactOperationDependency(
            TAKE_SIDECAR_ARTIFACT_KIND,
            ("release-owner", "write"),
        ),
    ),
    singleton_dependencies=(
        SingletonOperationDependency(
            CAPTURE_INDEX_ID,
            ("replace", "reveal", "status"),
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
)


@dataclass(frozen=True, slots=True)
class DirectorPlaintextDerivative:
    location: str
    data_class: str
    transition: str


TAKE_PLAINTEXT_DERIVATIVE_INVENTORY = (
    DirectorPlaintextDerivative(
        "generated media sibling *.helto_take.json and temporary variants",
        "registration, project, media, model, prompt-hash and path metadata",
        "replace the full sidecar with a protected value; emit only the safe sidecar projection",
    ),
    DirectorPlaintextDerivative(
        "io.NodeOutput ui.helto_take_capture_result",
        "registration, media path/name and debug summary metadata",
        "emit only the closed safe UI projection; preview serving remains a "
        "separate lease migration",
    ),
    DirectorPlaintextDerivative(
        "DEBUG_INFO summary and runtime-context take_registration entries",
        "paths, names, project identity, model settings and run metadata",
        "retain internally for association and expose only declared boolean/count diagnostics",
    ),
    DirectorPlaintextDerivative(
        "capture output and sidecar filenames derived from shot/take identifiers",
        "path-bearing and user-correlatable names",
        "remove identifier-derived naming during the D1 cutover; output "
        "ownership remains product-defined",
    ),
    DirectorPlaintextDerivative(
        "nodes/timeline_take_capture/node.py asset_id and take_id string output "
        "schema and NodeOutput slots",
        "raw stable identifiers exposed to downstream workflow nodes",
        "replace with capture-take opaque references, consume and revoke during "
        "deferred association, then retire raw sockets after workflow migration",
    ),
)


class DirectorManagedTakePrivacyError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Director safe take projection failed.")


@dataclass(frozen=True, slots=True, repr=False)
class CapturedAssetLocator:
    value: dict[str, Any] = field(repr=False, compare=False)
    capture_id: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class CapturedTakeLocator:
    value: dict[str, Any] = field(repr=False, compare=False)
    protected_sidecar: dict[str, Any] = field(repr=False, compare=False)
    safe_payload: dict[str, object] = field(repr=False, compare=False)
    capture_id: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class DirectorDeferredTakeCapture:
    association: object = field(repr=False, compare=False)
    safe_ui: dict[str, object] = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "DirectorDeferredTakeCapture()"


class DirectorTakeSidecarPayloadAdapter:
    """Exact JSON codec; shared artifact storage owns encryption and atomicity."""

    def encode(self, value: object) -> bytes:
        if type(value) is not dict:
            raise DirectorManagedTakePrivacyError()
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise DirectorManagedTakePrivacyError() from None
        if not encoded or len(encoded) > MAX_TAKE_SIDECAR_BYTES:
            raise DirectorManagedTakePrivacyError()
        return encoded

    def decode(self, value: bytes) -> dict[str, Any]:
        if type(value) not in {bytes, bytearray} or len(value) > MAX_TAKE_SIDECAR_BYTES:
            raise DirectorManagedTakePrivacyError()
        try:
            decoded = json.loads(bytes(value).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise DirectorManagedTakePrivacyError() from None
        if type(decoded) is not dict:
            raise DirectorManagedTakePrivacyError()
        return decoded

    def purge_plaintext_derivatives(self, artifact_kind: str) -> None:
        if artifact_kind != TAKE_SIDECAR_ARTIFACT_KIND:
            raise DirectorManagedTakePrivacyError()

    def prepare_mode_transition(self, *_args: object) -> None:
        return None

    def commit_mode_transition(self, *_args: object) -> None:
        return None

    def rollback_mode_transition(self, *_args: object) -> None:
        return None


class DirectorManagedTakeService:
    """Product seam over shared durable artifacts and associations."""

    def __init__(self, *, operations: object) -> None:
        if not callable(getattr(operations, "defer", None)):
            raise TypeError("A shared Director operation handle is required.")
        self._operations = operations

    async def capture(
        self,
        timeline: object,
        registration: dict[str, Any] | str,
        *,
        subject_mode: object,
        generated_asset_path: str | None = None,
        accept: bool = False,
        update_clip_instance: bool = True,
        sidecar: object = None,
        preview: object = None,
    ) -> DirectorDeferredTakeCapture:
        try:
            normalized = normalize_take_registration_candidate(
                registration,
                generated_asset_path=generated_asset_path,
                accept=accept,
                update_clip_instance=update_clip_instance,
            )
            protected_sidecar = _protected_sidecar(normalized, sidecar)
            safe_payload = _safe_capture_payload(
                normalized,
                sidecar=protected_sidecar,
                preview=preview,
                accepted=accept,
            )
            capture_id = generate_artifact_owner_id()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise DirectorManagedTakePrivacyError() from None
        asset_locator = CapturedAssetLocator(deepcopy(normalized), capture_id)
        take_locator = CapturedTakeLocator(
            deepcopy(normalized),
            deepcopy(protected_sidecar),
            deepcopy(safe_payload),
            capture_id,
        )
        adapter_result = ProtectedOperationAdapterResult(
            None,
            (
                OpaqueReferenceCandidate(
                    CAPTURE_ASSET_REFERENCE_KIND,
                    asset_locator,
                ),
                OpaqueReferenceCandidate(
                    CAPTURE_TAKE_REFERENCE_KIND,
                    take_locator,
                ),
            ),
            safe_payload,
        )
        try:
            association = self._operations.defer(
                CAPTURE_TAKE_OPERATION_ID,
                adapter_result,
                subject_mode=subject_mode,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise DirectorManagedTakePrivacyError() from None
        capture = DirectorDeferredTakeCapture(
            association,
            deepcopy(safe_payload),
        )
        return capture


async def _write_sidecar(
    sidecars: object,
    owner_id: str,
    value: object,
) -> object:
    return await sidecars.write(owner_id, value)


def _protected_sidecar(
    normalized: dict[str, Any],
    sidecar: object,
) -> dict[str, Any]:
    if sidecar is None:
        return {
            "schema_version": TAKE_SIDECAR_FORMAT_VERSION,
            "registration": deepcopy(normalized),
        }
    if type(sidecar) is not dict:
        raise DirectorManagedTakePrivacyError()
    return deepcopy(sidecar)


def build_director_take_privacy_profile(
    base_profile: PrivacyProfile | None = None,
) -> PrivacyProfile:
    """Compose the take fragment onto any valid Director base."""

    base = base_profile or build_director_timeline_privacy_profile()
    _require_director_base(base)
    present_singletons = {item.id for item in base.singletons}
    if present_singletons.isdisjoint(DURABLE_SINGLETON_IDS):
        base = build_director_durable_state_privacy_profile(base)
    elif not set(DURABLE_SINGLETON_IDS).issubset(present_singletons):
        raise ValueError("Director durable state fragment is incomplete.")
    _require_ids_available(
        base,
        resource_id=TAKE_OPERATION_RESOURCE_ID,
        adapter_id=TAKE_OPERATION_ADAPTER_ID,
        operation_id=CAPTURE_TAKE_OPERATION_ID,
    )
    return replace(
        base,
        resources=(
            *base.resources,
            TAKE_OPERATION_RESOURCE,
            TAKE_SIDECAR_RESOURCE,
        ),
        server_adapters=(
            *base.server_adapters,
            TAKE_OPERATION_ADAPTER_SLOT,
            TAKE_SIDECAR_ADAPTER_SLOT,
        ),
        protected_operations=(
            *base.protected_operations,
            CAPTURE_TAKE_OPERATION,
            ASSOCIATE_CAPTURE_OPERATION,
        ),
        subject_mode_bindings=(
            *base.subject_mode_bindings,
            SubjectModeBinding(
                TAKE_CAPTURE_SUBJECT_MODE_BINDING_ID,
                GLOBAL_SCOPE_ID,
                TAKE_CAPTURE_SUBJECT_MODE_INPUT,
                (TAKE_CAPTURE_NODE_TYPE,),
            ),
        ),
        artifacts=(*base.artifacts, TAKE_SIDECAR_ARTIFACT_DECLARATION),
        opaque_reference_kinds=(
            *base.opaque_reference_kinds,
            OpaqueReferenceKind(
                CAPTURE_ASSET_REFERENCE_KIND,
                TAKE_OPERATION_RESOURCE_ID,
                GLOBAL_SCOPE_ID,
            ),
            OpaqueReferenceKind(
                CAPTURE_TAKE_REFERENCE_KIND,
                TAKE_OPERATION_RESOURCE_ID,
                GLOBAL_SCOPE_ID,
            ),
        ),
        safe_payload_projections=(
            *base.safe_payload_projections,
            TAKE_SAFE_PAYLOAD_PROJECTION,
        ),
    )


def build_director_take_server_adapters() -> dict[str, object]:
    return {
        TAKE_OPERATION_ADAPTER_ID: DirectorTakeOperationProjectionAdapter(),
        TAKE_SIDECAR_ADAPTER_ID: DirectorTakeSidecarPayloadAdapter(),
    }


def has_complete_director_take_privacy_fragment(profile: PrivacyProfile) -> bool:
    present = any(
        item.id in {TAKE_OPERATION_RESOURCE_ID, TAKE_SIDECAR_RESOURCE_ID}
        for item in profile.resources
    )
    if not present:
        return False
    required = (
        TAKE_OPERATION_RESOURCE in profile.resources
        and TAKE_SIDECAR_RESOURCE in profile.resources
        and TAKE_OPERATION_ADAPTER_SLOT in profile.server_adapters
        and TAKE_SIDECAR_ADAPTER_SLOT in profile.server_adapters
        and CAPTURE_TAKE_OPERATION in profile.protected_operations
        and ASSOCIATE_CAPTURE_OPERATION in profile.protected_operations
        and TAKE_SIDECAR_ARTIFACT_DECLARATION in profile.artifacts
        and TAKE_SAFE_PAYLOAD_PROJECTION in profile.safe_payload_projections
        and {CAPTURE_ASSET_REFERENCE_KIND, CAPTURE_TAKE_REFERENCE_KIND}.issubset(
            {item.id for item in profile.opaque_reference_kinds}
        )
    )
    if not required:
        raise ValueError("Director take privacy fragment is incomplete.")
    return True


def normalize_take_registration_candidate(
    registration: dict[str, Any] | str,
    *,
    generated_asset_path: str | None = None,
    accept: bool = False,
    update_clip_instance: bool = True,
) -> dict[str, Any]:
    """Use the current product normalizer without changing its semantics."""

    return prepare_take_registration(
        registration,
        generated_asset_path=generated_asset_path,
        accept=accept,
        update_clip_instance=update_clip_instance,
    )


def associate_take_output_candidate(
    timeline: object,
    normalized_registration: dict[str, Any],
) -> dict[str, Any]:
    """Use the current product association boundary on an isolated copy."""

    if type(normalized_registration) is not dict:
        raise TypeError("Director take registration must be an object.")
    return register_generated_take(timeline, deepcopy(dict(normalized_registration)))


def project_safe_take_sidecar_candidate(sidecar: object) -> dict[str, object]:
    """Project a sidecar candidate without copying any uncontrolled string."""

    source = _mapping(sidecar)
    media = _mapping(source.get("media"))
    registration = _mapping(source.get("registration"))
    privacy = _mapping(source.get("privacy"))
    duration = _bounded_number(
        media.get("duration_seconds"),
        maximum=MAX_SAFE_DURATION_SECONDS,
    )
    return {
        "schema_version": _non_negative_count(source.get("schema_version")),
        "privacy": {"private": privacy.get("privacy_mode") is True},
        "registration": {
            "shot_count": _safe_shot_count(registration.get("shot_ids"))
        },
        "media": {
            "frame_count": _non_negative_count(media.get("frame_count")),
            "duration_milliseconds": min(
                MAX_SAFE_DIAGNOSTIC_COUNT,
                int(duration * 1000),
            ),
            "width": _non_negative_count(media.get("width")),
            "height": _non_negative_count(media.get("height")),
            "size_bytes": _non_negative_count(media.get("size_bytes")),
        },
    }


def project_safe_take_ui_candidate(
    result: object,
    *,
    sidecar: object = None,
    preview: object = None,
    private: bool = True,
) -> dict[str, object]:
    source = _mapping(result)
    projection = _safe_capture_payload(
        source,
        sidecar=sidecar,
        preview=preview,
        accepted=source.get("accepted") is True,
    )
    return {**projection, "private": private is True}


class DirectorTakeOperationProjectionAdapter:
    """D6 projection plus restart-classifiable external association phases."""

    def project(self, value: object, declaration: object) -> dict[str, object]:
        declaration_id = getattr(declaration, "id", None)
        if declaration_id == ASSOCIATE_CAPTURE_OPERATION_ID:
            source = _mapping(value)
            if set(source) != set(TAKE_SAFE_DIAGNOSTIC_LEAVES):
                raise DirectorManagedTakePrivacyError()
            return deepcopy(source)
        if declaration_id != CAPTURE_TAKE_OPERATION_ID:
            raise DirectorManagedTakePrivacyError()
        payload = _mapping(value)
        result = payload.get("result", value)
        return _safe_operation_projection(
            result,
            sidecar=payload.get("sidecar"),
            preview=payload.get("preview"),
        )

    def project_safe_payload(
        self,
        value: object,
        declaration: object,
    ) -> dict[str, object]:
        if getattr(declaration, "id", None) != TAKE_SAFE_PAYLOAD_PROJECTION_ID:
            raise DirectorManagedTakePrivacyError()
        return _validated_safe_capture_payload(value)

    def capture_external_operation(
        self,
        value: object,
        references: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> ExternalOperationCapture:
        if getattr(declaration, "id", None) != ASSOCIATE_CAPTURE_OPERATION_ID:
            raise DirectorManagedTakePrivacyError()
        source = _mapping(value)
        if (
            set(source) != {"timeline"}
            or type(source["timeline"]) is not dict
            or type(references) is not dict
        ):
            raise DirectorManagedTakePrivacyError()
        asset = getattr(references.get("asset"), "value", None)
        take = getattr(references.get("take"), "value", None)
        if (
            not isinstance(asset, CapturedAssetLocator)
            or not isinstance(take, CapturedTakeLocator)
            or asset.value != take.value
            or asset.capture_id != take.capture_id
        ):
            raise DirectorManagedTakePrivacyError()
        associated = associate_take_output_candidate(
            deepcopy(source["timeline"]),
            take.value,
        )
        if type(associated) is not dict or type(associated.get("timeline")) is not dict:
            raise DirectorManagedTakePrivacyError()
        context = {
            "captureId": take.capture_id,
            "externalTransactionId": invocation.transaction_id,
            "protectedSidecar": deepcopy(take.protected_sidecar),
            "safePayload": _validated_safe_capture_payload(take.safe_payload),
        }
        _require_capture_context(context, invocation)
        return ExternalOperationCapture(context, deepcopy(associated["timeline"]))

    def classify_external_operation(
        self,
        capture_context: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> ExternalOperationClassification:
        _require_association_declaration(declaration)
        context = _require_capture_context(capture_context, invocation)
        _capability, revision, index = _capture_index_state(dependencies)
        entry = index["captures"].get(context["captureId"])
        if entry is None:
            return ExternalOperationClassification(
                ExternalOperationDisposition.ABSENT,
            )
        _require_capture_entry(entry, context)
        if entry["phase"] == "committed":
            return ExternalOperationClassification(
                ExternalOperationDisposition.COMPLETED,
                result=_associated_capture_result(context["safePayload"]),
            )
        return ExternalOperationClassification(
            ExternalOperationDisposition.PREPARED,
            _prepared_capture_context(context, revision, entry["sidecarReference"]),
        )

    async def prepare_external_operation(
        self,
        capture_context: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> dict[str, object]:
        _require_association_declaration(declaration)
        context = _require_capture_context(capture_context, invocation)
        capability, revision, index = _capture_index_state(dependencies)
        if context["captureId"] in index["captures"]:
            raise DirectorManagedTakePrivacyError()
        sidecars = _sidecar_capability(dependencies)
        try:
            await sidecars.release_owner(context["captureId"])
            reference = await _write_sidecar(
                sidecars,
                context["captureId"],
                context["protectedSidecar"],
            )
            if not isinstance(reference, ArtifactReference):
                raise DirectorManagedTakePrivacyError()
            replacement = deepcopy(index)
            replacement["captures"][context["captureId"]] = {
                "phase": "sidecar-pending",
                "externalTransactionId": invocation.transaction_id,
                "artifactOwnerId": context["captureId"],
                "sidecarReference": reference.to_payload(),
            }
            replacement = normalize_capture_index(replacement)
            receipt = capability.replace(replacement, revision)
            if getattr(receipt, "revision", None) != revision + 1:
                raise DirectorManagedTakePrivacyError()
            return _prepared_capture_context(
                context,
                receipt.revision,
                reference.to_payload(),
            )
        except asyncio.CancelledError:
            await _release_sidecar_owner_silent(sidecars, context["captureId"])
            raise
        except Exception:
            await _release_sidecar_owner_silent(sidecars, context["captureId"])
            raise DirectorManagedTakePrivacyError() from None

    def finalize_external_operation(
        self,
        prepared_context: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> ProtectedOperationAdapterResult:
        _require_association_declaration(declaration)
        context = _require_prepared_capture_context(prepared_context, invocation)
        capability, revision, index = _capture_index_state(dependencies)
        entry = index["captures"].get(context["captureId"])
        _require_capture_entry(entry, context)
        if entry["phase"] != "sidecar-pending":
            raise DirectorManagedTakePrivacyError()
        if entry["sidecarReference"] != context["sidecarReference"]:
            raise DirectorManagedTakePrivacyError()
        replacement = deepcopy(index)
        replacement["captures"][context["captureId"]]["phase"] = "committed"
        receipt = capability.replace(normalize_capture_index(replacement), revision)
        if getattr(receipt, "revision", None) != revision + 1:
            raise DirectorManagedTakePrivacyError()
        return _associated_capture_result(context["safePayload"])

    async def rollback_external_operation(
        self,
        operation_context: object,
        invocation: ExternalOperationInvocation,
        declaration: object,
        dependencies: object,
    ) -> bool:
        _require_association_declaration(declaration)
        context = _capture_context_from_phase(operation_context, invocation)
        sidecars = _sidecar_capability(dependencies)
        capability, revision, index = _capture_index_state(dependencies)
        entry = index["captures"].get(context["captureId"])
        if entry is not None:
            _require_capture_entry(entry, context)
            if entry["phase"] != "sidecar-pending":
                raise DirectorManagedTakePrivacyError()
        await sidecars.release_owner(context["captureId"])
        if entry is not None:
            replacement = deepcopy(index)
            del replacement["captures"][context["captureId"]]
            receipt = capability.replace(normalize_capture_index(replacement), revision)
            if getattr(receipt, "revision", None) != revision + 1:
                raise DirectorManagedTakePrivacyError()
        return True


def _require_association_declaration(declaration: object) -> None:
    if getattr(declaration, "id", None) != ASSOCIATE_CAPTURE_OPERATION_ID:
        raise DirectorManagedTakePrivacyError()


def _require_capture_context(
    value: object,
    invocation: ExternalOperationInvocation,
) -> dict[str, object]:
    source = _mapping(value)
    if set(source) != {
        "captureId",
        "externalTransactionId",
        "protectedSidecar",
        "safePayload",
    }:
        raise DirectorManagedTakePrivacyError()
    capture_id = source["captureId"]
    if (
        not isinstance(invocation, ExternalOperationInvocation)
        or not isinstance(capture_id, str)
        or _CAPTURE_ID.fullmatch(capture_id) is None
        or source["externalTransactionId"] != invocation.transaction_id
        or type(source["protectedSidecar"]) is not dict
    ):
        raise DirectorManagedTakePrivacyError()
    return {
        "captureId": capture_id,
        "externalTransactionId": invocation.transaction_id,
        "protectedSidecar": deepcopy(source["protectedSidecar"]),
        "safePayload": _validated_safe_capture_payload(source["safePayload"]),
    }


def _require_prepared_capture_context(
    value: object,
    invocation: ExternalOperationInvocation,
) -> dict[str, object]:
    source = _mapping(value)
    if set(source) != {
        "captureId",
        "captureIndexRevision",
        "externalTransactionId",
        "protectedSidecar",
        "safePayload",
        "sidecarReference",
    }:
        raise DirectorManagedTakePrivacyError()
    base = _require_capture_context(
        {name: source[name] for name in (
            "captureId",
            "externalTransactionId",
            "protectedSidecar",
            "safePayload",
        )},
        invocation,
    )
    revision = source["captureIndexRevision"]
    reference = _artifact_reference_payload(source["sidecarReference"])
    if type(revision) is not int or revision <= 0:
        raise DirectorManagedTakePrivacyError()
    return {
        **base,
        "captureIndexRevision": revision,
        "sidecarReference": reference,
    }


def _capture_context_from_phase(
    value: object,
    invocation: ExternalOperationInvocation,
) -> dict[str, object]:
    source = _mapping(value)
    return (
        _require_prepared_capture_context(source, invocation)
        if "captureIndexRevision" in source
        else _require_capture_context(source, invocation)
    )


def _prepared_capture_context(
    context: dict[str, object],
    revision: int,
    reference: object,
) -> dict[str, object]:
    if type(revision) is not int or revision <= 0:
        raise DirectorManagedTakePrivacyError()
    return {
        **deepcopy(context),
        "captureIndexRevision": revision,
        "sidecarReference": _artifact_reference_payload(reference),
    }


def _artifact_reference_payload(value: object) -> dict[str, object]:
    source = _mapping(value)
    if (
        set(source) != {"schema", "version", "id"}
        or source.get("schema") != "helto.private-artifact-reference"
        or source.get("version") != 1
    ):
        raise DirectorManagedTakePrivacyError()
    try:
        reference = ArtifactReference(source["id"])
    except Exception:
        raise DirectorManagedTakePrivacyError() from None
    return reference.to_payload()


def _capture_index_state(
    dependencies: object,
) -> tuple[object, int, dict[str, object]]:
    try:
        capability = dependencies.singleton(CAPTURE_INDEX_ID)
        status = capability.status()
        revision = getattr(status, "revision", None)
        exists = getattr(status, "exists", None)
        if type(revision) is not int or revision < 0 or type(exists) is not bool:
            raise DirectorManagedTakePrivacyError()
        if not exists:
            value = {
                "schema": CAPTURE_INDEX_SCHEMA,
                "version": 1,
                "captures": {},
            }
        else:
            revealed = capability.reveal()
            if getattr(revealed, "revision", None) != revision:
                raise DirectorManagedTakePrivacyError()
            value = normalize_capture_index(getattr(revealed, "value", None))
        return capability, revision, value
    except DirectorManagedTakePrivacyError:
        raise
    except Exception:
        raise DirectorManagedTakePrivacyError() from None


def _require_capture_entry(
    entry: object,
    context: dict[str, object],
) -> None:
    source = _mapping(entry)
    if (
        source.get("externalTransactionId") != context["externalTransactionId"]
        or source.get("artifactOwnerId") != context["captureId"]
        or source.get("phase") not in {"sidecar-pending", "committed"}
    ):
        raise DirectorManagedTakePrivacyError()
    _artifact_reference_payload(source.get("sidecarReference"))


def _sidecar_capability(dependencies: object) -> object:
    try:
        sidecars = dependencies.artifact(TAKE_SIDECAR_ARTIFACT_KIND)
    except Exception:
        raise DirectorManagedTakePrivacyError() from None
    if any(
        not callable(getattr(sidecars, name, None))
        for name in ("write", "release_owner")
    ):
        raise DirectorManagedTakePrivacyError()
    return sidecars


async def _release_sidecar_owner_silent(sidecars: object, owner_id: str) -> None:
    try:
        await sidecars.release_owner(owner_id)
    except BaseException:
        return


def _associated_capture_result(
    safe_payload: dict[str, object],
) -> ProtectedOperationAdapterResult:
    return ProtectedOperationAdapterResult({
        name: safe_payload[name]
        for name in TAKE_SAFE_DIAGNOSTIC_LEAVES
    })


def _safe_operation_projection(
    result: object,
    *,
    sidecar: object,
    preview: object,
) -> dict[str, object]:
    source = _mapping(result)
    has_asset = _has_identifier(source.get("asset_id"))
    has_take = _has_identifier(source.get("take_id"))
    return {
        "ok": source.get("ok") is True or (has_asset and has_take),
        "accepted": source.get("accepted") is True,
        "has_sidecar": sidecar is not None,
        "has_preview": preview is not None,
        "asset_count": int(has_asset),
        "take_count": int(has_take),
    }


def _safe_capture_payload(
    result: object,
    *,
    sidecar: object,
    preview: object,
    accepted: bool,
) -> dict[str, object]:
    source = _mapping(result)
    asset = _mapping(source.get("asset"))
    take = _mapping(source.get("take"))
    sidecar_media = _mapping(_mapping(sidecar).get("media"))
    asset_id = source.get("asset_id", asset.get("asset_id"))
    take_id = source.get("take_id", take.get("take_id"))
    has_asset = _has_identifier(asset_id)
    has_take = _has_identifier(take_id)
    return {
        "accepted": accepted is True,
        "asset_count": int(has_asset),
        "duration_seconds": _bounded_number(
            sidecar_media.get("duration_seconds"),
            maximum=MAX_SAFE_DURATION_SECONDS,
        ),
        "has_preview": preview is not None,
        "has_sidecar": sidecar is not None,
        "ok": has_asset and has_take,
        "status": _safe_status(source.get("status", take.get("status"))),
        "take_count": int(has_take),
    }


def _validated_safe_capture_payload(value: object) -> dict[str, object]:
    source = _mapping(value)
    if set(source) != set(TAKE_SAFE_OPERATION_LEAVES):
        raise DirectorManagedTakePrivacyError()
    boolean_fields = ("accepted", "has_preview", "has_sidecar", "ok")
    if any(type(source.get(name)) is not bool for name in boolean_fields):
        raise DirectorManagedTakePrivacyError()
    count_fields = ("asset_count", "take_count")
    if any(
        type(source.get(name)) is not int
        or not 0 <= source[name] <= MAX_SAFE_DIAGNOSTIC_COUNT
        for name in count_fields
    ):
        raise DirectorManagedTakePrivacyError()
    duration = source.get("duration_seconds")
    if (
        type(duration) not in {int, float}
        or type(duration) is bool
        or not math.isfinite(float(duration))
        or not 0 <= float(duration) <= MAX_SAFE_DURATION_SECONDS
    ):
        raise DirectorManagedTakePrivacyError()
    status = source.get("status")
    if status != _safe_status(status):
        raise DirectorManagedTakePrivacyError()
    return deepcopy(source)


_SAFE_TAKE_STATUSES = frozenset({"Candidate", "Accepted", "Rejected", "Unavailable"})


def _safe_status(value: object) -> str:
    return value if type(value) is str and value in _SAFE_TAKE_STATUSES else "Unavailable"


def _require_director_base(base: PrivacyProfile) -> None:
    if base.id != DIRECTOR_PROFILE_ID or base.distribution != DIRECTOR_DISTRIBUTION:
        raise ValueError("Director take privacy requires the Director profile.")
    if not any(scope.id == GLOBAL_SCOPE_ID for scope in base.scopes):
        raise ValueError("Director take privacy requires the global Director scope.")


def _require_ids_available(
    base: PrivacyProfile,
    *,
    resource_id: str,
    adapter_id: str,
    operation_id: str,
) -> None:
    if any(resource.id == resource_id for resource in base.resources):
        raise ValueError("Director take privacy fragment is already present.")
    if any(adapter.id == adapter_id for adapter in base.server_adapters):
        raise ValueError("Director take privacy fragment is already present.")
    if any(operation.id == operation_id for operation in base.protected_operations):
        raise ValueError("Director take privacy fragment is already present.")
    reserved_ids = {
        TAKE_SIDECAR_RESOURCE_ID,
        TAKE_SIDECAR_ADAPTER_ID,
        TAKE_SIDECAR_ARTIFACT_KIND,
        TAKE_SAFE_PAYLOAD_PROJECTION_ID,
        ASSOCIATE_CAPTURE_OPERATION_ID,
        CAPTURE_ASSET_REFERENCE_KIND,
        CAPTURE_TAKE_REFERENCE_KIND,
    }
    existing = {
        *(item.id for item in base.resources),
        *(item.id for item in base.server_adapters),
        *(item.id for item in base.artifacts),
        *(item.id for item in base.safe_payload_projections),
        *(item.id for item in base.opaque_reference_kinds),
        *(item.id for item in base.protected_operations),
    }
    if existing.intersection(reserved_ids):
        raise ValueError("Director take privacy fragment is already present.")


def _mapping(value: object) -> dict[str, Any]:
    return value if type(value) is dict else {}


def _non_negative_count(value: object) -> int:
    if type(value) is bool:
        return 0
    if type(value) is int:
        return min(MAX_SAFE_DIAGNOSTIC_COUNT, max(0, value))
    if type(value) is not float or not math.isfinite(value):
        return 0
    if value >= MAX_SAFE_DIAGNOSTIC_COUNT:
        return MAX_SAFE_DIAGNOSTIC_COUNT
    return max(0, int(value))


def _bounded_number(value: object, *, maximum: float) -> float:
    if type(value) is bool:
        return 0.0
    if type(value) is int:
        if value <= 0:
            return 0.0
        if value >= maximum:
            return maximum
        return float(value)
    if type(value) is not float or not math.isfinite(value):
        return 0.0
    return min(maximum, max(0.0, value))


def _safe_shot_count(value: object) -> int:
    if type(value) not in {list, tuple}:
        return 0
    return sum(
        1
        for item in value[:MAX_SAFE_SHOT_IDS]
        if type(item) is str and bool(item.strip())
    )


def _has_identifier(value: object) -> bool:
    return type(value) is str and bool(value.strip())
