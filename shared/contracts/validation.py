from __future__ import annotations

from copy import deepcopy
from typing import Any


SEVERITY_ERROR = "Error"
SEVERITY_WARNING = "Warning"
SEVERITY_INFO = "Info"
VALID_SEVERITIES = {SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO}


def create_validation_entry(
    code: str,
    severity: str,
    source: str,
    scope: str,
    item_id: str | None,
    message: str,
    hint: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Unsupported validation severity: {severity}")
    return {
        "code": code,
        "severity": severity,
        "source": source,
        "scope": scope,
        "item_id": item_id,
        "message": message,
        "hint": hint,
        "details": deepcopy(details) if details else {},
    }


def create_validation_result(
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = {
        "is_valid": True,
        "errors": [],
        "warnings": [],
        "info": [],
    }
    for entry in entries or []:
        severity = entry.get("severity")
        if severity == SEVERITY_ERROR:
            result["errors"].append(deepcopy(entry))
        elif severity == SEVERITY_WARNING:
            result["warnings"].append(deepcopy(entry))
        elif severity == SEVERITY_INFO:
            result["info"].append(deepcopy(entry))
        else:
            raise ValueError(f"Unsupported validation severity: {severity}")
    result["is_valid"] = len(result["errors"]) == 0
    return result


def flatten_validation_result(validation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *deepcopy(validation.get("errors", [])),
        *deepcopy(validation.get("warnings", [])),
        *deepcopy(validation.get("info", [])),
    ]


def merge_validation_results(*validations: dict[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for validation in validations:
        entries.extend(flatten_validation_result(validation))
    return create_validation_result(entries)
