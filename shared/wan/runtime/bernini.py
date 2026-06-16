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
    prompt_debug: dict[str, Any] | None = None,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    bernini = plan.get("model_specific", {}).get("wan", {}).get("bernini") or {}
    width = int(plan.get("resolved_output", {}).get("width") or 832)
    height = int(plan.get("resolved_output", {}).get("height") or 480)
    frame_count = int(plan.get("resolved_output", {}).get("frame_count") or 1)
    media_decisions: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    _validate_bernini_latent_spec(latent_spec)
    source_video = _load_source_video_tensor(bernini, plan, width, height, frame_count, media_decisions, diagnostics)
    reference_images = _load_reference_image_tensors(bernini, media_decisions, diagnostics)
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
        reference_images=reference_images,
        ref_max_size=848,
    )
    positive, negative, latent = _node_output_args(output)
    helper_name = "BerniniConditioning"
    _validate_bernini_output_latent(latent)
    source_video_debug = _source_video_debug(source_video)
    reference_images_debug = _reference_images_debug(reference_images)
    context_debug = _context_latents_debug(positive)
    conditioning_prompt_debug = _positive_prompt_debug(positive, bernini, prompt_debug)
    media_decisions.append({
        "type": "bernini_conditioning_debug",
        "bernini_role": "conditioning_debug",
        "source_video": source_video_debug,
        "reference_images": reference_images_debug,
        "context_latents": context_debug,
        "positive_prompt": conditioning_prompt_debug,
        "task_type": bernini.get("task_type"),
        "system_prompt": bernini.get("system_prompt"),
    })
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
        "reference_image_count": len(reference_images or {}),
        "character_references": bernini.get("character_references") or {},
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
    if task_type in {"i2v", "rv2v"} and media_used.get("item_id"):
        for keyframe in requested:
            if keyframe.get("section_id") == media_used.get("item_id"):
                applied.append({**keyframe, "backend_role": "Bernini source_video_single_frame"})
            else:
                unsupported.append({**keyframe, "reason": "Bernini uses only the first timeline image as source/background context; subject references come from Director character references."})
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
        media_decisions[-1]["source_video_frame_count"] = int(image.shape[0])
        _annotate_source_video_aspect(media_decisions[-1], width, height, diagnostics)
        diagnostics.append(
            f"Bernini {bernini.get('task_type') or 'i2v'} requested; the first timeline image was passed as single-frame source_video background context."
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
            "source_video_frame_count": int(frames.shape[0]),
            "tensor_shape": _tensor_shape(frames),
            "tensor_stats": _tensor_stats(frames),
            **metadata,
        })
        return frames
    return None


def _load_reference_image_tensors(
    bernini: dict[str, Any],
    media_decisions: list[dict[str, Any]],
    diagnostics: list[str],
) -> dict[str, Any] | None:
    references = bernini.get("character_references") or {}
    specs = references.get("reference_specs") if isinstance(references, dict) else []
    if not specs:
        return None
    output: dict[str, Any] = {}
    for index, spec in enumerate(specs):
        image = spec.get("image") if isinstance(spec, dict) else None
        path = image.get("path") if isinstance(image, dict) else None
        label = spec.get("label") or spec.get("id") or f"reference_{index}"
        if not path:
            raise ValueError(f"BERNINI_CHARACTER_REFERENCE_MISSING_IMAGE: Bernini character reference '{label}' is missing an image path.")
        try:
            tensor = load_keyframe_image(
                {
                    "section_id": spec.get("section_id"),
                    "asset_id": spec.get("id"),
                    "path": path,
                },
                1,
                1,
                media_decisions,
                resize=False,
            )
        except ValueError as exc:
            raise ValueError(f"BERNINI_CHARACTER_REFERENCE_LOAD_FAILED: Could not load Bernini character reference '{label}' from '{path}': {exc}") from exc
        key = f"reference_image_{index}"
        output[key] = tensor
        media_decisions[-1]["bernini_role"] = "subject_reference_image"
        media_decisions[-1]["reference_key"] = key
        media_decisions[-1]["label"] = label
        media_decisions[-1]["reference_id"] = spec.get("id")
        media_decisions[-1]["description"] = spec.get("description") or ""
        media_decisions[-1]["strength"] = spec.get("strength")
        media_decisions[-1]["tokens"] = list(spec.get("tokens") or [])
    diagnostics.append(f"Bernini loaded {len(output)} Director character reference image(s) as subject reference_images.")
    return output


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


