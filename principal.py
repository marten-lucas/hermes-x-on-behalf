"""PrincipalContext: the single identity/scope structure for all propagation."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class PrincipalKind(str, Enum):
    """Interactive = real human request; system = cron/fallback; anonymous = no identity."""
    INTERACTIVE = "interactive"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class PrincipalContext:
    """Authoritative identity context for one request.

    Built exclusively from the platform adapter (authoritative identity from
    Nextcloud), never from LLM/tool arguments or client-supplied scope headers.
    """

    user_id: Optional[str] = None
    groups: Tuple[str, ...] = ()
    organization: Optional[str] = None
    conversation_id: Optional[str] = None
    conversation_description: Optional[str] = None  # room description / board title (memory tags)
    channel: Optional[str] = None
    kind: PrincipalKind = PrincipalKind.ANONYMOUS

    @staticmethod
    def normalize_user(user_id: Optional[str]) -> Optional[str]:
        """Lowercase, trimmed user id (Nextcloud ids may vary in case)."""
        if not user_id:
            return None
        cleaned = str(user_id).strip().lower()
        return cleaned or None

    @staticmethod
    def normalize_groups(groups) -> Tuple[str, ...]:
        """Lowercase, trimmed, deduplicated, sorted group tuple."""
        if not groups:
            return ()
        seen: set[str] = set()
        result: list[str] = []
        for g in groups:
            name = str(g).strip().lower()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return tuple(sorted(result))

    @classmethod
    def interactive(
        cls,
        user_id: str,
        groups=(),
        organization: Optional[str] = None,
        conversation_id: Optional[str] = None,
        conversation_description: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> "PrincipalContext":
        """Build a principal for a real human request (authoritative path)."""
        return cls(
            user_id=cls.normalize_user(user_id),
            groups=cls.normalize_groups(groups),
            organization=organization,
            conversation_id=conversation_id,
            conversation_description=conversation_description,
            channel=channel,
            kind=PrincipalKind.INTERACTIVE,
        )

    @classmethod
    def system(cls, user_id: Optional[str] = None) -> "PrincipalContext":
        """Build a system principal (cron, background job). Never personal memory."""
        return cls(
            user_id=cls.normalize_user(user_id),
            groups=(),
            kind=PrincipalKind.SYSTEM,
        )

    @classmethod
    def anonymous(cls) -> "PrincipalContext":
        return cls(kind=PrincipalKind.ANONYMOUS)

    @property
    def is_interactive(self) -> bool:
        return self.kind == PrincipalKind.INTERACTIVE

    @property
    def is_system(self) -> bool:
        return self.kind == PrincipalKind.SYSTEM

    @property
    def has_identity(self) -> bool:
        return bool(self.user_id) and self.kind != PrincipalKind.ANONYMOUS
