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
from ...shared.timeline.normalize import normalize_video_timeline
from ...shared.timeline.project_storage import (
    resolve_project_take_directory,
    resolved_project_storage_summary,
)
from ...shared.timeline.take_registration import (
    TakeRegistrationError,
    prepare_take_registration,
    register_generated_take,
)


DEFAULT_FILENAME_PREFIX = "%shot_id%_%take_id%"
_MISSING = object()


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
                DEBUG_INFO.Input("runtime_debug", display_name="runtime_debug", optional=True, lazy=True),
                io.Video.Input("video", display_name="video", optional=True, lazy=True),
                io.Image.Input("images", display_name="images", optional=True, lazy=True),
                io.Audio.Input("audio", display_name="audio", optional=True, lazy=True),
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
    def check_lazy_status(
        cls,
        video_timeline: dict,
        runtime_debug=_MISSING,
        video=_MISSING,
        images=_MISSING,
        audio=_MISSING,
        take_registration_json: str = "",
        generated_asset_path: str = "",
        shot_id_override: str = "",
        **kwargs,
    ) -> list[str]:
        if runtime_debug is None:
            return ["runtime_debug"]
        if _runtime_generation_skipped(runtime_debug):
            return []
        if (
            runtime_debug is not _MISSING
            and not _safe_string(take_registration_json)
            and not _safe_string(shot_id_override)
            and not _runtime_take_registration_status(runtime_debug)["ready"]
        ):
            return []
        if _safe_string(generated_asset_path):
            return []
        requested = []
        for name, value in (("video", video), ("images", images), ("audio", audio)):
            if value is None:
                requested.append(name)
        return requested

    @classmethod
    def execute(
        cls,
        video_timeline: dict,
        runtime_debug: dict | None = _MISSING,
        video=_MISSING,
        images=_MISSING,
        audio=_MISSING,
        frame_rate: float = 24.0,
        take_registration_json: str = "",
        generated_asset_path: str = "",
        shot_id_override: str = "",
        filename_prefix: str = DEFAULT_FILENAME_PREFIX,
        accept: bool = False,
        update_clip_instance: bool = True,
    ) -> io.NodeOutput:
        video_timeline = normalize_video_timeline(video_timeline)
        runtime_debug = None if runtime_debug is _MISSING else runtime_debug
        video = None if video is _MISSING else video
        images = None if images is _MISSING else images
        audio = None if audio is _MISSING else audio
        if _runtime_generation_skipped(runtime_debug):
            return io.NodeOutput(
                video_timeline,
                None,
                "",
                "",
                _skipped_debug_info(video_timeline, runtime_debug),
                ui=None,
            )
        if (
            runtime_debug is not None
            and not _safe_string(take_registration_json)
            and not _safe_string(shot_id_override)
        ):
            registration_status = _runtime_take_registration_status(runtime_debug)
            if not registration_status["ready"]:
                return io.NodeOutput(
                    video_timeline,
                    None,
                    "",
                    "",
                    _registration_not_ready_debug_info(video_timeline, runtime_debug, registration_status),
                    ui=None,
                )
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
                video_timeline,
                registration_input,
                filename_prefix=filename_prefix,
            )
        elif media_path:
            media_path, saved_result, media_payload = _copy_generated_asset_path(
                media_path,
                video_timeline,
                registration_input,
                filename_prefix=filename_prefix,
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
            timeline=result["timeline"],
        )
        debug_info = _debug_info(
            result,
            media_payload=media_payload,
            sidecar_path=sidecar_path,
            saved_result=saved_result,
        )
        preview = _take_capture_preview_ui(result["timeline"], saved_result)
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


def _runtime_generation_skipped(runtime_debug: Any) -> bool:
    if not isinstance(runtime_debug, dict):
        return False
    summary = runtime_debug.get("summary") if isinstance(runtime_debug.get("summary"), dict) else {}
    if summary.get("generation_required") is False:
        return True
    if summary.get("generation_status") == "skipped":
        return True
    policy = runtime_debug.get("generation_policy") if isinstance(runtime_debug.get("generation_policy"), dict) else {}
    return policy.get("status") == "skipped"


