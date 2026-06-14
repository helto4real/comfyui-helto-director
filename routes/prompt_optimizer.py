"""HTTP routes for the Helto Director prompt optimizer."""

from __future__ import annotations

try:
    from aiohttp import web
    import server

    try:
        from ..shared.prompt_optimizer import (
            PromptOptimizerError,
            clear_hf_token,
            get_model_statuses,
            get_optimizer_job_status,
            get_optimizer_settings_status,
            optimize_segments,
            reset_prompt_template,
            save_hf_token,
            save_prompt_template,
            start_optimizer_job,
            unload_optimizer_model,
        )
    except Exception:
        from shared.prompt_optimizer import (
            PromptOptimizerError,
            clear_hf_token,
            get_model_statuses,
            get_optimizer_job_status,
            get_optimizer_settings_status,
            optimize_segments,
            reset_prompt_template,
            save_hf_token,
            save_prompt_template,
            start_optimizer_job,
            unload_optimizer_model,
        )
except Exception:  # noqa: BLE001 - route registration is best-effort inside ComfyUI.
    web = None
    server = None


ROUTE_PREFIX = "/helto_director/prompt_optimizer"
_ROUTES_REGISTERED = False


def register_prompt_optimizer_routes() -> bool:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return True
    if web is None or server is None:
        return False
    prompt_server = getattr(server.PromptServer, "instance", None)
    if prompt_server is None:
        return False
    routes = prompt_server.routes

    @routes.get(f"{ROUTE_PREFIX}/models")
    async def get_prompt_optimizer_models(_request):
        try:
            return web.json_response(get_model_statuses())
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post(f"{ROUTE_PREFIX}/models/unload")
    async def post_prompt_optimizer_unload_model(request):
        try:
            payload = await request.json()
            return web.json_response(unload_optimizer_model(payload.get("model") or None))
        except PromptOptimizerError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.get(f"{ROUTE_PREFIX}/settings")
    async def get_prompt_optimizer_settings(_request):
        try:
            return web.json_response(get_optimizer_settings_status())
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post(f"{ROUTE_PREFIX}/settings")
    async def post_prompt_optimizer_settings(request):
        try:
            payload = await request.json()
            if payload.get("reset_prompt_template"):
                return web.json_response(reset_prompt_template())
            if "prompt_template" in payload:
                return web.json_response(save_prompt_template(payload.get("prompt_template", "")))
            if payload.get("clear"):
                return web.json_response(clear_hf_token())
            return web.json_response(save_hf_token(payload.get("hf_token", "")))
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    @routes.post(f"{ROUTE_PREFIX}/optimize")
    async def post_prompt_optimizer_optimize(request):
        try:
            payload = await request.json()
            return web.json_response(optimize_segments(payload))
        except PromptOptimizerError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post(f"{ROUTE_PREFIX}/optimize/start")
    async def post_prompt_optimizer_start(request):
        try:
            payload = await request.json()
            return web.json_response({"ok": True, "job_id": start_optimizer_job(payload)})
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    @routes.get(f"{ROUTE_PREFIX}/optimize/status")
    async def get_prompt_optimizer_status(request):
        try:
            job_id = request.query.get("job_id", "")
            return web.json_response(get_optimizer_job_status(job_id))
        except PromptOptimizerError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    _ROUTES_REGISTERED = True
    return True
