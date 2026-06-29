from comfy_api.latest import io

from ...shared.contracts.socket_types import (
    DEBUG_INFO,
    GUIDE_DATA,
    IC_LORA_PARAMETERS,
    LTX_IDENTITY_ANCHOR,
    LTX_TIMELINE_PLAN,
)
from ...shared.ltx.runtime import build_ltx_runtime_outputs
from ...shared.ltx.runtime.segmented import build_ltx_segmented_executor_outputs
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


def _ltx_generation_skipped(ltx_timeline_plan: dict | None) -> bool:
    if not isinstance(ltx_timeline_plan, dict):
        return False
    policy = ltx_timeline_plan.get("model_specific", {}).get("ltx", {}).get("generation_policy")
    return generation_policy_skips_generation(policy)


class LTXTimelineRuntime(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineRuntime",
            display_name="LTX 2.3 Timeline Runtime",
            category="timeline/ltx",
            description="Materialize an LTX 2.3 timeline plan into ComfyUI runtime objects.",
            inputs=[
                io.Model.Input("model", lazy=True),
                io.Clip.Input("clip", lazy=True),
                io.Vae.Input("vae", lazy=True),
                LTX_TIMELINE_PLAN.Input(
                    "ltx_timeline_plan",
                    display_name="LTX_TIMELINE_PLAN",
                ),
                io.Conditioning.Input("negative", optional=True),
                io.Latent.Input("optional_latent", optional=True),
                io.Vae.Input("audio_vae", display_name="Audio VAE", optional=True),
                LTX_IDENTITY_ANCHOR.Input("identity_anchor", display_name="LTX_IDENTITY_ANCHOR", optional=True),
                io.Sigmas.Input("sigmas", optional=True),
                IC_LORA_PARAMETERS.Input("iclora_parameters", display_name="IC_LORA_PARAMETERS", optional=True),
            ],
            outputs=[
                io.Model.Output("model", display_name="model"),
                io.Conditioning.Output("positive", display_name="positive"),
                io.Conditioning.Output("negative", display_name="negative"),
                io.Latent.Output("video_latent", display_name="video_latent"),
                io.Latent.Output("audio_latent", display_name="audio_latent"),
                io.Audio.Output("combined_audio", display_name="combined_audio"),
                GUIDE_DATA.Output("guide_data", display_name="guide_data"),
                io.Image.Output("source_video_images", display_name="source_video_images"),
                io.Audio.Output("source_video_audio", display_name="source_video_audio"),
                io.Float.Output("source_video_frame_rate", display_name="source_video_frame_rate"),
                io.Int.Output("source_video_frame_count", display_name="source_video_frame_count"),
                DEBUG_INFO.Output("runtime_debug", display_name="runtime_debug"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def check_lazy_status(
        cls,
        ltx_timeline_plan: dict | None,
        model=None,
        clip=None,
        vae=None,
        **kwargs,
    ) -> list[str]:
        if _ltx_generation_skipped(ltx_timeline_plan):
            return []
        requested = []
        if model is None:
            requested.append("model")
        if clip is None:
            requested.append("clip")
        if vae is None:
            requested.append("vae")
        return requested

    @classmethod
    def execute(
        cls,
        model=None,
        clip=None,
        vae=None,
        ltx_timeline_plan: dict | None = None,
        negative=None,
        optional_latent=None,
        audio_vae=None,
        identity_anchor=None,
        sigmas=None,
        iclora_parameters=None,
    ) -> io.NodeOutput:
        status_reporter = TimelineStatusReporter(
            model="ltx",
            node_id=_hidden_unique_id(cls),
            total=6,
        )
        try:
            return io.NodeOutput(
                *build_ltx_runtime_outputs(
                    model=model,
                    clip=clip,
                    vae=vae,
                    ltx_timeline_plan=ltx_timeline_plan,
                    negative=negative,
                    optional_latent=optional_latent,
                    audio_vae=audio_vae,
                    identity_anchor=identity_anchor,
                    sigmas=sigmas,
                    iclora_parameters=iclora_parameters,
                    status_reporter=status_reporter,
                )
            )
        except Exception:
            status_reporter.error("LTX Runtime: failed")
            raise


class LTXTimelineSegmentedExecutor(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoLTX23TimelineSegmentedExecutor",
            display_name="LTX 2.3 Timeline Segmented Executor",
            category="timeline/ltx",
            description="Automatically run segmented LTX timeline generations and stitch decoded frames.",
            inputs=[
                io.Model.Input("model"),
                io.Clip.Input("clip"),
                io.Vae.Input("vae"),
                LTX_TIMELINE_PLAN.Input(
                    "ltx_timeline_plan",
                    display_name="LTX_TIMELINE_PLAN",
                ),
                io.Int.Input("seed", display_name="Seed", default=0, min=0, max=0xFFFFFFFFFFFFFFFF, step=1, socketless=True),
                io.Int.Input("steps", display_name="Steps", default=20, min=1, max=10000, step=1, socketless=True),
                io.Float.Input("cfg", display_name="CFG", default=8.0, min=0.0, max=100.0, step=0.1, round=0.01, socketless=True),
                io.Combo.Input("sampler_name", display_name="Sampler", options=_samplers(), default=_samplers()[0], socketless=True),
                io.Combo.Input("scheduler", display_name="Scheduler", options=_schedulers(), default=_schedulers()[0], socketless=True),
                io.Float.Input("denoise", display_name="Denoise", default=1.0, min=0.0, max=1.0, step=0.01, round=0.01, socketless=True),
                io.Combo.Input("seed_mode", display_name="Seed Mode", options=list(SEED_MODES), default="Increment Per Segment", socketless=True),
                io.Conditioning.Input("negative", optional=True),
                io.Latent.Input("optional_latent", optional=True),
                io.Vae.Input("audio_vae", display_name="Audio VAE", optional=True),
                LTX_IDENTITY_ANCHOR.Input("identity_anchor", display_name="LTX_IDENTITY_ANCHOR", optional=True),
                io.Sigmas.Input("sigmas", optional=True),
                IC_LORA_PARAMETERS.Input("iclora_parameters", display_name="IC_LORA_PARAMETERS", optional=True),
            ],
            outputs=[
                io.Image.Output("images", display_name="images"),
                io.Audio.Output("audio", display_name="audio"),
                io.Float.Output("frame_rate", display_name="frame_rate"),
                DEBUG_INFO.Output("executor_debug", display_name="executor_debug"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls,
        model,
        clip,
        vae,
        ltx_timeline_plan: dict,
        seed: int = 0,
        steps: int = 20,
        cfg: float = 8.0,
        sampler_name: str = "euler",
        scheduler: str = "normal",
        denoise: float = 1.0,
        seed_mode: str = "Increment Per Segment",
        negative=None,
        optional_latent=None,
        audio_vae=None,
        identity_anchor=None,
        sigmas=None,
        iclora_parameters=None,
    ) -> io.NodeOutput:
        status_reporter = TimelineStatusReporter(
            model="ltx",
            node_id=_hidden_unique_id(cls),
            total=1,
        )
        try:
            return io.NodeOutput(
                *build_ltx_segmented_executor_outputs(
                    model=model,
                    clip=clip,
                    vae=vae,
                    ltx_timeline_plan=ltx_timeline_plan,
                    seed=seed,
                    steps=steps,
                    cfg=cfg,
                    sampler_name=sampler_name,
                    scheduler=scheduler,
                    denoise=denoise,
                    seed_mode=seed_mode,
                    negative=negative,
                    optional_latent=optional_latent,
                    audio_vae=audio_vae,
                    identity_anchor=identity_anchor,
                    sigmas=sigmas,
                    iclora_parameters=iclora_parameters,
                    status_reporter=status_reporter,
                )
            )
        except Exception:
            status_reporter.error("LTX Executor: failed")
            raise
