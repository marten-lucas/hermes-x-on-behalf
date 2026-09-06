"""Tests for hermes-x-on-behalf: principal, context isolation, scopes, headers."""
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
    # Führe das echte Paket-__init__ aus, damit alle Exporte existieren
    _init_spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.__init__", PKG_PATH / "__init__.py"
    )
    _init_mod = importlib.util.module_from_spec(_init_spec)
    _init_spec.loader.exec_module(_init_mod)
    sys.modules[PKG_NAME] = _init_mod

from hermes_x_on_behalf import (  # noqa: E402
    PrincipalContext,
    PrincipalKind,
    extract_identity_from_context,
    get_principal,
    parse_memory_tags,
    principal_context,
    principal_to_headers,
    resolve_conversation_scope,
    resolve_memory_scopes,
    validate_adapter_secret,
)
from hermes_x_on_behalf.config import PluginConfig, GroupMapping, MemoryConfig, reset_config_cache  # noqa: E402


def _test_config(**overrides) -> PluginConfig:
    cfg = PluginConfig(
        organization="kiga",
        group_mapping={
            "developers": GroupMapping("developers", "team"),
            "project-x": GroupMapping("project-x", "team"),
            "employees": GroupMapping("employees", "organization"),
            "admin": GroupMapping("admin", "ignore"),
        },
        memory=MemoryConfig(personal=True, teams=True, organization=True),
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class PrincipalTests(unittest.TestCase):
    def test_normalizes_user_and_groups(self):
        p = PrincipalContext.interactive("Marten_L ", ["Admin", "developers", "admin"], organization="kiga")
        self.assertEqual("marten_l", p.user_id)
        self.assertEqual(("admin", "developers"), p.groups)  # dedup + sorted
        self.assertTrue(p.is_interactive)

    def test_system_principal_has_no_groups(self):
        p = PrincipalContext.system("cron-user")
        self.assertTrue(p.is_system)
        self.assertEqual((), p.groups)


class ContextIsolationTests(unittest.TestCase):
    def test_context_resets_after_exit(self):
        p = PrincipalContext.interactive("alice")
        with principal_context(p):
            self.assertIs(p, get_principal())
        self.assertIsNone(get_principal())

    def test_reset_even_on_exception(self):
        p = PrincipalContext.interactive("alice")
        with self.assertRaises(RuntimeError):
            with principal_context(p):
                raise RuntimeError("boom")
        self.assertIsNone(get_principal())

    def test_no_identity_crossover_between_concurrent_tasks(self):
        alice = PrincipalContext.interactive("alice", ["developers"])
        bob = PrincipalContext.interactive("bob", ["project-x"])

        async def work(principal, delay):
            with principal_context(principal):
                await asyncio.sleep(delay)
                return get_principal().user_id

        async def run():
            # Alice starts first but finishes second → she must still see alice
            results = await asyncio.gather(work(alice, 0.03), work(bob, 0.01))
            return results

        r = asyncio.run(run())
        self.assertEqual(["alice", "bob"], r)

    def test_nested_context_restores_outer(self):
        alice = PrincipalContext.interactive("alice")
        bob = PrincipalContext.interactive("bob")
        with principal_context(alice):
            with principal_context(bob):
                self.assertEqual("bob", get_principal().user_id)
            self.assertEqual("alice", get_principal().user_id)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = _test_config()

    def test_team_groups_map_to_team_scopes(self):
        p = PrincipalContext.interactive("alice", ["developers", "project-x"])
        scopes = resolve_memory_scopes(p, self.cfg)
        self.assertEqual("personal:user:alice", scopes.personal)
        self.assertEqual(("team:developers", "team:project-x"), scopes.teams)

    def test_ignore_and_permission_groups_never_become_scopes(self):
        p = PrincipalContext.interactive("alice", ["admin", "employees"])
        scopes = resolve_memory_scopes(p, self.cfg)
        self.assertEqual((), scopes.teams)
        self.assertEqual("org:kiga", scopes.organization)  # employees → organization

    def test_system_principal_gets_no_personal_or_team_memory(self):
        p = PrincipalContext.system("cron-user")
        scopes = resolve_memory_scopes(p, self.cfg)
        self.assertIsNone(scopes.personal)
        self.assertEqual((), scopes.teams)

    def test_anonymous_gets_nothing(self):
        scopes = resolve_memory_scopes(PrincipalContext.anonymous(), self.cfg)
        self.assertEqual((), scopes.all())

    def test_unmapped_group_defaults_to_ignore(self):
        p = PrincipalContext.interactive("alice", ["unknown-group"])
        scopes = resolve_memory_scopes(p, self.cfg)
        self.assertIn("personal:user:alice", scopes.all())
        # unmapped group produces no team scope; org scope comes from config only
        self.assertEqual((), scopes.teams)

    def test_cross_user_isolation(self):
        alice = resolve_memory_scopes(PrincipalContext.interactive("alice"), self.cfg)
        bob = resolve_memory_scopes(PrincipalContext.interactive("bob"), self.cfg)
        self.assertNotEqual(alice.personal, bob.personal)

    def test_non_member_does_not_get_team_scope(self):
        alice = resolve_memory_scopes(PrincipalContext.interactive("alice", ["developers"]), self.cfg)
        bob = resolve_memory_scopes(PrincipalContext.interactive("bob"), self.cfg)
        self.assertIn("team:developers", alice.teams)
        self.assertNotIn("team:developers", bob.teams)


class TagAndConversationScopeTests(unittest.TestCase):
    def test_parse_memory_tags(self):
        self.assertEqual(("team:it-admin",), parse_memory_tags("Technik-Raum [memory:team:it-admin] für Server"))
        self.assertEqual(("team:vorstand", "team:it-admin"), parse_memory_tags("[memory:vorstand] und [memory:team:it-admin]"))
        self.assertEqual(("personal",), parse_memory_tags("[memory:personal]"))
        self.assertEqual(("org",), parse_memory_tags("[memory:org]"))
        self.assertEqual((), parse_memory_tags("Kein Tag hier"))
        self.assertEqual((), parse_memory_tags(None))

    def test_conversation_scope_priority(self):
        cfg = _test_config()
        # 1) Tag wins
        self.assertEqual(
            "team:it-admin",
            resolve_conversation_scope("talk:room:x", "[memory:team:it-admin]", cfg),
        )
        # 2) explicit mapping
        cfg.memory.conversation_scopes = {"talk:room:fixed": "team:vorstand"}
        self.assertEqual("team:vorstand", resolve_conversation_scope("talk:room:fixed", None, cfg))
        # 3) fallback
        self.assertEqual("personal", resolve_conversation_scope("talk:room:new", None, cfg))
        # no conversation → no scope
        self.assertIsNone(resolve_conversation_scope(None, "[memory:team:x]", cfg))

    def test_default_scope_is_gated_by_allowed_scopes(self):
        cfg = _test_config()
        # Alice is in it-admin → tag applies
        alice = PrincipalContext.interactive("alice", ["developers"], conversation_id="talk:room:1")
        scopes = resolve_memory_scopes(alice, cfg, conversation_description="[memory:team:it-admin]")
        self.assertIsNone(scopes.default_scope)  # it-admin not in alice's groups

        # Bob is a member of the tagged team → default allowed
        cfg.group_mapping["it-admin"] = GroupMapping("it-admin", "team")
        bob = PrincipalContext.interactive("bob", ["it-admin"], conversation_id="talk:room:1")
        scopes_bob = resolve_memory_scopes(bob, cfg, conversation_description="[memory:team:it-admin]")
        self.assertEqual("team:it-admin", scopes_bob.default_scope)

    def test_default_scope_personal_for_untagged_room(self):
        cfg = _test_config()
        alice = PrincipalContext.interactive("alice", conversation_id="talk:room:dm")
        scopes = resolve_memory_scopes(alice, cfg)
        self.assertEqual("personal:user:alice", scopes.default_scope)

    def test_default_scope_system_principal_gets_none(self):
        cfg = _test_config()
        sysp = PrincipalContext.system("cron", )
        scopes = resolve_memory_scopes(sysp, cfg)
        self.assertIsNone(scopes.default_scope)
        self.assertIsNone(scopes.personal)


class HeaderTests(unittest.TestCase):
    def test_headers_derived_from_principal(self):
        p = PrincipalContext.interactive("alice", ["developers"], conversation_id="talk:room:42", channel="nextcloud-talk")
        h = principal_to_headers(p)
        self.assertEqual("alice", h["X-On-Behalf-Of"])
        self.assertEqual("developers", h["X-User-Groups"])
        self.assertEqual("talk:room:42", h["X-Conversation-Id"])
        self.assertEqual("nextcloud-talk", h["X-Source-Adapter"])
        # Memory scopes must never be transported as client headers
        self.assertNotIn("X-Memory-Scopes", h)

    def test_empty_principal_yields_no_headers(self):
        self.assertEqual({}, principal_to_headers(None))
        self.assertEqual({}, principal_to_headers(PrincipalContext.anonymous()))


class SecretValidationTests(unittest.TestCase):
    def tearDown(self):
        reset_config_cache()

    def test_no_secret_configured_means_open(self):
        reset_config_cache()
        self.assertTrue(validate_adapter_secret(None))

    def test_secret_match(self):
        reset_config_cache()
        os.environ["HERMES_X_ON_BEHALF_ADAPTER_SECRET"] = "s3cret"
        try:
            self.assertTrue(validate_adapter_secret("s3cret"))
            self.assertFalse(validate_adapter_secret("wrong"))
            self.assertFalse(validate_adapter_secret(None))
        finally:
            os.environ.pop("HERMES_X_ON_BEHALF_ADAPTER_SECRET", None)
            reset_config_cache()


class ExtractionTests(unittest.TestCase):
    class FakeSource:
        def __init__(self, user_id=None, user_name=None, extra_headers=None):
            self.user_id = user_id
            self.user_name = user_name
            self.extra_headers = extra_headers or {}

    def test_extracts_from_extra_headers(self):
        ctx = type("ctx", (), {"source": self.FakeSource(
            user_id="hermes-bot",
            extra_headers={"X-On-Behalf-Of": "marten", "X-User-Groups": "admin,developers"},
        )})()
        user, groups = extract_identity_from_context(ctx)
        self.assertEqual("marten", user)
        self.assertEqual("admin,developers", groups)

    def test_fallback_user_becomes_system_principal(self):
        os.environ["MCP_IDENTITY_FALLBACK_USER"] = "cronjob-user"
        reset_config_cache()
        try:
            user, _ = extract_identity_from_context(None)
            self.assertEqual("cronjob-user", user)
            # and the principal derived from None context must be kind=system
            from hermes_x_on_behalf.plugin import build_principal_from_context

            p = build_principal_from_context(None)
            self.assertEqual(PrincipalKind.SYSTEM, p.kind)
        finally:
            os.environ.pop("MCP_IDENTITY_FALLBACK_USER", None)
            reset_config_cache()


if __name__ == "__main__":
    unittest.main()
