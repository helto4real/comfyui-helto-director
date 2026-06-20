import importlib
import sys
import types
from pathlib import Path

from typing_extensions import override

from comfy_api.latest import ComfyExtension, io


_PACKAGE_NAME = "comfyui_helto_director_runtime"
_PACKAGE_ROOT = Path(__file__).resolve().parent

if _PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(_PACKAGE_NAME)
    package.__file__ = str(_PACKAGE_ROOT / "__init__.py")
    package.__path__ = [str(_PACKAGE_ROOT)]
    sys.modules[_PACKAGE_NAME] = package

VideoTimelineDirector = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.video_timeline_director.node"
).VideoTimelineDirector
LTXTimelineConfig = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.ltx_timeline_config.node"
).LTXTimelineConfig
LTXTimelinePlanner = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.ltx_timeline_planner.node"
).LTXTimelinePlanner
LTXTimelineRuntime = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.ltx_timeline_runtime.node"
).LTXTimelineRuntime
LTXTimelineSegmentedExecutor = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.ltx_timeline_runtime.node"
).LTXTimelineSegmentedExecutor
WANTimelineConfig = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.wan_timeline_config.node"
).WANTimelineConfig
WANTimelinePlanner = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.wan_timeline_planner.node"
).WANTimelinePlanner
WANTimelineRuntime = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.wan_timeline_runtime.node"
).WANTimelineRuntime
WANTimelineSegmentedExecutor = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.wan_timeline_runtime.node"
).WANTimelineSegmentedExecutor
_identity_module = importlib.import_module(
    f"{_PACKAGE_NAME}.nodes.ltx_timeline_identity.node"
)
LTXTimelineCropReferenceTail = _identity_module.LTXTimelineCropReferenceTail
LTXTimelineReferenceImageSelector = _identity_module.LTXTimelineReferenceImageSelector
LTXTimelineIdentityAnchorLatentAware = _identity_module.LTXTimelineIdentityAnchorLatentAware
LTXTimelineIdentityAnchorFace = _identity_module.LTXTimelineIdentityAnchorFace
LTXTimelineIdentityAnchorCombine = _identity_module.LTXTimelineIdentityAnchorCombine
LTXTimelineApplyIdentityAnchor = _identity_module.LTXTimelineApplyIdentityAnchor
register_media_cache_routes = importlib.import_module(
    f"{_PACKAGE_NAME}.routes.media_cache"
).register_media_cache_routes
register_media_browser_routes = importlib.import_module(
    f"{_PACKAGE_NAME}.routes.media_browser"
).register_media_browser_routes
register_prompt_optimizer_routes = importlib.import_module(
    f"{_PACKAGE_NAME}.routes.prompt_optimizer"
).register_prompt_optimizer_routes
register_privacy_routes = importlib.import_module(
    f"{_PACKAGE_NAME}.routes.privacy"
).register_privacy_routes
register_timeline_library_routes = importlib.import_module(
    f"{_PACKAGE_NAME}.routes.timeline_library"
).register_timeline_library_routes

register_privacy_routes()
register_media_cache_routes()
register_media_browser_routes()
register_prompt_optimizer_routes()
register_timeline_library_routes()


WEB_DIRECTORY = "./web"


