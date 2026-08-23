from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _is_debug_enabled() -> bool:
    """Prüft, ob HERMES_X_ON_BEHALF_DEBUG aktiv ist."""
    return os.getenv("HERMES_X_ON_BEHALF_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _log(msg: str, *args: Any) -> None:
    if _is_debug_enabled():
        logger.info("[X-On-Behalf] " + msg, *args)
    else:
        logger.debug("[X-On-Behalf] " + msg, *args)


def extract_identity_from_context(ctx: Any) -> tuple[Optional[str], Optional[str]]:
    """Extrahiert X-On-Behalf-Of und X-User-Groups aus dem Session Context."""
    if ctx is None:
        fallback = os.getenv("MCP_IDENTITY_FALLBACK_USER", "").strip()
        return (fallback if fallback else None, None)

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


def on_pre_tool_call(tool_name: str = "", args: Any = None, **kwargs: Any) -> None:
    """Callback für valide Hermes pre_tool_call Hooks."""
    ctx = kwargs.get("context") or kwargs.get("ctx")
    _log("pre_tool_call getriggert für Tool '%s'", tool_name)

    on_behalf_of, user_groups = extract_identity_from_context(ctx)

    if on_behalf_of or user_groups:
        _log("Identität: X-On-Behalf-Of=%s | X-User-Groups=%s", on_behalf_of, user_groups)


def register(ctx: Any) -> None:
    """Registriert ausschließlich valide Hermes Lifecycle Hooks."""
    _log("Registriere Plugin-Hooks in Hermes...")

    if hasattr(ctx, "register_hook"):
        # Valide Hermes Hooks
        ctx.register_hook("pre_tool_call", on_pre_tool_call)
        logger.info("[X-On-Behalf] Erfolgreich für 'pre_tool_call' registriert.")

    if hasattr(ctx, "register_middleware"):
        # Valide Hermes Middleware (tool_request)
        ctx.register_middleware("tool_request", on_pre_tool_call)
