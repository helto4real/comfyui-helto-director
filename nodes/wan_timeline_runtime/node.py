from comfy_api.latest import io

from ...shared.contracts.socket_types import (
    DEBUG_INFO,
    WAN_TIMELINE_PLAN,
)
from ...shared.wan.runtime import build_wan_runtime_outputs


class WANTimelineRuntime(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoWAN22TimelineRuntime",
            display_name="WAN 2.2 Timeline Runtime",
            category="timeline/wan",
            description="Materialize a WAN 2.2 timeline plan into ComfyUI runtime conditioning objects.",
            inputs=[
                io.Model.Input("high_noise_model", display_name="High Noise Model", optional=True),
                io.Model.Input("low_noise_model", display_name="Low Noise Model", optional=True),
                io.Clip.Input("clip", optional=True),
                io.Vae.Input("vae", optional=True),
                WAN_TIMELINE_PLAN.Input(
                    "wan_timeline_plan",
                    display_name="WAN_TIMELINE_PLAN",
                ),
                io.Conditioning.Input("negative", optional=True),
                io.Int.Input("batch_size", display_name="Batch Size", default=1, min=1, max=4096, step=1, socketless=True),
            ],
            outputs=[
                io.Model.Output("high_noise_model", display_name="high_noise_model"),
                io.Model.Output("low_noise_model", display_name="low_noise_model"),
                io.Conditioning.Output("positive", display_name="positive"),
                io.Conditioning.Output("negative", display_name="negative"),
                io.Latent.Output("video_latent", display_name="video_latent"),
                DEBUG_INFO.Output("runtime_debug", display_name="runtime_debug"),
            ],
        )

    @classmethod
    def execute(
        cls,
        high_noise_model=None,
        low_noise_model=None,
        clip=None,
        vae=None,
        wan_timeline_plan: dict | None = None,
        negative=None,
        batch_size: int = 1,
    ) -> io.NodeOutput:
        return io.NodeOutput(
            *build_wan_runtime_outputs(
                high_noise_model=high_noise_model,
                low_noise_model=low_noise_model,
                clip=clip,
                vae=vae,
                wan_timeline_plan=wan_timeline_plan,
                negative=negative,
                batch_size=batch_size,
            )
        )
