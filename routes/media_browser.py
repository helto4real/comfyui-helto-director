from __future__ import annotations

import logging
import mimetypes
import urllib.parse

from aiohttp import web

from ..shared.media_browser import (
    add_folder,
    folder_by_alias,
    folder_payload,
    list_media,
    make_browser_thumbnail,
    media_definition,
    normalize_media_type,
    remove_folder,
    resolve_browser_media_path,
)


ROUTE_PREFIX = "/helto_director/media_browser"
_ROUTES_REGISTERED = False


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
            folder = folder_by_alias(media_type, alias)
            if not folder.enabled:
                return web.json_response({"error": f"Folder alias is disabled: {alias}"}, status=400)
            items_key = media_definition(media_type)["items_key"]
            items = list_media(media_type, folder.path, recursive=recursive)
            for item in items:
                params = {
                    "alias": alias,
                    "filename": item["filename"],
                    "t": int(item.get("mtime") or 0),
                }
                encoded = urllib.parse.urlencode(params)
                item["view_url"] = f"{ROUTE_PREFIX}/{media_type}/view?{encoded}"
                if media_type in {"image", "video"}:
                    item["thumb_url"] = f"{ROUTE_PREFIX}/{media_type}/thumb?{encoded}"
            return web.json_response({items_key: items})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get(f"{ROUTE_PREFIX}" + "/{media_type}/thumb")
    async def get_thumb(request):
        try:
            media_type = normalize_media_type(request.match_info["media_type"])
            filename = urllib.parse.unquote(request.rel_url.query.get("filename", ""))
            thumb = make_browser_thumbnail(
                media_type,
                request.rel_url.query.get("alias", ""),
                filename,
                int(request.rel_url.query.get("max_size", "320")),
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
