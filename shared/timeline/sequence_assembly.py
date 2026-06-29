from __future__ import annotations

from copy import deepcopy
from typing import Any

import torch

from ..contracts.video_timeline import (
    ASSET_TYPE_VIDEO,
    BOUNDARY_MODE_BLEND_SEAM,
    BOUNDARY_MODE_CONTINUOUS_SHOT,
    BOUNDARY_MODE_HARD_CUT,
    BOUNDARY_MODE_TRANSITION,
    SHOT_TYPE_IMPORTED,
)
from .normalize import normalize_video_timeline
from .time_mapping import time_range_to_frames


class SequenceAssemblyError(ValueError):
    """Raised when the sequence cannot be assembled into media outputs."""


def _assets_by_id(timeline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(asset.get("asset_id")): asset
        for asset in timeline.get("assets", [])
        if asset.get("asset_id") is not None
    }


def assemble_timeline_sequence(
    video_timeline: Any,
    *,
    missing_take_policy: str = "warning",
) -> tuple[torch.Tensor, dict[str, Any], float, dict[str, Any]]:
    """Assemble accepted/generated takes and imported clip instances into frames."""

    timeline = normalize_video_timeline(video_timeline)
    frame_rate = _safe_float(timeline.get("project", {}).get("frame_rate"), 24.0)
    frame_rate = frame_rate if frame_rate > 0 else 24.0
    assets_by_id = _assets_by_id(timeline)
    debug: dict[str, Any] = {
        "type": "DEBUG_INFO",
        "source": "Sequence Assembly",
        "summary": {
            "status": "pending",
            "shot_count": 0,
            "included_clip_count": 0,
            "missing_accepted_take_count": 0,
            "missing_asset_count": 0,
            "missing_source_media_count": 0,
            "warning_count": 0,
            "error_count": 0,
            "output_frame_count": 0,
            "placeholder_output_frame_count": 0,
            "frame_rate": frame_rate,
        },
        "shots": [],
        "clips": [],
        "boundaries": [],
        "resolution": {
            "policy": "first_clip_resolution",
            "width": None,
            "height": None,
            "resized_clip_count": 0,
        },
        "audio": {
            "status": "not_built",
            "clip_count": 0,
            "timeline_clip_count": 0,
            "source_clip_count": 0,
            "source_mixed_clip_count": 0,
            "source_skipped_clip_count": 0,
            "diagnostics": [],
        },
        "warnings": [],
        "errors": [],
    }

    clip_entries = _decode_sequence_clips(
        timeline,
        assets_by_id,
        frame_rate,
        missing_take_policy,
        debug,
    )
    if not clip_entries:
        return _empty_sequence_result(frame_rate, debug)

    frames = _assemble_clip_frames(clip_entries, timeline, debug)
    debug["clips"] = [_clip_debug_entry(entry) for entry in clip_entries]
    audio = _assemble_audio(timeline, clip_entries, frames, frame_rate, debug)
    debug["summary"]["status"] = "assembled"
    debug["summary"]["included_clip_count"] = len(clip_entries)
    debug["summary"]["output_frame_count"] = int(frames.shape[0])
    debug["summary"]["warning_count"] = len(debug["warnings"])
    debug["summary"]["error_count"] = len(debug["errors"])
    return frames, audio, frame_rate, debug


def _empty_sequence_result(
    frame_rate: float,
    debug: dict[str, Any],
) -> tuple[torch.Tensor, dict[str, Any], float, dict[str, Any]]:
    from ..ltx.runtime.audio import empty_audio

    frames = torch.zeros((1, 16, 16, 3), dtype=torch.float32)
    duration = 1.0 / frame_rate if frame_rate > 0 else 0.0
    debug["summary"]["status"] = "not_built"
    debug["summary"]["included_clip_count"] = 0
    debug["summary"]["output_frame_count"] = 0
    debug["summary"]["placeholder_output_frame_count"] = int(frames.shape[0])
    debug["resolution"]["policy"] = "placeholder_no_clips"
    debug["resolution"]["width"] = int(frames.shape[2])
    debug["resolution"]["height"] = int(frames.shape[1])
    debug["audio"]["status"] = "empty"
    debug["audio"]["diagnostics"].append("No accepted or imported video clips were present; returned a silent placeholder output.")
    _add_warning(
        debug,
        "SEQUENCE_ASSEMBLY_NO_CLIPS",
        "No accepted or imported video clips were available for assembly; returned a placeholder output.",
    )
    debug["summary"]["warning_count"] = len(debug["warnings"])
    debug["summary"]["error_count"] = len(debug["errors"])
    return frames, empty_audio(duration), frame_rate, debug


