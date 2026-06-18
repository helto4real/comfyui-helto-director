from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any

from ..contracts.validation import SEVERITY_ERROR, create_validation_entry
from ..timeline.references import (
    REFERENCE_KIND_CHARACTER,
    REFERENCE_TAG_RE,
    are_character_references_enabled,
    get_character_references,
    parse_reference_tags,
)


LTX_REFERENCE_MODE_DISABLED = "Disabled"
LTX_HIDDEN_REFERENCE_GUARD_LATENT_FRAMES = 8


def build_ltx_character_reference_plan(
    timeline: dict[str, Any],
    config: dict[str, Any],
    section_entries: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    mode = str(config.get("reference_mode") or "Prompt Relay")
    director_enabled = are_character_references_enabled(timeline)
    active = director_enabled and mode != LTX_REFERENCE_MODE_DISABLED
    references = _enabled_references(timeline) if active else []
    references_by_label = {reference["label"]: reference for reference in references}
    sections_by_id = {
        section.get("item_id"): section
        for section in timeline.get("director_track", {}).get("sections", [])
    }
    usage = []
    specs = []
    unsupported_tokens: set[str] = set()
    unknown_tokens: set[str] = set()
    substitutions = []

    for entry in section_entries:
        if entry.get("type") == "Gap" or entry.get("role") == "No Guidance":
            continue
        section = sections_by_id.get(entry.get("item_id"))
        if not section:
            continue
        prompt = str(section.get("prompt") or "")
        tags = parse_reference_tags(prompt)
        matched = []
        unknown = []
        unsupported = []
        for tag in tags:
            if not tag.get("supported"):
                unsupported.append(_safe_tag(tag))
                unsupported_tokens.add(str(tag.get("token") or ""))
                continue
            reference = references_by_label.get(tag.get("label"))
            if not active:
                continue
            if reference is None:
                unknown.append(_safe_tag(tag))
                unknown_tokens.add(str(tag.get("token") or ""))
                continue
            strength = _tag_strength(tag, reference)
            spec = {
                "id": reference.get("id"),
                "label": reference.get("label"),
                "kind": REFERENCE_KIND_CHARACTER,
                "description": reference.get("description") or "",
                "strength": strength,
                "strength_override": tag.get("strength_override"),
                "image": deepcopy(reference.get("image")),
                "section_id": entry.get("item_id"),
                "insert_frame": int(entry.get("start_frame") or 0),
            }
            matched.append({key: value for key, value in spec.items() if key != "image"})
            specs.append(spec)
        clean_prompt, prompt_substitutions = replace_reference_tags(prompt, references_by_label, active)
        substitutions.extend(
            {
                **item,
                "item_id": entry.get("item_id"),
            }
            for item in prompt_substitutions
        )
        usage.append(
            {
                "item_id": entry.get("item_id"),
                "start_frame": int(entry.get("start_frame") or 0),
                "original_prompt": prompt,
                "runtime_prompt": clean_prompt,
                "references": matched,
                "unknown_tags": unknown,
                "unsupported_tags": unsupported,
            }
        )

    global_prompt = str(timeline.get("project", {}).get("global_prompt", {}).get("prompt") or "")
    for tag in parse_reference_tags(global_prompt):
        if not tag.get("supported"):
            unsupported_tokens.add(str(tag.get("token") or ""))
        elif active and tag.get("label") not in references_by_label:
            unknown_tokens.add(str(tag.get("token") or ""))
    runtime_global_prompt, global_substitutions = replace_reference_tags(global_prompt, references_by_label, active)
    substitutions.extend({**item, "item_id": "project.global_prompt"} for item in global_substitutions)
    deduped_specs = _dedupe_reference_specs(specs)
    diagnostics = _diagnostics(mode, director_enabled, active, deduped_specs, unsupported_tokens, unknown_tokens)

    return (
        {
            "mode": mode,
            "director_enabled": director_enabled,
            "active": active,
            "references": [_safe_reference(reference) for reference in references],
            "section_usage": usage,
            "guide_specs": [_safe_spec(spec) for spec in deduped_specs],
            "runtime_global_prompt": runtime_global_prompt,
            "substitutions": substitutions,
            "unsupported_tags": sorted(token for token in unsupported_tokens if token),
            "unknown_tags": sorted(token for token in unknown_tokens if token),
            "diagnostics": diagnostics,
        },
        _validation_entries(unknown_tokens, references_by_label, active),
    )


def replace_reference_tags(
    prompt: Any,
    references_by_label: dict[str, dict[str, Any]],
    active: bool,
) -> tuple[str, list[dict[str, Any]]]:
    substitutions: list[dict[str, Any]] = []

    def replacement(match: re.Match) -> str:
        label = str(match.group("label") or "").lower()
        kind = str(match.group("kind") or "").lower()
        token = match.group(0)
        reference = references_by_label.get(label)
        replacement_text = ""
        status = "stripped"
        if active and kind == REFERENCE_KIND_CHARACTER and reference is not None:
            replacement_text = str(reference.get("description") or "").strip()
            status = "description" if replacement_text else "empty_description"
        elif active and kind == REFERENCE_KIND_CHARACTER:
            status = "unknown"
        elif kind != REFERENCE_KIND_CHARACTER:
            status = "unsupported"
        substitutions.append(
            {
                "token": token,
                "label": label,
                "kind": kind,
                "replacement": replacement_text,
                "status": status,
            }
        )
        return replacement_text or " "

    return _normalize_prompt_whitespace(REFERENCE_TAG_RE.sub(replacement, str(prompt or ""))), substitutions


def planned_hidden_reference_count(plan: dict[str, Any]) -> int:
    references = plan.get("model_specific", {}).get("ltx", {}).get("character_references", {})
    specs = references.get("guide_specs") if isinstance(references, dict) else []
    return len(specs) if isinstance(specs, list) else 0


def planned_hidden_reference_guard_latent_frames(plan: dict[str, Any]) -> int:
    return LTX_HIDDEN_REFERENCE_GUARD_LATENT_FRAMES if planned_hidden_reference_count(plan) > 0 else 0


def _enabled_references(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    references = []
    for reference in get_character_references(timeline):
        if not isinstance(reference, dict) or reference.get("enabled") is False:
            continue
        if reference.get("kind") != REFERENCE_KIND_CHARACTER:
            continue
        references.append(deepcopy(reference))
    return references


def _tag_strength(tag: dict[str, Any], reference: dict[str, Any]) -> float:
    strength = tag.get("strength_override")
    if strength is None:
        strength = reference.get("strength", 1.0)
    try:
        numeric = float(strength)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(numeric):
        return 1.0
    return max(0.0, min(1.0, numeric))


def _dedupe_reference_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}
    for spec in specs:
        label = str(spec.get("label") or spec.get("id") or "").strip().lower()
        if not label:
            label = str(len(deduped))
        try:
            strength = f"{float(spec.get('strength', 1.0)):.12g}"
        except (TypeError, ValueError):
            strength = "1"
        key = f"{label}:{strength}"
        section_id = spec.get("section_id")
        if key in index_by_key:
            existing = deduped[index_by_key[key]]
            ids = [part for part in str(existing.get("section_id") or "").split(",") if part]
            if section_id is not None and str(section_id) not in ids:
                ids.append(str(section_id))
            if ids:
                existing["section_id"] = ",".join(ids)
            continue
        entry = deepcopy(spec)
        if section_id is not None:
            entry["section_id"] = str(section_id)
        deduped.append(entry)
        index_by_key[key] = len(deduped) - 1
    return deduped


def _safe_reference(reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": reference.get("id"),
        "label": reference.get("label"),
        "kind": reference.get("kind"),
        "enabled": reference.get("enabled") is not False,
        "description": reference.get("description") or "",
        "strength": reference.get("strength"),
        "image": deepcopy(reference.get("image")),
    }


def _safe_spec(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": spec.get("id"),
        "label": spec.get("label"),
        "kind": spec.get("kind", REFERENCE_KIND_CHARACTER),
        "description": spec.get("description") or "",
        "strength": spec.get("strength"),
        "strength_override": spec.get("strength_override"),
        "image": deepcopy(spec.get("image")),
        "section_id": spec.get("section_id"),
        "insert_frame": int(spec.get("insert_frame") or 0),
    }


def _safe_tag(tag: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": tag.get("label"),
        "kind": tag.get("kind"),
        "token": tag.get("token"),
        "strength_override": tag.get("strength_override"),
    }


def _validation_entries(unknown_tokens: set[str], references_by_label: dict[str, dict[str, Any]], active: bool) -> list[dict[str, Any]]:
    entries = []
    if not active:
        return entries
    for token in sorted(token for token in unknown_tokens if token):
        entries.append(
            create_validation_entry(
                "LTX_CHARACTER_REFERENCE_UNKNOWN",
                SEVERITY_ERROR,
                "LTX Planner",
                "Prompt",
                None,
                "Prompt references a character image that LTX cannot resolve.",
                "Add and enable the referenced character image, or remove the prompt tag.",
                {"token": token, "available_labels": sorted(references_by_label.keys())},
            )
        )
    for reference in references_by_label.values():
        image = reference.get("image")
        if not isinstance(image, dict) or not (image.get("path") or image.get("file_path")):
            entries.append(
                create_validation_entry(
                    "LTX_CHARACTER_REFERENCE_MISSING_IMAGE",
                    SEVERITY_ERROR,
                    "LTX Planner",
                    "Reference",
                    reference.get("id") or reference.get("label"),
                    "LTX character reference requires an image path.",
                    "Choose an image for the reference, disable it, or remove prompt tags using it.",
                    {"label": reference.get("label")},
                )
            )
    return entries


def _diagnostics(
    mode: str,
    director_enabled: bool,
    active: bool,
    specs: list[dict[str, Any]],
    unsupported_tokens: set[str],
    unknown_tokens: set[str],
) -> list[str]:
    diagnostics = []
    if mode == LTX_REFERENCE_MODE_DISABLED:
        diagnostics.append("LTX Reference Mode is Disabled; character reference tags were stripped and no hidden reference guides were built.")
    elif not director_enabled:
        diagnostics.append("Director character references are globally disabled; tags were stripped and no hidden reference guides were built.")
    elif active and specs:
        diagnostics.append("Character references are inserted as hidden tail guide frames; crop with LTX Timeline Crop Reference Tail before decode.")
    if unsupported_tokens:
        diagnostics.append("Unsupported reference tags were stripped: " + ", ".join(sorted(unsupported_tokens)))
    if unknown_tokens and active:
        diagnostics.append("Unknown character reference tags were found: " + ", ".join(sorted(unknown_tokens)))
    return diagnostics


def _normalize_prompt_whitespace(value: str) -> str:
    cleaned = re.sub(r"[ \t]{2,}", " ", value)
    cleaned = re.sub(r"[ \t]+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([([{])[ \t]+", r"\1", cleaned)
    return cleaned.strip()
