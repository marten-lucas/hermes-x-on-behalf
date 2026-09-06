"""ContextVar handling with token-based set/reset (leak-proof)."""
from __future__ import annotations

import contextlib
import contextvars
from typing import Iterator, Optional

from .principal import PrincipalContext

current_principal: contextvars.ContextVar[Optional[PrincipalContext]] = contextvars.ContextVar(
    "current_principal", default=None
)


def get_principal() -> Optional[PrincipalContext]:
    """Return the active PrincipalContext, or None."""
    return current_principal.get()


@contextlib.contextmanager
def principal_context(principal: PrincipalContext) -> Iterator[PrincipalContext]:
    """Set the principal for the duration of the block; always resets via token.

    Usage in adapters:

        with principal_context(principal):
            await self.handle_message(event)
    """
    if not isinstance(principal, PrincipalContext):
        raise TypeError(f"principal_context expects PrincipalContext, got {type(principal)!r}")
    token = current_principal.set(principal)
    try:
        yield principal
    finally:
        current_principal.reset(token)