def _source_video_debug(source_video) -> dict[str, Any]:
    if source_video is None:
        return {
            "present": False,
            "frame_count": 0,
            "tensor_shape": None,
            "tensor_stats": None,
        }
    return {
        "present": True,
        "frame_count": int(source_video.shape[0]),
        "tensor_shape": _tensor_shape(source_video),
        "tensor_stats": _tensor_stats(source_video),
    }


def _reference_images_debug(reference_images) -> dict[str, Any]:
    if not reference_images:
        return {
            "present": False,
            "count": 0,
            "keys": [],
            "tensor_shapes": [],
        }
    keys = sorted(reference_images)
    return {
        "present": True,
        "count": len(keys),
        "keys": keys,
        "tensor_shapes": [_tensor_shape(reference_images[key]) for key in keys if hasattr(reference_images[key], "shape")],
    }


def _context_latents_debug(conditioning) -> dict[str, Any]:
    latents = _conditioning_metadata_value(conditioning, "context_latents") or []
    return {
        "count": len(latents),
        "shapes": [_tensor_shape(latent) for latent in latents if hasattr(latent, "shape")],
    }


def _positive_prompt_debug(
    conditioning,
    bernini: dict[str, Any],
    prompt_debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = str(_conditioning_metadata_value(conditioning, "prompt") or "")
    source = "conditioning_metadata"
    if not prompt and prompt_debug:
        prompt = str(prompt_debug.get("full_prompt") or "")
        source = "runtime_prompt_debug"
    system_prompt = str(bernini.get("system_prompt") or "")
    return {
        "present": bool(prompt.strip()),
        "starts_with_system_prompt": bool(system_prompt and prompt.startswith(system_prompt)),
        "length": len(prompt),
        "preview": prompt[:240],
        "source": source if prompt else "unavailable",
        "prompt_relay_status": (prompt_debug or {}).get("status"),
        "prompt_relay_patched": bool((prompt_debug or {}).get("patched")),
    }


def _conditioning_metadata_value(conditioning, key: str):
    for _tensor, metadata in conditioning or []:
        if isinstance(metadata, dict) and key in metadata:
            return metadata[key]
    return None


def _tensor_shape(tensor) -> list[int]:
    return [int(dim) for dim in tensor.shape]


def _tensor_stats(tensor) -> dict[str, float]:
    detached = tensor.detach().float()
    return {
        "min": float(detached.min().item()),
        "max": float(detached.max().item()),
        "mean": float(detached.mean().item()),
    }


def _annotate_source_video_aspect(
    decision: dict[str, Any],
    width: int,
    height: int,
    diagnostics: list[str],
) -> None:
    source_width, source_height = _source_image_dimensions(decision)
    if not source_width or not source_height:
        return
    source_aspect = source_width / source_height
    target_aspect = width / height
    delta = abs(source_aspect - target_aspect)
    decision["source_aspect_ratio"] = round(source_aspect, 6)
    decision["target_aspect_ratio"] = round(target_aspect, 6)
    decision["aspect_ratio_delta"] = round(delta, 6)
    decision["aspect_mismatch"] = delta > 0.02
    decision["comfy_source_video_resize"] = "common_upscale(area, center) to output width/height before VAE encoding"
    if decision["aspect_mismatch"]:
        diagnostics.append(
            "Bernini source_video aspect ratio does not match the output canvas; "
            "ComfyUI will center-crop and resize the source before VAE encoding."
        )


def _source_image_dimensions(decision: dict[str, Any]) -> tuple[int | None, int | None]:
    size = decision.get("exif_transposed_size") or decision.get("original_size") or []
    if isinstance(size, (list, tuple)) and len(size) >= 2:
        return int(size[0]), int(size[1])
    shape = decision.get("tensor_shape") or []
    if isinstance(shape, (list, tuple)) and len(shape) >= 3:
        return int(shape[2]), int(shape[1])
    return None, None


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
            if reference_images:
                for name in sorted(reference_images):
                    imgs = reference_images[name]
                    if imgs is None:
                        continue
                    for i in range(imgs.shape[0]):
                        context.append(vae.encode(imgs[i:i + 1, :, :, :3]))
            if context:
                values = {"context_latents": context}
                positive = conditioning_set_values(positive, values)
                negative = conditioning_set_values(negative, values)
            return positive, negative, {"samples": latent}
