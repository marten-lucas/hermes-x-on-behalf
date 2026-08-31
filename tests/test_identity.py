from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "plugin.py"
# Als Package laden, damit relative Imports (from .interceptor import ...) funktionieren
PKG_NAME = "hermes_x_on_behalf"
PKG_PATH = Path(__file__).resolve().parents[1]
if PKG_NAME not in __import__("sys").modules:
    import sys
    import types
    _pkg = types.ModuleType(PKG_NAME)
    _pkg.__path__ = [str(PKG_PATH)]
    sys.modules[PKG_NAME] = _pkg
SPEC = importlib.util.spec_from_file_location(f"{PKG_NAME}.plugin", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys_modules = __import__("sys").modules
sys_modules[f"{PKG_NAME}.plugin"] = MODULE
SPEC.loader.exec_module(MODULE)
extract_identity_from_context = MODULE.extract_identity_from_context
inject_mcp_identity_headers = MODULE.inject_mcp_identity_headers


class FakeSource:
    def __init__(self, *, user_id=None, user_name=None, extra_headers=None):
        self.user_id = user_id
        self.user_name = user_name
        self.extra_headers = extra_headers or {}


class IdentityInjectionTests(unittest.TestCase):
    def test_extracts_identity_from_source_headers(self):
        source = FakeSource(
            user_id="hermes-bot",
            extra_headers={"X-On-Behalf-Of": "marten", "X-User-Groups": "admin,kiga_board"},
        )
        self.assertEqual(("marten", "admin,kiga_board"), extract_identity_from_context(type("ctx", (), {"source": source})()))

    def test_extracts_identity_from_user_id_when_no_headers_exist(self):
        source = FakeSource(user_id="marten")
        self.assertEqual(("marten", None), extract_identity_from_context(type("ctx", (), {"source": source})()))

    def test_injects_headers_into_request_metadata(self):
        request = {"headers": {"Accept": "application/json"}}
        ctx = type("ctx", (), {"source": FakeSource(user_id="marten", extra_headers={"X-User-Groups": "admin"})})()

        result = inject_mcp_identity_headers(request, ctx)

        self.assertEqual("marten", result["headers"]["X-On-Behalf-Of"])
        self.assertEqual("admin", result["headers"]["X-User-Groups"])

    def test_uses_fallback_user_when_context_is_missing(self):
        original = os.environ.get("MCP_IDENTITY_FALLBACK_USER")
        try:
            os.environ["MCP_IDENTITY_FALLBACK_USER"] = "cronjob-user"
            self.assertEqual(("cronjob-user", None), extract_identity_from_context(None))
        finally:
            if original is None:
                os.environ.pop("MCP_IDENTITY_FALLBACK_USER", None)
            else:
                os.environ["MCP_IDENTITY_FALLBACK_USER"] = original


if __name__ == "__main__":
    unittest.main()
