from comfy_api.latest import io

from ...shared.contracts.socket_types import (
    DEBUG_INFO,
    LTX_TIMELINE_CONFIG,
    LTX_TIMELINE_PLAN,
    TIMELINE_VALIDATION,
    VIDEO_TIMELINE,
)
from ...shared.ltx.planner import build_ltx_timeline_plan


class LTXTimelinePlanner(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelinePlanner",
            display_name="LTX 2.3 Timeline Planner",
            category="timeline/ltx",
            description="Convert a generic VIDEO_TIMELINE into a serializable LTX 2.3 timeline plan.",
            inputs=[
                VIDEO_TIMELINE.Input(
                    "video_timeline",
                    display_name="VIDEO_TIMELINE",
                ),
                LTX_TIMELINE_CONFIG.Input(
                    "ltx_timeline_config",
                    display_name="LTX_TIMELINE_CONFIG",
                ),
            ],
            outputs=[
                LTX_TIMELINE_PLAN.Output(
                    "ltx_timeline_plan",
                    display_name="LTX_TIMELINE_PLAN",
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
        ltx_timeline_config: dict,
    ) -> io.NodeOutput:
        plan, validation, debug_info = build_ltx_timeline_plan(
            video_timeline=video_timeline,
            ltx_config=ltx_timeline_config,
        )
        return io.NodeOutput(plan, validation, debug_info)
