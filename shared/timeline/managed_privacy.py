"""Managed privacy declaration and adapters for Director timeline state."""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Callable, Mapping, MutableMapping
from pathlib import Path
from threading import RLock

from helto_privacy import (
    DIRECTOR_V1_JSON_KEY_IMPORT_ID,
    AdapterSlot,
    ExternalTransitionPolicy,
    FieldLocation,
    FieldLocationKind,
    LegacyKeyFormat,
    LegacyKeyImportBinding,
    LegacyLocationKind,
    PrivacyProfile,
    PrivacyEnvelopeCodec,
    PrivacyScope,
    ProfileResource,
    ProtectedField,
    ProtectedStateAuthority,
    ResourceKind,
    SemanticExecutionProjection,
    SubjectModeBinding,
)

from .execution import build_timeline_outputs
from .global_settings import (
    compare_and_set_global_privacy_mode_source,
    load_global_settings,
    normalize_global_settings,
    read_global_privacy_mode_source,
    rollback_global_privacy_mode_source,
    save_global_settings,
)
from .normalize import normalize_video_timeline


DIRECTOR_PROFILE_ID = "helto.director"
DIRECTOR_DISTRIBUTION = "comfyui-helto-director"
DIRECTOR_NODE_TYPE = "HeltoVideoTimelineDirector"
DIRECTOR_TAKE_CAPTURE_NODE_TYPE = "HeltoTimelineTakeCapture"
DIRECTOR_TIMELINE_SCHEMA = "helto.timeline-director"

GLOBAL_MODE_RESOURCE_ID = "director-global-mode"
TIMELINE_RESOURCE_ID = "timeline"
TIMELINE_EXECUTION_RESOURCE_ID = "timeline-render"
GLOBAL_SCOPE_ID = "director-global"

GLOBAL_MODE_ADAPTER_ID = "director-global-mode-state"
GLOBAL_MODE_BROWSER_ADAPTER_ID = "director-global-mode-browser"
TIMELINE_STATE_ADAPTER_ID = "director-timeline-state"
TIMELINE_BROWSER_ADAPTER_ID = "director-timeline-browser"
TIMELINE_PROJECTION_ADAPTER_ID = "director-timeline-projection"
TIMELINE_DISPATCH_ADAPTER_ID = "director-timeline-dispatch"

TIMELINE_FIELD_ID = "timeline-state"
TIMELINE_WIDGET_NAME = "video_timeline_json"
TIMELINE_SUBJECT_MODE_BINDING_ID = "timeline-render-mode"
TIMELINE_EXECUTION_PROJECTION_ID = "render-timeline"
TIMELINE_SUBJECT_INPUT = "privacy_mode_reference"
TIMELINE_EXECUTION_INPUT = "private_execution"
TIMELINE_KEY_IMPORT_BINDING_ID = "timeline-state-director-json-key-v1"


