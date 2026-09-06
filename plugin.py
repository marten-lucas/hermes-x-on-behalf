"""Hermes lifecycle hooks for the x-on-behalf identity propagation plugin."""
from __future__ import annotations

import logging
from typing import Any, Optional

from .config import load_config
from .context import current_principal, get_principal
from .headers import principal_to_headers, validate_adapter_secret
from .interceptor import apply_http_interceptors
from .principal import PrincipalContext, PrincipalKind

logger = logging.getLogger(__name__)


def _log(msg: str, *args: Any) -> None:
    cfg = load_config()
    if cfg.debug:
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

    return (
        getattr(ctx, "session_source", None)
        or getattr(ctx, "source", None)
        or getattr(getattr(ctx, "event", None), "source", None)
        or getattr(getattr(ctx, "event", None), "session_source", None)
    )


def build_principal_from_context(ctx: Any) -> PrincipalContext:
    """Build the PrincipalContext from a Hermes session context.

    The session context originates from the platform adapter (authoritative).
    Identity from LLM/tool arguments is never accepted.
    """
    cfg = load_config()

    if ctx is None:
        # System request (cron/background): strict kind=system, no personal memory
        if cfg.fallback_user:
            return PrincipalContext.system(cfg.fallback_user)
        return PrincipalContext.anonymous()

    session_source = _resolve_session_source(ctx)
    user_id: Optional[str] = None
    groups: tuple[str, ...] = ()
    conversation_id: Optional[str] = None
    channel: Optional[str] = None

    if session_source is not None:
        extra_headers: Optional[dict] = None
        if isinstance(session_source, dict):
            extra_headers = session_source.get("extra_headers") or None
            user_id = session_source.get("user_id") or session_source.get("user_name")
        else:
            extra_headers = getattr(session_source, "extra_headers", None)
            user_id = getattr(session_source, "user_id", None) or getattr(session_source, "user_name", None)

        if isinstance(extra_headers, dict):
            # Anti-Spoofing: ist ein Adapter-Secret konfiguriert, werden
            # Identity-Header nur mit gültigem X-Adapter-Secret akzeptiert.
            if not validate_adapter_secret(extra_headers.get("X-Adapter-Secret")):
                logger.warning(
                    "[X-On-Behalf] Ungültiges/fehlendes X-Adapter-Secret — "
                    "Identity-Header werden verworfen (anonymous)."
                )
                return PrincipalContext.anonymous()
            user_id = extra_headers.get("X-On-Behalf-Of") or user_id
            raw_groups = extra_headers.get("X-User-Groups")
            if raw_groups:
                groups = tuple(
                    g.strip().lower() for g in str(raw_groups).split(",") if g.strip()
                )
            conversation_id = extra_headers.get("X-Conversation-Id") or conversation_id
            channel = extra_headers.get("X-Source-Adapter") or channel

    if user_id:
        return PrincipalContext.interactive(
            user_id=str(user_id),
            groups=groups,
            organization=cfg.organization,
            conversation_id=conversation_id,
            channel=channel,
        )
    return PrincipalContext.anonymous()


def extract_identity_from_context(ctx: Any) -> tuple[Optional[str], Optional[str]]:
    """Backward-friendly helper: (user_id, groups-string) from the session context."""
    principal = build_principal_from_context(ctx)
    if not principal.has_identity:
        return (None, None)
    return (principal.user_id, ",".join(principal.groups) if principal.groups else None)


def on_pre_tool_call(tool_name: str = "", args: Any = None, **kwargs: Any) -> Any:
    """Hook: derive headers for the active principal on outbound tool requests.

    Security: identity comes exclusively from the ContextVar/session context —
    a header in tool arguments is never trusted as a source, only overwritten.
    """
    principal = get_principal()
    _log("pre_tool_call tool='%s' principal=%s", tool_name, principal.user_id if principal else None)

    headers = principal_to_headers(principal)
    if not headers:
        return kwargs.get("request") or kwargs.get("payload") or args or kwargs

    # Overwrite (never trust) identity headers — aber nur auf Containern, die
    # bereits ein headers-Dict tragen. Beliebige Tool-Payloads werden nicht
    # mutiert (die Transport-Header setzen ohnehin die HTTP-Interzeptoren).
    for candidate in (kwargs.get("request"), kwargs.get("payload"), args):
        if isinstance(candidate, dict):
            container = candidate.get("headers")
            if isinstance(container, dict):
                container.update(headers)
        elif candidate is not None and hasattr(candidate, "headers"):
            try:
                if isinstance(candidate.headers, dict):
                    candidate.headers.update(headers)
            except Exception:
                pass

    request_headers = kwargs.get("headers")
    if isinstance(request_headers, dict):
        request_headers.update(headers)

    return kwargs.get("request") or kwargs.get("payload") or args or kwargs


def register(ctx: Any) -> None:
    """Register Hermes lifecycle hooks and activate HTTP interceptors."""
    cfg = load_config()
    logger.info(
        "[X-On-Behalf] Registriere Identity-Hooks (organization=%s, honcho=%s, secret_check=%s)...",
        cfg.organization or "-",
        cfg.honcho.enabled,
        bool(cfg.adapter_secret),
    )

    apply_http_interceptors()

    # Optional Honcho integration (fails soft — Honcho usage is optional)
    if cfg.honcho.enabled:
        from .honcho import patch_honcho_provider

        try:
            patch_honcho_provider()
        except Exception as exc:
            logger.warning("[X-On-Behalf] Honcho-Provider-Patch fehlgeschlagen: %s", exc)

    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_tool_call", on_pre_tool_call)
        logger.info("[X-On-Behalf] Registered 'pre_tool_call'.")

    if hasattr(ctx, "register_middleware"):
        ctx.register_middleware("tool_request", on_pre_tool_call)
        logger.info("[X-On-Behalf] Registered 'tool_request' middleware.")

    # Bundled skills (same mechanism as the Deck plugin)
    _register_skills(ctx)


def _register_skills(ctx: Any) -> None:
    """Register plugin-local skills (e.g. memory-routing) via ctx.register_skill."""
    from pathlib import Path

    skills_dir = Path(__file__).parent / "skills"
    if not skills_dir.is_dir():
        return
    registered = 0
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.is_file() and hasattr(ctx, "register_skill"):
            try:
                ctx.register_skill(child.name, skill_md)
                registered += 1
            except Exception as exc:
                logger.warning("[X-On-Behalf] Skill '%s' konnte nicht registriert werden: %s", child.name, exc)
    if registered:
        logger.info("[X-On-Behalf] %d Skill(s) registriert.", registered)
