from __future__ import annotations

from copy import deepcopy
from typing import Any

import torch

from ..config import LTX_MODEL_FAMILY, LTX_MODEL_VERSION
from ..identity import apply_identity_anchor
from ..planner import LTX_PLAN_TYPE
from ..references import planned_hidden_reference_count, planned_hidden_reference_guard_latent_frames
from .audio import build_audio_latent, build_native_audio_latent, mix_timeline_audio
from .guides import apply_guide_data
from .media import build_guide_data, source_video_outputs
from .prompt_relay import encode_prompt_relay
from .patches import supports_ltx_native_audio
from ...timeline_status import TimelineStatusReporter, ensure_timeline_status_reporter
from ...lora import apply_lora_config, normalize_lora_config


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
    status_reporter: TimelineStatusReporter | None = None,
    complete_status: bool = True,
) -> tuple[Any, Any, Any, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], Any, dict[str, Any], float, int, dict[str, Any]]:
    status_reporter = ensure_timeline_status_reporter(status_reporter, model="ltx", total=6)
    status_reporter.report("timeline.prepare", "LTX Runtime: preparing latent")
    plan = deepcopy(ltx_timeline_plan)
    _validate_plan(plan)
    lora_config, lora_diagnostics = _resolve_ltx_lora_config(plan)
    if lora_config["loras"]:
        status_reporter.report("timeline.loras", "LTX Runtime: applying timeline LoRAs")
        model, clip, applied_loras = apply_lora_config(model=model, clip=clip, lora_config=lora_config)
    else:
        applied_loras = []
    width = int(plan["resolved_output"].get("width") or 768)
    height = int(plan["resolved_output"].get("height") or 512)
    frame_count = int(plan["resolved_output"].get("frame_count") or 1)
    frame_rate = float(plan["resolved_output"].get("frame_rate") or 24.0)
    clean_latent_frames = ((frame_count - 1) // 8) + 1
    hidden_reference_count = planned_hidden_reference_count(plan)
    hidden_reference_guard_latent_frames = planned_hidden_reference_guard_latent_frames(plan)
    total_latent_frames = clean_latent_frames + hidden_reference_guard_latent_frames + hidden_reference_count

    latent = (
        pad_latent_tail(clone_latent(optional_latent), hidden_reference_guard_latent_frames + hidden_reference_count)
        if optional_latent is not None
        else empty_ltx_video_latent(width, height, total_latent_frames)
    )
    prompt_inputs = _prompt_relay_inputs(plan)
    prompt_relay = plan.get("model_specific", {}).get("ltx", {}).get("prompt_relay", {})
    status_reporter.report("timeline.prompt", "LTX Runtime: building prompts")
    if prompt_relay.get("enabled", True) and prompt_inputs["local_prompts"]:
        runtime_model, positive, prompt_debug = encode_prompt_relay(
            model,
            clip,
            latent,
            prompt_inputs["global_prompt"],
            prompt_inputs["local_prompts"],
            prompt_inputs["pixel_lengths"],
            float(prompt_relay.get("epsilon", 0.15)),
            frame_ranges=prompt_inputs["frame_ranges"],
            prompt_sections=prompt_inputs["prompt_sections"],
        )
    else:
        prompt = _plain_prompt(prompt_inputs)
        positive = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
        runtime_model = model
        prompt_debug = {"full_prompt": prompt, "local_prompts": prompt_inputs["local_prompts"], "latent_lengths": []}

    negative = _resolve_negative_conditioning(negative, positive)
    status_reporter.report("ltx.guide_data", "LTX Runtime: preparing guide data")
    guide_data, guide_diagnostics = build_guide_data(plan, width, height)
    status_reporter.report("timeline.conditioning", "LTX Runtime: applying guide conditioning")
    runtime_model = apply_identity_anchor(
        runtime_model,
        identity_anchor=identity_anchor,
        sigmas=sigmas,
        vae=vae,
        guide_data=guide_data,
    )
    positive, negative, video_latent, guide_apply_debug = apply_guide_data(
        positive,
        negative,
        vae,
        latent,
        guide_data,
        iclora_parameters=iclora_parameters,
    )
    status_reporter.report("timeline.audio", "LTX Runtime: mixing audio")
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
            *lora_diagnostics,
        ],
        video_latent,
        combined_audio,
        applied_loras,
        status_reporter.snapshot(),
    )
    if complete_status:
        status_reporter.done("LTX Runtime: ready")
        runtime_debug["status_events"] = status_reporter.snapshot()
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