def _decode_sequence_clips(
    timeline: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    frame_rate: float,
    missing_take_policy: str,
    debug: dict[str, Any],
) -> list[dict[str, Any]]:
    from ..ltx.runtime.media import decode_video_frames, trim_video_source_frames

    shots = _sequence_shots(timeline)
    debug["summary"]["shot_count"] = len(shots)
    clip_entries = []
    for shot in shots:
        shot_id = str(shot.get("shot_id") or "")
        shot_debug = {
            "shot_id": shot_id,
            "type": shot.get("type"),
            "start_time": shot.get("start_time"),
            "end_time": shot.get("end_time"),
            "status": "pending",
        }
        try:
            source = _resolve_shot_source(shot, assets_by_id, missing_take_policy, debug)
        except SequenceAssemblyError:
            shot_debug["status"] = "error"
            debug["shots"].append(shot_debug)
            raise
        if source is None:
            shot_debug["status"] = "skipped"
            debug["shots"].append(shot_debug)
            continue

        asset = source["asset"]
        asset_id = str(asset.get("asset_id") or "")
        path = asset.get("path") or asset.get("file_path")
        if not path:
            _add_error(
                debug,
                "SEQUENCE_ASSEMBLY_ASSET_PATH_MISSING",
                f"Shot {shot_id} asset {asset_id} has no path.",
                {"shot_id": shot_id, "asset_id": asset_id},
            )
            shot_debug["status"] = "error"
            debug["shots"].append(shot_debug)
            raise SequenceAssemblyError(f"SEQUENCE_ASSEMBLY_ASSET_PATH_MISSING: Shot {shot_id} asset {asset_id} has no path.")
        try:
            decoded, source_fps, decoded_count = decode_video_frames(str(path))
        except FileNotFoundError:
            details = {
                "shot_id": shot_id,
                "asset_id": asset_id,
                "take_id": source.get("take_id"),
                "source_kind": source["source_kind"],
            }
            debug["summary"]["missing_source_media_count"] += 1
            if missing_take_policy == "error":
                _add_error(
                    debug,
                    "SEQUENCE_ASSEMBLY_SOURCE_MEDIA_MISSING",
                    f"Shot {shot_id} source media is missing on disk.",
                    details,
                )
                shot_debug["status"] = "error"
                debug["shots"].append(shot_debug)
                raise SequenceAssemblyError(
                    f"SEQUENCE_ASSEMBLY_SOURCE_MEDIA_MISSING: Shot {shot_id} source media is missing on disk."
                ) from None
            _add_warning(
                debug,
                "SEQUENCE_ASSEMBLY_SOURCE_MEDIA_MISSING",
                f"Shot {shot_id} source media is missing on disk; skipping.",
                details,
            )
            shot_debug.update(
                {
                    "status": "skipped",
                    "asset_id": asset_id,
                    "take_id": source.get("take_id"),
                    "source_kind": source["source_kind"],
                }
            )
            debug["shots"].append(shot_debug)
            continue
        media = {
            "item_id": shot_id,
            "source_in": source["clip_instance"].get("source_in"),
            "source_out": source["clip_instance"].get("source_out"),
        }
        trimmed, trim_debug = trim_video_source_frames(decoded, source_fps, media)
        target_frame_count = _shot_target_frame_count(shot, frame_rate, trimmed, source_fps)
        fitted = _fit_frames_to_count(trimmed, target_frame_count)
        clip_entry = {
            "shot_id": shot_id,
            "asset_id": asset_id,
            "take_id": source.get("take_id"),
            "source_kind": source["source_kind"],
            "path": str(path),
            "boundary_conditioning": _take_boundary_conditioning_from_take(source.get("take")),
            "frames": fitted,
            "source_fps": float(source_fps),
            "decoded_frame_count": int(decoded_count),
            "trimmed_frame_count": int(trimmed.shape[0]),
            "output_frame_count": int(fitted.shape[0]),
            "source_range": trim_debug.get("source_range"),
        }
        clip_entries.append(clip_entry)
        shot_debug.update(
            {
                "status": "included",
                "asset_id": asset_id,
                "take_id": source.get("take_id"),
                "source_kind": source["source_kind"],
                "output_frame_count": int(fitted.shape[0]),
            }
        )
        debug["shots"].append(shot_debug)
    return clip_entries


