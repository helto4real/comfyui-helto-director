from __future__ import annotations

from comfy_api.latest import io

from ...shared.contracts.socket_types import GUIDE_DATA, LTX_IDENTITY_ANCHOR
from ...shared.ltx.identity import (
    apply_identity_anchor,
    crop_latent_to_frame_count,
    select_timeline_reference_image,
)


class LTXTimelineIdentityAnchorLatentAware(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineIdentityAnchorLatentAware",
            display_name="LTX 2.3 Timeline Identity Anchor: Latent Aware",
            category="timeline/ltx/identity",
            description="Configures optional 10S latent-aware identity anchoring for LTX Timeline workflows.",
            inputs=[
                io.Combo.Input(
                    "energy_source",
                    options=["auto", "none", "reference_image", "energy_latent", "first_guide_image"],
                    default="auto",
                    tooltip="Spatial energy source. Auto prefers reference image, then energy latent, then first Timeline guide image.",
                ),
                io.Image.Input("reference_image", optional=True, tooltip="Optional image used only for spatial energy weighting."),
                io.Latent.Input("energy_latent", optional=True, tooltip="Optional latent used only for spatial energy weighting."),
                io.Float.Input("strength", default=0.10, min=0.0, max=5.0, step=0.01),
                io.Int.Input("cache_at_step", default=6, min=0, max=100, step=1),
                io.Float.Input("similarity_threshold", default=0.50, min=0.0, max=1.0, step=0.01),
                io.Float.Input("decay_with_distance", default=0.0, min=0.0, max=1.0, step=0.05),
                io.Float.Input("energy_threshold", default=0.30, min=0.0, max=1.0, step=0.05),
                io.Int.Input("anchor_frame", default=0, min=0, max=256, step=1, optional=True),
                io.Boolean.Input("advanced_mode", default=False, optional=True),
                io.Combo.Input(
                    "cache_mode",
                    options=["schedule", "live_extraction", "manual_calls"],
                    default="schedule",
                    optional=True,
                ),
                io.Int.Input("forwards_per_step", default=1, min=1, max=8, step=1, optional=True),
                io.Int.Input("cache_warmup", default=144, min=0, max=5000, step=1, optional=True),
                io.Combo.Input(
                    "depth_curve",
                    options=["flat", "ramp_up", "ramp_down", "late_focus", "middle"],
                    default="flat",
                    optional=True,
                ),
                io.String.Input("block_index_filter", default="", optional=True),
                io.Boolean.Input("bypass", default=False, optional=True),
                io.Boolean.Input("debug", default=False, optional=True),
            ],
            outputs=[
                LTX_IDENTITY_ANCHOR.Output("identity_anchor", display_name="identity_anchor"),
            ],
        )

    @classmethod
    def execute(
        cls,
        energy_source="auto",
        reference_image=None,
        energy_latent=None,
        strength=0.10,
        cache_at_step=6,
        similarity_threshold=0.50,
        decay_with_distance=0.0,
        energy_threshold=0.30,
        anchor_frame=0,
        advanced_mode=False,
        cache_mode="schedule",
        forwards_per_step=1,
        cache_warmup=144,
        depth_curve="flat",
        block_index_filter="",
        bypass=False,
        debug=False,
    ) -> io.NodeOutput:
        return io.NodeOutput(
            {
                "kind": "latent_aware",
                "energy_source": energy_source,
                "reference_image": reference_image,
                "energy_latent": energy_latent,
                "strength": strength,
                "cache_at_step": cache_at_step,
                "similarity_threshold": similarity_threshold,
                "decay_with_distance": decay_with_distance,
                "energy_threshold": energy_threshold,
                "anchor_frame": anchor_frame,
                "advanced_mode": advanced_mode,
                "cache_mode": cache_mode,
                "forwards_per_step": forwards_per_step,
                "cache_warmup": cache_warmup,
                "depth_curve": depth_curve,
                "block_index_filter": block_index_filter,
                "bypass": bypass,
                "debug": debug,
            }
        )


