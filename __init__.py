<<<<<<< HEAD
# ~/.hermes/plugins/hermes-x-on-behalf/__init__.py
from .plugin import extract_identity_from_context, inject_mcp_identity_headers, register

__all__ = ["register", "extract_identity_from_context", "inject_mcp_identity_headers"]
=======
from .plugin import register

__all__ = ["register"]
>>>>>>> f51496b (Füge Unterstützung für Benutzeridentitätskontext und HTTP-Header-Injektion hinzu)
