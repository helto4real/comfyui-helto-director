from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..bernini import BERNINI_MODEL_MODE, BERNINI_SYSTEM_PROMPTS
from ..planner import _build_prompt_relay, _latent_chunk_count
from ...ltx.runtime.audio import mix_timeline_audio
from ...segmented_executor import (
    SegmentSpillStore,
    build_segment_plan,
    decode_latent_images,
    post_decode_memory_cleanup,
    previous_tail,
    sample_latent,
    segment_seed,
    stitch_spilled_segment_images,
    trim_visible_segment_images,
)
from .runtime import build_wan_runtime_outputs


WAN_SINGLE_PHASE_MODEL_MODES = {"TI2V-5B"}
VRAM_UNLOAD_OFF = "Off"
VRAM_UNLOAD_BETWEEN_HIGH_LOW = "Between High Low"
VRAM_UNLOAD_BEFORE_DECODE = "Before Decode"
VRAM_UNLOAD_BETWEEN_HIGH_LOW_AND_DECODE = "Between High Low And Decode"


def build_wan_segmented_executor_outputs(
    *,
    high_noise_model=None,
    low_noise_model=None,
    clip=None,
    vae=None,
    wan_timeline_plan: dict[str, Any],
    seed: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    denoise: float,
    seed_mode: str,
    phase_split_percent: float = 0.5,
    negative=None,
    batch_size: int = 1,
):
    plan = deepcopy(wan_timeline_plan)
    segmented = plan.get("model_specific", {}).get("wan", {}).get("segmented_generation", {})
    segments = list(segmented.get("segments") or [])
    if not segments:
        frame_count = int(plan.get("resolved_output", {}).get("frame_count") or 1)
        segments = [{
            "id": "gen_001",
            "index": 0,
            "start_frame": 0,
            "end_frame_exclusive": frame_count,
            "frame_count": frame_count,
            "visible_frame_count": frame_count,
            "generation_frame_count": frame_count,
            "trim_leading_frames": 0,
            "trim_trailing_frames": 0,
            "continuity": {"mode": "initial"},
        }]

    privacy_mode = bool(plan.get("project", {}).get("privacy", {}).get("mode"))
    store = SegmentSpillStore(privacy_mode=privacy_mode)
    spill_records = []
    previous_images = None
    segment_debug = []
    cleanup_events = []
    try:
        for index, segment in enumerate(segments):
            tail = None
            if previous_images is not None:
                tail = previous_tail(previous_images, segment.get("continuity", {}).get("continuity_frame_count") or 1)
            segment_plan = build_segment_plan(
                plan,
                segment,
                model_key="wan",
                previous_tail_images=tail,
                prompt_relay_builder=_build_segment_prompt_relay,
            )
            _apply_wan_segment_continuity(segment_plan, tail)
            (
                runtime_high_model,
                runtime_low_model,
                positive,
                runtime_negative,
                video_latent,
                runtime_debug,
            ) = build_wan_runtime_outputs(
                high_noise_model=high_noise_model,
                low_noise_model=low_noise_model,
                clip=clip,
                vae=vae,
                wan_timeline_plan=segment_plan,
                negative=negative,
                batch_size=batch_size,
            )
            segment_seed_value = segment_seed(seed, index, seed_mode)
            sampled, sampling_debug = sample_wan_segment_latent(
                high_noise_model=runtime_high_model,
                low_noise_model=runtime_low_model,
                positive=positive,
                negative=runtime_negative,
                latent=video_latent,
                model_mode=str(segment_plan.get("model_specific", {}).get("wan", {}).get("config", {}).get("model_mode") or "I2V-A14B"),
                seed=segment_seed_value,
                steps=steps,
                cfg=cfg,
                sampler_name=sampler_name,
                scheduler=scheduler,
                denoise=denoise,
                phase_split_percent=phase_split_percent,
                vram_unload_policy=str(segment_plan.get("model_specific", {}).get("wan", {}).get("config", {}).get("vram_unload_policy") or VRAM_UNLOAD_OFF),
            )
            images = decode_latent_images(vae, sampled)
            visible_images = trim_visible_segment_images(images, segment)
            next_tail_count = (
                int(segments[index + 1].get("continuity", {}).get("continuity_frame_count") or 1)
                if index + 1 < len(segments)
                else 1
            )
            previous_images = previous_tail(visible_images.detach().cpu(), next_tail_count)
            record = store.write_segment(segment, visible_images)
            spill_records.append(record)
            cleanup_events.append(post_decode_memory_cleanup(f"post_decode_{segment.get('id') or index + 1}"))
            wan_debug = runtime_debug.get("wan", {}) if isinstance(runtime_debug, dict) else {}
            segment_debug.append({
                "id": segment.get("id"),
                "seed": segment_seed_value,
                "generation_frame_count": segment.get("generation_frame_count"),
                "visible_frame_count": segment.get("visible_frame_count"),
                "trim_leading_frames": segment.get("trim_leading_frames"),
                "decoded_frame_count": int(images.shape[0]),
                "spilled_frame_count": int(visible_images.shape[0]),
                "continuity": segment.get("continuity"),
                "actual_tail_frame_count": int(tail.shape[0]) if tail is not None else 0,
                "actual_tail_shape": [int(dim) for dim in tail.shape] if tail is not None else [],
                "sampling": sampling_debug,
                "bernini": wan_debug.get("bernini"),
            })
            del segment_plan, runtime_high_model, runtime_low_model, positive, runtime_negative, video_latent, sampled, images, visible_images

        final_images = stitch_spilled_segment_images(
            spill_records,
            store,
            final_frame_count=int(plan.get("resolved_output", {}).get("frame_count") or 1),
        )
        cleanup_summary = store.cleanup()
        combined_audio, audio_diagnostics = mix_timeline_audio(_wan_plan_as_audio_mix_plan(plan))
        debug = {
            "enabled": bool(segmented.get("enabled")),
            "model": "wan",
            "segment_count": len(segments),
            "segment_storage": cleanup_summary,
            "post_decode_cleanup": cleanup_events,
            "segments": segment_debug,
            "stitching": {
                "output_frame_count": int(final_images.shape[0]),
                "target_frame_count": int(plan.get("resolved_output", {}).get("frame_count") or 1),
                "audio_policy": "global_full_mix",
            },
            "diagnostics": [
                *(segmented.get("diagnostics") or []),
                *audio_diagnostics,
            ],
        }
        return final_images, combined_audio, float(plan.get("resolved_output", {}).get("frame_rate") or 24.0), debug
    except Exception:
        store.cleanup()
        raise


