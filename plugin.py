from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _is_debug_enabled() -> bool:
    """Prüft, ob das Debugging für dieses Plugin per Environment-Variable aktiviert ist."""
    return os.getenv("HERMES_X_ON_BEHALF_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _log(msg: str, *args: Any) -> None:
    """Loggt bei aktivem Debug-Schalter als INFO (sichtbar im Standard-Log), sonst als DEBUG."""
    if _is_debug_enabled():
        logger.info("[X-On-Behalf Plugin] " + msg, *args)
    else:
        logger.debug("[X-On-Behalf Plugin] " + msg, *args)


def _extract_identity_from_context(ctx: Any) -> tuple[Optional[str], Optional[str]]:
    """Sucht im Hermes Context nach X-On-Behalf-Of und X-User-Groups."""
    if ctx is None:
        _log("Kontext (ctx) ist None.")
        fallback = os.getenv("MCP_IDENTITY_FALLBACK_USER", "").strip()
        return (fallback if fallback else None, None)

    _log("Analysiere Kontext vom Typ: %s", type(ctx).__name__)

    session_source = (
        getattr(ctx, "session_source", None)
        or getattr(ctx, "source", None)
        or getattr(getattr(ctx, "event", None), "source", None)
    )

    if not session_source and hasattr(ctx, "parent_context"):
        _log("Keine session_source direkt gefunden. Prüfe parent_context...")
        parent = getattr(ctx, "parent_context", None)
        session_source = getattr(parent, "session_source", None) or getattr(parent, "source", None)

    on_behalf_of: Optional[str] = None
    user_groups: Optional[str] = None

    if session_source:
        _log("SessionSource gefunden: %s", type(session_source).__name__)
        extra_headers = getattr(session_source, "extra_headers", {}) or {}
        if isinstance(extra_headers, dict):
            on_behalf_of = extra_headers.get("X-On-Behalf-Of")
            user_groups = extra_headers.get("X-User-Groups")
            _log("Aus extra_headers gelesen -> X-On-Behalf-Of: %s | X-User-Groups: %s", on_behalf_of, user_groups)

        if not on_behalf_of:
            on_behalf_of = getattr(session_source, "user_id", None) or getattr(session_source, "user_name", None)
            _log("Fallback aus SessionSource Attributes -> User: %s", on_behalf_of)
    else:
        _log("Keine SessionSource im Kontext ermittelbar.")

    if not on_behalf_of:
        fallback = os.getenv("MCP_IDENTITY_FALLBACK_USER", "").strip()
        if fallback:
            on_behalf_of = fallback
            _log("Verwende MCP_IDENTITY_FALLBACK_USER: %s", fallback)

    return (
        str(on_behalf_of).strip() if on_behalf_of else None,
        str(user_groups).strip() if user_groups else None,
    )


def inject_mcp_identity_headers(
    ctx: Any = None,
    request_headers: Optional[Dict[str, str]] = None,
    headers: Optional[Dict[str, str]] = None,
    **kwargs: Any,
) -> Dict[str, str]:
    """Hook-Funktion für ausgehende MCP-Requests."""
    target_headers = request_headers if request_headers is not None else headers
    if not isinstance(target_headers, dict):
        target_headers = {}

    _log("Hook getriggert. Vorhandene Header: %s", list(target_headers.keys()))

    on_behalf_of, user_groups = _extract_identity_from_context(ctx)

    if on_behalf_of:
        target_headers["X-On-Behalf-Of"] = on_behalf_of
        _log("Injiziert: X-On-Behalf-Of = %s", on_behalf_of)

    if user_groups:
        target_headers["X-User-Groups"] = user_groups
        _log("Injiziert: X-User-Groups = %s", user_groups)

    if not on_behalf_of and not user_groups:
        _log("Keine Identitätsdaten zum Injizieren gefunden.")

    return target_headers


def register(ctx: Any) -> None:
    """Registriert Lifecycle-Hooks mit detailliertem Logging."""
    _log("Starte Registrierung im Gateway...")
    registered = False

    available_attrs = [attr for attr in dir(ctx) if any(k in attr for k in ("hook", "mcp", "middleware"))]
    _log("Relevante Schnittstellen auf ctx: %s", available_attrs)

    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_mcp_request", inject_mcp_identity_headers)
        ctx.register_hook("pre_tool_call", inject_mcp_identity_headers)
        _log("Hook über ctx.register_hook angemeldet.")
        registered = True

    if hasattr(ctx, "register_mcp_request_hook"):
        ctx.register_mcp_request_hook(inject_mcp_identity_headers)
        _log("Hook über ctx.register_mcp_request_hook angemeldet.")
        registered = True

    if hasattr(ctx, "register_middleware"):
        ctx.register_middleware("mcp_outbound", inject_mcp_identity_headers)
        ctx.register_middleware("mcp_discovery", inject_mcp_identity_headers)
        _log("Middleware über ctx.register_middleware angemeldet.")
        registered = True

    if hasattr(ctx, "on_mcp_before_request"):
        ctx.on_mcp_before_request(inject_mcp_identity_headers)
        _log("Hook über ctx.on_mcp_before_request angemeldet.")
        registered = True

    if registered:
        logger.info("[X-On-Behalf Plugin] Registrierung erfolgreich abgeschlossen.")
    else:
        logger.warning("[X-On-Behalf Plugin] Keine bekannte Registrierungsmethode auf ctx gefunden.")
