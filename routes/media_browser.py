from __future__ import annotations

import asyncio
import logging
import mimetypes
import urllib.parse

from aiohttp import web

try:
    from ..shared.media_browser import (
        add_folder,
        delete_project_take_capture,
        folder_by_alias,
        folder_payload,
        list_media,
        list_project_take_captures,
        media_definition,
        normalize_media_type,
        remove_folder,
        resolve_browser_media_path,
    )
    from ..shared.media_cache import make_thumbnail
except Exception:
    from shared.media_browser import (
        add_folder,
        delete_project_take_capture,
        folder_by_alias,
        folder_payload,
        list_media,
        list_project_take_captures,
        media_definition,
        normalize_media_type,
        remove_folder,
        resolve_browser_media_path,
    )
    from shared.media_cache import make_thumbnail


ROUTE_PREFIX = "/helto_director/media_browser"
MEDIA_ROUTE_PREFIX = "/helto_director/media"
PREVIEW_JOB_CONCURRENCY = 2
_PREVIEW_JOB_SEMAPHORE = asyncio.Semaphore(PREVIEW_JOB_CONCURRENCY)
_ROUTES_REGISTERED = False


def query_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def _run_preview_job(fn, *args, **kwargs):
    async with _PREVIEW_JOB_SEMAPHORE:
        return await asyncio.to_thread(fn, *args, **kwargs)


def register_media_browser_routes() -> bool:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return True

    try:
        import server

        prompt_server = getattr(server.PromptServer, "instance", None)
    except Exception as exc:
        logging.debug("Helto Director media browser routes unavailable: %s", exc)
        return False

    if prompt_server is None:
        return False

    routes = prompt_server.routes

    @routes.get(f"{ROUTE_PREFIX}" + "/{media_type}/folders")
    async def get_folders(request):
        try:
            media_type = normalize_media_type(request.match_info["media_type"])
            return web.json_response({"folders": folder_payload(media_type)})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.post(f"{ROUTE_PREFIX}" + "/{media_type}/folders")
    async def post_folder(request):
        try:
            media_type = normalize_media_type(request.match_info["media_type"])
            data = await request.json()
            add_folder(media_type, data.get("alias"), data.get("path"))
            return web.json_response({"status": "ok", "folders": folder_payload(media_type)})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.delete(f"{ROUTE_PREFIX}" + "/{media_type}/folders")
    async def delete_folder(request):
        try:
            media_type = normalize_media_type(request.match_info["media_type"])
            remove_folder(media_type, request.rel_url.query.get("alias", ""))
            return web.json_response({"status": "ok", "folders": folder_payload(media_type)})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get(f"{ROUTE_PREFIX}" + "/{media_type}/items")
    async def get_items(request):
        try:
            media_type = normalize_media_type(request.match_info["media_type"])
            alias = request.rel_url.query.get("alias", "")
            recursive = request.rel_url.query.get("recursive", "1").lower() not in {"0", "false", "no"}
            privacy_mode = query_bool(request.rel_url.query.get("privacy"))
            folder = folder_by_alias(media_type, alias)
            if not folder.enabled:
                return web.json_response({"error": f"Folder alias is disabled: {alias}"}, status=400)
            items_key = media_definition(media_type)["items_key"]
            items = list_media(media_type, folder.path, recursive=recursive, privacy_mode=privacy_mode)
            for item in items:
                params = {
                    "alias": alias,
                    "filename": item["filename"],
                    "t": int(item.get("mtime") or 0),
                }
                encoded = urllib.parse.urlencode(params)
                item["view_url"] = f"{ROUTE_PREFIX}/{media_type}/view?{encoded}"
                if media_type in {"image", "video"}:
                    thumb_params = dict(params)
                    if privacy_mode:
                        thumb_params["privacy"] = "1"
                    item["thumb_url"] = f"{ROUTE_PREFIX}/{media_type}/thumb?{urllib.parse.urlencode(thumb_params)}"
            return web.json_response({items_key: items})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.post(f"{ROUTE_PREFIX}/project_takes")
    async def post_project_takes(request):
        try:
            data = await request.json()
            privacy_value = data.get("privacy")
            privacy_mode = privacy_value if isinstance(privacy_value, bool) else query_bool(str(privacy_value or ""))
            payload = list_project_take_captures(
                data.get("project") if isinstance(data.get("project"), dict) else {},
                data.get("shot_id", ""),
                privacy_mode=privacy_mode,
            )
            for item in payload["captures"]:
                params = {
                    "path": item.get("path", ""),
                    "type": "Video",
                    "t": int(item.get("mtime") or 0),
                }
                encoded = urllib.parse.urlencode(params)
                item["view_url"] = f"{MEDIA_ROUTE_PREFIX}/view?{encoded}"
                thumb_params = dict(params)
                if privacy_mode:
                    thumb_params["privacy"] = "1"
                item["thumb_url"] = f"{MEDIA_ROUTE_PREFIX}/thumbnail?{urllib.parse.urlencode(thumb_params)}"
            if privacy_mode:
                payload["take_directory"] = "Private path"
                payload["storage"]["asset_root_directory"] = "Private path"
                payload["storage"]["project_directory"] = "Private path"
            return web.json_response(payload)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.post(f"{ROUTE_PREFIX}/project_takes/delete")
    async def post_project_take_delete(request):
        try:
            data = await request.json()
            privacy_value = data.get("privacy")
            privacy_mode = privacy_value if isinstance(privacy_value, bool) else query_bool(str(privacy_value or ""))
            payload = delete_project_take_capture(
                data.get("project") if isinstance(data.get("project"), dict) else {},
                data.get("shot_id", ""),
                data.get("path", ""),
                take_id=data.get("take_id"),
                privacy_mode=privacy_mode,
            )
            return web.json_response(payload)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get(f"{ROUTE_PREFIX}" + "/{media_type}/thumb")
    async def get_thumb(request):
        try:
            media_type = normalize_media_type(request.match_info["media_type"])
            filename = urllib.parse.unquote(request.rel_url.query.get("filename", ""))
            privacy_mode = query_bool(request.rel_url.query.get("privacy"))
            path = resolve_browser_media_path(
                media_type,
                request.rel_url.query.get("alias", ""),
                filename,
            )
            thumb = await _run_preview_job(
                make_thumbnail,
                path,
                int(request.rel_url.query.get("max_size", "320")),
                privacy_mode=privacy_mode,
            )
            if privacy_mode:
                return web.Response(
                    body=thumb,
                    headers={
                        "Cache-Control": "private, no-store",
                        "Content-Type": "image/webp",
                    },
                )
            return web.FileResponse(
                thumb,
                headers={
                    "Cache-Control": "private, max-age=86400",
                    "Content-Type": "image/webp",
                },
            )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get(f"{ROUTE_PREFIX}" + "/{media_type}/view")
    async def get_view(request):
        try:
            media_type = normalize_media_type(request.match_info["media_type"])
            filename = urllib.parse.unquote(request.rel_url.query.get("filename", ""))
            path = resolve_browser_media_path(media_type, request.rel_url.query.get("alias", ""), filename)
            return web.FileResponse(
                path,
                headers={
                    "Cache-Control": "private, max-age=300",
                    "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                },
            )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    _ROUTES_REGISTERED = True
    return True