def build_director_timeline_privacy_profile() -> PrivacyProfile:
    """Build the D2 profile slice without registering it independently."""

    return PrivacyProfile(
        id=DIRECTOR_PROFILE_ID,
        distribution=DIRECTOR_DISTRIBUTION,
        resources=(
            ProfileResource(
                GLOBAL_MODE_RESOURCE_ID,
                ResourceKind.MODE,
                (GLOBAL_MODE_ADAPTER_ID, GLOBAL_MODE_BROWSER_ADAPTER_ID),
            ),
            ProfileResource(
                TIMELINE_RESOURCE_ID,
                ResourceKind.WORKFLOW,
                (TIMELINE_STATE_ADAPTER_ID, TIMELINE_BROWSER_ADAPTER_ID),
            ),
            ProfileResource(
                TIMELINE_EXECUTION_RESOURCE_ID,
                ResourceKind.EXECUTION,
                (TIMELINE_PROJECTION_ADAPTER_ID, TIMELINE_DISPATCH_ADAPTER_ID),
            ),
        ),
        server_adapters=(
            AdapterSlot(
                GLOBAL_MODE_ADAPTER_ID,
                ResourceKind.MODE,
                GLOBAL_MODE_RESOURCE_ID,
            ),
            AdapterSlot(
                TIMELINE_STATE_ADAPTER_ID,
                ResourceKind.WORKFLOW,
                TIMELINE_RESOURCE_ID,
            ),
            AdapterSlot(
                TIMELINE_PROJECTION_ADAPTER_ID,
                ResourceKind.EXECUTION,
                TIMELINE_EXECUTION_RESOURCE_ID,
            ),
            AdapterSlot(
                TIMELINE_DISPATCH_ADAPTER_ID,
                ResourceKind.EXECUTION,
                TIMELINE_EXECUTION_RESOURCE_ID,
            ),
        ),
        browser_adapters=(
            AdapterSlot(
                GLOBAL_MODE_BROWSER_ADAPTER_ID,
                ResourceKind.MODE,
                GLOBAL_MODE_RESOURCE_ID,
                (DIRECTOR_NODE_TYPE, DIRECTOR_TAKE_CAPTURE_NODE_TYPE),
            ),
            AdapterSlot(
                TIMELINE_BROWSER_ADAPTER_ID,
                ResourceKind.WORKFLOW,
                TIMELINE_RESOURCE_ID,
                (DIRECTOR_NODE_TYPE,),
            ),
        ),
        scopes=(
            PrivacyScope(
                GLOBAL_SCOPE_ID,
                GLOBAL_MODE_RESOURCE_ID,
                GLOBAL_MODE_ADAPTER_ID,
                GLOBAL_MODE_BROWSER_ADAPTER_ID,
            ),
        ),
        protected_fields=(
            ProtectedField(
                TIMELINE_FIELD_ID,
                TIMELINE_RESOURCE_ID,
                GLOBAL_SCOPE_ID,
                TIMELINE_STATE_ADAPTER_ID,
                TIMELINE_BROWSER_ADAPTER_ID,
                (DIRECTOR_NODE_TYPE,),
                FieldLocation(FieldLocationKind.WIDGET, TIMELINE_WIDGET_NAME),
                DIRECTOR_TIMELINE_SCHEMA,
                TIMELINE_FIELD_ID,
                ProtectedStateAuthority.EXTERNAL_BROWSER_WORKFLOW,
                ExternalTransitionPolicy(
                    owner_identity="graph-node-field-v1",
                    max_owners=1024,
                    max_original_bytes_per_owner=2 * 1024 * 1024,
                    max_target_bytes_per_owner=2 * 1024 * 1024,
                    max_total_bytes=32 * 1024 * 1024,
                    lease_seconds=300,
                ),
                execution=True,
            ),
        ),
        subject_mode_bindings=(
            SubjectModeBinding(
                TIMELINE_SUBJECT_MODE_BINDING_ID,
                GLOBAL_SCOPE_ID,
                TIMELINE_SUBJECT_INPUT,
                (DIRECTOR_NODE_TYPE,),
            ),
        ),
        execution_projections=(
            SemanticExecutionProjection(
                TIMELINE_EXECUTION_PROJECTION_ID,
                TIMELINE_EXECUTION_RESOURCE_ID,
                TIMELINE_RESOURCE_ID,
                TIMELINE_PROJECTION_ADAPTER_ID,
                TIMELINE_DISPATCH_ADAPTER_ID,
                TIMELINE_SUBJECT_MODE_BINDING_ID,
                input_name=TIMELINE_EXECUTION_INPUT,
            ),
        ),
        legacy_key_imports=(
            LegacyKeyImportBinding(
                TIMELINE_KEY_IMPORT_BINDING_ID,
                DIRECTOR_V1_JSON_KEY_IMPORT_ID,
                TIMELINE_RESOURCE_ID,
                LegacyLocationKind.WORKFLOW_FIELD,
                TIMELINE_FIELD_ID,
                LegacyKeyFormat.JSON,
            ),
        ),
    )


