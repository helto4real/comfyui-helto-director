from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
import mimetypes
from pathlib import Path
import re
import shutil
from typing import Any

import folder_paths
from comfy_api.latest import InputImpl, Types, io, ui

from ...shared.contracts.socket_types import DEBUG_INFO, VIDEO_TIMELINE
from ...shared.contracts.video_timeline import ASSET_TYPE_VIDEO
from ...shared.timeline.generated_capture import build_generated_take_capture_sidecar
from ...shared.timeline.take_registration import (
    TakeRegistrationError,
    prepare_take_registration,
    register_generated_take,
)


DEFAULT_FILENAME_PREFIX = "helto_director/%shot_id%_%take_id%"


class TimelineTakeCapture(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="HeltoTimelineTakeCapture",
            display_name="Timeline Take Capture",
            category="timeline/tools",
            description="Register generated media as a VIDEO_TIMELINE shot take and write a sidecar for Director UI attachment.",
            inputs=[
                VIDEO_TIMELINE.Input("video_timeline", display_name="VIDEO_TIMELINE"),
                DEBUG_INFO.Input("runtime_debug", display_name="runtime_debug", optional=True),
                io.Video.Input("video", display_name="video", optional=True),
                io.Image.Input("images", display_name="images", optional=True),
                io.Audio.Input("audio", display_name="audio", optional=True),
                io.Float.Input(
                    "frame_rate",
                    display_name="Frame Rate",
                    default=24.0,
                    min=1.0,
                    max=240.0,
                    step=1.0,
                    round=0.001,
                    display_mode=io.NumberDisplay.number,
                    socketless=True,
                ),
                io.String.Input(
                    "take_registration_json",
                    display_name="take_registration_json",
                    default="",
                    multiline=True,
                    socketless=True,
                    advanced=True,
                ),
                io.String.Input(
                    "generated_asset_path",
                    display_name="Generated Asset Path",
                    default="",
                    socketless=True,
                    advanced=True,
                ),
                io.String.Input(
                    "capture_directory",
                    display_name="Take Directory",
                    default="",
                    socketless=True,
                    advanced=True,
                ),
                io.String.Input(
                    "shot_id_override",
                    display_name="Shot ID Override",
                    default="",
                    socketless=True,
                ),
                io.String.Input(
                    "filename_prefix",
                    display_name="Filename Prefix",
                    default=DEFAULT_FILENAME_PREFIX,
                    socketless=True,
                ),
                io.Boolean.Input(
                    "accept",
                    display_name="Accept Take",
                    default=False,
                    socketless=True,
                ),
                io.Boolean.Input(
                    "update_clip_instance",
                    display_name="Update Clip Instance",
                    default=True,
                    socketless=True,
                ),
            ],
            outputs=[
                VIDEO_TIMELINE.Output("video_timeline", display_name="VIDEO_TIMELINE"),
                io.Video.Output("video", display_name="video"),
                io.String.Output("asset_id", display_name="asset_id"),
                io.String.Output("take_id", display_name="take_id"),
                DEBUG_INFO.Output("debug_info", display_name="DEBUG_INFO"),
            ],
            is_output_node=True,
            not_idempotent=True,
        )

    @classmethod
    def execute(
        cls,
        video_timeline: dict,
        runtime_debug: dict | None = None,
        video=None,
        images=None,
        audio=None,
        frame_rate: float = 24.0,
        take_registration_json: str = "",
        generated_asset_path: str = "",
        capture_directory: str = "",
        shot_id_override: str = "",
        filename_prefix: str = DEFAULT_FILENAME_PREFIX,
        accept: bool = False,
        update_clip_instance: bool = True,
    ) -> io.NodeOutput:
        registration_input = _registration_from_inputs(
            runtime_debug=runtime_debug,
            take_registration_json=take_registration_json,
            shot_id_override=shot_id_override,
        )
        registration_input = _apply_shot_override(registration_input, shot_id_override)

        video_output = _video_from_inputs(video, images, audio, frame_rate)
        media_path = _safe_string(generated_asset_path)
        saved_result = None
        media_payload: dict[str, Any] = {}
        if video_output is not None:
            media_path, saved_result, media_payload = _save_video_output(
                video_output,
                registration_input,
                filename_prefix=filename_prefix,
                capture_directory=capture_directory,
            )
        elif media_path:
            media_path, saved_result, media_payload = _copy_generated_asset_path(
                media_path,
                registration_input,
                filename_prefix=filename_prefix,
                capture_directory=capture_directory,
            )
        else:
            raise TakeRegistrationError("TAKE_CAPTURE_NO_MEDIA: Provide video, images, or Generated Asset Path.")

        registration = prepare_take_registration(
            registration_input,
            generated_asset_path=media_path,
            accept=bool(accept),
            update_clip_instance=bool(update_clip_instance),
        )
        registration = _merge_media_metadata_into_registration(registration, media_payload)
        result = register_generated_take(video_timeline, registration)
        sidecar_path = _write_capture_sidecar(
            media_path,
            registration,
            media_payload=media_payload,
            asset_id=result["asset_id"],
            take_id=result["take_id"],
        )
        debug_info = _debug_info(
            result,
            media_payload=media_payload,
            sidecar_path=sidecar_path,
            saved_result=saved_result,
        )
        preview = (
            ui.PreviewVideo([saved_result])
            if saved_result is not None
            else None
        )
        return io.NodeOutput(
            result["timeline"],
            video_output,
            result["asset_id"],
            result["take_id"],
            debug_info,
            ui=preview,
        )