def _runtime_take_registration_status(runtime_debug: Any) -> dict[str, Any]:
    if not isinstance(runtime_debug, dict):
        return {
            "ready": False,
            "registration": None,
            "capture_blockers": ["TAKE_CAPTURE_NO_RUNTIME_REGISTRATION"],
            "shot_ids": [],
            "message": "Runtime debug did not contain take registration metadata.",
        }

    registrations = _runtime_take_registration_candidates(runtime_debug)
    summary = runtime_debug.get("summary") if isinstance(runtime_debug.get("summary"), dict) else {}
    if summary.get("take_registration_ready") is False and not registrations:
        return {
            "ready": False,
            "registration": None,
            "capture_blockers": ["TAKE_CAPTURE_NO_SHOT_ID"],
            "shot_ids": list(summary.get("take_registration_shot_ids") or []),
            "message": "Runtime reported that take registration is not ready.",
        }
    if len(registrations) != 1:
        blockers = ["TAKE_CAPTURE_NO_RUNTIME_REGISTRATION"] if not registrations else ["TAKE_CAPTURE_MULTIPLE_RUNTIME_REGISTRATIONS"]
        return {
            "ready": False,
            "registration": None,
            "capture_blockers": blockers,
            "shot_ids": _runtime_registration_shot_ids(registrations),
            "message": "Runtime did not provide exactly one take registration.",
        }

    registration = registrations[0]
    blockers = list(registration.get("capture_blockers") or [])
    shot_ids = [
        str(shot_id)
        for shot_id in registration.get("shot_ids") or []
        if shot_id is not None
    ]
    shot_id = _safe_string(registration.get("shot_id"))
    ready = registration.get("registration_ready") is True and bool(shot_id)
    if ready:
        return {
            "ready": True,
            "registration": registration,
            "capture_blockers": [],
            "shot_ids": shot_ids or [shot_id],
            "message": "Runtime take registration is ready.",
        }
    if not blockers:
        blockers = ["TAKE_CAPTURE_NO_SHOT_ID"]
    return {
        "ready": False,
        "registration": registration,
        "capture_blockers": blockers,
        "shot_ids": shot_ids,
        "message": "Runtime take registration does not identify exactly one target shot.",
    }


def _runtime_take_registration_candidates(runtime_debug: dict[str, Any]) -> list[dict[str, Any]]:
    direct = runtime_debug.get("take_registration")
    if isinstance(direct, dict):
        return [deepcopy(direct)]
    return [
        deepcopy(segment["take_registration"])
        for segment in runtime_debug.get("segments") or []
        if isinstance(segment, dict) and isinstance(segment.get("take_registration"), dict)
    ]


def _runtime_registration_shot_ids(registrations: list[dict[str, Any]]) -> list[str]:
    shot_ids: list[str] = []
    for registration in registrations:
        candidates = list(registration.get("shot_ids") or [])
        if registration.get("shot_id") is not None:
            candidates.insert(0, registration.get("shot_id"))
        for shot_id in candidates:
            text = _safe_string(shot_id)
            if text and text not in shot_ids:
                shot_ids.append(text)
    return shot_ids


def _skipped_debug_info(video_timeline: dict, runtime_debug: dict | None) -> dict[str, Any]:
    project = video_timeline.get("project") if isinstance(video_timeline.get("project"), dict) else {}
    privacy_mode = bool(_raw_dict(project.get("privacy")).get("mode"))
    storage_summary = resolved_project_storage_summary(project) if project else {}
    summary = runtime_debug.get("summary") if isinstance(runtime_debug, dict) and isinstance(runtime_debug.get("summary"), dict) else {}
    return {
        "type": "DEBUG_INFO",
        "ok": True,
        "code": "TAKE_CAPTURE_SKIPPED_NO_GENERATION_REQUIRED",
        "summary": {
            "generation_required": False,
            "generation_status": summary.get("generation_status") or "skipped",
            "generation_skip_reason": summary.get("generation_skip_reason"),
            "generation_mode": summary.get("generation_mode"),
            "asset_id": "",
            "take_id": "",
            "shot_id": summary.get("generation_target_shot_id"),
            "storage_action": "skipped",
            "sidecar_filename": None,
            "path": None,
            "project_id": storage_summary.get("project_id"),
            "project_directory": "Private path" if privacy_mode else storage_summary.get("project_directory"),
        },
        "ui": None,
    }


def _registration_not_ready_debug_info(
    video_timeline: dict,
    runtime_debug: dict | None,
    registration_status: dict[str, Any],
) -> dict[str, Any]:
    project = video_timeline.get("project") if isinstance(video_timeline.get("project"), dict) else {}
    privacy_mode = bool(_raw_dict(project.get("privacy")).get("mode"))
    storage_summary = resolved_project_storage_summary(project) if project else {}
    summary = runtime_debug.get("summary") if isinstance(runtime_debug, dict) and isinstance(runtime_debug.get("summary"), dict) else {}
    return {
        "type": "DEBUG_INFO",
        "ok": True,
        "code": "TAKE_CAPTURE_SKIPPED_REGISTRATION_NOT_READY",
        "summary": {
            "message": registration_status.get("message"),
            "generation_required": summary.get("generation_required"),
            "generation_status": summary.get("generation_status"),
            "generation_skip_reason": summary.get("generation_skip_reason"),
            "generation_mode": summary.get("generation_mode"),
            "capture_blockers": list(registration_status.get("capture_blockers") or []),
            "shot_ids": list(registration_status.get("shot_ids") or []),
            "asset_id": "",
            "take_id": "",
            "shot_id": None,
            "storage_action": "skipped",
            "sidecar_filename": None,
            "path": None,
            "project_id": storage_summary.get("project_id"),
            "project_directory": "Private path" if privacy_mode else storage_summary.get("project_directory"),
        },
        "ui": None,
    }


