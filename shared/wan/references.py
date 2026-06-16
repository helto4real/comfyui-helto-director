from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any

from ..contracts.validation import SEVERITY_ERROR, SEVERITY_WARNING, create_validation_entry
from ..timeline.references import (
    REFERENCE_KIND_CHARACTER,
    REFERENCE_TAG_RE,
    are_character_references_enabled,
    get_character_references,
    parse_reference_tags,
)
from .bernini import BERNINI_MODEL_MODE


BERNINI_MAX_REFERENCE_IMAGES = 8


def build_bernini_character_reference_plan(
    timeline: dict[str, Any],
    config: dict[str, Any],
    section_entries: list[dict[str, Any]],
    prompt_entries: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    enabled = str(config.get("model_mode") or "") == BERNINI_MODEL_MODE
    director_enabled = are_character_references_enabled(timeline)
    active = enabled and director_enabled
    references = _enabled_references(timeline) if active else []
    references_by_label = {reference["label"]: reference for reference in references}
    prompt_usage_by_id: dict[str, dict[str, Any]] = {}
    specs: list[dict[str, Any]] = []
    substitutions: list[dict[str, Any]] = []
    unknown_tokens: set[str] = set()
    disabled_tokens: set[str] = set()
    unsupported_tokens: set[str] = set()

    sections_by_id = {
        section.get("item_id"): section
        for section in timeline.get("director_track", {}).get("sections", [])
    }
    all_references_by_label = {
        str(reference.get("label") or "").lower(): reference
        for reference in get_character_references(timeline)
        if isinstance(reference, dict)
    }

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
        disabled = []
        unsupported = []
        for tag in tags:
            token = str(tag.get("token") or "")
            if not tag.get("supported"):
                unsupported.append(_safe_tag(tag))
                unsupported_tokens.add(token)
                continue
            label = str(tag.get("label") or "").lower()
            reference = references_by_label.get(label)
            if not active:
                disabled.append(_safe_tag(tag))
                disabled_tokens.add(token)
                continue
            if reference is None:
                state = all_references_by_label.get(label)
                if state is not None and state.get("enabled") is False:
                    disabled.append(_safe_tag(tag))
                    disabled_tokens.add(token)
                else:
                    unknown.append(_safe_tag(tag))
                    unknown_tokens.add(token)
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
                "token": token,
            }
            specs.append(spec)
            matched.append({key: value for key, value in spec.items() if key != "image"})
        clean_prompt, prompt_substitutions = replace_reference_tags(prompt, references_by_label, active)
        substitutions.extend({**item, "item_id": entry.get("item_id")} for item in prompt_substitutions)
        if tags:
            prompt_usage_by_id[str(entry.get("item_id"))] = {
                "item_id": entry.get("item_id"),
                "original_prompt": prompt,
                "runtime_prompt": clean_prompt,
                "references": matched,
                "unknown_tags": unknown,
                "disabled_tags": disabled,
                "unsupported_tags": unsupported,
            }

    global_prompt = str(timeline.get("project", {}).get("global_prompt", {}).get("prompt") or "")
    for tag in parse_reference_tags(global_prompt):
        token = str(tag.get("token") or "")
        if not tag.get("supported"):
            unsupported_tokens.add(token)
        elif not active:
            disabled_tokens.add(token)
        elif tag.get("label") not in references_by_label:
            unknown_tokens.add(token)
    runtime_global_prompt, global_substitutions = replace_reference_tags(global_prompt, references_by_label, active)
    substitutions.extend({**item, "item_id": "project.global_prompt"} for item in global_substitutions)

    deduped_specs, overflow_specs = _dedupe_reference_specs(specs)
    planned_specs = deduped_specs[:BERNINI_MAX_REFERENCE_IMAGES]
    empty_description_tokens = {
        str(item.get("token"))
        for item in substitutions
        if item.get("status") == "empty_description" and item.get("token")
    }
    diagnostics = _diagnostics(
        active,
        director_enabled,
        planned_specs,
        overflow_specs,
        unsupported_tokens,
        unknown_tokens,
        disabled_tokens,
        empty_description_tokens,
    )
    reference_plan = {
        "enabled": enabled,
        "director_enabled": director_enabled,
        "active": active,
        "max_reference_images": BERNINI_MAX_REFERENCE_IMAGES,
        "references": [_safe_reference(reference) for reference in references],
        "section_usage": list(prompt_usage_by_id.values()),
        "reference_specs": [_safe_spec(spec) for spec in planned_specs],
        "overflow_reference_specs": [_safe_spec(spec) for spec in overflow_specs],
        "runtime_global_prompt": runtime_global_prompt,
        "substitutions": substitutions,
        "empty_description_tags": sorted(empty_description_tokens),
        "unsupported_tags": sorted(token for token in unsupported_tokens if token),
        "unknown_tags": sorted(token for token in unknown_tokens if token),
        "disabled_tags": sorted(token for token in disabled_tokens if token),
        "diagnostics": diagnostics,
    }
    return (
        reference_plan,
        apply_bernini_reference_prompts(prompt_entries, prompt_usage_by_id, references_by_label, active),
        _validation_entries(reference_plan),
    )


