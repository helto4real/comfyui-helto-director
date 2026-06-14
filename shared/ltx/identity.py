from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

import torch


log = logging.getLogger(__name__)
_TEN_S_MODULE_CACHE: dict[str, Any] = {}


def _load_10s_class(filename: str, class_name: str):
    cache_key = class_name
    if cache_key in _TEN_S_MODULE_CACHE:
        return _TEN_S_MODULE_CACHE[cache_key]

    try:
        if class_name == "LTXLatentAnchorAware":
            try:
                from ...vendor.tenstrip_10s.latent_anchor_aware import LTXLatentAnchorAware
            except ImportError:
                from vendor.tenstrip_10s.latent_anchor_aware import LTXLatentAnchorAware

            klass = LTXLatentAnchorAware
        elif class_name == "LTXFaceAttentionAnchor":
            try:
                from ...vendor.tenstrip_10s.face_anchor import LTXFaceAttentionAnchor
            except ImportError:
                from vendor.tenstrip_10s.face_anchor import LTXFaceAttentionAnchor

            klass = LTXFaceAttentionAnchor
        else:
            klass = None
    except Exception as exc:  # noqa: BLE001 - keep workflows loadable if an optional vendor patch fails.
        print(
            f"[Helto Director] LTX Timeline Identity Anchor: bundled {class_name} could not be loaded "
            f"from {filename}: {type(exc).__name__}: {exc}"
        )
        klass = None

    _TEN_S_MODULE_CACHE[cache_key] = klass
    return klass


def select_timeline_reference_image(guide_data: dict[str, Any], reference_label: str = "image1"):
    if not isinstance(guide_data, dict):
        raise ValueError("LTX Timeline reference image selector needs guide_data from an LTX Timeline Runtime node.")

    references = guide_data.get("reference_images") or []
    if not references:
        raise ValueError("LTX Timeline guide_data does not contain any reference images.")

    label = str(reference_label or "").strip().lower()
    if not label:
        entry = references[0]
    else:
        entry = next(
            (
                ref
                for ref in references
                if str(ref.get("label") or "").strip().lower() == label
                or str(ref.get("id") or "").strip().lower() == label
            ),
            None,
        )
        if entry is None:
            available = sorted(
                {
                    str(ref.get("label") or ref.get("id") or "").strip()
                    for ref in references
                    if str(ref.get("label") or ref.get("id") or "").strip()
                }
            )
            suffix = f" Available references: {', '.join(available)}." if available else ""
            raise ValueError(f"LTX Timeline reference image '{reference_label}' was not found.{suffix}")

    image = entry.get("image")
    if image is None:
        raise ValueError(f"LTX Timeline reference image '{entry.get('label') or reference_label}' has no loaded image tensor.")
    return image


def apply_identity_anchor(model, identity_anchor=None, sigmas=None, vae=None, guide_data=None):
    patched = model
    for anchor in _ordered_anchors(identity_anchor):
        kind = anchor.get("kind")
        if kind == "off" or anchor.get("bypass_all", False):
            continue
        if kind == "latent_aware":
            patched = _apply_latent_aware(patched, anchor, sigmas=sigmas, vae=vae, guide_data=guide_data)
        elif kind == "face":
            patched = _apply_face(patched, anchor)
        else:
            print(f"[Helto Director] LTX Timeline Identity Anchor: unknown anchor kind '{kind}'; bypassing.")
    return patched


