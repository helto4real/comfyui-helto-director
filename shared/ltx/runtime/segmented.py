from __future__ import annotations

from copy import deepcopy
from typing import Any

from ...timeline.references import parse_reference_tags
from ...ltx.identity import crop_images_to_frame_count, crop_latent_to_frame_count
from ...segmented_executor import (
    SegmentSpillStore,
    blend_segment_seam,
    build_segment_plan,
    decode_latent_images,
    external_sigmas_step_count,
    post_decode_memory_cleanup,
    previous_tail,
    sample_latent,
    segment_seam_blend_frames,
    segment_seed,
    stitch_spilled_segment_images,
    trim_visible_segment_images,
)
from ...timeline_status import TimelineStatusReporter, ensure_timeline_status_reporter
from .audio import (
    apply_native_source_video_audio_fallback,
    build_native_av_sampling_latent,
    decode_native_generated_audio,
    mix_timeline_audio,
    stitch_native_generated_audio,
)
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
    sampling_debug = _sampling_schedule_debug(sigmas, steps, scheduler)
    store = SegmentSpillStore(privacy_mode=privacy_mode)
    spill_records = []
    previous_images = None
    guided_character_labels: set[str] = set()
    segment_debug = []
    cleanup_events = []
    native_audio_segment_records = []
    native_audio_diagnostics = []
    use_native_audio = bool(plan.get("project", {}).get("audio", {}).get("use_native_audio"))
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
            reference_guidance_debug = _filter_segment_character_reference_guides(
                segment_plan,
                guided_character_labels,
                has_previous_tail=tail is not None,
            )
            (
                runtime_model,
                positive,
                runtime_negative,
                video_latent,
                audio_latent,
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
            native_audio_debug = {"enabled": use_native_audio, "av_latent_sampling": False, "diagnostics": []}
            sampling_latent = video_latent
            if use_native_audio:
                sampling_latent, native_audio_debug = build_native_av_sampling_latent(video_latent, audio_latent)
                native_audio_diagnostics.extend(native_audio_debug.get("diagnostics", []))
            sampled = sample_latent(
                model=runtime_model,
                positive=positive,
                negative=runtime_negative,
                latent=sampling_latent,
                seed=segment_seed(seed, index, seed_mode),
                steps=steps,
                cfg=cfg,
                sampler_name=sampler_name,
                scheduler=scheduler,
                denoise=denoise,
                sigmas=sigmas,
            )
            if use_native_audio and native_audio_debug.get("av_latent_sampling"):
                decoded_audio, decoded_audio_debug = decode_native_generated_audio(sampled, audio_vae)
                native_audio_debug["decode"] = decoded_audio_debug
                native_audio_diagnostics.extend(decoded_audio_debug.get("diagnostics", []))
                if decoded_audio is not None:
                    native_audio_segment_records.append({
                        "segment": deepcopy(segment),
                        "audio": decoded_audio,
                    })
            status_reporter.report(
                "ltx.reference_tail_crop",
                f"LTX Executor: segment {segment_index}/{segment_count} - cropping reference tail",
                segment_index=segment_index,
                segment_count=segment_count,
            )
            sampled_latent_frames_before_crop = _latent_frame_count(sampled)
            cropped = crop_latent_to_frame_count(
                sampled,
                int(guide_data.get("clean_latent_frames") or sampled["samples"].shape[2]),
                int(guide_data.get("hidden_reference_count") or 0),
                int(guide_data.get("hidden_reference_guard_latent_frames") or 0),
            )
            sampled_latent_frames_after_crop = _latent_frame_count(cropped)
            status_reporter.report(
                "timeline.decode",
                f"LTX Executor: segment {segment_index}/{segment_count} - decoding",
                segment_index=segment_index,
                segment_count=segment_count,
                frame_count=segment.get("generation_frame_count"),
            )
            decoded_images = decode_latent_images(vae, cropped)
            clean_pixel_frames = int(guide_data.get("clean_pixel_frames") or segment.get("generation_frame_count") or decoded_images.shape[0])
            images = crop_images_to_frame_count(decoded_images, clean_pixel_frames)
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
                "clean_latent_frames": guide_data.get("clean_latent_frames"),
                "hidden_reference_count": guide_data.get("hidden_reference_count"),
                "hidden_reference_guard_latent_frames": guide_data.get("hidden_reference_guard_latent_frames"),
                "sampled_latent_frame_count_before_crop": sampled_latent_frames_before_crop,
                "sampled_latent_frame_count_after_crop": sampled_latent_frames_after_crop,
                "clean_pixel_frames": clean_pixel_frames,
                "decoded_frame_count_before_frame_crop": int(decoded_images.shape[0]),
                "decoded_frame_count_after_frame_crop": int(images.shape[0]),
                "frame_crop_applied": int(decoded_images.shape[0]) != int(images.shape[0]),
                "decoded_frame_count": int(images.shape[0]),
                "spilled_frame_count": int(visible_images.shape[0]),
                "continuity": segment.get("continuity"),
                "actual_tail_frame_count": int(tail.shape[0]) if tail is not None else 0,
                "actual_tail_shape": [int(dim) for dim in tail.shape] if tail is not None else [],
                "seam_blend": seam_blend_debug,
                "character_reference_guidance": reference_guidance_debug,
                "character_reference_guidance_policy": reference_guidance_debug["character_reference_guidance_policy"],
                "character_reference_labels_requested": reference_guidance_debug["character_reference_labels_requested"],
                "character_reference_labels_guided": reference_guidance_debug["character_reference_labels_guided"],
                "character_reference_labels_text_only": reference_guidance_debug["character_reference_labels_text_only"],
                "runtime_summary": runtime_debug.get("summary") if isinstance(runtime_debug, dict) else None,
                "sampling": sampling_debug,
                "native_audio": native_audio_debug,
            })
            guided_character_labels.update(reference_guidance_debug["character_reference_labels_guided"])
            del segment_plan, runtime_model, positive, runtime_negative, video_latent, audio_latent, sampling_latent, sampled, cropped, decoded_images, images, visible_images

        status_reporter.report("timeline.stitch", "Timeline Executor: stitching segments")
        final_images = stitch_spilled_segment_images(
            spill_records,
            store,
            final_frame_count=int(plan.get("resolved_output", {}).get("frame_count") or 1),
        )
        cleanup_summary = store.cleanup()
        status_reporter.report("timeline.audio", "Timeline Executor: mixing audio")
        combined_audio, audio_diagnostics = mix_timeline_audio(plan)
        native_audio_debug = {
            "enabled": use_native_audio,
            "policy": "timeline_mix",
            "decoded_segment_count": len(native_audio_segment_records),
            "segment_audio_shapes": [
                [int(dim) for dim in record["audio"]["waveform"].shape]
                for record in native_audio_segment_records
            ],
        }
        if use_native_audio:
            native_audio, native_stitch_debug = stitch_native_generated_audio(
                native_audio_segment_records,
                final_frame_count=int(plan.get("resolved_output", {}).get("frame_count") or 1),
                frame_rate=float(plan.get("resolved_output", {}).get("frame_rate") or 24.0),
            )
            native_audio_debug["stitch"] = native_stitch_debug
            native_audio_diagnostics.extend(native_stitch_debug.get("diagnostics", []))
            if native_audio is not None:
                combined_audio = native_audio
                native_audio_debug["policy"] = "native_generated"
                native_audio_diagnostics.append("Native generated audio decoded and returned as executor audio output.")
            else:
                combined_audio, source_audio_diagnostics = apply_native_source_video_audio_fallback(plan, combined_audio)
                native_audio_diagnostics.extend(source_audio_diagnostics)
                if any("fallback applied" in entry for entry in source_audio_diagnostics):
                    native_audio_debug["policy"] = "source_video_fallback"
                else:
                    native_audio_debug["policy"] = "timeline_mix_fallback"
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
                "audio_policy": native_audio_debug["policy"],
            },
            "sampling": sampling_debug,
            "native_audio": native_audio_debug,
            "diagnostics": [
                *(segmented.get("diagnostics") or []),
                *sampling_debug.get("diagnostics", []),
                *audio_diagnostics,
                *native_audio_diagnostics,
            ],
        }
        return final_images, combined_audio, float(plan.get("resolved_output", {}).get("frame_rate") or 24.0), debug
    except Exception:
        store.cleanup()
        raise