def apply_bernini_reference_prompts(
    prompt_entries: list[dict[str, Any]],
    prompt_usage_by_id: dict[str, dict[str, Any]],
    references_by_label: dict[str, dict[str, Any]],
    active: bool,
) -> list[dict[str, Any]]:
    output = []
    for entry in prompt_entries:
        raw_prompt = str(entry.get("raw_prompt") or "")
        effective_prompt = str(entry.get("effective_prompt") or "")
        runtime_prompt, raw_substitutions = replace_reference_tags(raw_prompt, references_by_label, active)
        runtime_effective_prompt, effective_substitutions = replace_reference_tags(effective_prompt, references_by_label, active)
        usage = prompt_usage_by_id.get(str(entry.get("item_id")))
        if not usage and not raw_substitutions and not effective_substitutions:
            output.append(deepcopy(entry))
            continue
        clean = deepcopy(entry)
        clean["runtime_prompt"] = runtime_prompt
        clean["original_effective_prompt"] = effective_prompt
        clean["runtime_effective_prompt"] = runtime_effective_prompt
        clean["effective_prompt"] = runtime_effective_prompt
        clean["raw_substitutions"] = raw_substitutions
        clean["effective_substitutions"] = effective_substitutions
        output.append(clean)
    return output


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
        substitutions.append({
            "token": token,
            "label": label,
            "kind": kind,
            "replacement": replacement_text,
            "status": status,
        })
        return replacement_text or " "

    return _normalize_prompt_whitespace(REFERENCE_TAG_RE.sub(replacement, str(prompt or ""))), substitutions


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


def _dedupe_reference_specs(specs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deduped: list[dict[str, Any]] = []
    index_by_label: dict[str, int] = {}
    overflow: list[dict[str, Any]] = []
    for spec in specs:
        label = str(spec.get("label") or spec.get("id") or "").strip().lower()
        if not label:
            label = str(len(deduped))
        section_id = spec.get("section_id")
        token = spec.get("token")
        if label in index_by_label:
            existing = deduped[index_by_label[label]]
            ids = [part for part in str(existing.get("section_id") or "").split(",") if part]
            if section_id is not None and str(section_id) not in ids:
                ids.append(str(section_id))
            if ids:
                existing["section_id"] = ",".join(ids)
            tokens = existing.setdefault("tokens", [])
            if token and token not in tokens:
                tokens.append(token)
            continue
        entry = deepcopy(spec)
        if section_id is not None:
            entry["section_id"] = str(section_id)
        entry["tokens"] = [token] if token else []
        deduped.append(entry)
        index_by_label[label] = len(deduped) - 1
    if len(deduped) > BERNINI_MAX_REFERENCE_IMAGES:
        overflow = deduped[BERNINI_MAX_REFERENCE_IMAGES:]
    return deduped, overflow


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
        "kind": spec.get("kind"),
        "description": spec.get("description") or "",
        "strength": spec.get("strength"),
        "strength_override": spec.get("strength_override"),
        "image": deepcopy(spec.get("image")),
        "section_id": spec.get("section_id"),
        "tokens": list(spec.get("tokens") or ([spec["token"]] if spec.get("token") else [])),
    }


def _safe_tag(tag: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": tag.get("label"),
        "kind": tag.get("kind"),
        "token": tag.get("token"),
        "strength_override": tag.get("strength_override"),
    }


