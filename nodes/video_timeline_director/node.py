from comfy_api.latest import io

from ...shared.contracts.socket_types import TIMELINE_VALIDATION, VIDEO_TIMELINE


class VideoTimelineDirector(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoVideoTimelineDirector",
            display_name="Video Timeline Director",
            category="timeline/director",
            description="Phase 0 scaffold for generic video timeline authoring.",
            inputs=[],
            outputs=[
                VIDEO_TIMELINE.Output(
                    "video_timeline",
                    display_name="VIDEO_TIMELINE",
                ),
                TIMELINE_VALIDATION.Output(
                    "timeline_validation",
                    display_name="TIMELINE_VALIDATION",
                ),
            ],
        )

    @classmethod
    def execute(cls) -> io.NodeOutput:
        video_timeline = {
            "schema_version": "0.0-phase0",
            "type": "VIDEO_TIMELINE",
            "phase": 0,
        }
        timeline_validation = {
            "is_valid": True,
            "errors": [],
            "warnings": [],
            "info": [
                {
                    "code": "PHASE_0_SCAFFOLD",
                    "severity": "Info",
                    "source": "Director",
                    "scope": "Node",
                    "item_id": None,
                    "message": "Timeline logic is not implemented in Phase 0.",
                    "hint": "Continue with Phase 1 shared contracts.",
                    "details": {},
                }
            ],
        }
        return io.NodeOutput(video_timeline, timeline_validation)
