# ~/.hermes/plugins/hermes-x-on-behalf/__init__.py
from .plugin import inject_mcp_identity_headers, register

__all__ = ["register", "inject_mcp_identity_headers"]
