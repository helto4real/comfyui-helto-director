from comfy_api.latest import io

from ...shared.contracts.socket_types import WAN_TIMELINE_CONFIG
from ...shared.wan.config import (
    AUDIO_MODES,
    RESOLUTION_PROFILE_AUTO,
    RESOLUTION_PROFILES,
    create_wan_timeline_config,
)


class WANTimelineConfig(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoWAN22TimelineConfig",
            display_name="WAN 2.2 Timeline Config",
            category="timeline/wan",
            description="Model-specific WAN 2.2 timeline planning policy.",
            inputs=[
                io.Combo.Input(
                    "resolution_profile",
                    display_name="Resolution Profile",
                    options=list(RESOLUTION_PROFILES),
                    default=RESOLUTION_PROFILE_AUTO,
                    socketless=True,
                ),
                io.Combo.Input(
                    "audio_mode",
                    display_name="Audio Mode",
                    options=list(AUDIO_MODES),
                    default="Ignore Timeline Audio",
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
                WAN_TIMELINE_CONFIG.Output(
                    "wan_timeline_config",
                    display_name="WAN_TIMELINE_CONFIG",
                ),
            ],
        )

    @classmethod
    def execute(
        cls,
        resolution_profile: str = RESOLUTION_PROFILE_AUTO,
        audio_mode: str = "Ignore Timeline Audio",
        debug_mode: bool = False,
    ) -> io.NodeOutput:
        config = create_wan_timeline_config(
            resolution_profile=resolution_profile,
            audio_mode=audio_mode,
            debug_mode=debug_mode,
        )
        return io.NodeOutput(config)