def sample_wan_segment_latent(
    *,
    high_noise_model=None,
    low_noise_model=None,
    positive,
    negative,
    latent: dict[str, Any],
    model_mode: str,
    seed: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    denoise: float,
    phase_split_percent: float,
    vram_unload_policy: str = VRAM_UNLOAD_OFF,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if _uses_single_phase_sampling(model_mode):
        model = high_noise_model or low_noise_model
        if model is None:
            raise ValueError("WAN segmented executor needs one connected model for TI2V-5B single-phase sampling.")
        sampled = sample_latent(
            model=model,
            positive=positive,
            negative=negative,
            latent=latent,
            seed=seed,
            steps=steps,
            cfg=cfg,
            sampler_name=sampler_name,
            scheduler=scheduler,
            denoise=denoise,
            force_full_denoise=True,
        )
        model_role = "high_noise_model" if high_noise_model is not None else "low_noise_model"
        unload_events = _maybe_unload_before_decode(model, None, vram_unload_policy, role=model_role)
        return sampled, {
            "sampling_policy": "single_phase",
            "model_mode": model_mode,
            "seed": int(seed),
            "steps": int(steps),
            "phase_split_percent": None,
            "split_step": None,
            "phases": [{"role": "single", "model": model_role}],
            "vram_unload_policy": vram_unload_policy,
            "unload_events": unload_events,
        }

    if high_noise_model is None or low_noise_model is None:
        raise ValueError(
            "WAN segmented executor two-phase sampling requires both high_noise_model and low_noise_model "
            f"for model mode {model_mode}."
        )
    steps = max(1, int(steps))
    if steps < 2:
        raise ValueError("WAN segmented executor two-phase sampling requires at least 2 total steps.")
    split_step = _split_step(steps, phase_split_percent)
    sampled_high = sample_latent(
        model=high_noise_model,
        positive=positive,
        negative=negative,
        latent=latent,
        seed=seed,
        steps=steps,
        cfg=cfg,
        sampler_name=sampler_name,
        scheduler=scheduler,
        denoise=denoise,
        start_step=0,
        last_step=split_step,
        force_full_denoise=False,
    )
    unload_events = _maybe_unload_between_phases(high_noise_model, low_noise_model, vram_unload_policy)
    sampled_low = sample_latent(
        model=low_noise_model,
        positive=positive,
        negative=negative,
        latent=sampled_high,
        seed=seed,
        steps=steps,
        cfg=cfg,
        sampler_name=sampler_name,
        scheduler=scheduler,
        denoise=denoise,
        disable_noise=True,
        start_step=split_step,
        last_step=steps,
        force_full_denoise=True,
    )
    unload_events.extend(_maybe_unload_before_decode(low_noise_model, high_noise_model, vram_unload_policy, role="low_noise_model"))
    return sampled_low, {
        "sampling_policy": "two_phase",
        "model_mode": model_mode,
        "seed": int(seed),
        "steps": int(steps),
        "phase_split_percent": float(phase_split_percent),
        "split_step": int(split_step),
        "phases": [
            {
                "role": "high_noise",
                "model": "high_noise_model",
                "start_step": 0,
                "last_step": int(split_step),
                "force_full_denoise": False,
                "disable_noise": False,
            },
            {
                "role": "low_noise",
                "model": "low_noise_model",
                "start_step": int(split_step),
                "last_step": int(steps),
                "force_full_denoise": True,
                "disable_noise": True,
            },
        ],
        "vram_unload_policy": vram_unload_policy,
        "unload_events": unload_events,
    }


def _uses_single_phase_sampling(model_mode: str) -> bool:
    return str(model_mode or "") in WAN_SINGLE_PHASE_MODEL_MODES


def _split_step(steps: int, phase_split_percent: float) -> int:
    steps = max(2, int(steps))
    try:
        percent = float(phase_split_percent)
    except (TypeError, ValueError):
        percent = 0.5
    split = int(round(steps * min(max(percent, 0.01), 0.99)))
    return min(max(1, split), steps - 1)


def _maybe_unload_between_phases(high_noise_model, low_noise_model, policy: str) -> list[dict[str, Any]]:
    if policy not in {VRAM_UNLOAD_BETWEEN_HIGH_LOW, VRAM_UNLOAD_BETWEEN_HIGH_LOW_AND_DECODE}:
        return []
    if high_noise_model is None or high_noise_model is low_noise_model:
        return [{
            "stage": "between_high_low",
            "role": "high_noise_model",
            "attempted": False,
            "success": False,
            "reason": "model_missing_or_reused",
        }]
    return [_unload_model("between_high_low", "high_noise_model", high_noise_model)]


def _maybe_unload_before_decode(model, other_model, policy: str, role: str = "low_noise_model") -> list[dict[str, Any]]:
    if policy not in {VRAM_UNLOAD_BEFORE_DECODE, VRAM_UNLOAD_BETWEEN_HIGH_LOW_AND_DECODE}:
        return []
    if model is None or model is other_model:
        return [{
            "stage": "before_decode",
            "role": role,
            "attempted": False,
            "success": False,
            "reason": "model_missing_or_reused",
        }]
    return [_unload_model("before_decode", role, model)]


def _unload_model(stage: str, role: str, model) -> dict[str, Any]:
    event = {
        "stage": stage,
        "role": role,
        "attempted": True,
        "success": False,
    }
    try:
        import comfy.model_management

        comfy.model_management.synchronize()
        comfy.model_management.unload_model_and_clones(
            model,
            unload_additional_models=True,
            all_devices=False,
        )
        comfy.model_management.soft_empty_cache(force=True)
        event["success"] = True
    except Exception as exc:
        event["error"] = str(exc)
    return event


def _build_segment_prompt_relay(
    segment_plan: dict[str, Any],
    section_plan: list[dict[str, Any]],
    prompt_plan: list[dict[str, Any]],
    frame_count: int,
) -> dict[str, Any]:
    config = segment_plan.get("model_specific", {}).get("wan", {}).get("config", {})
    latent_chunk_count = _latent_chunk_count(frame_count)
    return _build_prompt_relay(
        segment_plan,
        config,
        section_plan,
        prompt_plan,
        frame_count,
        latent_chunk_count,
        segment_plan.get("model_specific", {}).get("wan", {}).get("bernini", {}).get("character_references"),
    )


def _apply_wan_segment_continuity(segment_plan: dict[str, Any], tail) -> None:
    wan = segment_plan.get("model_specific", {}).get("wan", {})
    if tail is None:
        return
    wan["segment_continuity"] = {
        "mode": "previous_tail",
        "previous_tail_images": tail,
        "frame_count": int(tail.shape[0]),
    }
    visual = wan.get("visual_conditioning")
    if isinstance(visual, dict):
        visual["transient_start_image"] = tail
    bernini = wan.get("bernini")
    if isinstance(bernini, dict) and bernini.get("enabled"):
        bernini["segment_continuity"] = wan["segment_continuity"]
        if bernini.get("task_type") == "r2v":
            bernini["task_type"] = "rv2v"
            bernini["system_prompt"] = BERNINI_SYSTEM_PROMPTS["rv2v"]
            bernini["selection_reason"] = "Continuation segment uses the previous decoded tail as Bernini source_video with reference images."
        elif bernini.get("task_type") == "t2v":
            bernini["task_type"] = "v2v"
            bernini["system_prompt"] = BERNINI_SYSTEM_PROMPTS["v2v"]
            bernini["selection_reason"] = "Continuation segment uses the previous decoded tail as Bernini source_video."


def _wan_plan_as_audio_mix_plan(plan: dict[str, Any]) -> dict[str, Any]:
    # The LTX audio mixer is timeline-generic except for this config key.
    output = deepcopy(plan)
    output.setdefault("model_specific", {}).setdefault("ltx", {}).setdefault("config", {})["audio_mode"] = "Mix Timeline Audio"
    return output