def pad_latent_tail(latent: dict[str, Any], extra_latent_frames: int) -> dict[str, Any]:
    if extra_latent_frames <= 0:
        return latent
    if not isinstance(latent, dict) or not torch.is_tensor(latent.get("samples")):
        raise ValueError("LTX character references need optional_latent to be a LATENT dict with tensor samples.")
    samples = latent["samples"]
    if samples.ndim != 5:
        raise ValueError(
            "LTX character references can only auto-pad 5D video latent samples "
            f"(got shape {tuple(samples.shape)})."
        )
    padded = dict(latent)
    pad_shape = list(samples.shape)
    pad_shape[2] = int(extra_latent_frames)
    padded["samples"] = torch.cat(
        [samples, torch.zeros(pad_shape, dtype=samples.dtype, device=samples.device)],
        dim=2,
    )
    noise_mask = latent.get("noise_mask")
    if torch.is_tensor(noise_mask) and noise_mask.ndim == 5:
        mask_shape = list(noise_mask.shape)
        mask_shape[2] = int(extra_latent_frames)
        padded["noise_mask"] = torch.cat(
            [noise_mask, torch.ones(mask_shape, dtype=noise_mask.dtype, device=noise_mask.device)],
            dim=2,
        )
    return padded


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
    character_references = plan.get("model_specific", {}).get("ltx", {}).get("character_references", {})
    runtime_global_prompt = character_references.get("runtime_global_prompt") if isinstance(character_references, dict) else None
    global_prompt = str(runtime_global_prompt if runtime_global_prompt is not None else project_global.get("prompt") or "") if project_global.get("enabled") else ""
    local_prompts: list[str] = []
    pixel_lengths: list[int] = []
    frame_ranges: list[dict[str, int]] = []
    prompt_sections: list[dict[str, Any]] = []
    sections = plan.get("section_plan", [])
    for index, section in enumerate(sections):
        if section.get("type") == "Gap" or section.get("role") == "No Guidance":
            continue
        prompt = prompts_by_id.get(section.get("item_id"), {})
        raw_prompt = str(prompt.get("runtime_prompt") if prompt.get("runtime_prompt") is not None else prompt.get("raw_prompt") or "").strip()
        effective_prompt = str(prompt.get("effective_prompt") or "").strip()
        local_prompt = raw_prompt or effective_prompt
        if not raw_prompt and section.get("type") in {"Image", "Video"}:
            local_prompt = _next_prompt_after_section(sections, prompts_by_id, index) or effective_prompt
        if not local_prompt:
            continue
        frame_count = int(section.get("frame_count") or 0)
        if frame_count <= 0:
            continue
        local_prompts.append(local_prompt)
        pixel_lengths.append(frame_count)
        frame_ranges.append({
            "start_frame": int(section.get("start_frame") or 0),
            "end_frame_exclusive": int(section.get("end_frame_exclusive") or 0),
        })
        prompt_sections.append({
            "item_id": section.get("item_id"),
            "type": section.get("type"),
            "start_frame": int(section.get("start_frame") or 0),
            "end_frame_exclusive": int(section.get("end_frame_exclusive") or 0),
            "frame_count": frame_count,
        })
    return {
        "global_prompt": global_prompt,
        "local_prompts": local_prompts,
        "pixel_lengths": pixel_lengths,
        "frame_ranges": frame_ranges,
        "prompt_sections": prompt_sections,
    }


