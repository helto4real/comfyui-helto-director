from comfy_api.latest import io

from .backend import build_director_outputs
from ...shared.contracts.socket_types import TIMELINE_VALIDATION, VIDEO_TIMELINE
from ...shared.contracts.socket_types import HELTO_LORA_CONFIG
from ...shared.contracts.video_timeline import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_FRAME_RATE,
    DEFAULT_ORIENTATION,
    DEFAULT_QUALITY_PRESET,
    QUALITY_PRESETS,
)


ASPECT_RATIO_OPTIONS = ["16:9", "4:3", "3:2", "21:9", "1:1"]
ORIENTATION_OPTIONS = ["Landscape", "Portrait"]


class VideoTimelineDirector(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoVideoTimelineDirector",
            display_name="Video Timeline Director",
            category="timeline/director",
            description="Generic video timeline authoring and validation.",
            inputs=[
                io.Float.Input(
                    "duration_seconds",
                    display_name="Duration",
                    default=DEFAULT_DURATION_SECONDS,
                    min=0.25,
                    max=3600.0,
                    step=0.25,
                    round=0.001,
                    display_mode=io.NumberDisplay.number,
                    socketless=True,
                ),
                io.Float.Input(
                    "frame_rate",
                    display_name="Frame Rate",
                    default=DEFAULT_FRAME_RATE,
                    min=1.0,
                    max=240.0,
                    step=1.0,
                    round=0.001,
                    display_mode=io.NumberDisplay.number,
                    socketless=True,
                ),
                io.Combo.Input(
                    "aspect_ratio",
                    display_name="Aspect Ratio",
                    options=ASPECT_RATIO_OPTIONS,
                    default=DEFAULT_ASPECT_RATIO,
                    socketless=True,
                ),
                io.Combo.Input(
                    "orientation",
                    display_name="Orientation",
                    options=ORIENTATION_OPTIONS,
                    default=DEFAULT_ORIENTATION,
                    socketless=True,
                ),
                io.Combo.Input(
                    "quality_preset",
                    display_name="Quality Preset",
                    options=list(QUALITY_PRESETS),
                    default=DEFAULT_QUALITY_PRESET,
                    socketless=True,
                ),
                io.String.Input(
                    "video_timeline_json",
                    display_name="video_timeline_json",
                    multiline=True,
                    default="",
                    socketless=True,
                    advanced=True,
                    extra_dict={"hidden": True},
                ),
                HELTO_LORA_CONFIG.Input(
                    "lora_config_hi",
                    display_name="lora_config_hi",
                    optional=True,
                    tooltip="Optional high-noise or primary timeline LoRA stack.",
                ),
                HELTO_LORA_CONFIG.Input(
                    "lora_config_low",
                    display_name="lora_config_low",
                    optional=True,
                    tooltip="Optional low-noise timeline LoRA stack. LTX accepts one of the hi/low inputs, not both.",
                ),
            ],
            outputs=[
                VIDEO_TIMELINE.Output(
                    "video_timeline",
                    display_name="VIDEO_TIMELINE",
                ),
                TIMELINE_VALIDATION.Output(
                    "timeline_validation",
                    display_name="TIMELINE_VALIDATION",
                ),
                io.Float.Output(
                    "frame_rate",
                    display_name="frame_rate",
                ),
            ],
        )

    @classmethod
    def execute(
        cls,
        duration_seconds: float = DEFAULT_DURATION_SECONDS,
        frame_rate: float = DEFAULT_FRAME_RATE,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        orientation: str = DEFAULT_ORIENTATION,
        quality_preset: str = DEFAULT_QUALITY_PRESET,
        video_timeline_json: str = "",
        lora_config_hi: dict | None = None,
        lora_config_low: dict | None = None,
    ) -> io.NodeOutput:
        video_timeline, timeline_validation = build_director_outputs(
            video_timeline_json=video_timeline_json,
            duration_seconds=duration_seconds,
            frame_rate=frame_rate,
            aspect_ratio=aspect_ratio,
            orientation=orientation,
            quality_preset=quality_preset,
            lora_config_hi=lora_config_hi,
            lora_config_low=lora_config_low,
        )
        return io.NodeOutput(video_timeline, timeline_validation, float(video_timeline["project"]["frame_rate"]))
