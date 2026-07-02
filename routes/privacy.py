from __future__ import annotations

import asyncio
import hmac
import logging

from aiohttp import web

try:
    from ..shared import privacy_keystore
    from ..shared.privacy import (
        PrivacyError,
        crypto_status,
        decrypt_state,
        encrypt_state,
        initialize_privacy_keystore,
    )
    from ..shared.privacy_keystore import PrivacyKeystoreError
except Exception:
    from shared import privacy_keystore
    from shared.privacy import (
        PrivacyError,
        crypto_status,
        decrypt_state,
        encrypt_state,
        initialize_privacy_keystore,
    )
    from shared.privacy_keystore import PrivacyKeystoreError


ROUTE_PREFIX = "/helto_director/privacy"
PRIVACY_TOKEN_HEADER = "X-Helto-Privacy-Token"
PRIVACY_TOKEN_COOKIE = "helto_privacy_token"
_ROUTES_REGISTERED = False


def _token_error() -> web.Response:
    return web.json_response(
        {
            "ok": False,
            "error": (
                "PRIVACY_TOKEN_REQUIRED: This ComfyUI has a privacy keystore; "
                "unlock it to obtain a session token."
            ),
        },
        status=401,
    )


def check_privacy_token(request) -> web.Response | None:
    """When the keystore is active, privacy operations require the unlock token.

    The token can arrive as a header (fetch/XHR callers) or as a cookie
    (image/media elements cannot send custom headers). Legacy installs with
    no keystore keep the historical open behavior.
    """
    if not privacy_keystore.keystore_exists():
        return None
    expected = privacy_keystore.session_token()
    if expected is None:
        return web.json_response(
            {
                "ok": False,
                "error": f"{privacy_keystore.ERROR_LOCKED}: Privacy keystore is locked. Unlock it with your privacy password.",
            },
            status=401,
        )
    provided = str(
        request.headers.get(PRIVACY_TOKEN_HEADER)
        or request.cookies.get(PRIVACY_TOKEN_COOKIE)
        or ""
    )
    if not provided or not hmac.compare_digest(provided, expected):
        return _token_error()
    return None


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

    @routes.post(f"{ROUTE_PREFIX}/keystore/init")
    async def post_privacy_keystore_init(request):
        try:
            payload = await request.json()
            result = await _to_thread(initialize_privacy_keystore, str(payload.get("password") or ""))
            return web.json_response({"ok": True, **result})
        except (PrivacyError, PrivacyKeystoreError) as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post(f"{ROUTE_PREFIX}/unlock")
    async def post_privacy_unlock(request):
        try:
            payload = await request.json()
            # scrypt is deliberately slow; keep it off the event loop.
            result = await _to_thread(privacy_keystore.unlock_keystore, str(payload.get("password") or ""))
            return web.json_response({"ok": True, **result})
        except PrivacyKeystoreError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post(f"{ROUTE_PREFIX}/lock")
    async def post_privacy_lock(_request):
        try:
            return web.json_response({"ok": True, **privacy_keystore.lock_keystore()})
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post(f"{ROUTE_PREFIX}/keystore/change_password")
    async def post_privacy_change_password(request):
        try:
            payload = await request.json()
            result = await _to_thread(
                privacy_keystore.change_keystore_password,
                str(payload.get("current_password") or ""),
                str(payload.get("new_password") or ""),
            )
            return web.json_response({"ok": True, **result})
        except PrivacyKeystoreError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post(f"{ROUTE_PREFIX}/encrypt")
    async def post_privacy_encrypt(request):
        denied = check_privacy_token(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
            envelope = encrypt_state(payload.get("state", {}))
            return web.json_response({"ok": True, "envelope": envelope})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    @routes.post(f"{ROUTE_PREFIX}/decrypt")
    async def post_privacy_decrypt(request):
        denied = check_privacy_token(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
            state = decrypt_state(payload.get("payload", {}))
            return web.json_response({"ok": True, "state": state})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    _ROUTES_REGISTERED = True
    return True


async def _to_thread(fn, *args):
    return await asyncio.to_thread(fn, *args)
