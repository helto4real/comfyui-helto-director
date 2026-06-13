from typing_extensions import override

from comfy_api.latest import ComfyExtension, io

from .nodes.video_timeline_director.node import VideoTimelineDirector


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
]
