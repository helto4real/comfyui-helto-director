from __future__ import annotations

from copy import deepcopy
from typing import Any


BERNINI_MODEL_MODE = "Bernini-A14B"

BERNINI_TASK_PROMPT_AUTO = "Auto"
BERNINI_TASK_PROMPT_OFF = "Off"
BERNINI_SUPPORTED_TASK_TYPES = ("t2v", "i2v", "v2v", "r2v", "rv2v")
BERNINI_TASK_PROMPT_MODES = (
    BERNINI_TASK_PROMPT_AUTO,
    BERNINI_TASK_PROMPT_OFF,
    *BERNINI_SUPPORTED_TASK_TYPES,
)

BERNINI_DEFERRED_TASK_TYPES = (
    "t2i",
    "i2i",
    "r2i",
    "vi2v",
    "ads2v",
    "vrc2v",
    "mv2v",
)

BERNINI_SYSTEM_PROMPTS = {
    "default": "You are a helpful assistant.",
    "t2i": "You are a helpful assistant specialized in text-to-image generation.",
    "t2v": "You are a helpful assistant specialized in text-to-video generation.",
    "i2i": "You are a helpful assistant specialized in image editing.",
    "r2i": "You are a helpful assistant specialized in subject-to-image generation.",
    "i2v": "You are a helpful assistant specialized in image-to-video generation.",
    "v2v": "You are a helpful assistant specialized in video editing.",
    "r2v": "You are a helpful assistant specialized in subject-to-video generation.",
    "vi2v": "You are a helpful assistant specialized in video editing on content propagation.",
    "rv2v": "You are a helpful assistant specialized in video editing with reference.",
    "ads2v": "You are a helpful assistant specialized in ads insertion.",
    "vrc2v": "You are a helpful assistant for editing. You may need to adjust the subject's action or position.",
    "mv2v": "You are a helpful assistant for editing. You might need to adjust the video's style, lighting, colors, textures, and the subject's pose or action.",
}


def is_bernini_config(config: dict[str, Any]) -> bool:
    return str(config.get("model_mode") or "") == BERNINI_MODEL_MODE


