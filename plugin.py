from __future__ import annotations

import contextvars
import logging
import os
from typing import Any, Optional

from .interceptor import apply_http_interceptors

logger = logging.getLogger(__name__)

# Task- und Thread-sicherer Identitätskontext für den HTTP-Transport
current_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_user_id", default=None
)
current_user_groups: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_user_groups", default=None
)

def _is_debug_enabled() -> bool:
    """Checks whether HERMES_X_ON_BEHALF_DEBUG is active."""
    return os.getenv("HERMES_X_ON_BEHALF_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _log(msg: str, *args: Any) -> None:
    if _is_debug_enabled():
        logger.info("[X-On-Behalf] " + msg, *args)
    else:
        logger.debug("[X-On-Behalf] " + msg, *args)


def _resolve_session_source(ctx: Any) -> Any:
    if ctx is None:
        return None
    if isinstance(ctx, dict):
        for key in ("session_source", "source", "context"):
            value = ctx.get(key)
            if value is not None:
                return value
        event = ctx.get("event")
        if event is not None:
            return getattr(event, "source", None) or getattr(event, "session_source", None)
        return None

    session_source = (
        getattr(ctx, "session_source", None)
        or getattr(ctx, "source", None)
        or getattr(getattr(ctx, "event", None), "source", None)
        or getattr(getattr(ctx, "event", None), "session_source", None)
    )
    if session_source is None and hasattr(ctx, "parent_context"):
        parent = getattr(ctx, "parent_context", None)
        if parent is not None:
            return _resolve_session_source(parent)

    return session_source


def extract_identity_from_context(ctx: Any) -> tuple[Optional[str], Optional[str]]:
    """Extracts X-On-Behalf-Of and X-User-Groups from the current Hermes session context."""
    fallback_user = os.getenv("MCP_IDENTITY_FALLBACK_USER", "").strip()
    if ctx is None:
        return (fallback_user or None, None)

    session_source = _resolve_session_source(ctx)
    on_behalf_of: Optional[str] = None
    user_groups: Optional[str] = None

    if session_source is not None:
        # Objekt-basierter Session-Source (SessionSource mit extra_headers-Attribut)
        extra_headers = getattr(session_source, "extra_headers", None)
        if isinstance(extra_headers, dict):
            on_behalf_of = extra_headers.get("X-On-Behalf-Of")
            user_groups = extra_headers.get("X-User-Groups")

        # Fallback: user_id / user_name direkt vom Source (Objekt oder Dict)
        if not on_behalf_of:
            on_behalf_of = (
                getattr(session_source, "user_id", None)
                or getattr(session_source, "user_name", None)
            )

        # Dict-basierter Session-Source
        if isinstance(session_source, dict):
            extra = session_source.get("extra_headers") or {}
            if isinstance(extra, dict):
                on_behalf_of = on_behalf_of or extra.get("X-On-Behalf-Of")
                user_groups = user_groups or extra.get("X-User-Groups")
            if not on_behalf_of:
                on_behalf_of = session_source.get("user_id") or session_source.get("user_name")

    if not on_behalf_of:
        on_behalf_of = fallback_user or None

    final_user = str(on_behalf_of).strip() if on_behalf_of else None
    final_groups = str(user_groups).strip() if user_groups else None

    return final_user, final_groups


def _resolve_headers_container(payload: Any) -> Optional[Dict[str, str]]:
    if payload is None:
        return None

    if isinstance(payload, dict):
        if "headers" in payload and isinstance(payload["headers"], dict):
            return payload["headers"]
        if "extra_headers" in payload and isinstance(payload["extra_headers"], dict):
            return payload["extra_headers"]
        if all(key in payload for key in ("X-On-Behalf-Of", "X-User-Groups")):
            return payload

    headers = getattr(payload, "headers", None)
    if isinstance(headers, dict):
        return headers

    extra_headers = getattr(payload, "extra_headers", None)
    if isinstance(extra_headers, dict):
        return extra_headers

    return None


def inject_mcp_identity_headers(payload: Any, ctx: Any = None) -> Any:
    """Injects the current human identity into outgoing MCP requests, if present in the session context."""
    if payload is None:
        return payload

    on_behalf_of, user_groups = extract_identity_from_context(ctx)
    if not on_behalf_of and not user_groups:
        return payload

    headers = _resolve_headers_container(payload) if not isinstance(payload, (str, bytes)) else None
    if headers is None:
        if isinstance(payload, dict):
            headers = payload.setdefault("headers", {})
        elif hasattr(payload, "headers"):
            headers = getattr(payload, "headers")
            if headers is None:
                headers = {}
                setattr(payload, "headers", headers)
        else:
            try:
                payload.headers = {}
                headers = payload.headers
            except Exception:
                return payload

    if on_behalf_of:
        headers["X-On-Behalf-Of"] = on_behalf_of
        if hasattr(payload, "extra_headers"):
            try:
                payload.extra_headers["X-On-Behalf-Of"] = on_behalf_of
            except Exception:
                pass
    if user_groups:
        headers["X-User-Groups"] = user_groups
        if hasattr(payload, "extra_headers"):
            try:
                payload.extra_headers["X-User-Groups"] = user_groups
            except Exception:
                pass

    return payload


def on_pre_tool_call(tool_name: str = "", args: Any = None, **kwargs: Any) -> Any:
    """Hook that mutates outbound tool request metadata with the human identity from the active session context."""
    ctx = kwargs.get("context") or kwargs.get("ctx") or kwargs.get("request_context")
    _log("pre_tool_call triggered for tool '%s'", tool_name)

    on_behalf_of, user_groups = extract_identity_from_context(ctx)
    if on_behalf_of or user_groups:
        _log("Identity: X-On-Behalf-Of=%s | X-User-Groups=%s", on_behalf_of, user_groups)

    payload_candidates = []
    for key in ("request", "tool_request", "payload", "params", "data"):
        if key in kwargs and kwargs[key] is not None:
            payload_candidates.append(kwargs[key])
    if args is not None:
        payload_candidates.append(args)
    if isinstance(args, dict):
        payload_candidates.append(args.get("headers"))

    for candidate in payload_candidates:
        try:
            inject_mcp_identity_headers(candidate, ctx)
        except Exception:
            pass

    headers = kwargs.get("headers")
    if isinstance(headers, dict):
        if on_behalf_of:
            headers["X-On-Behalf-Of"] = on_behalf_of
        if user_groups:
            headers["X-User-Groups"] = user_groups

    return kwargs.get("request") or kwargs.get("payload") or args or kwargs


def register(ctx: Any) -> None:
    """Registers the Hermes lifecycle hooks required for identity propagation."""
    _log("Registering identity hooks in Hermes...")

    # HTTP-Transport-Interzeptoren aktivieren (ContextVars → Header auf allen aiohttp/httpx Requests)
    try:
        apply_http_interceptors()
    except Exception as exc:
        logger.warning("[X-On-Behalf] HTTP-Interzeptoren konnten nicht aktiviert werden: %s", exc)

    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_tool_call", on_pre_tool_call)
        logger.info("[X-On-Behalf] Registered 'pre_tool_call'.")

    if hasattr(ctx, "register_middleware"):
        ctx.register_middleware("tool_request", on_pre_tool_call)
        logger.info("[X-On-Behalf] Registered 'tool_request' middleware.")
