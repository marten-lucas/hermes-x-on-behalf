# Hermes X-On-Behalf: Identity / Principal Context Propagation Plugin
from .context import current_principal, get_principal, principal_context
from .headers import current_headers, principal_to_headers, validate_adapter_secret
from .plugin import (
    build_principal_from_context,
    extract_identity_from_context,
    on_pre_tool_call,
    register,
)
from .principal import PrincipalContext, PrincipalKind
from .scopes import (
    MemoryScopes,
    parse_memory_tags,
    resolve_conversation_scope,
    resolve_memory_scopes,
)

__all__ = [
    "PrincipalContext",
    "PrincipalKind",
    "MemoryScopes",
    "current_principal",
    "get_principal",
    "principal_context",
    "current_headers",
    "principal_to_headers",
    "validate_adapter_secret",
    "resolve_memory_scopes",
    "resolve_conversation_scope",
    "parse_memory_tags",
    "build_principal_from_context",
    "extract_identity_from_context",
    "on_pre_tool_call",
    "register",
]
