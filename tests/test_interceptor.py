"""Tests for the HTTP interceptors and the adapter-secret gate.

Covers the paths that were previously untested:
- httpx/aiohttp interceptors inject the ACTIVE principal's headers
  (X-On-Behalf-Of, X-User-Groups, X-Conversation-Id, X-Source-Adapter)
- no active principal → no identity headers
- build_principal_from_context rejects spoofed identity headers when
  HERMES_X_ON_BEHALF_ADAPTER_SECRET is configured
- conversation_scopes prefix matching respects segment boundaries
  ("deck:board:3" must NOT match "deck:board:30")
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

PKG_PATH = Path(__file__).resolve().parents[1]
PKG_NAME = "hermes_x_on_behalf"
if PKG_NAME not in sys.modules:
    import importlib.util
    import types

    _pkg = types.ModuleType(PKG_NAME)
    _pkg.__path__ = [str(PKG_PATH)]
    sys.modules[PKG_NAME] = _pkg
    _init_spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.__init__", PKG_PATH / "__init__.py"
    )
    _init_mod = importlib.util.module_from_spec(_init_spec)
    _init_spec.loader.exec_module(_init_mod)
    sys.modules[PKG_NAME] = _init_mod

from hermes_x_on_behalf import (  # noqa: E402
    PrincipalContext,
    PrincipalKind,
    get_principal,
    principal_context,
    resolve_conversation_scope,
)
from hermes_x_on_behalf.config import (  # noqa: E402
    GroupMapping,
    MemoryConfig,
    PluginConfig,
    reset_config_cache,
)
from hermes_x_on_behalf.interceptor import _get_active_headers, apply_http_interceptors  # noqa: E402
from hermes_x_on_behalf.plugin import build_principal_from_context  # noqa: E402


def _test_config() -> PluginConfig:
    return PluginConfig(
        organization="kiga",
        group_mapping={"developers": GroupMapping("developers", "team")},
        memory=MemoryConfig(personal=True, teams=True, organization=True),
    )


class ActiveHeaderTests(unittest.TestCase):
    def test_no_principal_no_headers(self):
        self.assertEqual({}, _get_active_headers())

    def test_active_principal_yields_all_headers(self):
        p = PrincipalContext.interactive(
            "alice",
            ["developers"],
            conversation_id="talk:room:42",
            channel="nextcloud-talk",
        )
        with principal_context(p):
            headers = _get_active_headers()
        self.assertEqual("alice", headers["X-On-Behalf-Of"])
        self.assertEqual("developers", headers["X-User-Groups"])
        self.assertEqual("talk:room:42", headers["X-Conversation-Id"])
        self.assertEqual("nextcloud-talk", headers["X-Source-Adapter"])

    def test_system_principal_yields_only_user_header(self):
        # System-Principals (Cron) propagieren ihre user_id, aber niemals
        # Gruppen/Conversation — und sie erhalten serverseitig kein
        # Personal-/Team-Memory (siehe resolve_memory_scopes).
        with principal_context(PrincipalContext.system("cron")):
            headers = _get_active_headers()
        self.assertEqual("cron", headers["X-On-Behalf-Of"])
        self.assertNotIn("X-User-Groups", headers)
        self.assertNotIn("X-Conversation-Id", headers)

    def test_anonymous_principal_yields_no_headers(self):
        with principal_context(PrincipalContext.anonymous()):
            self.assertEqual({}, _get_active_headers())

    def test_no_principal_uses_service_identity(self):
        # Ohne aktiven Principal liefert der Interceptor die konfigurierte
        # Service-Identity (ki-assistent + it-admin) für Agentgateway-RBAC.
        reset_config_cache()
        os.environ["MCP_IDENTITY_SERVICE_USER"] = "ki-assistent"
        os.environ["MCP_IDENTITY_SERVICE_GROUPS"] = "it-admin,vorstand"
        try:
            headers = _get_active_headers()
        finally:
            os.environ.pop("MCP_IDENTITY_SERVICE_USER", None)
            os.environ.pop("MCP_IDENTITY_SERVICE_GROUPS", None)
            reset_config_cache()
        self.assertEqual("ki-assistent", headers["X-On-Behalf-Of"])
        self.assertEqual("it-admin,vorstand", headers["X-User-Groups"])

    def test_no_principal_no_service_identity(self):
        reset_config_cache()
        # Keine Service-Identity konfiguriert → keine Header.
        self.assertEqual({}, _get_active_headers())


class HttpxInterceptorTests(unittest.TestCase):
    def test_patched_send_injects_headers(self):
        httpx = pytest_importorskip_httpx()
        apply_http_interceptors()

        sent_requests = []

        async def handler(request):
            sent_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        async def run():
            p = PrincipalContext.interactive("alice", ["developers"], conversation_id="talk:room:7")
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                with principal_context(p):
                    await client.get("https://mcp.example.org/tool")
                # outside the context: no identity headers
                await client.get("https://mcp.example.org/other")

        asyncio.run(run())

        self.assertEqual(2, len(sent_requests))
        with_identity, without_identity = sent_requests
        self.assertEqual("alice", with_identity.headers.get("X-On-Behalf-Of"))
        self.assertEqual("developers", with_identity.headers.get("X-User-Groups"))
        self.assertEqual("talk:room:7", with_identity.headers.get("X-Conversation-Id"))
        self.assertIsNone(without_identity.headers.get("X-On-Behalf-Of"))

    def test_parallel_requests_keep_their_own_identity(self):
        httpx = pytest_importorskip_httpx()
        apply_http_interceptors()

        seen = []

        async def handler(request):
            seen.append((request.headers.get("X-On-Behalf-Of"), request.url.path))
            return httpx.Response(200)

        async def work(user, path, delay):
            p = PrincipalContext.interactive(user)
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                with principal_context(p):
                    await asyncio.sleep(delay)
                    await client.get(f"https://mcp.example.org/{path}")

        async def run():
            await asyncio.gather(work("alice", "a", 0.03), work("bob", "b", 0.01))

        asyncio.run(run())
        by_path = {path: user for user, path in seen}
        self.assertEqual("alice", by_path["/a"])
        self.assertEqual("bob", by_path["/b"])


def pytest_importorskip_httpx():
    try:
        import httpx

        return httpx
    except ImportError:
        raise unittest.SkipTest("httpx nicht installiert")


class AdapterSecretGateTests(unittest.TestCase):
    """build_principal_from_context must reject spoofed identity headers."""

    def setUp(self):
        reset_config_cache()
        os.environ["HERMES_X_ON_BEHALF_ADAPTER_SECRET"] = "s3cret"

    def tearDown(self):
        os.environ.pop("HERMES_X_ON_BEHALF_ADAPTER_SECRET", None)
        reset_config_cache()

    @staticmethod
    def _ctx(extra_headers):
        source = type("src", (), {"user_id": "bot", "extra_headers": extra_headers})
        return type("ctx", (), {"source": source()})()

    def test_valid_secret_accepts_identity(self):
        ctx = self._ctx({"X-On-Behalf-Of": "marten", "X-Adapter-Secret": "s3cret"})
        p = build_principal_from_context(ctx)
        self.assertEqual("marten", p.user_id)
        self.assertEqual(PrincipalKind.INTERACTIVE, p.kind)

    def test_missing_secret_rejects_identity(self):
        ctx = self._ctx({"X-On-Behalf-Of": "mallory"})
        p = build_principal_from_context(ctx)
        self.assertEqual(PrincipalKind.ANONYMOUS, p.kind)
        self.assertIsNone(p.user_id)

    def test_wrong_secret_rejects_identity(self):
        ctx = self._ctx({"X-On-Behalf-Of": "mallory", "X-Adapter-Secret": "wrong"})
        p = build_principal_from_context(ctx)
        self.assertEqual(PrincipalKind.ANONYMOUS, p.kind)

    def test_no_secret_configured_accepts_identity(self):
        os.environ.pop("HERMES_X_ON_BEHALF_ADAPTER_SECRET", None)
        reset_config_cache()
        ctx = self._ctx({"X-On-Behalf-Of": "marten"})
        p = build_principal_from_context(ctx)
        self.assertEqual("marten", p.user_id)


class PrefixBoundaryTests(unittest.TestCase):
    def test_board_prefix_does_not_match_longer_id(self):
        cfg = _test_config()
        cfg.memory.conversation_scopes = {"deck:board:3": "team:developers"}
        # exact card under board 3 → match
        self.assertEqual(
            "team:developers",
            resolve_conversation_scope("deck:board:3:card:44", None, cfg),
        )
        # board 30 must NOT match the board:3 prefix
        self.assertEqual(
            cfg.memory.fallback_scope,
            resolve_conversation_scope("deck:board:30:card:44", None, cfg),
        )
        self.assertEqual(
            cfg.memory.fallback_scope,
            resolve_conversation_scope("deck:board:30", None, cfg),
        )

    def test_longest_prefix_wins(self):
        cfg = _test_config()
        cfg.memory.conversation_scopes = {
            "deck:board:3": "team:developers",
            "deck:board:3:card:44": "team:other",
        }
        self.assertEqual(
            "team:other",
            resolve_conversation_scope("deck:board:3:card:44", None, cfg),
        )
        self.assertEqual(
            "team:developers",
            resolve_conversation_scope("deck:board:3:card:45", None, cfg),
        )


if __name__ == "__main__":
    unittest.main()