def _validation_entries(reference_plan: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    available = sorted(str(reference.get("label")) for reference in reference_plan.get("references", []) if reference.get("label"))
    for token in reference_plan.get("unknown_tags", []):
        entries.append(_entry(
            "BERNINI_CHARACTER_REFERENCE_UNKNOWN",
            SEVERITY_ERROR,
            "Prompt",
            None,
            f"Prompt references a Bernini character image that cannot be resolved: {token}.",
            "Add and enable the referenced character image, or remove the prompt tag.",
            {"token": token, "available_labels": available},
        ))
    for token in reference_plan.get("disabled_tags", []):
        entries.append(_entry(
            "BERNINI_CHARACTER_REFERENCE_DISABLED",
            SEVERITY_WARNING,
            "Prompt",
            None,
            f"Prompt references a disabled Bernini character reference: {token}.",
            "Turn on character references, enable the reference, or remove the prompt tag.",
            {"token": token},
        ))
    for token in reference_plan.get("unsupported_tags", []):
        entries.append(_entry(
            "BERNINI_CHARACTER_REFERENCE_UNSUPPORTED_TAG",
            SEVERITY_WARNING,
            "Prompt",
            None,
            f"Unsupported Bernini reference tag was stripped: {token}.",
            "Use @imageN:character tags for Bernini subject reference images.",
            {"token": token},
        ))
    for token in reference_plan.get("empty_description_tags", []):
        entries.append(_entry(
            "BERNINI_CHARACTER_REFERENCE_EMPTY_DESCRIPTION",
            SEVERITY_WARNING,
            "CharacterReference",
            None,
            f"Bernini character reference tag has no text description to insert: {token}.",
            "Add a short character description so the prompt text names the referenced subject.",
            {"token": token},
        ))
    for spec in reference_plan.get("reference_specs", []):
        image = spec.get("image")
        if not isinstance(image, dict) or not image.get("path"):
            entries.append(_entry(
                "BERNINI_CHARACTER_REFERENCE_MISSING_IMAGE",
                SEVERITY_ERROR,
                "CharacterReference",
                spec.get("id") or spec.get("label"),
                "Bernini character reference requires an image path.",
                "Choose an image for the reference, disable it, or remove prompt tags using it.",
                {"label": spec.get("label")},
            ))
    if reference_plan.get("overflow_reference_specs"):
        entries.append(_entry(
            "BERNINI_CHARACTER_REFERENCE_LIMIT_EXCEEDED",
            SEVERITY_WARNING,
            "CharacterReference",
            None,
            f"Bernini supports at most {BERNINI_MAX_REFERENCE_IMAGES} subject reference images.",
            "Only the first eight tagged references are passed to Bernini; remove extra tags for deterministic subject selection.",
            {
                "max_reference_images": BERNINI_MAX_REFERENCE_IMAGES,
                "ignored_labels": [spec.get("label") for spec in reference_plan.get("overflow_reference_specs", [])],
            },
        ))
    return entries


def _diagnostics(
    active: bool,
    director_enabled: bool,
    specs: list[dict[str, Any]],
    overflow_specs: list[dict[str, Any]],
    unsupported_tokens: set[str],
    unknown_tokens: set[str],
    disabled_tokens: set[str],
    empty_description_tokens: set[str],
) -> list[str]:
    diagnostics = []
    if specs:
        diagnostics.append(f"Bernini will pass {len(specs)} Director character reference image(s) as subject reference_images.")
    if not director_enabled:
        diagnostics.append("Director character references are globally disabled; Bernini reference tags were stripped.")
    elif not active:
        diagnostics.append("Bernini character reference support is inactive for the selected WAN model mode.")
    if overflow_specs:
        diagnostics.append(f"Bernini reference image limit exceeded; {len(overflow_specs)} tagged reference(s) were ignored.")
    if unsupported_tokens:
        diagnostics.append("Unsupported Bernini reference tags were stripped: " + ", ".join(sorted(unsupported_tokens)))
    if unknown_tokens:
        diagnostics.append("Unknown Bernini character reference tags were found: " + ", ".join(sorted(unknown_tokens)))
    if disabled_tokens:
        diagnostics.append("Disabled Bernini character reference tags were stripped: " + ", ".join(sorted(disabled_tokens)))
    if empty_description_tokens:
        diagnostics.append("Bernini character reference tags with empty descriptions were stripped from prompt text: " + ", ".join(sorted(empty_description_tokens)))
    return diagnostics


def _normalize_prompt_whitespace(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt).strip()


def _entry(
    code: str,
    severity: str,
    scope: str,
    item_id: str | None,
    message: str,
    hint: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return create_validation_entry(code, severity, scope, scope, item_id, message, hint, details)