def _resolve_shot_source(
    shot: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    missing_take_policy: str,
    debug: dict[str, Any],
) -> dict[str, Any] | None:
    shot_id = str(shot.get("shot_id") or "")
    accepted_take = _accepted_take(shot)
    clip_instance = _clip_instance(shot)
    if accepted_take is not None:
        asset_id = accepted_take.get("asset_id")
        asset = _asset_for_id(assets_by_id, asset_id, shot_id, debug)
        if asset.get("type") != ASSET_TYPE_VIDEO:
            _add_warning(
                debug,
                "SEQUENCE_ASSEMBLY_TAKE_ASSET_NOT_VIDEO",
                f"Shot {shot_id} accepted take asset is not a video; skipping.",
                {"shot_id": shot_id, "asset_id": asset_id, "asset_type": asset.get("type")},
            )
            return None
        return {
            "source_kind": "accepted_take",
            "take_id": accepted_take.get("take_id"),
            "take": accepted_take,
            "asset": asset,
            "clip_instance": (
                clip_instance
                if clip_instance and clip_instance.get("asset_id") == asset_id
                else {"source_in": 0.0, "source_out": None}
            ),
        }
    if clip_instance and clip_instance.get("asset_id"):
        asset_id = clip_instance.get("asset_id")
        asset = _asset_for_id(assets_by_id, asset_id, shot_id, debug)
        if asset.get("type") != ASSET_TYPE_VIDEO:
            _add_warning(
                debug,
                "SEQUENCE_ASSEMBLY_CLIP_ASSET_NOT_VIDEO",
                f"Shot {shot_id} clip asset is not a video; skipping.",
                {"shot_id": shot_id, "asset_id": asset_id, "asset_type": asset.get("type")},
            )
            return None
        return {
            "source_kind": "imported_clip" if shot.get("type") == SHOT_TYPE_IMPORTED else "clip_instance",
            "take_id": None,
            "asset": asset,
            "clip_instance": clip_instance,
        }
    if missing_take_policy == "error":
        _add_error(
            debug,
            "SEQUENCE_ASSEMBLY_ACCEPTED_TAKE_MISSING",
            f"Shot {shot_id} has no accepted take or clip instance.",
            {"shot_id": shot_id},
        )
        raise SequenceAssemblyError(f"SEQUENCE_ASSEMBLY_ACCEPTED_TAKE_MISSING: Shot {shot_id} has no accepted take or clip instance.")
    debug["summary"]["missing_accepted_take_count"] += 1
    _add_warning(
        debug,
        "SEQUENCE_ASSEMBLY_ACCEPTED_TAKE_MISSING",
        f"Shot {shot_id} has no accepted take or clip instance; skipping.",
        {"shot_id": shot_id},
    )
    return None


def _asset_for_id(
    assets_by_id: dict[str, dict[str, Any]],
    asset_id: Any,
    shot_id: str,
    debug: dict[str, Any],
) -> dict[str, Any]:
    if asset_id is None or str(asset_id) not in assets_by_id:
        debug["summary"]["missing_asset_count"] += 1
        _add_error(
            debug,
            "SEQUENCE_ASSEMBLY_ASSET_NOT_FOUND",
            f"Shot {shot_id} references missing asset {asset_id}.",
            {"shot_id": shot_id, "asset_id": asset_id},
        )
        raise SequenceAssemblyError(f"SEQUENCE_ASSEMBLY_ASSET_NOT_FOUND: Shot {shot_id} references missing asset {asset_id}.")
    return assets_by_id[str(asset_id)]


