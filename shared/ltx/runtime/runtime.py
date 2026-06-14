from __future__ import annotations

from copy import deepcopy
from typing import Any

import torch

from ..config import LTX_MODEL_FAMILY, LTX_MODEL_VERSION
from ..planner import LTX_PLAN_TYPE
from .audio import build_audio_latent, build_native_audio_latent, mix_timeline_audio
from .guides import apply_guide_data
from .media import build_guide_data, source_video_outputs
from .prompt_relay import encode_prompt_relay
from .patches import supports_ltx_native_audio


def build_ltx_runtime_outputs(
    *,
    model,
    clip,
    vae,
    ltx_timeline_plan: dict[str, Any],
    negative=None,
    optional_latent=None,
    audio_vae=None,
    identity_anchor=None,
    sigmas=None,
    iclora_parameters=None,
) -> tuple[Any, Any, Any, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], Any, dict[str, Any], float, int, dict[str, Any]]:
    plan = deepcopy(ltx_timeline_plan)
    _validate_plan(plan)
    width = int(plan["resolved_output"].get("width") or 768)
    height = int(plan["resolved_output"].get("height") or 512)
    frame_count = int(plan["resolved_output"].get("frame_count") or 1)
    frame_rate = float(plan["resolved_output"].get("frame_rate") or 24.0)
    clean_latent_frames = ((frame_count - 1) // 8) + 1

    latent = clone_latent(optional_latent) if optional_latent is not None else empty_ltx_video_latent(width, height, clean_latent_frames)
    prompt_inputs = _prompt_relay_inputs(plan)
    prompt_relay = plan.get("model_specific", {}).get("ltx", {}).get("prompt_relay", {})
    if prompt_relay.get("enabled", True):
        runtime_model, positive, prompt_debug = encode_prompt_relay(
            model,
            clip,
            latent,
            prompt_inputs["global_prompt"],
            prompt_inputs["local_prompts"],
            prompt_inputs["pixel_lengths"],
            float(prompt_relay.get("epsilon", 0.15)),
        )
    else:
        prompt = ", ".join(prompt_inputs["local_prompts"])
        positive = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
        runtime_model = model
        prompt_debug = {"full_prompt": prompt, "local_prompts": prompt_inputs["local_prompts"], "latent_lengths": []}

    negative = _resolve_negative_conditioning(negative, positive)
    guide_data, guide_diagnostics = build_guide_data(plan, width, height)
    positive, negative, video_latent, guide_apply_debug = apply_guide_data(
        positive,
        negative,
        vae,
        latent,
        guide_data,
        iclora_parameters=iclora_parameters,
    )
    combined_audio, audio_diagnostics = mix_timeline_audio(plan)
    use_native_audio = bool(plan.get("project", {}).get("audio", {}).get("use_native_audio"))
    if use_native_audio:
        if not supports_ltx_native_audio(model):
            raise ValueError("LTX native audio is enabled, but the connected model does not support native audio. Use an LTX audio-video model or turn off Use Native Audio.")
        audio_latent, audio_latent_diagnostics = build_native_audio_latent(audio_vae, frame_count, frame_rate)
    else:
        audio_latent, audio_latent_diagnostics = build_audio_latent(combined_audio, audio_vae, frame_count, frame_rate)
    source_images, source_audio, source_fps, source_frame_count = source_video_outputs(plan, width, height)
    runtime_debug = _runtime_debug(
        plan,
        prompt_debug,
        guide_data,
        guide_apply_debug,
        [
            *guide_diagnostics,
            *audio_diagnostics,
            *audio_latent_diagnostics,
            *_advanced_input_diagnostics(identity_anchor, sigmas),
        ],
        video_latent,
        combined_audio,
    )
    return (
        runtime_model,
        positive,
        negative,
        video_latent,
        audio_latent,
        combined_audio,
        guide_data,
        source_images,
        source_audio,
        float(source_fps),
        int(source_frame_count),
        runtime_debug,
    )


def empty_ltx_video_latent(width: int, height: int, latent_frames: int) -> dict[str, Any]:
    try:
        import comfy.model_management

        device = comfy.model_management.intermediate_device()
    except Exception:
        device = "cpu"
    latent_width = max(1, int(width) // 32)
    latent_height = max(1, int(height) // 32)
    samples = torch.zeros((1, 128, max(1, int(latent_frames)), latent_height, latent_width), device=device)
    return {"samples": samples, "downscale_ratio_spacial": 32}


def clone_latent(latent: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(latent)
    for key, value in latent.items():
        if torch.is_tensor(value):
            cloned[key] = value.clone()
    return cloned


def zero_out_conditioning(conditioning):
    zeroed = []
    for tensor, metadata in conditioning:
        next_metadata = metadata.copy()
        pooled_output = next_metadata.get("pooled_output")
        if pooled_output is not None:
            next_metadata["pooled_output"] = torch.zeros_like(pooled_output)
        conditioning_lyrics = next_metadata.get("conditioning_lyrics")
        if conditioning_lyrics is not None:
            next_metadata["conditioning_lyrics"] = torch.zeros_like(conditioning_lyrics)
        zeroed.append([torch.zeros_like(tensor), next_metadata])
    return zeroed


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("LTX runtime requires an LTX_TIMELINE_PLAN dictionary.")
    if plan.get("type") != LTX_PLAN_TYPE:
        raise ValueError(f"LTX runtime expected plan type {LTX_PLAN_TYPE}, got {plan.get('type')!r}.")
    if plan.get("model_family") != LTX_MODEL_FAMILY or plan.get("model_version") != LTX_MODEL_VERSION:
        raise ValueError(
            f"LTX runtime expected model {LTX_MODEL_FAMILY} {LTX_MODEL_VERSION}, got {plan.get('model_family')} {plan.get('model_version')}."
        )
    validation = plan.get("validation") or {}
    if validation.get("is_valid") is False:
        codes = ", ".join(str(entry.get("code")) for entry in validation.get("errors", []))
        raise ValueError(f"LTX runtime cannot run an invalid timeline plan: {codes or 'unknown validation error'}.")


def _prompt_relay_inputs(plan: dict[str, Any]) -> dict[str, Any]:
    prompts_by_id = {entry.get("item_id"): entry for entry in plan.get("prompt_plan", [])}
    project_global = plan.get("project", {}).get("global_prompt", {})
    global_prompt = str(project_global.get("prompt") or "") if project_global.get("enabled") else ""
    local_prompts: list[str] = []
    pixel_lengths: list[int] = []
    for section in plan.get("section_plan", []):
        if section.get("type") == "Gap" or section.get("role") == "No Guidance":
            continue
        prompt = prompts_by_id.get(section.get("item_id"), {})
        raw_prompt = str(prompt.get("raw_prompt") or "").strip()
        effective_prompt = str(prompt.get("effective_prompt") or "").strip()
        local_prompt = raw_prompt or effective_prompt
        if not local_prompt:
            raise ValueError(f"LTX runtime section {section.get('item_id')} is missing an effective prompt.")
        local_prompts.append(local_prompt)
        pixel_lengths.append(int(section.get("frame_count") or 1))
    return {
        "global_prompt": global_prompt,
        "local_prompts": local_prompts,
        "pixel_lengths": pixel_lengths,
    }


def _resolve_negative_conditioning(negative, positive):
    return negative if negative is not None else zero_out_conditioning(positive)


def _runtime_debug(plan, prompt_debug, guide_data, guide_apply_debug, diagnostics, video_latent, combined_audio):
    return {
        "type": "DEBUG_INFO",
        "source": "LTX Runtime",
        "enabled": bool(plan.get("model_specific", {}).get("ltx", {}).get("config", {}).get("debug_mode")),
        "summary": {
            "section_count": len([entry for entry in plan.get("section_plan", []) if entry.get("type") != "Gap"]),
            "guide_count": len(guide_data.get("images", [])),
            "applied_guides": int(guide_apply_debug.get("applied_guides", 0)),
            "audio_clip_count": len(plan.get("audio_plan", [])),
            "video_latent_shape": tuple(video_latent["samples"].shape),
            "combined_audio_shape": tuple(combined_audio["waveform"].shape),
        },
        "prompt_relay": prompt_debug,
        "guide_data": {
            "insert_frames": list(guide_data.get("insert_frames", [])),
            "strengths": list(guide_data.get("strengths", [])),
            "clean_pixel_frames": guide_data.get("clean_pixel_frames"),
            "clean_latent_frames": guide_data.get("clean_latent_frames"),
            "hidden_reference_count": guide_data.get("hidden_reference_count"),
        },
        "diagnostics": diagnostics,
    }


def _advanced_input_diagnostics(identity_anchor, sigmas) -> list[str]:
    diagnostics = []
    if identity_anchor is not None:
        diagnostics.append("identity_anchor input is accepted but Phase 9 runtime does not apply identity helpers yet.")
    if sigmas is not None:
        diagnostics.append("sigmas input is accepted for graph compatibility but Phase 9 runtime does not consume it yet.")
    return diagnostics
