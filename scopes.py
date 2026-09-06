"""MemoryScopeResolver: maps groups (server-side) to memory scopes.

Security rule: scopes are ALWAYS computed here from the authoritative
PrincipalContext — never accepted from client headers or tool arguments.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

from .config import MEMORY_TAG_PREFIX, PluginConfig, load_config
from .principal import PrincipalContext

logger = logging.getLogger(__name__)

# [memory:team:it-admin] / [memory:personal] / [memory:org] — leading tag in
# Talk room descriptions or Deck board titles. Multiple tags allowed.
_MEMORY_TAG_RE = re.compile(re.escape(MEMORY_TAG_PREFIX) + r"([^\]]+)\]", re.IGNORECASE)

_SCOPE_PREFIXES = ("team:", "org:", "personal")


def parse_memory_tags(text: Optional[str]) -> Tuple[str, ...]:
    """Extract memory scopes from a conversation description.

    Convention (Talk only): `[memory:team:it-admin]` anywhere in the room
    description. Falls back to shorthand: `[memory:personal]` → the user's
    personal scope; `[memory:org]` → the organization scope. Bare names like
    `[memory:it-admin]` are treated as team scopes. Returns an empty tuple
    when no tag is present.

    Deck board titles are deliberately NOT used for tagging (user-owned).
    """
    if not text:
        return ()
    tags: list[str] = []
    for match in _MEMORY_TAG_RE.finditer(text):
        raw = match.group(1).strip().lower()
        if not raw:
            continue
        if raw in ("personal", "org") or raw.startswith(_SCOPE_PREFIXES):
            tags.append(raw)
        else:
            # bare name like [memory:it-admin] → treat as team scope
            tags.append(f"team:{raw}")
    # dedup, keep order
    seen: set[str] = set()
    return tuple(t for t in tags if not (t in seen or seen.add(t)))


def resolve_conversation_scope(
    conversation_id: Optional[str],
    conversation_description: Optional[str] = None,
    config: Optional[PluginConfig] = None,
) -> Optional[str]:
    """Deterministic default scope for a conversation surface.

    Priority: memory tag in the Talk room description > explicit
    conversation_scopes mapping (used for Deck boards and tag-less rooms) >
    fallback_scope (usually "personal"). Returns None when no conversation
    context exists.

    Deck: scopes come from the explicit `conversation_scopes` list keyed by
    `deck:board:<id>` / `deck:board:<id>:card:<id>` — board titles are never
    parsed for tags.
    """
    if not conversation_id:
        return None
    cfg = config or load_config()

    # 1) Tag from the room description (Talk only — scales with new rooms)
    tags = parse_memory_tags(conversation_description)
    if tags:
        return tags[0]

    # 2) Explicit mapping: exact match first, then longest prefix match
    # (e.g. conversation "deck:board:3:card:44" matches configured
    # "deck:board:3"; Talk rooms match on the full token). The boundary check
    # prevents "deck:board:3" from matching "deck:board:30".
    scopes_map = cfg.memory.conversation_scopes
    if conversation_id in scopes_map:
        return scopes_map[conversation_id]
    best_prefix = ""
    for key in scopes_map:
        if (
            len(key) > len(best_prefix)
            and conversation_id.startswith(key)
            and conversation_id[len(key):len(key) + 1] in ("", ":", "/")
        ):
            best_prefix = key
    if best_prefix:
        return scopes_map[best_prefix]

    # 3) Fallback (new untagged rooms, DMs)
    return cfg.memory.fallback_scope or None


@dataclass(frozen=True)
class MemoryScopes:
    """Resolved memory view for one request."""
    personal: Optional[str] = None          # e.g. "personal:user:alice"
    teams: Tuple[str, ...] = ()             # e.g. ("team:developers",)
    organization: Optional[str] = None      # e.g. "org:kiga"
    # Deterministic default scope for the current conversation surface
    default_scope: Optional[str] = field(default=None)

    def all(self) -> Tuple[str, ...]:
        scopes: list[str] = []
        if self.personal:
            scopes.append(self.personal)
        scopes.extend(self.teams)
        if self.organization:
            scopes.append(self.organization)
        return tuple(scopes)

    def is_allowed(self, scope: str) -> bool:
        """Whether the agent may route memory to this scope (security gate)."""
        return scope in self.all()


def resolve_memory_scopes(
    principal: PrincipalContext,
    config: Optional[PluginConfig] = None,
    conversation_description: Optional[str] = None,
) -> MemoryScopes:
    """Compute MemoryScopes from a PrincipalContext via the configured group mapping.

    Rules (Briefing 3 §8, §9):
    - Only interactive principals get personal scopes.
    - System/anonymous principals get no personal or team memory.
    - Unmapped groups default to "ignore" (never implicitly a memory team).
    - organization groups map to the org scope, permission/ignore groups to nothing.
    - default_scope: deterministic conversation scope, intersected with allowed scopes.
    """
    cfg = config or load_config()
    if principal is None or not principal.has_identity:
        return MemoryScopes()

    personal: Optional[str] = None
    teams: list[str] = []
    organization: Optional[str] = None

    if principal.is_interactive:
        if cfg.memory.personal:
            personal = f"personal:user:{principal.user_id}"

        for group in principal.groups:
            mapping = cfg.group_mapping.get(group)
            gtype = mapping.type if mapping else "ignore"

            if gtype == "team" and cfg.memory.teams:
                scope = mapping.scope if mapping and mapping.scope else f"team:{mapping.name if mapping else group}"
                teams.append(scope)
            elif gtype == "organization" and cfg.memory.organization:
                organization = organization or (
                    mapping.scope if mapping and mapping.scope
                    else f"org:{cfg.organization or principal.organization or 'default'}"
                )
            # "permission" and "ignore" never become memory scopes

        # explicit organization on the principal (from config) wins as org scope
        if organization is None and cfg.memory.organization and cfg.organization:
            organization = f"org:{cfg.organization}"

    # Deterministic conversation default scope — only kept when the principal
    # is actually allowed to use it (security gate against cross-team leaks).
    raw_default = resolve_conversation_scope(
        principal.conversation_id,
        conversation_description or principal.conversation_description,
        cfg,
    )
    default_scope: Optional[str] = None
    if raw_default:
        if raw_default == "personal":
            default_scope = personal
        elif raw_default == "org":
            default_scope = organization
        elif raw_default.startswith("team:") and raw_default in tuple(teams):
            default_scope = raw_default
        elif raw_default in (personal, organization):
            default_scope = raw_default
        # else: scope not in the principal's allowed set → stays None (falls
        # back to the skill's ask-first rule)

    scopes = MemoryScopes(
        personal=personal,
        teams=tuple(sorted(set(teams))),
        organization=organization,
        default_scope=default_scope,
    )

    if cfg.debug:
        logger.info(
            "[X-On-Behalf] principal user=%s kind=%s groups=%s conversation=%s "
            "default_scope=%s memory_scopes=%s",
            principal.user_id,
            principal.kind.value,
            ",".join(principal.groups) or "-",
            principal.conversation_id or "-",
            default_scope or "-",
            list(scopes.all()),
        )
    return scopes
