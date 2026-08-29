# ~/.hermes/plugins/hermes-x-on-behalf/__init__.py
from .plugin import extract_identity_from_context, inject_mcp_identity_headers, register

__all__ = ["register", "extract_identity_from_context", "inject_mcp_identity_headers"]
