from comfy_api.latest import io

from ...shared.contracts.socket_types import (
    DEBUG_INFO,
    TIMELINE_VALIDATION,
    VIDEO_TIMELINE,
    WAN_TIMELINE_CONFIG,
    WAN_TIMELINE_PLAN,
)
from ...shared.wan.planner import build_wan_timeline_plan


class WANTimelinePlanner(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoWAN22TimelinePlanner",
            display_name="WAN 2.2 Timeline Planner",
            category="timeline/wan",
            description="Convert a generic VIDEO_TIMELINE into a serializable WAN 2.2 timeline plan.",
            inputs=[
                VIDEO_TIMELINE.Input(
                    "video_timeline",
                    display_name="VIDEO_TIMELINE",
                ),
                WAN_TIMELINE_CONFIG.Input(
                    "wan_timeline_config",
                    display_name="WAN_TIMELINE_CONFIG",
                ),
                io.String.Input(
                    "shot_id",
                    display_name="Shot ID",
                    default="",
                    socketless=True,
                ),
            ],
            outputs=[
                WAN_TIMELINE_PLAN.Output(
                    "wan_timeline_plan",
                    display_name="WAN_TIMELINE_PLAN",
                ),
                TIMELINE_VALIDATION.Output(
                    "timeline_validation",
                    display_name="TIMELINE_VALIDATION",
                ),
                DEBUG_INFO.Output(
                    "debug_info",
                    display_name="DEBUG_INFO",
                ),
            ],
        )

    @classmethod
    def execute(
        cls,
        video_timeline: dict,
        wan_timeline_config: dict,
        shot_id: str = "",
    ) -> io.NodeOutput:
        plan, validation, debug_info = build_wan_timeline_plan(
            video_timeline=video_timeline,
            wan_config=wan_timeline_config,
            shot_id=shot_id,
        )
        return io.NodeOutput(plan, validation, debug_info)
