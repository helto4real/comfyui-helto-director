from comfy_api.latest import io

from ...shared.contracts.socket_types import LTX_TIMELINE_CONFIG
from ...shared.ltx.config import (
    AUDIO_MODES,
    IMAGE_GUIDANCE_MODES,
    REFERENCE_MODES,
    RESOLUTION_PROFILE_AUTO,
    RESOLUTION_PROFILES,
    VIDEO_SECTION_MODES,
    create_ltx_timeline_config,
)


class LTXTimelineConfig(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineConfig",
            display_name="LTX 2.3 Timeline Config",
            category="timeline/ltx",
            description="Model-specific LTX 2.3 timeline planning policy.",
            inputs=[
                io.Combo.Input(
                    "resolution_profile",
                    display_name="Resolution Profile",
                    options=list(RESOLUTION_PROFILES),
                    default=RESOLUTION_PROFILE_AUTO,
                    socketless=True,
                ),
                io.Float.Input(
                    "prompt_relay_epsilon",
                    display_name="Prompt Relay Epsilon",
                    default=0.15,
                    min=0.0,
                    max=2.0,
                    step=0.01,
                    round=0.001,
                    display_mode=io.NumberDisplay.slider,
                    socketless=True,
                ),
                io.Combo.Input(
                    "image_guidance_mode",
                    display_name="Image Guidance Mode",
                    options=list(IMAGE_GUIDANCE_MODES),
                    default="Section Guides",
                    socketless=True,
                ),
                io.Combo.Input(
                    "video_section_mode",
                    display_name="Video Section Mode",
                    options=list(VIDEO_SECTION_MODES),
                    default="Source Video Guides",
                    socketless=True,
                ),
                io.Combo.Input(
                    "reference_mode",
                    display_name="Reference Mode",
                    options=list(REFERENCE_MODES),
                    default="Prompt Relay",
                    socketless=True,
                ),
                io.Combo.Input(
                    "audio_mode",
                    display_name="Audio Mode",
                    options=list(AUDIO_MODES),
                    default="Mix Timeline Audio",
                    socketless=True,
                ),
                io.Boolean.Input(
                    "debug_mode",
                    display_name="Debug Mode",
                    default=False,
                    socketless=True,
                ),
            ],
            outputs=[
                LTX_TIMELINE_CONFIG.Output(
                    "ltx_timeline_config",
                    display_name="LTX_TIMELINE_CONFIG",
                ),
            ],
        )

    @classmethod
    def execute(
        cls,
        resolution_profile: str = RESOLUTION_PROFILE_AUTO,
        prompt_relay_epsilon: float = 0.15,
        image_guidance_mode: str = "Section Guides",
        video_section_mode: str = "Source Video Guides",
        reference_mode: str = "Prompt Relay",
        audio_mode: str = "Mix Timeline Audio",
        debug_mode: bool = False,
    ) -> io.NodeOutput:
        config = create_ltx_timeline_config(
            resolution_profile=resolution_profile,
            prompt_relay_epsilon=prompt_relay_epsilon,
            image_guidance_mode=image_guidance_mode,
            video_section_mode=video_section_mode,
            reference_mode=reference_mode,
            audio_mode=audio_mode,
            debug_mode=debug_mode,
        )
        return io.NodeOutput(config)
