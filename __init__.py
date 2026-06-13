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
register_media_cache_routes = importlib.import_module(
    f"{_PACKAGE_NAME}.routes.media_cache"
).register_media_cache_routes

register_media_cache_routes()


WEB_DIRECTORY = "./web"


NODE_CLASS_MAPPINGS = {
    "HeltoVideoTimelineDirector": VideoTimelineDirector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "HeltoVideoTimelineDirector": "Video Timeline Director",
}


class TimelineDirectorExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            VideoTimelineDirector,
        ]


async def comfy_entrypoint() -> TimelineDirectorExtension:
    return TimelineDirectorExtension()


__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
    "comfy_entrypoint",
    "register_media_cache_routes",
]