NODE_CLASS_MAPPINGS = {
    "HeltoVideoTimelineDirector": VideoTimelineDirector,
    "HeltoLTX23TimelineConfig": LTXTimelineConfig,
    "HeltoLTX23TimelinePlanner": LTXTimelinePlanner,
    "HeltoLTX23TimelineRuntime": LTXTimelineRuntime,
    "HeltoLTX23TimelineSegmentedExecutor": LTXTimelineSegmentedExecutor,
    "HeltoWAN22TimelineConfig": WANTimelineConfig,
    "HeltoWAN22TimelinePlanner": WANTimelinePlanner,
    "HeltoWAN22TimelineRuntime": WANTimelineRuntime,
    "HeltoWAN22TimelineSegmentedExecutor": WANTimelineSegmentedExecutor,
    "HeltoLTX23TimelineCropReferenceTail": LTXTimelineCropReferenceTail,
    "HeltoLTX23TimelineReferenceImageSelector": LTXTimelineReferenceImageSelector,
    "HeltoLTX23TimelineIdentityAnchorLatentAware": LTXTimelineIdentityAnchorLatentAware,
    "HeltoLTX23TimelineIdentityAnchorFace": LTXTimelineIdentityAnchorFace,
    "HeltoLTX23TimelineIdentityAnchorCombine": LTXTimelineIdentityAnchorCombine,
    "HeltoLTX23TimelineApplyIdentityAnchor": LTXTimelineApplyIdentityAnchor,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "HeltoVideoTimelineDirector": "Video Timeline Director",
    "HeltoLTX23TimelineConfig": "LTX 2.3 Timeline Config",
    "HeltoLTX23TimelinePlanner": "LTX 2.3 Timeline Planner",
    "HeltoLTX23TimelineRuntime": "LTX 2.3 Timeline Runtime",
    "HeltoLTX23TimelineSegmentedExecutor": "LTX 2.3 Timeline Segmented Executor",
    "HeltoWAN22TimelineConfig": "WAN 2.2 Timeline Config",
    "HeltoWAN22TimelinePlanner": "WAN 2.2 Timeline Planner",
    "HeltoWAN22TimelineRuntime": "WAN 2.2 Timeline Runtime",
    "HeltoWAN22TimelineSegmentedExecutor": "WAN 2.2 Timeline Segmented Executor",
    "HeltoLTX23TimelineCropReferenceTail": "LTX 2.3 Timeline Crop Reference Tail",
    "HeltoLTX23TimelineReferenceImageSelector": "LTX 2.3 Timeline Reference Image Selector",
    "HeltoLTX23TimelineIdentityAnchorLatentAware": "LTX 2.3 Timeline Identity Anchor: Latent Aware",
    "HeltoLTX23TimelineIdentityAnchorFace": "LTX 2.3 Timeline Identity Anchor: Face",
    "HeltoLTX23TimelineIdentityAnchorCombine": "LTX 2.3 Timeline Identity Anchor: Combine",
    "HeltoLTX23TimelineApplyIdentityAnchor": "LTX 2.3 Timeline Apply Identity Anchor",
}


class TimelineDirectorExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            VideoTimelineDirector,
            LTXTimelineConfig,
            LTXTimelinePlanner,
            LTXTimelineRuntime,
            LTXTimelineSegmentedExecutor,
            WANTimelineConfig,
            WANTimelinePlanner,
            WANTimelineRuntime,
            WANTimelineSegmentedExecutor,
            LTXTimelineCropReferenceTail,
            LTXTimelineReferenceImageSelector,
            LTXTimelineIdentityAnchorLatentAware,
            LTXTimelineIdentityAnchorFace,
            LTXTimelineIdentityAnchorCombine,
            LTXTimelineApplyIdentityAnchor,
        ]


async def comfy_entrypoint() -> TimelineDirectorExtension:
    return TimelineDirectorExtension()


__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
    "comfy_entrypoint",
    "LTXTimelineApplyIdentityAnchor",
    "LTXTimelineCropReferenceTail",
    "LTXTimelineIdentityAnchorCombine",
    "LTXTimelineIdentityAnchorFace",
    "LTXTimelineIdentityAnchorLatentAware",
    "LTXTimelineReferenceImageSelector",
    "LTXTimelineSegmentedExecutor",
    "WANTimelineConfig",
    "WANTimelinePlanner",
    "WANTimelineRuntime",
    "WANTimelineSegmentedExecutor",
    "register_media_browser_routes",
    "register_media_cache_routes",
    "register_privacy_routes",
    "register_prompt_optimizer_routes",
    "register_timeline_library_routes",
]
