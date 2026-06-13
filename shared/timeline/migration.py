from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from ..contracts.video_timeline import SCHEMA_VERSION, VIDEO_TIMELINE_TYPE
from .defaults import create_default_video_timeline


def migrate_video_timeline(timeline: Any) -> dict:
    if timeline is None or timeline == "":
        return create_default_video_timeline()

    if isinstance(timeline, str):
        timeline = json.loads(timeline)

    if not isinstance(timeline, dict):
        raise TypeError("VIDEO_TIMELINE must be a dict or JSON object string.")

    if timeline.get("type") != VIDEO_TIMELINE_TYPE and "project" not in timeline:
        return create_default_video_timeline()

    migrated = deepcopy(timeline)
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["type"] = VIDEO_TIMELINE_TYPE
    return migrated
