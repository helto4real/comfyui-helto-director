from __future__ import annotations

import logging

from aiohttp import web

from ..shared.privacy import crypto_status, decrypt_state, encrypt_state


ROUTE_PREFIX = "/helto_director/privacy"
_ROUTES_REGISTERED = False


def register_privacy_routes() -> bool:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return True

    try:
        import server

        prompt_server = getattr(server.PromptServer, "instance", None)
    except Exception as exc:
        logging.debug("Helto Director privacy routes unavailable: %s", exc)
        return False

    if prompt_server is None:
        return False

    routes = prompt_server.routes

    @routes.get(f"{ROUTE_PREFIX}/status")
    async def get_privacy_status(_request):
        return web.json_response({"ok": True, **crypto_status()})

    @routes.post(f"{ROUTE_PREFIX}/encrypt")
    async def post_privacy_encrypt(request):
        try:
            payload = await request.json()
            envelope = encrypt_state(payload.get("state", {}))
            return web.json_response({"ok": True, "envelope": envelope})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    @routes.post(f"{ROUTE_PREFIX}/decrypt")
    async def post_privacy_decrypt(request):
        try:
            payload = await request.json()
            state = decrypt_state(payload.get("payload", {}))
            return web.json_response({"ok": True, "state": state})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    _ROUTES_REGISTERED = True
    return True
