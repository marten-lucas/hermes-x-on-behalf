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
    """Headers des aktiven PrincipalContext (X-On-Behalf-Of, X-User-Groups,
    X-Conversation-Id, X-Source-Adapter).

    Ist KEIN Principal aktiv (Gateway-Start, MCP-Discovery, Cron), wird die
    konfigurierte Service-Identity als Default gesetzt — so sieht Agentgateway
    beim initialen tools/list die volle Tool-Sicht (z. B. ki-assistent mit
    Gruppe it-admin). Ein explizit gesetzter Principal (auch anonymous) hat
    IMMER Vorrang und wird nicht eskaliert.
    """
    from .config import load_config
    from .context import get_principal
    from .headers import current_headers, principal_to_headers
    from .principal import PrincipalContext

    try:
        principal = get_principal()
        if principal is not None:
            # interactive/system/anonymous → unverändert (bei anonymous leer)
            return current_headers()
        cfg = load_config()
        if cfg.service_user:
            return principal_to_headers(
                PrincipalContext.system(cfg.service_user, groups=cfg.service_groups)
            )
        return {}
    except Exception:
        # Header-Injektion darf einen Request niemals zum Scheitern bringen
        logger.debug("[X-On-Behalf] Header-Ableitung fehlgeschlagen — Request ohne Identity-Header.", exc_info=True)
        return {}


def _patch_httpx_module(module_name: str) -> None:
    """Patch ``AsyncClient.send`` in one httpx-family module.

    Hermes' MCP client uses ``httpx2`` (MCP SDK 2.0) while other adapters may
    use ``httpx`` — both expose the same ``AsyncClient.send`` signature, so we
    patch whichever module is importable (and used by the transport).
    """
    try:
        httpx_mod = __import__(module_name)
    except ImportError:
        logger.debug("[X-On-Behalf] %s nicht installiert, Patch übersprungen.", module_name)
        return

    original_send = httpx_mod.AsyncClient.send

    async def patched_send(
        self: Any, request: Any, *args: Any, **kwargs: Any
    ) -> Any:
        active_headers = _get_active_headers()
        for k, v in active_headers.items():
            request.headers[k] = v
        return await original_send(self, request, *args, **kwargs)

    httpx_mod.AsyncClient.send = patched_send  # type: ignore[assignment]
    logger.debug("[X-On-Behalf] %s.AsyncClient.send erfolgreich gepatcht.", module_name)


def _patch_httpx() -> None:
    # Both plain "httpx" and the newer "httpx2" (used by MCP SDK 2.0) —
    # patch whichever resolves so identity headers reach MCP transports.
    for name in ("httpx", "httpx2"):
        _patch_httpx_module(name)


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