def _next_prompt_after_section(sections: list[dict[str, Any]], prompts_by_id: dict[Any, dict[str, Any]], index: int) -> str:
    section = sections[index]
    section_end = int(section.get("end_frame_exclusive") or section.get("start_frame") or 0)
    for candidate in sections[index + 1:]:
        if int(candidate.get("start_frame") or 0) < section_end:
            continue
        prompt = prompts_by_id.get(candidate.get("item_id"), {})
        raw_prompt = str(prompt.get("raw_prompt") or "").strip()
        effective_prompt = str(prompt.get("effective_prompt") or "").strip()
        if raw_prompt:
            return raw_prompt
        if effective_prompt and candidate.get("type") != "Gap" and candidate.get("role") != "No Guidance":
            return effective_prompt
    return ""


def _plain_prompt(prompt_inputs: dict[str, Any]) -> str:
    local_prompts = [str(prompt).strip() for prompt in prompt_inputs.get("local_prompts", []) if str(prompt).strip()]
    if local_prompts:
        return ", ".join(local_prompts)
    return str(prompt_inputs.get("global_prompt") or "").strip()


def _resolve_negative_conditioning(negative, positive):
    return negative if negative is not None else zero_out_conditioning(positive)


def _resolve_ltx_lora_config(plan: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    model_loras = plan.get("project", {}).get("model_loras", {})
    if not isinstance(model_loras, dict):
        model_loras = {}
    hi = normalize_lora_config(model_loras.get("lora_config_hi"))
    low = normalize_lora_config(model_loras.get("lora_config_low"))
    if hi["loras"] and low["loras"]:
        raise ValueError(
            "LTX timeline runtime accepts only one timeline LoRA config. "
            "Connect either lora_config_hi or lora_config_low, not both."
        )
    if hi["loras"]:
        return hi, ["LTX runtime applied timeline LoRAs from lora_config_hi."]
    if low["loras"]:
        return low, ["LTX runtime applied timeline LoRAs from lora_config_low."]
    return hi, []


def _runtime_debug(plan, prompt_debug, guide_data, guide_apply_debug, diagnostics, video_latent, combined_audio, applied_loras=None, status_events=None):
    character_references = plan.get("model_specific", {}).get("ltx", {}).get("character_references", {})
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
            "lora_count": len(applied_loras or []),
        },
        "loras": list(applied_loras or []),
        "prompt_relay": prompt_debug,
        "guide_data": {
            "insert_frames": list(guide_data.get("insert_frames", [])),
            "strengths": list(guide_data.get("strengths", [])),
            "clean_pixel_frames": guide_data.get("clean_pixel_frames"),
            "clean_latent_frames": guide_data.get("clean_latent_frames"),
            "hidden_reference_count": guide_data.get("hidden_reference_count"),
            "hidden_reference_guard_latent_frames": guide_data.get("hidden_reference_guard_latent_frames"),
            "reserved_latent_frames": guide_data.get("reserved_latent_frames"),
        },
        "character_references": {
            "mode": character_references.get("mode") if isinstance(character_references, dict) else None,
            "active": bool(character_references.get("active")) if isinstance(character_references, dict) else False,
            "guide_count": len(character_references.get("guide_specs", [])) if isinstance(character_references, dict) else 0,
            "hidden_reference_count": guide_data.get("hidden_reference_count"),
            "hidden_reference_guard_latent_frames": guide_data.get("hidden_reference_guard_latent_frames"),
            "substitutions": character_references.get("substitutions", []) if isinstance(character_references, dict) else [],
            "diagnostics": character_references.get("diagnostics", []) if isinstance(character_references, dict) else [],
        },
        "status_events": list(status_events or []),
        "diagnostics": diagnostics,
    }


def _advanced_input_diagnostics(identity_anchor, sigmas) -> list[str]:
    diagnostics = []
    if sigmas is not None and identity_anchor is None:
        diagnostics.append("sigmas input is connected but is only consumed when identity_anchor is connected.")
    return diagnostics