class DirectorGlobalModeAdapter:
    """Revisioned CAS facade over the existing global privacy setting."""

    def __init__(
        self,
        *,
        base_dir: str | Path | None = None,
        loader: Callable[[], Mapping[str, object]] | None = None,
        saver: Callable[[Mapping[str, object]], object] | None = None,
    ) -> None:
        if (loader is None) != (saver is None):
            raise ValueError("Director global mode source requires both persistence callbacks.")
        if base_dir is not None and loader is not None:
            raise ValueError("Director global mode source persistence is ambiguous.")
        self._base_dir = base_dir
        self._loader = loader
        self._saver = saver
        self._injected_lock = RLock()
        self._injected_snapshot: dict[str, object] | None = None

    def read_declared_mode(self, scope_id: str) -> str:
        return str(self.read_mode_source(scope_id)["declared"])

    def write_declared_mode(self, scope_id: str, mode: object) -> None:
        current = self.read_mode_source(scope_id)
        self.compare_and_set_mode_source(
            scope_id,
            current["revision"],
            current["declared"],
            mode,
        )

    def read_mode_source(self, scope_id: str) -> dict[str, object]:
        _require_scope(scope_id)
        if self._loader is None:
            return read_global_privacy_mode_source(self._base_dir)
        with self._injected_lock:
            return dict(self._read_injected_mode_source())

    def compare_and_set_mode_source(
        self,
        scope_id: str,
        expected_revision: object,
        expected_declared: object,
        target_declared: object,
    ) -> dict[str, object]:
        _require_scope(scope_id)
        expected = _mode_source_snapshot(
            {"revision": expected_revision, "declared": expected_declared}
        )
        target = _declared_mode_value(target_declared)
        if self._loader is None:
            return compare_and_set_global_privacy_mode_source(
                int(expected["revision"]),
                expected["declared"],
                target,
                self._base_dir,
            )
        with self._injected_lock:
            current = self._read_injected_mode_source()
            if current != expected:
                raise RuntimeError("Director global privacy mode changed concurrently.")
            assert self._saver is not None
            settings = normalize_global_settings(self._loader())
            settings["privacy"]["mode"] = target == "private"
            self._saver(settings)
            self._injected_snapshot = {
                "revision": int(expected["revision"]) + 1,
                "declared": target,
            }
            return dict(self._injected_snapshot)

    def classify_mode_source(
        self,
        scope_id: str,
        prior: object,
        target: object,
    ) -> str:
        current = self.read_mode_source(scope_id)
        normalized_prior = _mode_source_snapshot(prior)
        normalized_target = _mode_source_snapshot(target)
        if current == normalized_prior:
            return "prior"
        if current == normalized_target:
            return "target"
        return "diverged"

    def rollback_mode_source(
        self,
        scope_id: str,
        target: object,
        prior: object,
    ) -> dict[str, object]:
        _require_scope(scope_id)
        normalized_target = _mode_source_snapshot(target)
        normalized_prior = _mode_source_snapshot(prior)
        if self._loader is None:
            return rollback_global_privacy_mode_source(
                normalized_target,
                normalized_prior,
                self._base_dir,
            )
        restored = {
            "revision": int(normalized_target["revision"]) + 1,
            "declared": normalized_prior["declared"],
        }
        with self._injected_lock:
            current = self._read_injected_mode_source()
            if current == restored:
                return dict(restored)
            if current != normalized_target:
                raise RuntimeError("Director global privacy mode changed concurrently.")
            assert self._saver is not None
            settings = normalize_global_settings(self._loader())
            settings["privacy"]["mode"] = normalized_prior["declared"] == "private"
            self._saver(settings)
            self._injected_snapshot = dict(restored)
            return dict(restored)

    def _read_injected_mode_source(self) -> dict[str, object]:
        assert self._loader is not None
        settings = normalize_global_settings(self._loader())
        declared = "private" if settings["privacy"]["mode"] else "public"
        if self._injected_snapshot is None:
            self._injected_snapshot = {"revision": 0, "declared": declared}
        elif self._injected_snapshot["declared"] != declared:
            self._injected_snapshot = {
                "revision": int(self._injected_snapshot["revision"]) + 1,
                "declared": declared,
            }
        return self._injected_snapshot