def _assemble_clip_frames(
    clip_entries: list[dict[str, Any]],
    timeline: dict[str, Any],
    debug: dict[str, Any],
) -> torch.Tensor:
    from ..contracts.video_timeline import CROP_MODE_PAD
    from ..ltx.runtime.media import resize_image_frames

    target_height = int(clip_entries[0]["frames"].shape[1])
    target_width = int(clip_entries[0]["frames"].shape[2])
    debug["resolution"]["width"] = target_width
    debug["resolution"]["height"] = target_height
    for entry in clip_entries:
        frames = entry["frames"]
        if int(frames.shape[1]) == target_height and int(frames.shape[2]) == target_width:
            continue
        entry["frames"] = resize_image_frames(
            frames,
            target_width,
            target_height,
            CROP_MODE_PAD,
            1,
        )
        entry["resized"] = True
        debug["resolution"]["resized_clip_count"] += 1

    output = clip_entries[0]["frames"]
    clip_entries[0]["output_start_frame"] = 0
    clip_entries[0]["output_end_frame_exclusive"] = int(output.shape[0])
    previous_entry = clip_entries[0]
    boundaries_by_pair = _boundaries_by_pair(timeline)
    for entry in clip_entries[1:]:
        boundary = boundaries_by_pair.get((previous_entry["shot_id"], entry["shot_id"]))
        output_frame_count = int(output.shape[0])
        overlap_frames = _boundary_overlap_frames(output, entry["frames"], boundary)
        entry["output_start_frame"] = max(0, output_frame_count - overlap_frames)
        output = _append_with_boundary(output, entry["frames"], boundary, previous_entry, entry, debug)
        entry["output_end_frame_exclusive"] = entry["output_start_frame"] + int(entry["frames"].shape[0])
        previous_entry = entry
    return output


def _append_with_boundary(
    current: torch.Tensor,
    next_frames: torch.Tensor,
    boundary: dict[str, Any] | None,
    previous_entry: dict[str, Any],
    next_entry: dict[str, Any],
    debug: dict[str, Any],
) -> torch.Tensor:
    mode = boundary.get("mode") if isinstance(boundary, dict) else BOUNDARY_MODE_HARD_CUT
    boundary_debug = {
        "boundary_id": boundary.get("boundary_id") if isinstance(boundary, dict) else None,
        "left_shot_id": previous_entry["shot_id"],
        "right_shot_id": next_entry["shot_id"],
        "mode": mode,
        "status": "concatenated",
        "blend_frames": 0,
        "warnings": [],
    }
    if mode in {BOUNDARY_MODE_HARD_CUT, BOUNDARY_MODE_CONTINUOUS_SHOT}:
        debug["boundaries"].append(boundary_debug)
        return torch.cat((current, next_frames), dim=0)
    if mode == BOUNDARY_MODE_BLEND_SEAM:
        blend_frames = max(0, int((boundary or {}).get("blend_frames") or 0))
        boundary_debug["blend_frames"] = blend_frames
        if _can_blend(current, next_frames, blend_frames):
            blended = _blend_frames(current[-blend_frames:], next_frames[:blend_frames])
            boundary_debug["status"] = "blend_applied"
            debug["boundaries"].append(boundary_debug)
            return torch.cat((current[:-blend_frames], blended, next_frames[blend_frames:]), dim=0)
        boundary_debug["status"] = "blend_fallback_concatenate"
        boundary_debug["warnings"].append("Blend Seam requires matching frame shapes and enough frames on both sides.")
        _add_warning(
            debug,
            "SEQUENCE_ASSEMBLY_BLEND_FALLBACK",
            "Blend Seam fell back to concatenation.",
            {
                "left_shot_id": previous_entry["shot_id"],
                "right_shot_id": next_entry["shot_id"],
                "blend_frames": blend_frames,
            },
        )
        debug["boundaries"].append(boundary_debug)
        return torch.cat((current, next_frames), dim=0)
    if mode == BOUNDARY_MODE_TRANSITION:
        if _has_generated_transition_boundary(previous_entry, next_entry, boundary):
            boundary_debug["status"] = "transition_generated_bridge_concatenate"
            boundary_debug["boundary_conditioning"] = deepcopy(next_entry.get("boundary_conditioning") or {})
            debug["boundaries"].append(boundary_debug)
            return torch.cat((current, next_frames), dim=0)
        boundary_debug["status"] = "transition_fallback_concatenate"
        boundary_debug["warnings"].append("Transition assembly is deferred; used concatenation.")
        _add_warning(
            debug,
            "SEQUENCE_ASSEMBLY_TRANSITION_FALLBACK",
            "Transition boundary fell back to concatenation.",
            {
                "left_shot_id": previous_entry["shot_id"],
                "right_shot_id": next_entry["shot_id"],
            },
        )
        debug["boundaries"].append(boundary_debug)
        return torch.cat((current, next_frames), dim=0)
    boundary_debug["status"] = "unsupported_boundary_fallback_concatenate"
    boundary_debug["warnings"].append("Unsupported boundary mode; used concatenation.")
    debug["boundaries"].append(boundary_debug)
    return torch.cat((current, next_frames), dim=0)


