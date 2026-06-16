from __future__ import annotations

from copy import deepcopy
from typing import Any

from ...ltx.identity import crop_latent_to_frame_count
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
from .audio import mix_timeline_audio
from .runtime import build_ltx_runtime_outputs


def build_ltx_segmented_executor_outputs(
    *,
    model,
    clip,
    vae,
    ltx_timeline_plan: dict[str, Any],
    seed: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    denoise: float,
    seed_mode: str,
    negative=None,
    optional_latent=None,
    audio_vae=None,
    identity_anchor=None,
    sigmas=None,
    iclora_parameters=None,
):
    plan = deepcopy(ltx_timeline_plan)
    segmented = plan.get("model_specific", {}).get("ltx", {}).get("segmented_generation", {})
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
                model_key="ltx",
                previous_tail_images=tail,
            )
            (
                runtime_model,
                positive,
                runtime_negative,
                video_latent,
                _audio_latent,
                _segment_audio,
                guide_data,
                *_rest,
                runtime_debug,
            ) = build_ltx_runtime_outputs(
                model=model,
                clip=clip,
                vae=vae,
                ltx_timeline_plan=segment_plan,
                negative=negative,
                optional_latent=optional_latent if index == 0 else None,
                audio_vae=audio_vae,
                identity_anchor=identity_anchor,
                sigmas=sigmas,
                iclora_parameters=iclora_parameters,
            )
            sampled = sample_latent(
                model=runtime_model,
                positive=positive,
                negative=runtime_negative,
                latent=video_latent,
                seed=segment_seed(seed, index, seed_mode),
                steps=steps,
                cfg=cfg,
                sampler_name=sampler_name,
                scheduler=scheduler,
                denoise=denoise,
            )
            cropped = crop_latent_to_frame_count(
                sampled,
                int(guide_data.get("clean_latent_frames") or sampled["samples"].shape[2]),
                int(guide_data.get("hidden_reference_count") or 0),
            )
            images = decode_latent_images(vae, cropped)
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
            segment_debug.append({
                "id": segment.get("id"),
                "seed": segment_seed(seed, index, seed_mode),
                "generation_frame_count": segment.get("generation_frame_count"),
                "visible_frame_count": segment.get("visible_frame_count"),
                "trim_leading_frames": segment.get("trim_leading_frames"),
                "decoded_frame_count": int(images.shape[0]),
                "spilled_frame_count": int(visible_images.shape[0]),
                "continuity": segment.get("continuity"),
                "actual_tail_frame_count": int(tail.shape[0]) if tail is not None else 0,
                "actual_tail_shape": [int(dim) for dim in tail.shape] if tail is not None else [],
                "runtime_summary": runtime_debug.get("summary") if isinstance(runtime_debug, dict) else None,
            })
            del segment_plan, runtime_model, positive, runtime_negative, video_latent, sampled, cropped, images, visible_images

        final_images = stitch_spilled_segment_images(
            spill_records,
            store,
            final_frame_count=int(plan.get("resolved_output", {}).get("frame_count") or 1),
        )
        cleanup_summary = store.cleanup()
        combined_audio, audio_diagnostics = mix_timeline_audio(plan)
        debug = {
            "enabled": bool(segmented.get("enabled")),
            "model": "ltx",
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
