"""Shared privacy mechanics for Director media routes."""

from __future__ import annotations

from typing import Any

from aiohttp import web

try:
    from ..shared.media_cache import effective_media_privacy_mode
except Exception:
    from shared.media_cache import effective_media_privacy_mode


def requested_privacy_mode(value: Any) -> bool:
    """Allow requests to enable privacy without weakening the global mode."""
    requested = value if isinstance(value, bool) else _query_bool(value)
    return effective_media_privacy_mode(requested)


def media_error_response(
    exc: Exception,
    privacy_mode: bool,
    *,
    private_error: str,
) -> web.Response:
    """Return a redacted private error or the original public error."""
    error = private_error if privacy_mode else str(exc)
    return web.json_response({"error": error}, status=400)


def _query_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
