from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_interceptors_applied = False


def apply_http_interceptors() -> None:
    """Koppelt httpx und aiohttp an die ContextVars zur automatischen Header-Injizierung."""
    global _interceptors_applied
    if _interceptors_applied:
        return

    _patch_httpx()
    _patch_aiohttp()
    _interceptors_applied = True
    logger.info("[X-On-Behalf] HTTP-Transport-Interzeptoren aktiviert (httpx + aiohttp).")


def _get_active_headers() -> dict[str, str]:
    from .plugin import current_user_groups, current_user_id

    headers = {}
    uid = current_user_id.get()
    groups = current_user_groups.get()

    if uid:
        headers["X-On-Behalf-Of"] = str(uid)
    if groups:
        headers["X-User-Groups"] = str(groups)

    return headers


def _patch_httpx() -> None:
    try:
        import httpx

        original_send = httpx.AsyncClient.send

        async def patched_send(
            self: httpx.AsyncClient, request: httpx.Request, *args: Any, **kwargs: Any
        ) -> httpx.Response:
            active_headers = _get_active_headers()
            for k, v in active_headers.items():
                request.headers[k] = v
            return await original_send(self, request, *args, **kwargs)

        httpx.AsyncClient.send = patched_send  # type: ignore[assignment]
        logger.debug("[X-On-Behalf] httpx.AsyncClient.send erfolgreich gepatcht.")
    except ImportError:
        logger.debug("[X-On-Behalf] httpx nicht installiert, Patch übersprungen.")


def _patch_aiohttp() -> None:
    try:
        import aiohttp

        original_request = aiohttp.ClientSession._request

        async def patched_request(
            self: aiohttp.ClientSession,
            method: str,
            str_or_url: Any,
            *args: Any,
            **kwargs: Any,
        ) -> aiohttp.ClientResponse:
            active_headers = _get_active_headers()
            if active_headers:
                headers = kwargs.get("headers")
                if headers is None:
                    headers = {}
                    kwargs["headers"] = headers

                if isinstance(headers, dict):
                    for k, v in active_headers.items():
                        headers[k] = v
                elif hasattr(headers, "__setitem__"):
                    for k, v in active_headers.items():
                        headers[k] = v

            return await original_request(self, method, str_or_url, *args, **kwargs)

        aiohttp.ClientSession._request = patched_request  # type: ignore[assignment]
        logger.debug("[X-On-Behalf] aiohttp.ClientSession._request erfolgreich gepatcht.")
    except ImportError:
        logger.debug("[X-On-Behalf] aiohttp nicht installiert, Patch übersprungen.")