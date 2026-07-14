"""Director binding over shared subject-mode and protected-execution references."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from helto_privacy.runtime import bound_privacy_pack

from .managed_privacy import (
    DIRECTOR_PROFILE_ID,
    TIMELINE_EXECUTION_RESOURCE_ID,
    TIMELINE_SUBJECT_MODE_BINDING_ID,
)


def _reference(value: object, error_code: str) -> Mapping[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError(error_code) from None
    if not isinstance(value, Mapping):
        raise ValueError(error_code)
    return value


@contextmanager
def consume_director_subject_mode(reference: object, subject_id: object) -> Iterator[object]:
    with consume_director_subject_mode_for_binding(
        reference,
        TIMELINE_SUBJECT_MODE_BINDING_ID,
        subject_id,
    ) as lease:
        yield lease


@contextmanager
def consume_director_subject_mode_for_binding(
    reference: object,
    binding_id: str,
    subject_id: object,
) -> Iterator[object]:
    parsed = _reference(reference, "PRIVACY_SUBJECT_MODE_REFERENCE_INVALID")
    pack = bound_privacy_pack(DIRECTOR_PROFILE_ID)
    with pack.subject_modes(binding_id).consume(
        parsed,
        subject_id,
    ) as lease:
        yield lease


def director_subject_requires_private_execution(lease: object) -> bool:
    pack = bound_privacy_pack(DIRECTOR_PROFILE_ID)
    check = getattr(lease, "requires_private_execution", None)
    if not callable(check):
        raise ValueError("PRIVACY_SUBJECT_MODE_REFERENCE_INVALID")
    return bool(
        check(
            profile=pack.profile,
            binding_id=TIMELINE_SUBJECT_MODE_BINDING_ID,
        )
    )


def dispatch_director_execution(
    reference: object,
    context: Mapping[str, object],
    *,
    subject_id: object,
) -> object:
    parsed = _reference(reference, "PRIVACY_EXECUTION_REFERENCE_INVALID")
    execution = bound_privacy_pack(DIRECTOR_PROFILE_ID).execution(
        TIMELINE_EXECUTION_RESOURCE_ID
    )
    result = execution.dispatch(parsed, dict(context), subject_id=subject_id)
    return result.value


__all__ = [
    "consume_director_subject_mode",
    "consume_director_subject_mode_for_binding",
    "director_subject_requires_private_execution",
    "dispatch_director_execution",
]