def _assemble_audio(
    timeline: dict[str, Any],
    clip_entries: list[dict[str, Any]],
    frames: torch.Tensor,
    frame_rate: float,
    debug: dict[str, Any],
) -> dict[str, Any]:
    from ..ltx.runtime.audio import empty_audio, mix_timeline_audio

    duration = max(0.0, int(frames.shape[0]) / frame_rate if frame_rate > 0 else 0.0)
    source_audio_plan = _build_source_audio_plan(clip_entries, frame_rate, debug)
    timeline_audio_plan = _build_audio_plan(timeline, frame_rate)
    audio_plan = [*source_audio_plan, *timeline_audio_plan]
    debug["audio"]["timeline_clip_count"] = len(timeline_audio_plan)
    debug["audio"]["clip_count"] = len(audio_plan)
    if not audio_plan:
        debug["audio"]["status"] = "empty"
        if debug["audio"]["source_clip_count"] > 0:
            debug["audio"]["diagnostics"].append("No decodable source video audio or timeline audio clips were present; returned silent audio.")
        else:
            debug["audio"]["diagnostics"].append("No timeline audio clips were present; returned silent audio.")
        return empty_audio(duration)
    mix_plan = {
        "project": deepcopy(timeline.get("project") or {}),
        "resolved_output": {
            "duration_seconds": duration,
            "frame_count": int(frames.shape[0]),
            "frame_rate": frame_rate,
        },
        "audio_plan": audio_plan,
        "model_specific": {"ltx": {"config": {}}},
    }
    try:
        audio, diagnostics = mix_timeline_audio(mix_plan)
    except Exception as exc:
        debug["audio"]["status"] = "unsupported"
        debug["audio"]["diagnostics"].append(f"Timeline audio mix failed; returned silent audio: {exc}.")
        _add_warning(
            debug,
            "SEQUENCE_ASSEMBLY_AUDIO_MIX_FAILED",
            "Timeline audio mix failed; returned silent audio.",
        )
        return empty_audio(duration)
    debug["audio"]["status"] = "mixed"
    if debug["audio"]["source_mixed_clip_count"] > 0:
        debug["audio"]["diagnostics"].append(
            f"Mixed embedded source audio from {debug['audio']['source_mixed_clip_count']} assembled video clip(s)."
        )
    debug["audio"]["diagnostics"].extend(diagnostics)
    return audio


def _build_source_audio_plan(
    clip_entries: list[dict[str, Any]],
    frame_rate: float,
    debug: dict[str, Any],
) -> list[dict[str, Any]]:
    audio_entries = []
    debug["audio"]["source_clip_count"] = len(clip_entries)
    for entry in clip_entries:
        shot_id = str(entry.get("shot_id") or "")
        path = entry.get("path")
        if not path or not _source_audio_is_decodable(str(path)):
            debug["audio"]["source_skipped_clip_count"] += 1
            debug["audio"]["diagnostics"].append(
                f"Skipped source video audio for shot {shot_id or 'unknown'}; no decodable audio stream was found."
            )
            continue
        start_frame = max(0, int(entry.get("output_start_frame") or 0))
        end_frame = max(start_frame, int(entry.get("output_end_frame_exclusive") or start_frame))
        source_range = entry.get("source_range") if isinstance(entry.get("source_range"), dict) else {}
        audio_entries.append(
            {
                "track_id": "sequence_source_audio",
                "item_id": f"source_audio_{shot_id or len(audio_entries) + 1}",
                "asset_id": entry.get("asset_id"),
                "path": path,
                "start_frame": start_frame,
                "end_frame_exclusive": end_frame,
                "start_time": start_frame / frame_rate if frame_rate > 0 else 0.0,
                "end_time": end_frame / frame_rate if frame_rate > 0 else 0.0,
                "source_in": source_range.get("start", 0.0),
                "source_out": source_range.get("end"),
                "volume": 100.0,
                "fade_in": 0.0,
                "fade_out": 0.0,
                "enabled": True,
                "lane": None,
            }
        )
    debug["audio"]["source_mixed_clip_count"] = len(audio_entries)
    return audio_entries


