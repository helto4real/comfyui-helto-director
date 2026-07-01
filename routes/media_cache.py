from __future__ import annotations

import asyncio
import logging
import mimetypes

from aiohttp import web

try:
    from ..shared.media_cache import (
        AUDIO_EXTENSIONS,
        IMAGE_EXTENSIONS,
        VIDEO_EXTENSIONS,
        clear_media_cache,
        make_thumbnail,
        make_waveform,
        resolve_media_path,
    )
except Exception:
    from shared.media_cache import (
        AUDIO_EXTENSIONS,
        IMAGE_EXTENSIONS,
        VIDEO_EXTENSIONS,
        clear_media_cache,
        make_thumbnail,
        make_waveform,
        resolve_media_path,
    )


ROUTE_PREFIX = "/helto_director/media"
SERVABLE_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
PREVIEW_JOB_CONCURRENCY = 2
_PREVIEW_JOB_SEMAPHORE = asyncio.Semaphore(PREVIEW_JOB_CONCURRENCY)
_ROUTES_REGISTERED = False


def query_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def _run_preview_job(fn, *args, **kwargs):
    async with _PREVIEW_JOB_SEMAPHORE:
        return await asyncio.to_thread(fn, *args, **kwargs)


def register_media_cache_routes() -> bool:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return True

    try:
        import server

        prompt_server = getattr(server.PromptServer, "instance", None)
    except Exception as exc:
        logging.debug("Helto Director media routes unavailable: %s", exc)
        return False

    if prompt_server is None:
        return False

    routes = prompt_server.routes

    @routes.get(f"{ROUTE_PREFIX}/thumbnail")
    async def get_thumbnail(request):
        try:
            privacy_mode = query_bool(request.rel_url.query.get("privacy"))
            path = resolve_media_path(
                request.rel_url.query.get("path", ""),
                request.rel_url.query.get("type"),
            )
            thumbnail = await _run_preview_job(
                make_thumbnail,
                path,
                int(request.rel_url.query.get("max_size", "320")),
                privacy_mode=privacy_mode,
            )
            if privacy_mode:
                return web.Response(
                    body=thumbnail,
                    headers={
                        "Cache-Control": "private, no-store",
                        "Content-Type": "image/webp",
                    },
                )
            return web.FileResponse(
                thumbnail,
                headers={
                    "Cache-Control": "private, max-age=86400",
                    "Content-Type": "image/webp",
                },
            )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get(f"{ROUTE_PREFIX}/waveform")
    async def get_waveform(request):
        try:
            privacy_mode = query_bool(request.rel_url.query.get("privacy"))
            path = resolve_media_path(
                request.rel_url.query.get("path", ""),
                request.rel_url.query.get("type"),
            )
            waveform = await _run_preview_job(
                make_waveform,
                path,
                int(request.rel_url.query.get("peaks", "96")),
                privacy_mode=privacy_mode,
            )
            cache_control = "private, no-store" if privacy_mode else "private, max-age=86400"
            return web.json_response(waveform, headers={"Cache-Control": cache_control})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get(f"{ROUTE_PREFIX}/view")
    async def get_view(request):
        try:
            path = resolve_media_path(
                request.rel_url.query.get("path", ""),
                request.rel_url.query.get("type"),
            )
            if path.suffix.lower() not in SERVABLE_MEDIA_EXTENSIONS:
                raise ValueError(f"Unsupported media extension: {path.suffix}")
            return web.FileResponse(
                path,
                headers={
                    "Cache-Control": "private, max-age=300",
                    "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                },
            )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.post(f"{ROUTE_PREFIX}/cache/clear")
    async def post_clear_cache(_request):
        try:
            clear_media_cache()
            return web.json_response({"status": "ok"})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    _ROUTES_REGISTERED = True
    return True