def _sampling_schedule_debug(sigmas, steps: int, scheduler: str) -> dict[str, Any]:
    if sigmas is None:
        return {
            "external_sigmas_used": False,
            "configured_steps": int(steps),
            "configured_scheduler": str(scheduler),
            "effective_steps": int(steps),
            "diagnostics": [],
        }
    effective_steps = external_sigmas_step_count(sigmas)
    sigma_count = effective_steps + 1
    return {
        "external_sigmas_used": True,
        "configured_steps": int(steps),
        "configured_scheduler": str(scheduler),
        "sigma_count": sigma_count,
        "effective_steps": effective_steps,
        "diagnostics": [
            (
                "Connected sigmas input is controlling the sampling schedule; "
                f"executor Steps ({int(steps)}) and Scheduler ({str(scheduler)}) are ignored for schedule construction."
            )
        ],
    }


def _latent_frame_count(latent: dict[str, Any]) -> int | None:
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if samples is None:
        return None
    if getattr(samples, "is_nested", False):
        try:
            streams = list(samples.unbind())
        except Exception:
            streams = []
        samples = streams[0] if streams else None
    shape = getattr(samples, "shape", None)
    if shape is None or len(shape) < 3:
        return None
    try:
        return int(shape[2])
    except Exception:
        return None


