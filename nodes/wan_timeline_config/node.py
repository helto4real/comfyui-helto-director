from comfy_api.latest import io

from ...shared.contracts.socket_types import WAN_TIMELINE_CONFIG
from ...shared.wan.config import (
    AUDIO_POLICIES,
    BERNINI_TASK_PROMPT_MODES,
    DEBUG_MODES,
    DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES,
    GAP_POLICIES,
    MODEL_MODES,
    PROMPT_ROUTING_MODES,
    RESOLUTION_PROFILE_AUTO,
    RESOLUTION_PROFILES,
    RUNTIME_BACKEND_PROFILES,
    SEGMENT_CONTINUITY_TAIL_FRAME_OPTIONS,
    UNSUPPORTED_VIDEO_SECTION_POLICIES,
    UNSUPPORTED_VISUAL_KEYFRAME_POLICIES,
    VISUAL_CONDITIONING_MODES,
    VRAM_UNLOAD_POLICIES,
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
                    "model_mode",
                    display_name="WAN Model Mode",
                    options=list(MODEL_MODES),
                    default="I2V-A14B",
                    socketless=True,
                ),
                io.Combo.Input(
                    "prompt_routing",
                    display_name="Prompt Routing",
                    options=list(PROMPT_ROUTING_MODES),
                    default="Prompt Relay",
                    socketless=True,
                ),
                io.Float.Input(
                    "prompt_relay_epsilon",
                    display_name="Prompt Relay Epsilon",
                    default=0.001,
                    min=0.0,
                    max=1.0,
                    step=0.001,
                    socketless=True,
                ),
                io.Combo.Input(
                    "bernini_task_prompt",
                    display_name="Bernini Task Prompt",
                    options=list(BERNINI_TASK_PROMPT_MODES),
                    default="Auto",
                    socketless=True,
                ),
                io.Combo.Input(
                    "visual_conditioning_mode",
                    display_name="Visual Conditioning Mode",
                    options=list(VISUAL_CONDITIONING_MODES),
                    default="Timed Keyframes",
                    socketless=True,
                ),
                io.Combo.Input(
                    "unsupported_visual_keyframe_policy",
                    display_name="Unsupported Visual Keyframe Policy",
                    options=list(UNSUPPORTED_VISUAL_KEYFRAME_POLICIES),
                    default="Warn And Keep In Plan",
                    socketless=True,
                ),
                io.Combo.Input(
                    "gap_policy",
                    display_name="Gap Policy",
                    options=list(GAP_POLICIES),
                    default="Warning",
                    socketless=True,
                ),
                io.Combo.Input(
                    "unsupported_video_section_policy",
                    display_name="Unsupported Video Section Policy",
                    options=list(UNSUPPORTED_VIDEO_SECTION_POLICIES),
                    default="Warn And Use Prompt Only",
                    socketless=True,
                ),
                io.Combo.Input(
                    "audio_policy",
                    display_name="Audio Policy",
                    options=list(AUDIO_POLICIES),
                    default="Final Mix Only",
                    socketless=True,
                ),
                io.Combo.Input(
                    "runtime_backend_profile",
                    display_name="Runtime Backend Profile",
                    options=list(RUNTIME_BACKEND_PROFILES),
                    default="Plan Only",
                    socketless=True,
                ),
                io.Float.Input(
                    "max_generation_duration",
                    display_name="Max Generation Duration",
                    default=0.0,
                    min=0.0,
                    max=600.0,
                    step=0.25,
                    round=0.01,
                    socketless=True,
                ),
                io.Combo.Input(
                    "vram_unload_policy",
                    display_name="VRAM Unload Policy",
                    options=list(VRAM_UNLOAD_POLICIES),
                    default="Off",
                    socketless=True,
                ),
                io.Combo.Input(
                    "debug_mode",
                    display_name="Debug Mode",
                    options=list(DEBUG_MODES),
                    default="Off",
                    socketless=True,
                ),
                io.Combo.Input(
                    "segment_continuity_tail_frames",
                    display_name="Segment Continuity Tail Frames",
                    options=[str(value) for value in SEGMENT_CONTINUITY_TAIL_FRAME_OPTIONS],
                    default=str(DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES),
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
        model_mode: str = "I2V-A14B",
        prompt_routing: str = "Prompt Relay",
        prompt_relay_epsilon: float = 0.001,
        bernini_task_prompt: str = "Auto",
        visual_conditioning_mode: str = "Timed Keyframes",
        unsupported_visual_keyframe_policy: str = "Warn And Keep In Plan",
        gap_policy: str = "Warning",
        unsupported_video_section_policy: str = "Warn And Use Prompt Only",
        audio_policy: str = "Final Mix Only",
        runtime_backend_profile: str = "Plan Only",
        max_generation_duration: float = 0.0,
        vram_unload_policy: str = "Off",
        debug_mode: str = "Off",
        segment_continuity_tail_frames: str = str(DEFAULT_SEGMENT_CONTINUITY_TAIL_FRAMES),
    ) -> io.NodeOutput:
        config = create_wan_timeline_config(
            resolution_profile=resolution_profile,
            model_mode=model_mode,
            prompt_routing=prompt_routing,
            prompt_relay_epsilon=prompt_relay_epsilon,
            bernini_task_prompt=bernini_task_prompt,
            visual_conditioning_mode=visual_conditioning_mode,
            unsupported_visual_keyframe_policy=unsupported_visual_keyframe_policy,
            gap_policy=gap_policy,
            unsupported_video_section_policy=unsupported_video_section_policy,
            audio_policy=audio_policy,
            runtime_backend_profile=runtime_backend_profile,
            max_generation_duration=max_generation_duration,
            segment_continuity_tail_frames=segment_continuity_tail_frames,
            vram_unload_policy=vram_unload_policy,
            debug_mode=debug_mode,
        )
        return io.NodeOutput(config)