def _runtime_take_registration(runtime_debug: dict | None) -> dict[str, Any] | None:
    status = _runtime_take_registration_status(runtime_debug)
    return deepcopy(status["registration"]) if status["ready"] and isinstance(status.get("registration"), dict) else None


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
    video_timeline: dict,
    registration: dict[str, Any] | str,
    *,
    filename_prefix: str,
) -> tuple[str, ui.SavedResult | None, dict[str, Any]]:
    width, height = _video_dimensions(video)
    output_path = _capture_output_path(
        video_timeline,
        registration,
        filename_prefix=filename_prefix,
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
    video_timeline: dict,
    registration: dict[str, Any] | str,
    *,
    filename_prefix: str,
) -> tuple[str, ui.SavedResult | None, dict[str, Any]]:
    source = Path(source_path)
    if not source.is_file():
        raise TakeRegistrationError(f"TAKE_CAPTURE_SOURCE_NOT_FOUND: Generated Asset Path does not exist: {source_path}")
    extension = source.suffix or ".mp4"
    output_path = _capture_output_path(
        video_timeline,
        registration,
        filename_prefix=filename_prefix,
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
    timeline: dict,
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
    project = timeline.get("project") if isinstance(timeline, dict) else {}
    if isinstance(project, dict):
        identity = project.get("identity") if isinstance(project.get("identity"), dict) else {}
        storage = project.get("storage") if isinstance(project.get("storage"), dict) else {}
        sidecar_registration["project_context"] = {
            "project_id": identity.get("project_id"),
            "project_name": identity.get("name"),
            "project_directory_name": storage.get("project_directory_name"),
        }
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
    video_timeline: dict,
    registration: dict[str, Any] | str,
    *,
    filename_prefix: str,
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
    capture_root = resolve_project_take_directory(
        video_timeline.get("project", {}),
        shot_id,
        create=True,
    )
    full_output_folder, filename, counter, _subfolder, _ = folder_paths.get_save_image_path(
        resolved_prefix,
        str(capture_root),
        width,
        height,
    )
    suffix = extension if str(extension).startswith(".") else f".{extension}"
    return Path(full_output_folder) / f"{filename}_{counter:05}_{suffix}"


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
    timeline = result.get("timeline") if isinstance(result.get("timeline"), dict) else {}
    project = timeline.get("project") if isinstance(timeline.get("project"), dict) else {}
    privacy_mode = bool(_raw_dict(project.get("privacy")).get("mode"))
    storage_summary = resolved_project_storage_summary(project) if project else {}
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
            "filename": "Generated video" if privacy_mode else media_payload.get("filename"),
            "subfolder": None if privacy_mode else media_payload.get("subfolder"),
            "path": "Private path" if privacy_mode else media_payload.get("path"),
            "storage_action": media_payload.get("storage_action"),
            "sidecar_filename": "Private sidecar" if privacy_mode and sidecar_path else (Path(sidecar_path).name if sidecar_path else None),
            "project_id": storage_summary.get("project_id"),
            "project_directory": "Private path" if privacy_mode else storage_summary.get("project_directory"),
        },
        "ui": _debug_ui(saved_result, privacy_mode=privacy_mode),
    }


def _take_capture_preview_ui(timeline: dict, saved_result: ui.SavedResult | None) -> dict[str, Any] | None:
    if saved_result is None:
        return None
    privacy_mode = _timeline_privacy_mode(timeline)
    preview = ui.PreviewVideo([saved_result]).as_dict()
    return {
        **preview,
        "helto_take_capture_preview": [True],
        "helto_privacy_mode": [privacy_mode],
    }


def _debug_ui(saved_result: ui.SavedResult | None, *, privacy_mode: bool) -> dict[str, Any] | None:
    if saved_result is None:
        return None
    if privacy_mode:
        return {"private": True}
    return {
        "filename": saved_result.filename,
        "subfolder": saved_result.subfolder,
        "type": saved_result.type.value,
    }


def _timeline_privacy_mode(timeline: dict) -> bool:
    project = timeline.get("project") if isinstance(timeline, dict) else {}
    return bool(_raw_dict(_raw_dict(project).get("privacy")).get("mode"))


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
