from comfy_api.latest import io

from ...shared.contracts.socket_types import (
    DEBUG_INFO,
    WAN_TIMELINE_PLAN,
)
from ...shared.wan.runtime import build_wan_runtime_outputs
from ...shared.wan.runtime.segmented import build_wan_segmented_executor_outputs
from ...shared.segmented_executor import SEED_MODES
from ...shared.timeline import generation_policy_skips_generation
from ...shared.timeline_status import TimelineStatusReporter


def _samplers() -> list[str]:
    try:
        import comfy.samplers

        return list(comfy.samplers.KSampler.SAMPLERS)
    except Exception:
        return ["euler"]


def _schedulers() -> list[str]:
    try:
        import comfy.samplers

        return list(comfy.samplers.KSampler.SCHEDULERS)
    except Exception:
        return ["normal"]


def _hidden_unique_id(cls) -> str | None:
    return getattr(getattr(cls, "hidden", None), "unique_id", None)


_MISSING = object()


def _wan_generation_skipped(wan_timeline_plan: dict | None) -> bool:
    if not isinstance(wan_timeline_plan, dict):
        return False
    policy = wan_timeline_plan.get("model_specific", {}).get("wan", {}).get("generation_policy")
    return generation_policy_skips_generation(policy)


class WANTimelineRuntime(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoWAN22TimelineRuntime",
            display_name="WAN 2.2 Timeline Runtime",
            category="timeline/wan",
            description="Materialize a WAN 2.2 timeline plan into ComfyUI runtime conditioning objects.",
            inputs=[
                io.Model.Input("high_noise_model", display_name="High Noise Model", optional=True, lazy=True),
                io.Model.Input("low_noise_model", display_name="Low Noise Model", optional=True, lazy=True),
                io.Clip.Input("clip", optional=True, lazy=True),
                io.Vae.Input("vae", optional=True, lazy=True),
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
                DEBUG_INFO.Output("runtime_context", display_name="runtime_context"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def check_lazy_status(
        cls,
        wan_timeline_plan: dict | None,
        high_noise_model=_MISSING,
        low_noise_model=_MISSING,
        clip=_MISSING,
        vae=_MISSING,
        **kwargs,
    ) -> list[str]:
        if _wan_generation_skipped(wan_timeline_plan):
            return []
        requested = []
        for name, value in (
            ("high_noise_model", high_noise_model),
            ("low_noise_model", low_noise_model),
            ("clip", clip),
            ("vae", vae),
        ):
            if value is None:
                requested.append(name)
        return requested

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
        status_reporter = TimelineStatusReporter(
            model="wan",
            node_id=_hidden_unique_id(cls),
            total=4,
        )
        try:
            return io.NodeOutput(
                *build_wan_runtime_outputs(
                    high_noise_model=high_noise_model,
                    low_noise_model=low_noise_model,
                    clip=clip,
                    vae=vae,
                    wan_timeline_plan=wan_timeline_plan,
                    negative=negative,
                    batch_size=batch_size,
                    status_reporter=status_reporter,
                )
            )
        except Exception:
            status_reporter.error("WAN Runtime: failed")
            raise


class WANTimelineSegmentedExecutor(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoWAN22TimelineSegmentedExecutor",
            display_name="WAN 2.2 Timeline Segmented Executor",
            category="timeline/wan",
            description="Automatically run segmented WAN/Bernini timeline generations and stitch decoded frames.",
            inputs=[
                io.Model.Input("high_noise_model", display_name="High Noise Model", optional=True),
                io.Model.Input("low_noise_model", display_name="Low Noise Model", optional=True),
                io.Clip.Input("clip", optional=True),
                io.Vae.Input("vae"),
                WAN_TIMELINE_PLAN.Input(
                    "wan_timeline_plan",
                    display_name="WAN_TIMELINE_PLAN",
                ),
                io.Int.Input("seed", display_name="Seed", default=0, min=0, max=0xFFFFFFFFFFFFFFFF, step=1, socketless=True),
                io.Int.Input("steps", display_name="Steps", default=20, min=1, max=10000, step=1, socketless=True),
                io.Float.Input("cfg", display_name="CFG", default=8.0, min=0.0, max=100.0, step=0.1, round=0.01, socketless=True),
                io.Combo.Input("sampler_name", display_name="Sampler", options=_samplers(), default=_samplers()[0], socketless=True),
                io.Combo.Input("scheduler", display_name="Scheduler", options=_schedulers(), default=_schedulers()[0], socketless=True),
                io.Float.Input("denoise", display_name="Denoise", default=1.0, min=0.0, max=1.0, step=0.01, round=0.01, socketless=True),
                io.Int.Input("phase_split_step", display_name="Phase Split Step", default=10, min=1, max=10000, step=1, socketless=True),
                io.Combo.Input("seed_mode", display_name="Seed Mode", options=list(SEED_MODES), default="Increment Per Segment", socketless=True),
                io.Conditioning.Input("negative", optional=True),
                io.Int.Input("batch_size", display_name="Batch Size", default=1, min=1, max=4096, step=1, socketless=True),
            ],
            outputs=[
                io.Image.Output("images", display_name="images"),
                io.Audio.Output("audio", display_name="audio"),
                io.Float.Output("frame_rate", display_name="frame_rate"),
                DEBUG_INFO.Output("executor_context", display_name="executor_context"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls,
        high_noise_model=None,
        low_noise_model=None,
        clip=None,
        vae=None,
        wan_timeline_plan: dict | None = None,
        seed: int = 0,
        steps: int = 20,
        cfg: float = 8.0,
        sampler_name: str = "euler",
        scheduler: str = "normal",
        denoise: float = 1.0,
        phase_split_step: int = 10,
        seed_mode: str = "Increment Per Segment",
        negative=None,
        batch_size: int = 1,
    ) -> io.NodeOutput:
        status_reporter = TimelineStatusReporter(
            model="wan",
            node_id=_hidden_unique_id(cls),
            total=1,
        )
        try:
            return io.NodeOutput(
                *build_wan_segmented_executor_outputs(
                    high_noise_model=high_noise_model,
                    low_noise_model=low_noise_model,
                    clip=clip,
                    vae=vae,
                    wan_timeline_plan=wan_timeline_plan,
                    seed=seed,
                    steps=steps,
                    cfg=cfg,
                    sampler_name=sampler_name,
                    scheduler=scheduler,
                    denoise=denoise,
                    phase_split_step=phase_split_step,
                    seed_mode=seed_mode,
                    negative=negative,
                    batch_size=batch_size,
                    status_reporter=status_reporter,
                )
            )
        except Exception:
            status_reporter.error("WAN Executor: failed")
            raise
