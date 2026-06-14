from comfy_api.latest import io

from ...shared.contracts.socket_types import (
    DEBUG_INFO,
    GUIDE_DATA,
    IC_LORA_PARAMETERS,
    LTX_IDENTITY_ANCHOR,
    LTX_TIMELINE_PLAN,
)
from ...shared.ltx.runtime import build_ltx_runtime_outputs


class LTXTimelineRuntime(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineRuntime",
            display_name="LTX 2.3 Timeline Runtime",
            category="timeline/ltx",
            description="Materialize an LTX 2.3 timeline plan into ComfyUI runtime objects.",
            inputs=[
                io.Model.Input("model"),
                io.Clip.Input("clip"),
                io.Conditioning.Input("negative"),
                io.Vae.Input("vae"),
                LTX_TIMELINE_PLAN.Input(
                    "ltx_timeline_plan",
                    display_name="LTX_TIMELINE_PLAN",
                ),
                io.Latent.Input("optional_latent", optional=True),
                io.Vae.Input("audio_vae", display_name="Audio VAE", optional=True),
                LTX_IDENTITY_ANCHOR.Input("identity_anchor", display_name="LTX_IDENTITY_ANCHOR", optional=True),
                io.Sigmas.Input("sigmas", optional=True),
                IC_LORA_PARAMETERS.Input("iclora_parameters", display_name="IC_LORA_PARAMETERS", optional=True),
            ],
            outputs=[
                io.Model.Output("model", display_name="model"),
                io.Conditioning.Output("positive", display_name="positive"),
                io.Conditioning.Output("negative", display_name="negative"),
                io.Latent.Output("video_latent", display_name="video_latent"),
                io.Latent.Output("audio_latent", display_name="audio_latent"),
                io.Audio.Output("combined_audio", display_name="combined_audio"),
                GUIDE_DATA.Output("guide_data", display_name="guide_data"),
                io.Image.Output("source_video_images", display_name="source_video_images"),
                io.Audio.Output("source_video_audio", display_name="source_video_audio"),
                io.Float.Output("source_video_frame_rate", display_name="source_video_frame_rate"),
                io.Int.Output("source_video_frame_count", display_name="source_video_frame_count"),
                DEBUG_INFO.Output("runtime_debug", display_name="runtime_debug"),
            ],
        )

    @classmethod
    def execute(
        cls,
        model,
        clip,
        negative,
        vae,
        ltx_timeline_plan: dict,
        optional_latent=None,
        audio_vae=None,
        identity_anchor=None,
        sigmas=None,
        iclora_parameters=None,
    ) -> io.NodeOutput:
        return io.NodeOutput(
            *build_ltx_runtime_outputs(
                model=model,
                clip=clip,
                negative=negative,
                vae=vae,
                ltx_timeline_plan=ltx_timeline_plan,
                optional_latent=optional_latent,
                audio_vae=audio_vae,
                identity_anchor=identity_anchor,
                sigmas=sigmas,
                iclora_parameters=iclora_parameters,
            )
        )