def build_bernini_plan(
    config: dict[str, Any],
    section_entries: list[dict[str, Any]],
    media_entries: list[dict[str, Any]],
    prompt_entries: list[dict[str, Any]] | None = None,
    global_prompt: str | None = None,
    character_references: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enabled = is_bernini_config(config)
    image_media = _sorted_media(media_entries, section_entries, "Image")
    video_media = _sorted_media(media_entries, section_entries, "Video")
    prompt_stats = _prompt_stats(prompt_entries or [], global_prompt)
    references = character_references or {}
    reference_specs = references.get("reference_specs") if isinstance(references, dict) else []
    reference_count = len(reference_specs) if isinstance(reference_specs, list) else 0
    auto_task, reason = _auto_task_type(image_media, video_media, reference_count, prompt_stats["timeline_prompt_count"])
    policy = str(config.get("bernini_task_prompt") or BERNINI_TASK_PROMPT_AUTO)
    if policy not in BERNINI_TASK_PROMPT_MODES:
        policy = BERNINI_TASK_PROMPT_AUTO

    if policy in BERNINI_SUPPORTED_TASK_TYPES:
        selected_task = policy
        selection_source = "Manual"
        selection_reason = f"Bernini task prompt was manually set to {policy}."
    else:
        selected_task = auto_task
        selection_source = "Auto"
        selection_reason = reason

    media_used, ignored_media = _select_bernini_media(selected_task, image_media, video_media)
    prompt_prefix_enabled = enabled and policy != BERNINI_TASK_PROMPT_OFF
    system_prompt = BERNINI_SYSTEM_PROMPTS.get(selected_task, BERNINI_SYSTEM_PROMPTS["default"]) if prompt_prefix_enabled else ""
    return {
        "enabled": enabled,
        "task_prompt_policy": policy,
        "task_type": selected_task,
        "auto_task_type": auto_task,
        "selection_source": selection_source,
        "selection_reason": selection_reason,
        "system_prompt": system_prompt,
        "prompt_prefix_enabled": prompt_prefix_enabled,
        "media_used": media_used,
        "ignored_timeline_media": ignored_media,
        "timeline_image_count": len(image_media),
        "timeline_video_count": len(video_media),
        "timeline_prompt_count": prompt_stats["timeline_prompt_count"],
        "reference_image_count": reference_count,
        "character_references": deepcopy(references),
        "has_user_prompt_text": prompt_stats["has_user_prompt_text"],
        "has_media_conditioning": bool(image_media or video_media),
        "has_reference_conditioning": reference_count > 0,
        "has_user_conditioning": bool(prompt_stats["has_user_prompt_text"] or image_media or video_media or reference_count > 0),
        "deferred_task_types": list(BERNINI_DEFERRED_TASK_TYPES),
        "reference_image_support": "Director character references tagged in prompts are passed as Bernini subject reference_images; timeline images remain source/background context.",
    }


def apply_bernini_prompt_prefix(prompt_relay: dict[str, Any], bernini: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(prompt_relay)
    system_prompt = str(bernini.get("system_prompt") or "").strip()
    if not bernini.get("enabled") or not bernini.get("prompt_prefix_enabled") or not system_prompt:
        output["bernini_prompt_prefix_applied"] = False
        return output
    existing_global = str(output.get("global_prompt") or "").strip()
    output["global_prompt"] = _join_prompt_prefix(system_prompt, existing_global)
    output["bernini_prompt_prefix_applied"] = True
    output["bernini_task_type"] = bernini.get("task_type")
    return output


def _auto_task_type(
    image_media: list[dict[str, Any]],
    video_media: list[dict[str, Any]],
    reference_count: int = 0,
    prompt_count: int = 0,
) -> tuple[str, str]:
    if reference_count > 0:
        if video_media:
            return "rv2v", "Prompt-tagged Director character references and Video Section media are present, so Bernini Auto selected rv2v with the first video as source_video."
        if image_media:
            return "rv2v", "Prompt-tagged Director character references and Image Section media are present, so Bernini Auto selected rv2v with the first image as single-frame source_video."
        return "r2v", "Prompt-tagged Director character references are present without timeline media, so Bernini Auto selected r2v."
    if video_media:
        if image_media:
            return "v2v", "Video Section media is present, so Bernini Auto selected v2v; timeline images remain normal keyframes and are not reference images."
        return "v2v", "Video Section media is present, so Bernini Auto selected v2v."
    if image_media:
        return "i2v", "Image Section media is present without source video, so Bernini Auto selected i2v. Multiple images stay keyframes, not r2v references."
    if prompt_count <= 0:
        return "t2v", "No image, video, or prompted timeline sections are present, so Bernini Auto selected t2v with no user conditioning."
    return "t2v", "No image or video timeline media is present, so Bernini Auto selected t2v."


def _select_bernini_media(
    task_type: str,
    image_media: list[dict[str, Any]],
    video_media: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    ignored: list[dict[str, Any]] = []
    if task_type in {"v2v", "rv2v"} and video_media:
        media_used = _media_selection(video_media[0], "source_video")
        ignored.extend(_ignored_media(video_media[1:], "Only the first video section is used as Bernini source_video in this version."))
        ignored.extend(_ignored_media(image_media, "Timeline images are source/background candidates, not Bernini subject reference images."))
        return media_used, ignored
    if task_type in {"i2v", "rv2v"} and image_media:
        media_used = _media_selection(image_media[0], "source_video_single_frame")
        ignored.extend(_ignored_media(image_media[1:], "Only the first image section is used as Bernini single-frame source_video in this version."))
        ignored.extend(_ignored_media(video_media, f"Video sections are not used by the selected Bernini {task_type} task."))
        return media_used, ignored
    ignored.extend(_ignored_media(image_media, f"Timeline image media is not used by Bernini {task_type}."))
    ignored.extend(_ignored_media(video_media, f"Timeline video media is not used by Bernini {task_type}."))
    return None, ignored


def _sorted_media(media_entries: list[dict[str, Any]], section_entries: list[dict[str, Any]], section_type: str) -> list[dict[str, Any]]:
    sections_by_id = {entry.get("item_id"): entry for entry in section_entries}
    entries = [
        media
        for media in media_entries
        if media.get("section_type") == section_type and media.get("path")
    ]
    return sorted(entries, key=lambda media: sections_by_id.get(media.get("item_id"), {}).get("start_frame", 0))


def _media_selection(media: dict[str, Any], bernini_role: str) -> dict[str, Any]:
    return {
        "item_id": media.get("item_id"),
        "section_type": media.get("section_type"),
        "asset_id": media.get("asset_id"),
        "asset_type": media.get("asset_type"),
        "path": media.get("path"),
        "bernini_role": bernini_role,
        "source_in": media.get("source_in"),
        "source_out": media.get("source_out"),
        "timing_mode": media.get("timing_mode"),
        "video_guidance_range": media.get("video_guidance_range"),
        "video_guidance_frame_count": media.get("video_guidance_frame_count"),
        "crop_mode": media.get("crop_mode"),
    }


def _ignored_media(media_entries: list[dict[str, Any]], reason: str) -> list[dict[str, Any]]:
    return [
        {
            "item_id": media.get("item_id"),
            "section_type": media.get("section_type"),
            "asset_id": media.get("asset_id"),
            "reason": reason,
        }
        for media in media_entries
    ]


def _prompt_stats(prompt_entries: list[dict[str, Any]], global_prompt: str | None = None) -> dict[str, Any]:
    prompted = [
        entry for entry in prompt_entries
        if str(entry.get("raw_prompt") or entry.get("effective_prompt") or "").strip()
    ]
    return {
        "timeline_prompt_count": len(prompted),
        "has_user_prompt_text": bool(prompted or str(global_prompt or "").strip()),
    }


def _join_prompt_prefix(system_prompt: str, prompt: str) -> str:
    if not prompt:
        return system_prompt
    if prompt.startswith(system_prompt):
        return prompt
    return f"{system_prompt}\n\n{prompt}"
