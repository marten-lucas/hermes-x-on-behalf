from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _extract_identity_from_context(ctx: Any) -> tuple[Optional[str], Optional[str]]:
    """
    Sucht im Hermes Executive Context, der SessionSource oder dem Parent-Context
    nach X-On-Behalf-Of und X-User-Groups.
    """
    session_source = (
        getattr(ctx, "session_source", None)
        or getattr(ctx, "source", None)
        or getattr(getattr(ctx, "event", None), "source", None)
    )

    if not session_source and hasattr(ctx, "parent_context"):
        parent = getattr(ctx, "parent_context", None)
        session_source = getattr(parent, "session_source", None) or getattr(parent, "source", None)

    on_behalf_of: Optional[str] = None
    user_groups: Optional[str] = None

    if session_source:
        extra_headers = getattr(session_source, "extra_headers", {}) or {}
        if isinstance(extra_headers, dict):
            on_behalf_of = extra_headers.get("X-On-Behalf-Of")
            user_groups = extra_headers.get("X-User-Groups")

        if not on_behalf_of:
            on_behalf_of = getattr(session_source, "user_id", None) or getattr(session_source, "user_name", None)

    if not on_behalf_of:
        fallback = os.getenv("MCP_IDENTITY_FALLBACK_USER", "").strip()
        if fallback:
            on_behalf_of = fallback

    return (
        str(on_behalf_of).strip() if on_behalf_of else None,
        str(user_groups).strip() if user_groups else None,
    )


def inject_mcp_identity_headers(ctx: Any, request_headers: Dict[str, str]) -> Dict[str, str]:
    """
    Hook-Funktion für ausgehende MCP-Requests (sowohl tools/list als auch tools/call).
    Injektiert X-On-Behalf-Of und X-User-Groups in die HTTP/SSE-Header.
    """
    if not isinstance(request_headers, dict):
        request_headers = {}

    on_behalf_of, user_groups = _extract_identity_from_context(ctx)

    if on_behalf_of:
        request_headers["X-On-Behalf-Of"] = on_behalf_of
        logger.debug("Hermes X-On-Behalf Hook: Injiziert X-On-Behalf-Of=%s", on_behalf_of)

    if user_groups:
        request_headers["X-User-Groups"] = user_groups
        logger.debug("Hermes X-On-Behalf Hook: Injiziert X-User-Groups=%s", user_groups)

    return request_headers


def register(ctx: Any) -> None:
    """
    Registriert den Outbound-Hook im Hermes Gateway / MCP-Subsystem.
    """
    registered = False

    if hasattr(ctx, "register_mcp_request_hook"):
        ctx.register_mcp_request_hook(inject_mcp_identity_headers)
        registered = True

    if hasattr(ctx, "register_middleware"):
        ctx.register_middleware("mcp_outbound", inject_mcp_identity_headers)
        ctx.register_middleware("mcp_discovery", inject_mcp_identity_headers)
        registered = True

    if hasattr(ctx, "on_mcp_before_request"):
        ctx.on_mcp_before_request(inject_mcp_identity_headers)
        registered = True

    if registered:
        logger.info("Plugin 'Hermes X-On-Behalf' erfolgreich für Outbound-Requests (tools/list & tools/call) registriert.")
    else:
        logger.warning(
            "Plugin 'Hermes X-On-Behalf' konnte keinen passenden Hook an 'ctx' finden. "
            "Bitte prüfe die Hermes-SDK-Version."
        )