class DirectorTimelineStateAdapter:
    """Normalize timeline plaintext while rejecting protected/default fallback."""

    def capture(self, source: object, declaration: object) -> object:
        _require_field(declaration)
        if isinstance(source, Mapping):
            value = source.get(TIMELINE_WIDGET_NAME, source)
        else:
            value = getattr(source, TIMELINE_WIDGET_NAME)
        return copy.deepcopy(value)

    def normalize(self, value: object, declaration: object) -> dict[str, object]:
        _require_field(declaration)
        return {"timeline": _normalized_plaintext_timeline(value)}

    def apply_revealed(self, target: object, value: object, declaration: object) -> None:
        _require_field(declaration)
        normalized = self.normalize(value, declaration)
        _assign(target, TIMELINE_WIDGET_NAME, _serialized(normalized["timeline"]))

    def clear_plaintext(self, target: object, declaration: object) -> None:
        _require_field(declaration)
        _assign(target, TIMELINE_WIDGET_NAME, "")

    def classify_mode_transition_representation(
        self,
        value: object,
        _context: object,
    ) -> str:
        payload = _decode_exact_widget_json(value)
        if _is_exact_current_timeline_envelope(payload):
            return "private"
        _require_public_timeline(payload)
        return "public"

    def decode_mode_transition_representation(
        self,
        value: object,
        context: object,
    ) -> object:
        payload = _decode_exact_widget_json(value)
        representation = self.classify_mode_transition_representation(value, context)
        if representation == "private":
            return PrivacyEnvelopeCodec(DIRECTOR_TIMELINE_SCHEMA).decrypt_state(payload)
        return payload

    def normalize_mode_transition_value(
        self,
        value: object,
        _context: object,
    ) -> dict[str, object]:
        return {"timeline": _normalize_transition_timeline(value)}

    def encode_public_mode_transition(
        self,
        value: object,
        context: object,
    ) -> bytes:
        normalized = self.normalize_mode_transition_value(value, context)
        try:
            return json.dumps(
                normalized["timeline"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError(
                "Director timeline transition representation is invalid."
            ) from None

class DirectorTimelineExecutionProjectionAdapter:
    def project(self, fields: Mapping[str, object], declaration: object) -> dict[str, object]:
        if getattr(declaration, "id", None) != TIMELINE_EXECUTION_PROJECTION_ID:
            raise ValueError("Unknown Director timeline projection.")
        if set(fields) != {TIMELINE_FIELD_ID}:
            raise ValueError("Director timeline execution snapshot is incomplete.")
        return _normalized_plaintext_timeline(fields[TIMELINE_FIELD_ID])


class DirectorTimelineExecutionDispatchAdapter:
    """Dispatch normalized state through the existing Director build boundary."""

    def dispatch(self, value: object, context: object, cancellation: object) -> object:
        checkpoint = getattr(cancellation, "checkpoint", None)
        if callable(checkpoint):
            checkpoint()
        timeline = _normalized_plaintext_timeline(value)
        callback = context.get("dispatch") if isinstance(context, Mapping) else None
        if callable(callback):
            result = callback(copy.deepcopy(timeline))
        else:
            if not isinstance(context, Mapping):
                raise ValueError("Director timeline execution context is unavailable.")
            required = (
                "duration_seconds", "frame_rate", "aspect_ratio",
                "orientation", "quality_preset",
            )
            if any(name not in context for name in required):
                raise ValueError("Director timeline execution context is incomplete.")
            result = build_timeline_outputs(
                timeline,
                **{name: context[name] for name in required},
            )
        if callable(checkpoint):
            checkpoint()
        return result


def build_director_timeline_server_adapters() -> dict[str, object]:
    return {
        GLOBAL_MODE_ADAPTER_ID: DirectorGlobalModeAdapter(),
        TIMELINE_STATE_ADAPTER_ID: DirectorTimelineStateAdapter(),
        TIMELINE_PROJECTION_ADAPTER_ID: DirectorTimelineExecutionProjectionAdapter(),
        TIMELINE_DISPATCH_ADAPTER_ID: DirectorTimelineExecutionDispatchAdapter(),
    }


def _normalized_plaintext_timeline(value: object) -> dict[str, object]:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("Director timeline plaintext is unavailable.")
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("Director timeline plaintext is invalid.") from None
    if not isinstance(value, Mapping):
        raise ValueError("Director timeline plaintext is unavailable.")
    if value.get("encrypted") is True or "ciphertext" in value:
        raise ValueError("Protected Director timeline cannot execute as plaintext.")
    if set(value) == {"timeline"}:
        value = value["timeline"]
    if not isinstance(value, Mapping):
        raise ValueError("Director timeline plaintext is unavailable.")
    if value.get("encrypted") is True or "ciphertext" in value:
        raise ValueError("Protected Director timeline cannot execute as plaintext.")
    return normalize_video_timeline(copy.deepcopy(dict(value)))


def _decode_exact_widget_json(value: object) -> Mapping[str, object]:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("Director timeline transition representation is invalid.")
    try:
        text = bytes(value).decode("utf-8", errors="strict")
        if not text.strip():
            raise ValueError

        def unique_object(
            pairs: list[tuple[str, object]],
        ) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError
                result[key] = item
            return result

        def reject_constant(_value: str) -> object:
            raise ValueError

        parsed = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError(
            "Director timeline transition representation is invalid."
        ) from None
    if not isinstance(parsed, Mapping):
        raise ValueError("Director timeline transition representation is invalid.")
    return parsed


def _is_exact_current_timeline_envelope(value: Mapping[str, object]) -> bool:
    return (
        set(value)
        == {
            "version",
            "schema",
            "encrypted",
            "algorithm",
            "keyId",
            "nonce",
            "ciphertext",
        }
        and value.get("version") == 1
        and value.get("schema") == DIRECTOR_TIMELINE_SCHEMA
        and value.get("encrypted") is True
        and value.get("algorithm") == "AES-256-GCM"
        and isinstance(value.get("keyId"), str)
        and bool(value.get("keyId"))
        and _valid_base64url(value.get("nonce"), exact_bytes=12)
        and _valid_base64url(value.get("ciphertext"), minimum_bytes=16)
    )


def _valid_base64url(
    value: object,
    *,
    exact_bytes: int | None = None,
    minimum_bytes: int | None = None,
) -> bool:
    if not isinstance(value, str) or not value or "=" in value:
        return False
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeError):
        return False
    return (
        base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") == value
        and (exact_bytes is None or len(decoded) == exact_bytes)
        and (minimum_bytes is None or len(decoded) >= minimum_bytes)
    )


def _require_public_timeline(value: Mapping[str, object]) -> None:
    protected_markers = {
        "algorithm",
        "ciphertext",
        "encrypted",
        "keyId",
        "nonce",
        "private",
        "schema",
    }
    if protected_markers.intersection(value):
        raise ValueError("Director timeline transition representation is invalid.")
    _require_timeline_shape(value)


def _require_timeline_shape(value: object) -> Mapping[str, object]:
    mapping_roots = (
        "project",
        "ui_state",
        "sequence",
        "director_track",
        "model_outputs",
        "validation",
    )
    list_roots = ("assets", "audio_tracks")
    if (
        not isinstance(value, Mapping)
        or value.get("type") != "VIDEO_TIMELINE"
        or not isinstance(value.get("schema_version"), (str, int))
        or isinstance(value.get("schema_version"), bool)
        or any(not isinstance(value.get(name), Mapping) for name in mapping_roots)
        or any(not isinstance(value.get(name), list) for name in list_roots)
    ):
        raise ValueError("Director timeline transition representation is invalid.")
    return value


def _normalize_transition_timeline(value: object) -> dict[str, object]:
    if isinstance(value, Mapping) and set(value) == {"timeline"}:
        value = value["timeline"]
    timeline = _require_timeline_shape(value)
    if isinstance(timeline, Mapping) and (
        timeline.get("encrypted") is True or "ciphertext" in timeline
    ):
        raise ValueError("Director timeline transition representation is invalid.")
    return normalize_video_timeline(copy.deepcopy(dict(timeline)))


def _require_scope(scope_id: object) -> None:
    if scope_id != GLOBAL_SCOPE_ID:
        raise ValueError("Unknown Director privacy scope.")


def _require_field(declaration: object) -> None:
    if getattr(declaration, "id", None) != TIMELINE_FIELD_ID:
        raise ValueError("Unknown Director timeline field.")


def _mode_source_snapshot(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"revision", "declared"}:
        raise ValueError("Invalid Director global mode source snapshot.")
    revision = value["revision"]
    if type(revision) is not int or revision < 0:
        raise ValueError("Invalid Director global mode source snapshot.")
    return {"revision": revision, "declared": _declared_mode_value(value["declared"])}


def _declared_mode_value(value: object) -> str:
    candidate = getattr(value, "value", value)
    if candidate not in {"private", "public"}:
        raise ValueError("Invalid Director privacy declaration.")
    return str(candidate)


def _assign(target: object, name: str, value: object) -> None:
    if isinstance(target, MutableMapping):
        target[name] = value
    else:
        setattr(target, name, value)


def _serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


__all__ = [
    name
    for name in globals()
    if name.startswith("DIRECTOR_") or name.startswith("TIMELINE_")
] + [
    "DirectorGlobalModeAdapter",
    "DirectorTimelineExecutionDispatchAdapter",
    "DirectorTimelineExecutionProjectionAdapter",
    "DirectorTimelineStateAdapter",
    "build_director_timeline_privacy_profile",
    "build_director_timeline_server_adapters",
]
