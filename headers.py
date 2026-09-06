"""Header derivation from PrincipalContext + optional shared-secret validation."""
from __future__ import annotations

import hmac
import logging
from typing import Dict, Optional

from .config import load_config
from .context import get_principal
from .principal import PrincipalContext
from .scopes import resolve_memory_scopes

logger = logging.getLogger(__name__)


def validate_adapter_secret(incoming_secret: Optional[str]) -> bool:
    """Validate X-Adapter-Secret if HERMES_X_ON_BEHALF_ADAPTER_SECRET is configured.

    If no secret is configured, validation is disabled (returns True).
    """
    cfg = load_config()
    if not cfg.adapter_secret:
        return True
    if not incoming_secret:
        return False
    return hmac.compare_digest(incoming_secret, cfg.adapter_secret)


def principal_to_headers(principal: Optional[PrincipalContext]) -> Dict[str, str]:
    """Derive propagation headers from a PrincipalContext.

    X-User-Groups carries the *raw* Nextcloud groups (for Agentgateway RBAC);
    memory scopes are computed server-side and are NOT transported as headers
    (Briefing 3 §8: Hermes computes scopes authoritatively).
    """
    if principal is None or not principal.has_identity:
        return {}

    headers: Dict[str, str] = {}
    if principal.user_id:
        headers["X-On-Behalf-Of"] = principal.user_id
    if principal.groups:
        headers["X-User-Groups"] = ",".join(principal.groups)
    if principal.conversation_id:
        headers["X-Conversation-Id"] = principal.conversation_id
    if principal.channel:
        headers["X-Source-Adapter"] = principal.channel
    return headers


def current_headers() -> Dict[str, str]:
    """Headers for the currently active principal (used by HTTP interceptors)."""
    return principal_to_headers(get_principal())
