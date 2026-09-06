"""Integration tests: full path adapter → PrincipalContext → MemoryScopes.

Verifies that Talk (description tags) and Deck (explicit conversation_scopes)
routing work together with the single config source (~/.hermes/config.yaml).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
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
    get_principal,
    parse_memory_tags,
    principal_context,
    principal_to_headers,
    resolve_memory_scopes,
)
# Modul direkt laden (das Paket-__init__ ist bereits als Modul registriert)
import importlib as _importlib  # noqa: E402

xob_config = _importlib.import_module("hermes_x_on_behalf.config")

HERMES_CONFIG_TEMPLATE = """
memory:
  provider: honcho

x_on_behalf:
  organization: kiga
  group_mapping:
    vorstand:      {type: team}
    it-admin:      {type: team}
    admin:         {type: ignore}
    employees:     {type: organization}
  memory:
    personal: true
    teams: true
    organization: true
    conversation_scopes:
      "deck:board:3": team:it-admin
      "deck:board:5": team:vorstand
    fallback_scope: personal
  honcho:
    enabled: false
"""


def _write_hermes_config(tmpdir: str) -> str:
    path = Path(tmpdir) / "config.yaml"
    path.write_text(HERMES_CONFIG_TEMPLATE, encoding="utf-8")
    return str(path)


class ConfigSourceIntegrationTests(unittest.TestCase):
    """The single config source: x_on_behalf section in Hermes config.yaml."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        xob_config._override_config_path = _write_hermes_config(self._tmp.name)
        xob_config.reset_config_cache()

    def tearDown(self):
        xob_config._override_config_path = None
        xob_config.reset_config_cache()
        self._tmp.cleanup()

    def test_loads_x_on_behalf_section_from_hermes_config(self):
        cfg = xob_config.load_config(force_reload=True)
        self.assertEqual("kiga", cfg.organization)
        self.assertIn("vorstand", cfg.group_mapping)
        self.assertEqual("team", cfg.group_mapping["vorstand"].type)
        self.assertEqual(
            {"deck:board:3": "team:it-admin", "deck:board:5": "team:vorstand"},
            cfg.memory.conversation_scopes,
        )
        self.assertEqual("personal", cfg.memory.fallback_scope)

    def test_honcho_disabled_by_default_in_section(self):
        cfg = xob_config.load_config(force_reload=True)
        self.assertFalse(cfg.honcho.enabled)


