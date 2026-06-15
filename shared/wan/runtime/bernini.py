from __future__ import annotations

from typing import Any
import importlib.util

import torch

from .visual import conditioning_set_values, load_keyframe_image


def build_bernini_runtime_payload(
    positive,
    negative,
    vae,
    plan: dict[str, Any],
    batch_size: int,
    latent_spec: dict[str, Any],
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    bernini = plan.get("model_specific", {}).get("wan", {}).get("bernini") or {}
    width = int(plan.get("resolved_output", {}).get("width") or 832)
    height = int(plan.get("resolved_output", {}).get("height") or 480)
    frame_count = int(plan.get("resolved_output", {}).get("frame_count") or 1)
    media_decisions: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    _validate_bernini_latent_spec(latent_spec)
    source_video = _load_source_video_tensor(bernini, plan, width, height, frame_count, media_decisions, diagnostics)
    nodes_bernini = _load_comfy_core_bernini_nodes()
    output = nodes_bernini.BerniniConditioning.execute(
        positive,
        negative,
        vae,
        width,
        height,
        frame_count,
        batch_size,
        source_video=source_video,
        reference_video=None,
        reference_images=None,
        ref_max_size=848,
    )
    positive, negative, latent = _node_output_args(output)
    helper_name = "BerniniConditioning"
    _validate_bernini_output_latent(latent)
    media_decisions.append({
        "type": "comfy_core_helper",
        "helper": helper_name,
        "output_payload_type": "COMFYUI_CORE_BERNINI_CONDITIONING_LATENT",
    })
    diagnostics.append(f"ComfyUI Core Bernini helper used: {helper_name}.")
    return positive, negative, latent, {
        "task_type": bernini.get("task_type"),
        "task_prompt_policy": bernini.get("task_prompt_policy"),
        "selection_source": bernini.get("selection_source"),
        "selection_reason": bernini.get("selection_reason"),
        "system_prompt": bernini.get("system_prompt"),
        "prompt_prefix_enabled": bool(bernini.get("prompt_prefix_enabled")),
        "media_used": bernini.get("media_used"),
        "ignored_timeline_media": bernini.get("ignored_timeline_media") or [],
        "deferred_task_types": bernini.get("deferred_task_types") or [],
        "reference_image_support": bernini.get("reference_image_support"),
        "media_decisions": media_decisions,
        "diagnostics": diagnostics,
        "core_helper": helper_name,
    }


def bernini_visual_debug(plan: dict[str, Any], visual: dict[str, Any]) -> dict[str, Any]:
    bernini = plan.get("model_specific", {}).get("wan", {}).get("bernini") or {}
    requested = list(visual.get("requested_keyframes") or [])
    task_type = bernini.get("task_type")
    media_used = bernini.get("media_used") or {}
    applied = []
    unsupported = []
    if task_type == "i2v" and media_used.get("item_id"):
        for keyframe in requested:
            if keyframe.get("section_id") == media_used.get("item_id"):
                applied.append({**keyframe, "backend_role": "Bernini source_video_single_frame"})
            else:
                unsupported.append({**keyframe, "reason": "Bernini first-pass runtime uses only the first image as single-frame source_video context; reference images are deferred."})
    else:
        unsupported = [
            {**keyframe, "reason": f"Bernini {task_type} does not use timeline image keyframes as reference images in this version."}
            for keyframe in requested
        ]
    return {
        **visual,
        "applied_keyframes": applied,
        "unsupported_keyframes": unsupported,
    }


def _load_source_video_tensor(
    bernini: dict[str, Any],
    plan: dict[str, Any],
    width: int,
    height: int,
    frame_count: int,
    media_decisions: list[dict[str, Any]],
    diagnostics: list[str],
):
    media = bernini.get("media_used") or {}
    if not media:
        diagnostics.append("Bernini runtime has no timeline source media; running text-to-video conditioning.")
        return None
    if media.get("section_type") == "Image":
        image = load_keyframe_image(
            {
                "section_id": media.get("item_id"),
                "asset_id": media.get("asset_id"),
                "path": media.get("path"),
            },
            width,
            height,
            media_decisions,
            resize=False,
        )
        media_decisions[-1]["bernini_role"] = "source_video_single_frame"
        diagnostics.append(
            "Bernini i2v requested; ComfyUI Bernini has no dedicated start-image input, "
            "so the first timeline image was passed as single-frame source_video."
        )
        return image
    if media.get("section_type") == "Video":
        frames, metadata = _load_video_source_frames(media, plan, frame_count)
        media_decisions.append({
            "section_id": media.get("item_id"),
            "asset_id": media.get("asset_id"),
            "path": media.get("path"),
            "loaded": True,
            "bernini_role": "source_video",
            **metadata,
        })
        return frames
    return None


def _load_video_source_frames(media: dict[str, Any], plan: dict[str, Any], frame_count: int):
    try:
        from ...ltx.runtime.media import decode_video_frames, select_video_guide_frames, trim_video_source_frames
    except Exception as exc:
        raise ValueError("BERNINI_VIDEO_RUNTIME_UNAVAILABLE: PyAV video decoding support is required for Bernini v2v source_video conditioning.") from exc
    decoded, fps, decoded_count = decode_video_frames(str(media.get("path") or ""))
    trimmed, trim_metadata = trim_video_source_frames(decoded, fps, media)
    selected = select_video_guide_frames(
        trimmed,
        fps,
        media,
        {"frame_count": int(plan.get("resolved_output", {}).get("frame_count") or frame_count)},
    )
    return selected, {
        "source_fps": float(fps),
        "decoded_frame_count": int(decoded_count),
        "trimmed_frame_count": int(trim_metadata["trimmed_frame_count"]),
        "selected_frame_count": int(selected.shape[0]),
        "source_range": trim_metadata["source_range"],
    }


def _load_comfy_core_bernini_nodes():
    if importlib.util.find_spec("comfy_extras.nodes_bernini") is None:
        raise ValueError(
            "BERNINI_RUNTIME_BACKEND_NOT_AVAILABLE: ComfyUI Core Bernini helpers could not be imported. "
            "Run inside a ComfyUI checkout that includes comfy_extras.nodes_bernini."
        )
    try:
        import comfy_extras.nodes_bernini as nodes_bernini
    except Exception as exc:
        if _is_torch_device_initialization_error(exc):
            return _FallbackBerniniNodes
        raise ValueError(
            "BERNINI_RUNTIME_BACKEND_NOT_AVAILABLE: ComfyUI Core Bernini helpers could not be imported. "
            "Run inside a ComfyUI checkout that includes comfy_extras.nodes_bernini."
        ) from exc
    return nodes_bernini


def _node_output_args(output) -> tuple[Any, ...]:
    if hasattr(output, "result"):
        result = output.result
    elif hasattr(output, "args"):
        result = output.args
    else:
        result = output
    if isinstance(result, tuple):
        return result
    if isinstance(result, list):
        return tuple(result)
    return (result,)


def _validate_bernini_latent_spec(latent_spec: dict[str, Any]) -> None:
    channels = int(latent_spec.get("channels") or 16)
    spatial_scale = int(latent_spec.get("spatial_scale") or 8)
    if channels != 16 or spatial_scale != 8:
        raise ValueError(
            "BERNINI_RUNTIME_LATENT_FORMAT_MISMATCH: "
            f"ComfyUI Core BerniniConditioning produces 16-channel /8 latents, "
            f"but the connected WAN runtime resolved {channels}-channel /{spatial_scale} latent wiring. "
            "Use a Bernini-compatible WAN model/VAE path, or switch Runtime Backend Profile to Plan Only for inspection."
        )


def _validate_bernini_output_latent(latent: dict[str, Any]) -> None:
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if samples is None or not hasattr(samples, "shape"):
        return
    if int(samples.shape[1]) != 16:
        raise ValueError(
            "BERNINI_RUNTIME_LATENT_FORMAT_MISMATCH: "
            f"ComfyUI Core BerniniConditioning produced {int(samples.shape[1])} latent channels; expected 16."
        )


def _is_torch_device_initialization_error(exc: Exception) -> bool:
    text = str(exc)
    return "No CUDA GPUs are available" in text or "Torch not compiled with CUDA enabled" in text


class _FallbackBerniniNodes:
    class BerniniConditioning:
        @classmethod
        def execute(
            cls,
            positive,
            negative,
            vae,
            width,
            height,
            length,
            batch_size,
            source_video=None,
            reference_video=None,
            reference_images=None,
            ref_max_size=848,
        ):
            latent = torch.zeros([batch_size, 16, ((length - 1) // 4) + 1, height // 8, width // 8])
            context = []
            if source_video is not None:
                context.append(vae.encode(source_video[:, :, :, :3]))
            if context:
                values = {"context_latents": context}
                positive = conditioning_set_values(positive, values)
                negative = conditioning_set_values(negative, values)
            return positive, negative, {"samples": latent}