def crop_latent_to_frame_count(latent, frame_count, hidden_reference_count: int = 0):
    try:
        metadata_target = int(frame_count)
    except (TypeError, ValueError):
        return latent
    try:
        hidden_reference_count = max(0, int(hidden_reference_count or 0))
    except (TypeError, ValueError):
        hidden_reference_count = 0
    if metadata_target <= 0 or not isinstance(latent, dict):
        return latent

    cropped = latent.copy()

    def tensor_frame_count(tensor):
        if not torch.is_tensor(tensor):
            return None
        if tensor.ndim == 5:
            return int(tensor.shape[2])
        if tensor.ndim in (3, 4):
            return int(tensor.shape[0])
        return None

    def crop_tensor(tensor):
        if not torch.is_tensor(tensor):
            return tensor
        current_frames = tensor_frame_count(tensor)
        if current_frames is None:
            return tensor
        keep_frames = min(target_frames, current_frames)
        if tensor.ndim == 5:
            try:
                return torch.narrow(tensor, 2, 0, keep_frames)
            except Exception:
                return tensor[:, :, :keep_frames, :, :]
        if tensor.ndim in (3, 4):
            try:
                return torch.narrow(tensor, 0, 0, keep_frames)
            except Exception:
                return tensor[:keep_frames]
        return tensor

    def first_video_stream(value):
        if getattr(value, "is_nested", False):
            try:
                streams = list(value.unbind())
            except Exception:
                return None
            return streams[0] if streams else None
        return value

    def crop_value(value):
        if getattr(value, "is_nested", False):
            try:
                streams = list(value.unbind())
            except Exception:
                return value
            if not streams:
                return value
            streams[0] = crop_tensor(streams[0])
            try:
                return type(value)(streams)
            except Exception:
                return value
        return crop_tensor(value)

    before_frames = tensor_frame_count(first_video_stream(cropped.get("samples")))
    candidate_targets = [metadata_target]
    extra_tail_latent_frames = 2 if hidden_reference_count > 0 else 0
    if hidden_reference_count > 0 and before_frames is not None:
        tail_count_target = before_frames - hidden_reference_count - extra_tail_latent_frames
        if tail_count_target > 0:
            candidate_targets.append(tail_count_target)
    target_frames = min(candidate_targets)

    if "samples" in cropped:
        cropped["samples"] = crop_value(cropped["samples"])
    if "noise_mask" in cropped:
        cropped["noise_mask"] = crop_value(cropped["noise_mask"])
    after_frames = tensor_frame_count(first_video_stream(cropped.get("samples")))
    if before_frames is not None and after_frames is not None:
        if before_frames <= target_frames:
            log.warning(
                "LTX Timeline Crop Reference Tail received %d latent frames; "
                "metadata_target=%d, hidden_reference_count=%d, extra_tail_latent_frames=%d, chosen_target=%d. "
                "No reference tail was removed.",
                before_frames,
                metadata_target,
                hidden_reference_count,
                extra_tail_latent_frames,
                target_frames,
            )
        else:
            log.info(
                "LTX Timeline Crop Reference Tail cropped video latent frames from %d to %d; "
                "metadata_target=%d, hidden_reference_count=%d, extra_tail_latent_frames=%d, chosen_target=%d.",
                before_frames,
                after_frames,
                metadata_target,
                hidden_reference_count,
                extra_tail_latent_frames,
                target_frames,
            )
    return cropped


def _first_guide_image(guide_data):
    if not isinstance(guide_data, dict):
        return None
    images = guide_data.get("images") or []
    return images[0] if images else None


def _scaled_anchor(anchor, strength_scale: float):
    if not isinstance(anchor, dict):
        return anchor
    scaled = deepcopy(anchor)
    if "strength" in scaled:
        scaled["strength"] = float(scaled["strength"]) * float(strength_scale)
    return scaled


def _ordered_anchors(identity_anchor):
    if identity_anchor is None:
        return []
    if isinstance(identity_anchor, dict) and identity_anchor.get("kind") == "combined":
        anchors = identity_anchor.get("anchors", [])
        if identity_anchor.get("scale_strengths", True):
            scale = identity_anchor.get("strength_scale", 0.75)
            anchors = [_scaled_anchor(anchor, scale) for anchor in anchors]
    else:
        anchors = [identity_anchor]

    anchors = [anchor for anchor in anchors if isinstance(anchor, dict)]
    order = {"latent_aware": 0, "face": 1}
    return sorted(anchors, key=lambda anchor: order.get(anchor.get("kind"), 99))


