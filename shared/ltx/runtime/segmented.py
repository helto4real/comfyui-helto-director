from __future__ import annotations

from copy import deepcopy
from typing import Any

from ...ltx.identity import crop_latent_to_frame_count
from ...segmented_executor import (
    SegmentSpillStore,
    blend_segment_seam,
    build_segment_plan,
    decode_latent_images,
    post_decode_memory_cleanup,
    previous_tail,
    sample_latent,
    segment_seam_blend_frames,
    segment_seed,
    stitch_spilled_segment_images,
    trim_visible_segment_images,
)
from ...timeline_status import TimelineStatusReporter, ensure_timeline_status_reporter
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
    status_reporter: TimelineStatusReporter | None = None,
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

    status_reporter = ensure_timeline_status_reporter(
        status_reporter,
        model="ltx",
        total=(len(segments) * 12) + 4,
    )
    status_reporter.report("timeline.prepare", f"LTX Executor: preparing {len(segments)} segment(s)")
    privacy_mode = bool(plan.get("project", {}).get("privacy", {}).get("mode"))
    store = SegmentSpillStore(privacy_mode=privacy_mode)
    spill_records = []
    previous_images = None
    segment_debug = []
    cleanup_events = []
    config = plan.get("model_specific", {}).get("ltx", {}).get("config", {})
    configured_seam_blend_frames = segment_seam_blend_frames(
        config.get("segment_seam_blend_frames", 3) if isinstance(config, dict) else 3
    )
    try:
        for index, segment in enumerate(segments):
            segment_index = index + 1
            segment_count = len(segments)
            tail = None
            if previous_images is not None:
                tail = previous_tail(previous_images, segment.get("continuity", {}).get("continuity_frame_count") or 1)
            status_reporter.report(
                "timeline.conditioning",
                f"LTX Executor: segment {segment_index}/{segment_count} - conditioning",
                segment_index=segment_index,
                segment_count=segment_count,
                frame_count=segment.get("generation_frame_count"),
            )
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
                status_reporter=status_reporter,
                complete_status=False,
            )
            status_reporter.report(
                "timeline.sample",
                f"LTX Executor: segment {segment_index}/{segment_count} - sampling",
                segment_index=segment_index,
                segment_count=segment_count,
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
            status_reporter.report(
                "ltx.reference_tail_crop",
                f"LTX Executor: segment {segment_index}/{segment_count} - cropping reference tail",
                segment_index=segment_index,
                segment_count=segment_count,
            )
            cropped = crop_latent_to_frame_count(
                sampled,
                int(guide_data.get("clean_latent_frames") or sampled["samples"].shape[2]),
                int(guide_data.get("hidden_reference_count") or 0),
            )
            status_reporter.report(
                "timeline.decode",
                f"LTX Executor: segment {segment_index}/{segment_count} - decoding",
                segment_index=segment_index,
                segment_count=segment_count,
                frame_count=segment.get("generation_frame_count"),
            )
            images = decode_latent_images(vae, cropped)
            visible_images = trim_visible_segment_images(images, segment)
            visible_images, seam_blend_debug = blend_segment_seam(
                visible_images,
                previous_images,
                configured_seam_blend_frames if index > 0 else 0,
            )
            next_tail_count = (
                int(segments[index + 1].get("continuity", {}).get("continuity_frame_count") or 1)
                if index + 1 < len(segments)
                else 1
            )
            next_previous_frame_count = max(next_tail_count, configured_seam_blend_frames)
            previous_images = previous_tail(visible_images.detach().cpu(), next_previous_frame_count)
            status_reporter.report(
                "timeline.spill",
                f"LTX Executor: segment {segment_index}/{segment_count} - saving {'encrypted ' if privacy_mode else ''}segment frames",
                segment_index=segment_index,
                segment_count=segment_count,
                frame_count=int(visible_images.shape[0]),
                encrypted_spill=privacy_mode,
            )
            record = store.write_segment(segment, visible_images)
            spill_records.append(record)
            status_reporter.report(
                "timeline.cleanup",
                f"LTX Executor: segment {segment_index}/{segment_count} - releasing memory",
                segment_index=segment_index,
                segment_count=segment_count,
            )
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
                "seam_blend": seam_blend_debug,
                "runtime_summary": runtime_debug.get("summary") if isinstance(runtime_debug, dict) else None,
            })
            del segment_plan, runtime_model, positive, runtime_negative, video_latent, sampled, cropped, images, visible_images

        status_reporter.report("timeline.stitch", "Timeline Executor: stitching segments")
        final_images = stitch_spilled_segment_images(
            spill_records,
            store,
            final_frame_count=int(plan.get("resolved_output", {}).get("frame_count") or 1),
        )
        cleanup_summary = store.cleanup()
        status_reporter.report("timeline.audio", "Timeline Executor: mixing audio")
        combined_audio, audio_diagnostics = mix_timeline_audio(plan)
        status_reporter.done("Timeline Executor: done")
        debug = {
            "enabled": bool(segmented.get("enabled")),
            "model": "ltx",
            "segment_count": len(segments),
            "segment_storage": cleanup_summary,
            "post_decode_cleanup": cleanup_events,
            "status_events": status_reporter.snapshot(),
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