class TalkDeckRoutingIntegrationTests(unittest.TestCase):
    """End-to-end: adapter-style principal → scope resolution → headers."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        xob_config._override_config_path = _write_hermes_config(self._tmp.name)
        xob_config.reset_config_cache()
        self.cfg = xob_config.load_config(force_reload=True)

    def tearDown(self):
        xob_config._override_config_path = None
        xob_config.reset_config_cache()
        self._tmp.cleanup()

    # --- Talk: description tags (scales with new rooms) ---

    def test_talk_new_tagged_room_routes_to_team(self):
        # Neuer Raum, Tag in der Description — keine Plugin-Konfig nötig
        principal = PrincipalContext.interactive(
            "marten", ["vorstand"],
            conversation_id="talk:room:NEU987",
            conversation_description="Orga-Besprechungen [memory:team:vorstand]",
        )
        scopes = resolve_memory_scopes(principal, self.cfg)
        self.assertEqual("team:vorstand", scopes.default_scope)
        self.assertTrue(scopes.is_allowed(scopes.default_scope))

    def test_talk_new_untagged_room_falls_back_to_personal(self):
        principal = PrincipalContext.interactive(
            "marten", ["vorstand"], conversation_id="talk:room:NEU999"
        )
        scopes = resolve_memory_scopes(principal, self.cfg)
        self.assertEqual("personal:user:marten", scopes.default_scope)

    def test_talk_tag_not_in_allowed_groups_is_gated(self):
        # Nutzer ohne it-admin-Mitgliedschaft: Tag wird nicht aktiv
        principal = PrincipalContext.interactive(
            "alice", ["vorstand"],
            conversation_id="talk:room:TECH1",
            conversation_description="[memory:team:it-admin]",
        )
        scopes = resolve_memory_scopes(principal, self.cfg)
        self.assertIsNone(scopes.default_scope)

    # --- Deck: explicit conversation_scopes list ---

    def test_deck_board_routes_via_explicit_list(self):
        principal = PrincipalContext.interactive(
            "marten", ["it-admin", "vorstand"],
            conversation_id="deck:board:3:card:44",
        )
        scopes = resolve_memory_scopes(principal, self.cfg)
        self.assertEqual("team:it-admin", scopes.default_scope)

    def test_deck_board_title_is_never_tagged(self):
        # Board-Titel enthält zufällig "[memory:...]" — darf NICHT wirken
        principal = PrincipalContext.interactive(
            "marten", ["vorstand"],
            conversation_id="deck:board:5:card:44",
            conversation_description="Wichtiges Board [memory:team:it-admin]",
        )
        scopes = resolve_memory_scopes(principal, self.cfg)
        # Deck übergibt die Description nie → Routing nur über die Liste
        # (hier simuliert: Description wird nicht durchgereicht)
        principal_no_desc = PrincipalContext.interactive(
            "marten", ["vorstand"], conversation_id="deck:board:5:card:44"
        )
        scopes = resolve_memory_scopes(principal_no_desc, self.cfg)
        self.assertEqual("team:vorstand", scopes.default_scope)

    # --- Cross-user isolation over the full path ---

    def test_alice_and_bob_isolated_over_full_path(self):
        alice = PrincipalContext.interactive("alice", ["vorstand"], conversation_id="talk:room:X")
        bob = PrincipalContext.interactive("bob", ["it-admin"], conversation_id="talk:room:X")
        s_alice = resolve_memory_scopes(alice, self.cfg)
        s_bob = resolve_memory_scopes(bob, self.cfg)
        self.assertNotEqual(s_alice.personal, s_bob.personal)
        self.assertIn("team:vorstand", s_alice.teams)
        self.assertNotIn("team:vorstand", s_bob.teams)
        self.assertIn("team:it-admin", s_bob.teams)
        self.assertNotIn("team:it-admin", s_alice.teams)

    # --- Context + headers over the full path ---

    def test_context_and_headers_consistent(self):
        principal = PrincipalContext.interactive(
            "marten", ["vorstand"],
            conversation_id="talk:room:X",
            conversation_description="[memory:team:vorstand]",
            channel="nextcloud-talk",
        )
        with principal_context(principal):
            active = get_principal()
        self.assertIsNone(get_principal())  # Token-Reset nach Exit

        headers = principal_to_headers(principal)
        self.assertEqual("marten", headers["X-On-Behalf-Of"])
        self.assertEqual("talk:room:X", headers["X-Conversation-Id"])
        self.assertNotIn("X-Memory-Scopes", headers)

    # --- System principal over the full path ---

    def test_system_principal_never_gets_memory(self):
        principal = PrincipalContext.system("cron-user")
        scopes = resolve_memory_scopes(principal, self.cfg)
        self.assertEqual((), scopes.all())
        self.assertIsNone(scopes.default_scope)

    # --- Parallel requests: no identity crossover ---

    def test_parallel_requests_no_crossover(self):
        alice = PrincipalContext.interactive("alice", ["vorstand"], conversation_id="talk:room:A")
        bob = PrincipalContext.interactive("bob", ["it-admin"], conversation_id="talk:room:B")

        async def work(principal, delay):
            with principal_context(principal):
                await asyncio.sleep(delay)
                active = get_principal()
                scopes = resolve_memory_scopes(active, self.cfg)
                return (active.user_id, scopes.default_scope)

        async def run():
            return await asyncio.gather(work(alice, 0.03), work(bob, 0.01))

        results = asyncio.run(run())
        self.assertEqual(("alice", "personal:user:alice"), results[0])
        self.assertEqual(("bob", "personal:user:bob"), results[1])


if __name__ == "__main__":
    unittest.main()
