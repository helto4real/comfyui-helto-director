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
register_media_cache_routes = importlib.import_module(
    f"{_PACKAGE_NAME}.routes.media_cache"
).register_media_cache_routes
register_media_browser_routes = importlib.import_module(
    f"{_PACKAGE_NAME}.routes.media_browser"
).register_media_browser_routes

register_media_cache_routes()
register_media_browser_routes()


WEB_DIRECTORY = "./web"


NODE_CLASS_MAPPINGS = {
    "HeltoVideoTimelineDirector": VideoTimelineDirector,
    "HeltoLTX23TimelineConfig": LTXTimelineConfig,
    "HeltoLTX23TimelinePlanner": LTXTimelinePlanner,
    "HeltoLTX23TimelineRuntime": LTXTimelineRuntime,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "HeltoVideoTimelineDirector": "Video Timeline Director",
    "HeltoLTX23TimelineConfig": "LTX 2.3 Timeline Config",
    "HeltoLTX23TimelinePlanner": "LTX 2.3 Timeline Planner",
    "HeltoLTX23TimelineRuntime": "LTX 2.3 Timeline Runtime",
}


class TimelineDirectorExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            VideoTimelineDirector,
            LTXTimelineConfig,
            LTXTimelinePlanner,
            LTXTimelineRuntime,
        ]


async def comfy_entrypoint() -> TimelineDirectorExtension:
    return TimelineDirectorExtension()


__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
    "comfy_entrypoint",
    "register_media_browser_routes",
    "register_media_cache_routes",
]
