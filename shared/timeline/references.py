from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any

from ..contracts.video_timeline import ASSET_SOURCE_FILE_PATH, ASSET_TYPE_IMAGE

REFERENCE_KIND_CHARACTER = "character"
REFERENCE_TAG_RE = re.compile(
    r"@(?P<label>image[1-9]\d*):(?P<kind>[A-Za-z][A-Za-z0-9_-]*)"
    r"(?:\[(?P<strength>[^\]]*)\])?"
)


def get_character_references(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    references = (
        timeline.get("project", {})
        .get("metadata", {})
        .get("character_references", [])
    )
    return references if isinstance(references, list) else []


def are_character_references_enabled(timeline: dict[str, Any]) -> bool:
    return (
        timeline.get("project", {})
        .get("metadata", {})
        .get("character_references_enabled")
        is not False
    )


def normalize_character_references(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, reference in enumerate(value):
        if not isinstance(reference, dict):
            continue
        item = deepcopy(reference)
        item["label"] = normalize_reference_label(item.get("label"), len(normalized))
        item["id"] = str(item.get("id") or item["label"])
        item["kind"] = REFERENCE_KIND_CHARACTER
        item["enabled"] = item.get("enabled") is not False
        item["description"] = str(item.get("description") or "")
        item["strength"] = _clamp_strength(item.get("strength"))
        item["image"] = normalize_reference_image(item.get("image"))
        normalized.append(item)
    return normalized


def normalize_reference_label(value: Any, fallback_index: int = 0) -> str:
    label = str(value or "").strip().lower()
    if re.fullmatch(r"image[1-9]\d*", label):
        return label
    return f"image{fallback_index + 1}"


def normalize_reference_image(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    image = deepcopy(value)
    path = str(image.get("path") or image.get("file_path") or "").strip()
    image["type"] = ASSET_TYPE_IMAGE
    image["source_kind"] = image.get("source_kind") or ASSET_SOURCE_FILE_PATH
    image["path"] = path or None
    image["name"] = str(image.get("name") or _basename(path) or "")
    image["mime_type"] = image.get("mime_type") or ""
    image["size_bytes"] = image.get("size_bytes") if isinstance(image.get("size_bytes"), int) else None
    if not isinstance(image.get("metadata"), dict):
        image["metadata"] = {}
    image.pop("asset_id", None)
    return image


def parse_reference_tags(prompt: Any) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    for match in REFERENCE_TAG_RE.finditer(str(prompt or "")):
        kind = str(match.group("kind") or "").lower()
        tags.append(
            {
                "label": normalize_reference_label(match.group("label")),
                "kind": kind,
                "token": match.group(0),
                "supported": kind == REFERENCE_KIND_CHARACTER,
                "strength_override": _finite_float_or_none(match.group("strength")),
            }
        )
    return tags


def _clamp_strength(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(numeric):
        return 1.0
    return max(0.0, min(1.0, numeric))


def _finite_float_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _basename(path: Any) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").split("/")[-1]