def _build_audio_plan(timeline: dict[str, Any], frame_rate: float) -> list[dict[str, Any]]:
    assets_by_id = _assets_by_id(timeline)
    audio_entries = []
    for track in timeline.get("audio_tracks", []):
        for clip in track.get("clips", []):
            frame_range = time_range_to_frames(
                clip.get("start_time", 0.0),
                clip.get("end_time", 0.0),
                frame_rate,
            )
            asset = _resolve_asset_reference(clip.get("audio"), assets_by_id)
            audio_entries.append(
                {
                    "track_id": track.get("track_id"),
                    "item_id": clip.get("item_id"),
                    "asset_id": asset.get("asset_id") if asset else None,
                    "path": asset.get("path") if asset else None,
                    "start_frame": frame_range["start_frame"],
                    "end_frame_exclusive": frame_range["end_frame_exclusive"],
                    "start_time": clip.get("start_time"),
                    "end_time": clip.get("end_time"),
                    "source_in": clip.get("source_in"),
                    "source_out": clip.get("source_out"),
                    "volume": clip.get("volume"),
                    "fade_in": clip.get("fade_in"),
                    "fade_out": clip.get("fade_out"),
                    "enabled": clip.get("enabled"),
                    "lane": clip.get("lane"),
                }
            )
    return audio_entries


def _resolve_asset_reference(reference: Any, assets_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if isinstance(reference, dict):
        asset_id = reference.get("asset_id")
        if asset_id is not None:
            return assets_by_id.get(str(asset_id))
    return None


def _clip_debug_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in entry.items()
        if key not in {"frames", "path"}
    }


