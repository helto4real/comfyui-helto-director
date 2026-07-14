"""Declared Director product routes over the installed shared privacy pack."""

from __future__ import annotations

from threading import RLock

from helto_privacy import BoundPrivacyPack
from helto_privacy.protected_operations import protected_operation_response_payload


_ROUTE_LOCK = RLock()
_ROUTES_REGISTERED = False


def register_director_managed_privacy_routes(pack: BoundPrivacyPack) -> bool:
    global _ROUTES_REGISTERED
    if not isinstance(pack, BoundPrivacyPack):
        raise TypeError("Director managed routes require the installed privacy pack.")
    with _ROUTE_LOCK:
        if _ROUTES_REGISTERED:
            return True
        try:
            from aiohttp import web
            import server
            prompt_server = getattr(server.PromptServer, "instance", None)
        except Exception:
            return False
        if prompt_server is None:
            return False
        routes = prompt_server.routes
        declarations = tuple(
            item for item in pack.profile.protected_operations if item.route is not None
        )
        for declaration in declarations:
            registrar = getattr(routes, declaration.method.lower(), None)
            if not callable(registrar):
                raise RuntimeError("Director managed route method is unavailable.")

            async def handler(request, *, _declaration=declaration):
                try:
                    body = await request.json()
                    if not isinstance(body, dict) or set(body) != {"input", "references"}:
                        raise ValueError
                    references = body["references"]
                    if not isinstance(references, dict):
                        raise ValueError
                    handle = pack.operations(_declaration.resource_id)
                    result = await handle.dispatch(
                        request,
                        _declaration.id,
                        body["input"],
                        references=references,
                    )
                    payload = protected_operation_response_payload(result)
                    if _declaration.returns_lease and payload["lease"] is None:
                        reference_input = _declaration.reference_inputs[0]
                        reference_id = references.get(reference_input.name)
                        authorization = pack.authorization.authorize_request(
                            request,
                            _declaration.id,
                        )
                        published = await handle.source_leases(
                            _declaration.id
                        ).publish(reference_id, authorization)
                        payload["lease"] = published.to_payload()["lease"]
                    return web.json_response(
                        payload,
                        headers={"Cache-Control": "no-store"},
                    )
                except Exception:
                    return web.json_response(
                        {
                            "ok": False,
                            "error": "PRIVACY_DIRECTOR_OPERATION_FAILED",
                        },
                        status=400,
                        headers={"Cache-Control": "no-store"},
                    )

            registrar(declaration.route)(handler)
        _ROUTES_REGISTERED = True
        return True


__all__ = ["register_director_managed_privacy_routes"]
