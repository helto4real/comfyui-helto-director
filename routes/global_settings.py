from __future__ import annotations

from collections.abc import Mapping
import logging

from aiohttp import web

try:
    from ..shared.media_cache import clear_public_media_cache
    from ..shared.timeline.global_settings import (
        GlobalSettingsError,
        global_privacy_mode,
        global_settings_status,
        load_global_settings,
        patch_global_settings,
    )
except Exception:
    from shared.media_cache import clear_public_media_cache
    from shared.timeline.global_settings import (
        GlobalSettingsError,
        global_privacy_mode,
        global_settings_status,
        load_global_settings,
        patch_global_settings,
    )

try:
    from .privacy import check_privacy_token
except Exception:
    from routes.privacy import check_privacy_token


ROUTE_PREFIX = "/helto_director/global_settings"
_ROUTES_REGISTERED = False
PRIVACY_CACHE_PURGE_ERROR = (
    "PRIVACY_CACHE_PURGE_FAILED: Could not remove public preview caches; "
    "Privacy Mode was not changed."
)


def privacy_mode_transition(payload: Mapping | None) -> tuple[bool, bool]:
    current_mode = global_privacy_mode()
    if not isinstance(payload, Mapping) or "privacy" not in payload:
        return current_mode, current_mode

    privacy_patch = payload.get("privacy")
    if not isinstance(privacy_patch, Mapping):
        return current_mode, True
    if "mode" not in privacy_patch:
        return current_mode, current_mode
    return current_mode, privacy_patch.get("mode") is not False


def apply_global_settings_patch(request, payload: Mapping | None) -> web.Response | None:
    safe_payload = payload if isinstance(payload, Mapping) else {}
    current_mode, next_mode = privacy_mode_transition(safe_payload)

    storage_patch = safe_payload.get("storage")
    root_changed = False
    if isinstance(storage_patch, Mapping) and "asset_root_directory" in storage_patch:
        current_root = str(load_global_settings()["storage"]["asset_root_directory"] or "").strip()
        next_root = str(storage_patch.get("asset_root_directory") or "").strip()
        root_changed = current_root != next_root

    if (current_mode and not next_mode) or root_changed:
        denied = check_privacy_token(request)
        if denied is not None:
            return denied

    if not current_mode and next_mode:
        try:
            clear_public_media_cache()
        except Exception as exc:
            raise GlobalSettingsError(PRIVACY_CACHE_PURGE_ERROR) from exc

    patch_global_settings(safe_payload)
    return None


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
            denied = apply_global_settings_patch(request, payload)
            if denied is not None:
                return denied
            return web.json_response(global_settings_status())
        except GlobalSettingsError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    _ROUTES_REGISTERED = True
    return True


__all__ = [
    "ROUTE_PREFIX",
    "apply_global_settings_patch",
    "privacy_mode_transition",
    "register_global_settings_routes",
]