class LTXTimelineIdentityAnchorFace(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineIdentityAnchorFace",
            display_name="LTX 2.3 Timeline Identity Anchor: Face",
            category="timeline/ltx/identity",
            description="Configures optional 10S face-region identity anchoring for LTX Timeline workflows.",
            inputs=[
                io.String.Input("face_bbox_norm", default="0.35,0.10,0.65,0.50"),
                io.Float.Input("strength", default=0.10, min=0.0, max=5.0, step=0.01),
                io.Combo.Input("inject_mode", options=["tracked", "tracked_correction"], default="tracked"),
                io.Int.Input("anchor_frame", default=0, min=0, max=256, step=1),
                io.Int.Input("anchor_upsample", default=2, min=1, max=4, step=1),
                io.Float.Input("track_threshold", default=0.50, min=0.0, max=1.0, step=0.01),
                io.Float.Input("face_threshold", default=0.30, min=0.0, max=1.0, step=0.01),
                io.Float.Input("identity_threshold", default=0.75, min=0.0, max=1.0, step=0.01),
                io.Combo.Input(
                    "depth_curve",
                    options=["flat", "ramp_up", "ramp_down", "late_focus", "middle"],
                    default="flat",
                ),
                io.Float.Input("spatial_prior", default=0.50, min=0.0, max=1.0, step=0.05),
                io.String.Input("block_index_filter", default="", optional=True),
                io.Boolean.Input("bypass", default=False, optional=True),
                io.Boolean.Input("debug", default=False, optional=True),
            ],
            outputs=[
                LTX_IDENTITY_ANCHOR.Output("identity_anchor", display_name="identity_anchor"),
            ],
        )

    @classmethod
    def execute(
        cls,
        face_bbox_norm="0.35,0.10,0.65,0.50",
        strength=0.10,
        inject_mode="tracked",
        anchor_frame=0,
        anchor_upsample=2,
        track_threshold=0.50,
        face_threshold=0.30,
        identity_threshold=0.75,
        depth_curve="flat",
        spatial_prior=0.50,
        block_index_filter="",
        bypass=False,
        debug=False,
    ) -> io.NodeOutput:
        return io.NodeOutput(
            {
                "kind": "face",
                "face_bbox_norm": face_bbox_norm,
                "strength": strength,
                "inject_mode": inject_mode,
                "anchor_frame": anchor_frame,
                "anchor_upsample": anchor_upsample,
                "track_threshold": track_threshold,
                "face_threshold": face_threshold,
                "identity_threshold": identity_threshold,
                "depth_curve": depth_curve,
                "spatial_prior": spatial_prior,
                "block_index_filter": block_index_filter,
                "bypass": bypass,
                "debug": debug,
            }
        )


class LTXTimelineIdentityAnchorCombine(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineIdentityAnchorCombine",
            display_name="LTX 2.3 Timeline Identity Anchor: Combine",
            category="timeline/ltx/identity",
            description="Combines latent-aware and face identity anchor configs for one adapter input.",
            inputs=[
                LTX_IDENTITY_ANCHOR.Input("anchor_a", optional=True),
                LTX_IDENTITY_ANCHOR.Input("anchor_b", optional=True),
                io.Boolean.Input(
                    "scale_strengths",
                    default=True,
                    tooltip="Reduce both strengths when combining to avoid over-constraining motion.",
                ),
                io.Float.Input("strength_scale", default=0.75, min=0.0, max=1.0, step=0.05),
            ],
            outputs=[
                LTX_IDENTITY_ANCHOR.Output("identity_anchor", display_name="identity_anchor"),
            ],
        )

    @classmethod
    def execute(cls, anchor_a=None, anchor_b=None, scale_strengths=True, strength_scale=0.75) -> io.NodeOutput:
        anchors = [anchor for anchor in (anchor_a, anchor_b) if isinstance(anchor, dict)]
        return io.NodeOutput(
            {
                "kind": "combined",
                "anchors": anchors,
                "scale_strengths": scale_strengths,
                "strength_scale": strength_scale,
            }
        )