def _registration_from_inputs(
    *,
    runtime_debug: dict | None,
    take_registration_json: str,
    shot_id_override: str,
) -> dict[str, Any] | str:
    text = _safe_string(take_registration_json)
    if text:
        return text
    runtime_registration = _runtime_take_registration(runtime_debug)
    if runtime_registration is not None:
        return runtime_registration
    shot_id = _safe_string(shot_id_override)
    if not shot_id:
        raise TakeRegistrationError("TAKE_CAPTURE_NO_SHOT_ID: Shot ID Override is required when no registration metadata is provided.")
    return {
        "shot_id": shot_id,
        "shot_ids": [shot_id],
        "asset": {
            "type": ASSET_TYPE_VIDEO,
            "source_kind": "Generated",
            "metadata": {},
        },
        "take": {},
    }


def _runtime_take_registration(runtime_debug: dict | None) -> dict[str, Any] | None:
    if not isinstance(runtime_debug, dict):
        return None
    direct = runtime_debug.get("take_registration")
    if isinstance(direct, dict):
        return deepcopy(direct)
    segment_registrations = [
        deepcopy(segment["take_registration"])
        for segment in runtime_debug.get("segments") or []
        if isinstance(segment, dict) and isinstance(segment.get("take_registration"), dict)
    ]
    if len(segment_registrations) == 1:
        return segment_registrations[0]
    return None


def _apply_shot_override(
    registration: dict[str, Any] | str,
    shot_id_override: str,
) -> dict[str, Any] | str:
    shot_id = _safe_string(shot_id_override)
    if not shot_id or isinstance(registration, str):
        return registration
    copy = deepcopy(registration)
    copy["shot_id"] = shot_id
    shot_ids = [
        str(item)
        for item in copy.get("shot_ids") or []
        if item is not None
    ]
    if shot_id not in shot_ids:
        shot_ids.insert(0, shot_id)
    copy["shot_ids"] = shot_ids or [shot_id]
    return copy


def _video_from_inputs(video, images, audio, frame_rate: float):
    if video is not None:
        return video
    if images is None:
        return None
    fps = max(1.0, float(frame_rate or 24.0))
    return InputImpl.VideoFromComponents(
        Types.VideoComponents(
            images=images,
            audio=audio,
            frame_rate=Fraction(round(fps * 1000), 1000),
        )
    )


def _save_video_output(
    video,
    registration: dict[str, Any] | str,
    *,
    filename_prefix: str,
    capture_directory: str,
) -> tuple[str, ui.SavedResult | None, dict[str, Any]]:
    width, height = _video_dimensions(video)
    output_path = _capture_output_path(
        registration,
        filename_prefix=filename_prefix,
        capture_directory=capture_directory,
        extension=".mp4",
        width=width,
        height=height,
    )
    video.save_to(
        str(output_path),
        format=Types.VideoContainer.MP4,
        codec=Types.VideoCodec.AUTO,
    )
    media_payload = _media_payload_for_video(
        video,
        path=output_path,
        storage_action="saved",
    )
    return (
        str(output_path),
        _preview_saved_result(output_path),
        media_payload,
    )