def _apply_latent_aware(model, anchor, sigmas=None, vae=None, guide_data=None):
    klass = _load_10s_class("latent_anchor_aware.py", "LTXLatentAnchorAware")
    if klass is None:
        print("[Helto Director] LTX Timeline Identity Anchor: bundled LTXLatentAnchorAware unavailable; bypassing.")
        return model

    energy_source = anchor.get("energy_source", "auto")
    reference_image = anchor.get("reference_image")
    energy_latent = anchor.get("energy_latent")

    if energy_source == "none":
        reference_image = None
        energy_latent = None
    elif energy_source == "reference_image":
        energy_latent = None
        if reference_image is None:
            print("[Helto Director] LTX Timeline Identity Anchor: reference_image selected but not connected.")
    elif energy_source == "energy_latent":
        reference_image = None
        if energy_latent is None:
            print("[Helto Director] LTX Timeline Identity Anchor: energy_latent selected but not connected.")
    elif energy_source == "first_guide_image":
        reference_image = _first_guide_image(guide_data)
        energy_latent = None
        if reference_image is None:
            print("[Helto Director] LTX Timeline Identity Anchor: first guide image selected but guide_data is empty.")
    else:
        if reference_image is not None:
            energy_latent = None
        elif energy_latent is not None:
            reference_image = None
        else:
            reference_image = _first_guide_image(guide_data)

    if reference_image is not None and vae is None:
        print(
            "[Helto Director] LTX Timeline Identity Anchor: reference image energy needs a VAE; "
            "10S will continue with energy modulation disabled."
        )

    return klass().patch(
        model,
        reference_image=reference_image,
        vae=vae,
        energy_latent=energy_latent,
        sigmas=sigmas,
        strength=anchor.get("strength", 0.10),
        cache_at_step=anchor.get("cache_at_step", 6),
        similarity_threshold=anchor.get("similarity_threshold", 0.50),
        decay_with_distance=anchor.get("decay_with_distance", 0.0),
        energy_threshold=anchor.get("energy_threshold", 0.30),
        bypass=anchor.get("bypass", False),
        debug=anchor.get("debug", False),
        advanced_mode=anchor.get("advanced_mode", False),
        cache_mode=anchor.get("cache_mode", "schedule"),
        forwards_per_step=anchor.get("forwards_per_step", 1),
        cache_warmup=anchor.get("cache_warmup", 144),
        anchor_frame=anchor.get("anchor_frame", 0),
        depth_curve=anchor.get("depth_curve", "flat"),
        block_index_filter=anchor.get("block_index_filter", ""),
    )[0]


def _apply_face(model, anchor):
    klass = _load_10s_class("face_anchor.py", "LTXFaceAttentionAnchor")
    if klass is None:
        print("[Helto Director] LTX Timeline Identity Anchor: bundled LTXFaceAttentionAnchor unavailable; bypassing.")
        return model

    return klass().patch(
        model,
        face_bbox_norm=anchor.get("face_bbox_norm", "0.35,0.10,0.65,0.50"),
        strength=anchor.get("strength", 0.10),
        inject_mode=anchor.get("inject_mode", "tracked"),
        anchor_frame=anchor.get("anchor_frame", 0),
        anchor_upsample=anchor.get("anchor_upsample", 2),
        track_threshold=anchor.get("track_threshold", 0.50),
        face_threshold=anchor.get("face_threshold", 0.30),
        identity_threshold=anchor.get("identity_threshold", 0.75),
        depth_curve=anchor.get("depth_curve", "flat"),
        spatial_prior=anchor.get("spatial_prior", 0.50),
        block_index_filter=anchor.get("block_index_filter", ""),
        bypass=anchor.get("bypass", False),
        debug=anchor.get("debug", False),
    )[0]
