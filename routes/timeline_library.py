from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

try:
    from ..shared.timeline_library import (
        CHARACTER_KIND,
        TIMELINE_KIND,
        TimelineLibraryError,
        create_item,
        delete_item,
        duplicate_item,
        list_items,
        patch_item,
        preview_timeline_item,
        replace_item,
        use_item,
    )
except Exception:
    from shared.timeline_library import (
        CHARACTER_KIND,
        TIMELINE_KIND,
        TimelineLibraryError,
        create_item,
        delete_item,
        duplicate_item,
        list_items,
        patch_item,
        preview_timeline_item,
        replace_item,
        use_item,
    )


ROUTE_PREFIX = "/helto_director/library"
_ROUTES_REGISTERED = False


def register_timeline_library_routes() -> bool:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return True

    try:
        import server

        prompt_server = getattr(server.PromptServer, "instance", None)
    except Exception as exc:
        logging.debug("Helto Director library routes unavailable: %s", exc)
        return False

    if prompt_server is None:
        return False

    routes = prompt_server.routes

    @routes.get(f"{ROUTE_PREFIX}/items")
    async def get_items(_request):
        try:
            return web.json_response({"ok": True, **list_items()})
        except Exception as exc:
            return _error_response(exc)

    @routes.post(f"{ROUTE_PREFIX}/items")
    async def post_item(request):
        try:
            data = await _json_payload(request)
            kind = _kind_from_payload(data)
            item = create_item(kind, _entry_payload(data, kind), metadata=data)
            return web.json_response({"ok": True, "item": item})
        except Exception as exc:
            return _error_response(exc)

    @routes.post(f"{ROUTE_PREFIX}/timelines")
    async def post_timeline(request):
        return await _create_typed_item(request, TIMELINE_KIND)

    @routes.post(f"{ROUTE_PREFIX}/characters")
    async def post_character(request):
        return await _create_typed_item(request, CHARACTER_KIND)

    @routes.put(f"{ROUTE_PREFIX}/timelines" + "/{item_id}")
    async def put_timeline(request):
        return await _replace_typed_item(request, TIMELINE_KIND)

    @routes.put(f"{ROUTE_PREFIX}/characters" + "/{item_id}")
    async def put_character(request):
        return await _replace_typed_item(request, CHARACTER_KIND)

    @routes.patch(f"{ROUTE_PREFIX}/timelines" + "/{item_id}")
    async def patch_timeline(request):
        return await _patch_typed_item(request, TIMELINE_KIND)

    @routes.patch(f"{ROUTE_PREFIX}/characters" + "/{item_id}")
    async def patch_character(request):
        return await _patch_typed_item(request, CHARACTER_KIND)

    @routes.post(f"{ROUTE_PREFIX}/timelines" + "/{item_id}/duplicate")
    async def duplicate_timeline(request):
        return await _duplicate_typed_item(request, TIMELINE_KIND)

    @routes.post(f"{ROUTE_PREFIX}/characters" + "/{item_id}/duplicate")
    async def duplicate_character(request):
        return await _duplicate_typed_item(request, CHARACTER_KIND)

    @routes.delete(f"{ROUTE_PREFIX}/timelines" + "/{item_id}")
    async def delete_timeline(request):
        return await _delete_typed_item(request, TIMELINE_KIND)

    @routes.delete(f"{ROUTE_PREFIX}/characters" + "/{item_id}")
    async def delete_character(request):
        return await _delete_typed_item(request, CHARACTER_KIND)

    @routes.post(f"{ROUTE_PREFIX}/timelines" + "/{item_id}/use")
    async def use_timeline(request):
        return await _use_typed_item(request, TIMELINE_KIND)

    @routes.post(f"{ROUTE_PREFIX}/timelines" + "/{item_id}/preview")
    async def preview_timeline(request):
        try:
            preview = preview_timeline_item(request.match_info["item_id"])
            return web.json_response({"ok": True, **preview})
        except Exception as exc:
            return _error_response(exc)

    @routes.post(f"{ROUTE_PREFIX}/characters" + "/{item_id}/use")
    async def use_character(request):
        return await _use_typed_item(request, CHARACTER_KIND)

    _ROUTES_REGISTERED = True
    return True


async def _create_typed_item(request, kind: str):
    try:
        data = await _json_payload(request)
        item = create_item(kind, _entry_payload(data, kind), metadata=data)
        return web.json_response({"ok": True, "item": item})
    except Exception as exc:
        return _error_response(exc)


async def _replace_typed_item(request, kind: str):
    try:
        data = await _json_payload(request)
        item = replace_item(
            kind,
            request.match_info["item_id"],
            _entry_payload(data, kind),
            metadata=data,
        )
        return web.json_response({"ok": True, "item": item})
    except Exception as exc:
        return _error_response(exc)


async def _patch_typed_item(request, kind: str):
    try:
        data = await _json_payload(request)
        payload = _optional_entry_payload(data, kind)
        item = patch_item(
            kind,
            request.match_info["item_id"],
            metadata=data,
            payload=payload,
        )
        return web.json_response({"ok": True, "item": item})
    except Exception as exc:
        return _error_response(exc)


async def _duplicate_typed_item(request, kind: str):
    try:
        data = await _json_payload(request, empty_ok=True)
        item = duplicate_item(kind, request.match_info["item_id"], metadata=data)
        return web.json_response({"ok": True, "item": item})
    except Exception as exc:
        return _error_response(exc)


async def _delete_typed_item(request, kind: str):
    try:
        deleted = delete_item(kind, request.match_info["item_id"])
        return web.json_response({"ok": True, **deleted})
    except Exception as exc:
        return _error_response(exc)


async def _use_typed_item(request, kind: str):
    try:
        item = use_item(kind, request.match_info["item_id"])
        response = {"ok": True, "item": item}
        response[kind] = item["payload"]
        return web.json_response(response)
    except Exception as exc:
        return _error_response(exc)


async def _json_payload(request, *, empty_ok: bool = False) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        if empty_ok:
            return {}
        raise TimelineLibraryError("Request body must be JSON.")
    if not isinstance(data, dict):
        raise TimelineLibraryError("Request JSON body must be an object.")
    return data


def _kind_from_payload(data: dict[str, Any]) -> str:
    kind = str(data.get("kind") or data.get("type") or "").strip().lower()
    if kind in {"timeline", "timelines"}:
        return TIMELINE_KIND
    if kind in {"character", "characters"}:
        return CHARACTER_KIND
    raise TimelineLibraryError("Request must include kind: 'timeline' or 'character'.")


def _entry_payload(data: dict[str, Any], kind: str) -> dict[str, Any]:
    payload = _optional_entry_payload(data, kind)
    if payload is None:
        raise TimelineLibraryError(f"Request must include a {kind} payload.")
    return payload


def _optional_entry_payload(data: dict[str, Any], kind: str) -> dict[str, Any] | None:
    keys = (kind, f"{kind}_payload", "payload")
    if kind == TIMELINE_KIND:
        keys = ("timeline", "video_timeline", "payload")
    elif kind == CHARACTER_KIND:
        keys = ("character", "reference", "payload")
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return None


def _error_response(exc: Exception):
    status = 400 if isinstance(exc, (TimelineLibraryError, ValueError)) else 500
    logging.debug("Helto Director library route failed: %s", exc, exc_info=status >= 500)
    return web.json_response({"ok": False, "error": str(exc)}, status=status)


__all__ = [
    "ROUTE_PREFIX",
    "register_timeline_library_routes",
]