def _copy_generated_asset_path(
    source_path: str,
    registration: dict[str, Any] | str,
    *,
    filename_prefix: str,
    capture_directory: str,
) -> tuple[str, ui.SavedResult | None, dict[str, Any]]:
    source = Path(source_path)
    if not source.is_file():
        raise TakeRegistrationError(f"TAKE_CAPTURE_SOURCE_NOT_FOUND: Generated Asset Path does not exist: {source_path}")
    extension = source.suffix or ".mp4"
    output_path = _capture_output_path(
        registration,
        filename_prefix=filename_prefix,
        capture_directory=capture_directory,
        extension=extension,
    )
    shutil.copy2(source, output_path)
    media_payload = _media_payload_from_path(str(output_path))
    media_payload.update(
        {
            "storage_action": "copied",
            "path": str(output_path),
            "filename": output_path.name,
            "folder": "output" if _preview_saved_result(output_path) is not None else "external",
            "subfolder": _preview_subfolder(output_path),
        }
    )
    return str(output_path), _preview_saved_result(output_path), media_payload


def _write_capture_sidecar(
    media_path: str,
    registration: dict[str, Any],
    *,
    media_payload: dict[str, Any],
    asset_id: str,
    take_id: str,
) -> str | None:
    path = Path(media_path)
    if not path.name:
        return None
    sidecar_registration = deepcopy(registration)
    asset = _raw_dict(sidecar_registration.get("asset"))
    asset["asset_id"] = asset_id
    sidecar_registration["asset"] = asset
    take = _raw_dict(sidecar_registration.get("take"))
    take["take_id"] = take_id
    sidecar_registration["take"] = take
    sidecar = build_generated_take_capture_sidecar(
        sidecar_registration,
        media={
            **media_payload,
            "filename": media_payload.get("filename") or path.name,
            "mime_type": media_payload.get("mime_type") or mimetypes.guess_type(path.name)[0] or "video/mp4",
        },
    )
    sidecar_path = path.with_suffix(".helto_take.json")
    sidecar_path.write_text(
        json.dumps(sidecar, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return str(sidecar_path)


def _capture_output_path(
    registration: dict[str, Any] | str,
    *,
    filename_prefix: str,
    capture_directory: str,
    extension: str,
    width: int = 0,
    height: int = 0,
) -> Path:
    shot_id, take_id = _registration_ids(registration)
    resolved_prefix = _resolve_filename_prefix(
        filename_prefix,
        shot_id=shot_id,
        take_id=take_id,
    )
    capture_root = _capture_root(capture_directory)
    full_output_folder, filename, counter, _subfolder, _ = folder_paths.get_save_image_path(
        resolved_prefix,
        str(capture_root),
        width,
        height,
    )
    suffix = extension if str(extension).startswith(".") else f".{extension}"
    return Path(full_output_folder) / f"{filename}_{counter:05}_{suffix}"


def _capture_root(capture_directory: str) -> Path:
    text = _safe_string(capture_directory)
    if not text:
        return Path(folder_paths.get_output_directory()).resolve()
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise TakeRegistrationError("TAKE_CAPTURE_DIRECTORY_NOT_ABSOLUTE: Take Directory must be an absolute path.")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _preview_saved_result(path: Path) -> ui.SavedResult | None:
    subfolder = _preview_subfolder(path)
    if subfolder is None:
        return None
    return ui.SavedResult(path.name, subfolder, io.FolderType.output)


def _preview_subfolder(path: Path) -> str | None:
    try:
        output_root = Path(folder_paths.get_output_directory()).resolve()
        parent = path.parent.resolve()
        relative = parent.relative_to(output_root)
    except ValueError:
        return None
    except Exception:
        return None
    return "" if str(relative) == "." else relative.as_posix()


def _merge_media_metadata_into_registration(
    registration: dict[str, Any],
    media_payload: dict[str, Any],
) -> dict[str, Any]:
    copy = deepcopy(registration)
    asset = _raw_dict(copy.get("asset"))
    metadata = _raw_dict(asset.get("metadata"))
    for key in ("frame_rate", "frame_count", "duration_seconds", "width", "height"):
        value = media_payload.get(key)
        if value not in (None, ""):
            metadata[key] = value
    if media_payload.get("mime_type") and not asset.get("mime_type"):
        asset["mime_type"] = media_payload["mime_type"]
    if media_payload.get("size_bytes") is not None and asset.get("size_bytes") is None:
        asset["size_bytes"] = media_payload["size_bytes"]
    asset["metadata"] = metadata
    copy["asset"] = asset
    return copy


def _debug_info(
    result: dict[str, Any],
    *,
    media_payload: dict[str, Any],
    sidecar_path: str | None,
    saved_result: ui.SavedResult | None,
) -> dict[str, Any]:
    return {
        "type": "DEBUG_INFO",
        "ok": True,
        "code": "TAKE_CAPTURE_REGISTERED",
        "summary": {
            "shot_id": result.get("shot_id"),
            "asset_id": result.get("asset_id"),
            "take_id": result.get("take_id"),
            "accepted": bool(result.get("accepted")),
            "media_type": media_payload.get("type") or ASSET_TYPE_VIDEO,
            "filename": media_payload.get("filename"),
            "subfolder": media_payload.get("subfolder"),
            "path": media_payload.get("path"),
            "storage_action": media_payload.get("storage_action"),
            "sidecar_filename": Path(sidecar_path).name if sidecar_path else None,
        },
        "ui": (
            {
                "filename": saved_result.filename,
                "subfolder": saved_result.subfolder,
                "type": saved_result.type.value,
            }
            if saved_result is not None
            else None
        ),
    }


def _media_payload_for_video(
    video,
    *,
    path: Path,
    storage_action: str,
) -> dict[str, Any]:
    width, height = _video_dimensions(video)
    preview_subfolder = _preview_subfolder(path)
    return {
        **_media_payload_from_path(str(path)),
        "folder": "output" if preview_subfolder is not None else "external",
        "subfolder": preview_subfolder,
        "filename": path.name,
        "path": str(path),
        "storage_action": storage_action,
        "width": width,
        "height": height,
        "frame_rate": _safe_float(_call_video_method(video, "get_frame_rate")),
        "frame_count": _safe_int(_call_video_method(video, "get_frame_count")),
        "duration_seconds": _safe_float(_call_video_method(video, "get_duration")),
    }


def _media_payload_from_path(path: str) -> dict[str, Any]:
    media_path = Path(path)
    try:
        stat = media_path.stat()
        size_bytes = stat.st_size
    except Exception:
        size_bytes = None
    return {
        "type": ASSET_TYPE_VIDEO,
        "source_kind": "Generated",
        "filename": media_path.name,
        "name": media_path.name,
        "mime_type": mimetypes.guess_type(media_path.name)[0] or "video/mp4",
        "size_bytes": size_bytes,
    }


def _video_dimensions(video) -> tuple[int, int]:
    try:
        width, height = video.get_dimensions()
        return max(1, int(width or 1)), max(1, int(height or 1))
    except Exception:
        return 1, 1


def _call_video_method(video, method_name: str):
    try:
        method = getattr(video, method_name, None)
        if callable(method):
            return method()
    except Exception:
        return None
    return None


def _registration_ids(registration: dict[str, Any] | str) -> tuple[str, str]:
    if isinstance(registration, str):
        try:
            registration = json.loads(registration)
        except Exception:
            return "shot", "take"
    payload = _raw_dict(registration)
    if isinstance(payload.get("registration"), dict):
        payload = _raw_dict(payload.get("registration"))
    shot_id = _safe_string(payload.get("shot_id")) or "shot"
    take = _raw_dict(payload.get("take"))
    take_id = _safe_string(take.get("take_id")) or "take"
    return shot_id, take_id


def _resolve_filename_prefix(
    template: str,
    *,
    shot_id: str,
    take_id: str,
) -> str:
    text = _safe_string(template) or DEFAULT_FILENAME_PREFIX
    text = text.replace("%shot_id%", _safe_path_part(shot_id or "shot"))
    text = text.replace("%take_id%", _safe_path_part(take_id or "take"))
    parts = [
        _safe_path_part(part)
        for part in re.split(r"[\\/]+", text)
        if _safe_path_part(part)
    ]
    return "/".join(parts) or "helto_director/generated_take"


def _safe_path_part(value: Any) -> str:
    text = _safe_string(value)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")
    return text[:96] or "item"


def _safe_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _raw_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