def _filter_segment_character_reference_guides(
    segment_plan: dict[str, Any],
    guided_character_labels: set[str],
    *,
    has_previous_tail: bool,
) -> dict[str, Any]:
    character_references = segment_plan.get("model_specific", {}).get("ltx", {}).get("character_references", {})
    if not isinstance(character_references, dict):
        return _reference_guidance_debug([], [], [])

    requested_labels = _segment_requested_character_labels(segment_plan.get("prompt_plan", []))
    section_ids = {str(entry.get("item_id")) for entry in segment_plan.get("section_plan", []) if entry.get("item_id") is not None}
    specs = character_references.get("guide_specs")
    if not isinstance(specs, list):
        character_references["segment_guidance"] = _reference_guidance_debug(requested_labels, [], [])
        return character_references["segment_guidance"]

    guided_specs = []
    guided_labels: list[str] = []
    text_only_labels: list[str] = []
    for spec in specs:
        label = _normalized_reference_label(spec.get("label") or spec.get("id"))
        if not label or label not in requested_labels:
            continue
        if section_ids and not _spec_matches_section(spec, section_ids):
            continue
        if has_previous_tail and label in guided_character_labels:
            text_only_labels.append(label)
            continue
        guided_specs.append(deepcopy(spec))
        guided_labels.append(label)

    for label in requested_labels:
        if label in guided_labels or label in text_only_labels:
            continue
        if has_previous_tail and label in guided_character_labels:
            text_only_labels.append(label)

    debug = _reference_guidance_debug(requested_labels, guided_labels, text_only_labels)
    character_references["guide_specs"] = guided_specs
    character_references["segment_guidance"] = debug
    return debug


def _segment_requested_character_labels(prompt_plan: list[dict[str, Any]]) -> list[str]:
    labels = []
    seen = set()
    for prompt in prompt_plan:
        for tag in parse_reference_tags(str(prompt.get("raw_prompt") or "")):
            if not tag.get("supported"):
                continue
            label = _normalized_reference_label(tag.get("label"))
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


def _spec_matches_section(spec: dict[str, Any], section_ids: set[str]) -> bool:
    raw = str(spec.get("section_id") or "").strip()
    if not raw:
        return True
    spec_section_ids = {part for part in raw.split(",") if part}
    return bool(spec_section_ids.intersection(section_ids))


def _normalized_reference_label(value: Any) -> str:
    return str(value or "").strip().lower()


def _reference_guidance_debug(
    requested_labels: list[str],
    guided_labels: list[str],
    text_only_labels: list[str],
) -> dict[str, Any]:
    return {
        "character_reference_guidance_policy": "segment_local_continuation",
        "character_reference_labels_requested": sorted(set(requested_labels)),
        "character_reference_labels_guided": sorted(set(guided_labels)),
        "character_reference_labels_text_only": sorted(set(text_only_labels)),
    }
