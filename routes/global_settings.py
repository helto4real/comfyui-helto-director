from __future__ import annotations

import logging

from aiohttp import web

try:
    from ..shared.timeline.global_settings import (
        GlobalSettingsError,
        global_settings_status,
        patch_global_settings,
    )
except Exception:
    from shared.timeline.global_settings import (
        GlobalSettingsError,
        global_settings_status,
        patch_global_settings,
    )


ROUTE_PREFIX = "/helto_director/global_settings"
_ROUTES_REGISTERED = False


def register_global_settings_routes() -> bool:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return True

    try:
        import server

        prompt_server = getattr(server.PromptServer, "instance", None)
    except Exception as exc:
        logging.debug("Helto Director global settings routes unavailable: %s", exc)
        return False

    if prompt_server is None:
        return False

    routes = prompt_server.routes

    @routes.get(ROUTE_PREFIX)
    async def get_global_settings(_request):
        try:
            return web.json_response(global_settings_status())
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post(ROUTE_PREFIX)
    async def post_global_settings(request):
        try:
            payload = await request.json()
            patch_global_settings(payload if isinstance(payload, dict) else {})
            return web.json_response(global_settings_status())
        except GlobalSettingsError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    _ROUTES_REGISTERED = True
    return True


__all__ = ["ROUTE_PREFIX", "register_global_settings_routes"]
