from __future__ import annotations

from fractions import Fraction

from comfy_api.latest import InputImpl, Types, io
from comfy_execution.graph_utils import ExecutionBlocker

from ...shared.contracts.socket_types import DEBUG_INFO, VIDEO_TIMELINE
from ...shared.timeline.sequence_assembly import assemble_timeline_sequence
from ...shared.timeline_status import TimelineStatusReporter


MISSING_TAKE_POLICIES = ["warning", "error"]


def _hidden_unique_id(cls) -> str | None:
    return getattr(getattr(cls, "hidden", None), "unique_id", None)


class TimelineSequenceAssembler(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoTimelineSequenceAssembler",
            display_name="Timeline Sequence Assembler",
            category="timeline/tools",
            description="Assemble accepted timeline takes and imported clips into final video components.",
            inputs=[
                VIDEO_TIMELINE.Input("video_timeline", display_name="VIDEO_TIMELINE"),
                io.Combo.Input(
                    "missing_take_policy",
                    display_name="Missing Take Policy",
                    options=MISSING_TAKE_POLICIES,
                    default="warning",
                    socketless=True,
                ),
                io.Int.Input(
                    "bit_depth",
                    display_name="Bit Depth",
                    default=8,
                    min=8,
                    max=10,
                    step=2,
                    socketless=True,
                ),
            ],
            outputs=[
                io.Video.Output("video", display_name="video"),
                io.Image.Output("images", display_name="images"),
                io.Audio.Output("audio", display_name="audio"),
                io.Float.Output("frame_rate", display_name="frame_rate"),
                DEBUG_INFO.Output("debug_info", display_name="DEBUG_INFO"),
                io.Boolean.Output("has_assembled_video", display_name="has_assembled_video"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls,
        video_timeline: dict,
        missing_take_policy: str = "warning",
        bit_depth: int = 8,
    ) -> io.NodeOutput:
        policy = missing_take_policy if missing_take_policy in MISSING_TAKE_POLICIES else "warning"
        status_reporter = TimelineStatusReporter(
            model="sequence",
            node_id=_hidden_unique_id(cls),
            total=1,
        )
        frames, audio, frame_rate, debug_info = assemble_timeline_sequence(
            video_timeline,
            missing_take_policy=policy,
            status_reporter=status_reporter,
        )
        has_assembled_video = _has_assembled_video(debug_info)
        if not has_assembled_video:
            blocker = ExecutionBlocker(None)
            return io.NodeOutput(blocker, blocker, blocker, float(frame_rate), debug_info, False)
        video = InputImpl.VideoFromComponents(
            Types.VideoComponents(
                images=frames,
                audio=audio,
                frame_rate=_frame_rate_fraction(frame_rate),
            ),
            bit_depth=_safe_bit_depth(bit_depth),
        )
        return io.NodeOutput(video, frames, audio, float(frame_rate), debug_info, has_assembled_video)


def _frame_rate_fraction(frame_rate: float) -> Fraction:
    value = float(frame_rate or 24.0)
    return Fraction(round(value * 1000), 1000)


def _safe_bit_depth(bit_depth: int) -> int:
    try:
        value = int(bit_depth)
    except (TypeError, ValueError):
        return 8
    return 10 if value >= 10 else 8


def _has_assembled_video(debug_info: dict) -> bool:
    summary = debug_info.get("summary") if isinstance(debug_info, dict) else None
    if not isinstance(summary, dict):
        return False
    try:
        included_clip_count = int(summary.get("included_clip_count") or 0)
    except (TypeError, ValueError):
        included_clip_count = 0
    return summary.get("status") == "assembled" and included_clip_count > 0