class LTXTimelineReferenceImageSelector(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineReferenceImageSelector",
            display_name="LTX 2.3 Timeline Reference Image Selector",
            category="timeline/ltx/identity",
            description=(
                "Selects a reference image from LTX Timeline guide_data so it can be connected "
                "to LTX 2.3 Timeline Identity Anchor: Latent Aware.reference_image."
            ),
            inputs=[
                GUIDE_DATA.Input("guide_data", tooltip="Guide data produced by LTX 2.3 Timeline Runtime."),
                io.String.Input(
                    "reference_label",
                    default="image1",
                    tooltip="Timeline reference label or id, for example image1. Leave blank to use the first reference.",
                ),
            ],
            outputs=[
                io.Image.Output("reference_image", display_name="reference_image"),
            ],
        )

    @classmethod
    def execute(cls, guide_data, reference_label="image1") -> io.NodeOutput:
        return io.NodeOutput(select_timeline_reference_image(guide_data, reference_label))


class LTXTimelineApplyIdentityAnchor(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineApplyIdentityAnchor",
            display_name="LTX 2.3 Timeline Apply Identity Anchor",
            category="timeline/ltx/identity",
            description="Optionally applies a configured 10S identity anchor to an LTX Timeline model.",
            inputs=[
                io.Model.Input("model"),
                LTX_IDENTITY_ANCHOR.Input("identity_anchor", optional=True),
                GUIDE_DATA.Input("guide_data", optional=True, tooltip="Optional Timeline guide data for first-guide-image energy."),
                io.Sigmas.Input("sigmas", optional=True, tooltip="Optional sampler sigmas for predictable latent-aware cache timing."),
                io.Vae.Input("vae", optional=True, tooltip="Optional VAE required when latent-aware uses reference image energy."),
            ],
            outputs=[
                io.Model.Output("model", display_name="model"),
            ],
        )

    @classmethod
    def execute(cls, model, identity_anchor=None, guide_data=None, sigmas=None, vae=None) -> io.NodeOutput:
        if identity_anchor is None:
            return io.NodeOutput(model)
        return io.NodeOutput(
            apply_identity_anchor(
                model,
                identity_anchor=identity_anchor,
                sigmas=sigmas,
                vae=vae,
                guide_data=guide_data,
            )
        )


class LTXTimelineCropReferenceTail(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineCropReferenceTail",
            display_name="LTX 2.3 Timeline Crop Reference Tail",
            category="timeline/ltx/identity",
            description=(
                "Crops hidden character reference tail frames from a sampled LTX Timeline latent. "
                "Connect the latent after sampling and guide_data from LTX 2.3 Timeline Runtime."
            ),
            inputs=[
                io.Latent.Input("latent", tooltip="Sampled latent to crop back to the visible Timeline duration."),
                GUIDE_DATA.Input("guide_data", tooltip="Guide data produced by LTX 2.3 Timeline Runtime."),
            ],
            outputs=[
                io.Latent.Output("latent", display_name="latent", tooltip="Latent cropped to the visible Timeline duration."),
                io.Int.Output("clean_pixel_frames", display_name="clean_pixel_frames", tooltip="Visible pixel-frame count for downstream video output."),
            ],
        )

    @classmethod
    def execute(cls, latent, guide_data) -> io.NodeOutput:
        clean_latent_frames = None
        clean_pixel_frames = 0
        hidden_reference_count = 0
        if isinstance(guide_data, dict):
            clean_latent_frames = guide_data.get("clean_latent_frames")
            try:
                hidden_reference_count = int(guide_data.get("hidden_reference_count") or 0)
            except (TypeError, ValueError):
                hidden_reference_count = 0
            try:
                clean_pixel_frames = int(guide_data.get("clean_pixel_frames") or 0)
            except (TypeError, ValueError):
                clean_pixel_frames = 0

        if clean_latent_frames is None:
            return io.NodeOutput(latent, clean_pixel_frames)

        return io.NodeOutput(
            crop_latent_to_frame_count(latent, clean_latent_frames, hidden_reference_count),
            clean_pixel_frames,
        )