def _sequence_shots(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    shots = [
        shot
        for shot in timeline.get("sequence", {}).get("shots", [])
        if isinstance(shot, dict)
    ]
    return sorted(
        shots,
        key=lambda shot: (
            _safe_float(shot.get("start_time"), 0.0),
            _safe_float(shot.get("end_time"), 0.0),
            str(shot.get("shot_id") or ""),
        ),
    )


def _accepted_take(shot: dict[str, Any]) -> dict[str, Any] | None:
    accepted_take_id = shot.get("accepted_take_id")
    if accepted_take_id is None:
        return None
    for take in shot.get("takes", []):
        if isinstance(take, dict) and str(take.get("take_id") or "") == str(accepted_take_id):
            return take
    return None


def _clip_instance(shot: dict[str, Any]) -> dict[str, Any] | None:
    clip_instance = shot.get("clip_instance")
    if isinstance(clip_instance, dict) and clip_instance.get("enabled") is not False:
        return clip_instance
    return None


def _shot_target_frame_count(
    shot: dict[str, Any],
    frame_rate: float,
    frames: torch.Tensor,
    source_fps: float,
) -> int:
    start = _safe_float(shot.get("start_time"), 0.0)
    end = _safe_float(shot.get("end_time"), start)
    duration = max(0.0, end - start)
    if duration > 0 and frame_rate > 0:
        return max(1, int(round(duration * frame_rate)))
    source_fps = source_fps if source_fps > 0 else frame_rate
    return max(1, int(round(int(frames.shape[0]) / source_fps * frame_rate)))


def _fit_frames_to_count(frames: torch.Tensor, target_count: int) -> torch.Tensor:
    target_count = max(1, int(target_count or 1))
    source_count = int(frames.shape[0])
    if source_count == target_count:
        return frames
    if source_count <= 0:
        raise SequenceAssemblyError("SEQUENCE_ASSEMBLY_EMPTY_CLIP: Clip decoded to zero frames.")
    indices = torch.linspace(0, source_count - 1, steps=target_count).round().to(torch.long)
    indices = torch.clamp(indices, 0, source_count - 1)
    return frames[indices]


def _boundaries_by_pair(timeline: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    output = {}
    for boundary in timeline.get("sequence", {}).get("boundaries", []):
        if not isinstance(boundary, dict):
            continue
        left = boundary.get("left_shot_id")
        right = boundary.get("right_shot_id")
        if left is None or right is None:
            continue
        output[(str(left), str(right))] = boundary
    return output


def _boundary_overlap_frames(
    current: torch.Tensor,
    next_frames: torch.Tensor,
    boundary: dict[str, Any] | None,
) -> int:
    mode = boundary.get("mode") if isinstance(boundary, dict) else BOUNDARY_MODE_HARD_CUT
    if mode != BOUNDARY_MODE_BLEND_SEAM:
        return 0
    blend_frames = max(0, int((boundary or {}).get("blend_frames") or 0))
    return blend_frames if _can_blend(current, next_frames, blend_frames) else 0


def _take_boundary_conditioning_from_take(take: Any) -> dict[str, Any]:
    if not isinstance(take, dict):
        return {}
    metadata = take.get("metadata") if isinstance(take.get("metadata"), dict) else {}
    candidates = []
    model_specific = metadata.get("model_specific") if isinstance(metadata, dict) else None
    if isinstance(model_specific, dict):
        candidates.extend(value for value in model_specific.values() if isinstance(value, dict))
    direct_model_specific = take.get("model_specific") if isinstance(take.get("model_specific"), dict) else None
    if isinstance(direct_model_specific, dict):
        candidates.extend(value for value in direct_model_specific.values() if isinstance(value, dict))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        boundary = candidate.get("boundary_conditioning")
        if isinstance(boundary, dict):
            return deepcopy(boundary)
    return {}


def _has_generated_transition_boundary(
    previous_entry: dict[str, Any],
    next_entry: dict[str, Any],
    boundary: dict[str, Any] | None,
) -> bool:
    conditioning = next_entry.get("boundary_conditioning")
    if not isinstance(conditioning, dict):
        return False
    if conditioning.get("model_status") != "applied" and conditioning.get("status") != "applied":
        return False
    runtime_status = conditioning.get("runtime_status")
    if runtime_status is not None and runtime_status != "applied":
        return False
    if conditioning.get("policy") != "transition" and conditioning.get("mode") != BOUNDARY_MODE_TRANSITION:
        return False
    if isinstance(boundary, dict):
        boundary_id = boundary.get("boundary_id")
        if conditioning.get("boundary_id") and boundary_id and conditioning.get("boundary_id") != boundary_id:
            return False
    source_shot_id = conditioning.get("source_shot_id")
    target_shot_id = conditioning.get("target_shot_id")
    if source_shot_id and str(source_shot_id) != str(previous_entry.get("shot_id")):
        return False
    if target_shot_id and str(target_shot_id) != str(next_entry.get("shot_id")):
        return False
    return True


def _can_blend(current: torch.Tensor, next_frames: torch.Tensor, blend_frames: int) -> bool:
    return (
        blend_frames > 0
        and int(current.shape[0]) >= blend_frames
        and int(next_frames.shape[0]) >= blend_frames
        and tuple(current.shape[1:]) == tuple(next_frames.shape[1:])
    )


def _blend_frames(left: torch.Tensor, right: torch.Tensor) -> torch.Tensor:
    count = int(left.shape[0])
    weights = torch.linspace(
        1.0 / (count + 1),
        count / (count + 1),
        count,
        dtype=left.dtype,
        device=left.device,
    ).reshape((count,) + (1,) * (left.ndim - 1))
    return left * (1.0 - weights) + right * weights


def _source_audio_is_decodable(path: str) -> bool:
    import av

    from ..media_cache import resolve_media_path

    try:
        resolved = resolve_media_path(path)
        with av.open(str(resolved)) as container:
            stream = next((stream for stream in container.streams if stream.type == "audio"), None)
            if stream is None:
                return False
            for _frame in container.decode(stream):
                return True
    except Exception:
        return False
    return False


def _add_warning(
    debug: dict[str, Any],
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    debug["warnings"].append(
        {
            "code": code,
            "message": message,
            "details": deepcopy(details or {}),
        }
    )


def _add_error(
    debug: dict[str, Any],
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    debug["errors"].append(
        {
            "code": code,
            "message": message,
            "details": deepcopy(details or {}),
        }
    )


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
