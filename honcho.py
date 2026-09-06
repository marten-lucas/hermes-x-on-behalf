"""Honcho provider wrapper (optional): PrincipalContext -> workspace/peer/session.

Only active when `honcho.enabled: true` in the YAML config (R1: we wrap Hermes'
own Honcho memory provider rather than talking to Honcho directly).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .config import load_config
from .context import get_principal
from .principal import PrincipalContext
from .scopes import MemoryScopes, resolve_memory_scopes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HonchoTarget:
    """Honcho entities derived from a PrincipalContext."""
    workspace_id: str
    peer_id: str
    session_id: str
    scopes: MemoryScopes


def resolve_honcho_target(
    principal: Optional[PrincipalContext] = None,
    fallback_session: str = "default",
) -> Optional[HonchoTarget]:
    """Translate a PrincipalContext into Honcho workspace/peer/session identifiers.

    Model (Briefing 3 §6): one workspace, one peer per user, one session per
    conversation surface. Returns None when Honcho is disabled or no principal
    with identity is active.
    """
    cfg = load_config()
    if not cfg.honcho.enabled:
        return None

    principal = principal or get_principal()
    if principal is None or not principal.has_identity:
        return None

    workspace_id = cfg.honcho.workspace_id or cfg.organization or "default"
    peer_id = f"user:{principal.user_id}"
    session_id = principal.conversation_id or fallback_session
    scopes = resolve_memory_scopes(principal, cfg)

    return HonchoTarget(
        workspace_id=workspace_id,
        peer_id=peer_id,
        session_id=session_id,
        scopes=scopes,
    )


def patch_honcho_provider() -> bool:
    """Wrap Hermes' Honcho memory provider so it uses the PrincipalContext.

    Tries to locate Hermes' honcho memory plugin/provider and replaces the
    peer resolution with principal-derived values. Idempotent; returns True
    when a provider was patched, False when Honcho integration is unavailable
    or disabled (which is fine — Honcho usage is optional).
    """
    cfg = load_config()
    if not cfg.honcho.enabled:
        logger.debug("[X-On-Behalf] Honcho-Integration deaktiviert (honcho.enabled=false).")
        return False

    try:
        # Hermes ships its honcho memory provider under plugins/memory/honcho;
        # we patch its peer/session resolution defensively.
        from hermes.plugins.memory.honcho import provider as honcho_provider  # type: ignore[import-not-found]
    except Exception as exc:
        logger.warning(
            "[X-On-Behalf] Hermes-Honcho-Provider nicht gefunden — Honcho-Anbindung übersprungen. "
            "Prüfe die Provider-Schnittstelle nach einem Hermes-Update. (%s)",
            exc,
        )
        return False

    if getattr(honcho_provider, "_xonbehalf_patched", False):
        return True

    original_resolve = getattr(honcho_provider, "resolve_peer", None)
    if original_resolve is None:
        logger.warning(
            "[X-On-Behalf] Hermes-Honcho-Provider hat keine 'resolve_peer'-Methode — "
            "Schnittstelle hat sich möglicherweise geändert, Patch übersprungen."
        )
        return False

    def resolve_peer_with_principal(*args: Any, **kwargs: Any) -> Any:
        target = resolve_honcho_target()
        if target is not None:
            return target.peer_id
        return original_resolve(*args, **kwargs)

    resolve_peer_with_principal._xonbehalf_original = original_resolve  # type: ignore[attr-defined]
    honcho_provider.resolve_peer = resolve_peer_with_principal  # type: ignore[assignment]
    honcho_provider._xonbehalf_patched = True  # type: ignore[attr-defined]
    logger.info("[X-On-Behalf] Hermes-Honcho-Provider gepatcht (Principal-basierte Peer-Auflösung).")
    return True
