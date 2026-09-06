---
name: memory-routing
description: Route memory reads and writes to the correct memory scope (personal, team, organization) based on conversation context and topic.
---

# Memory Routing

Use this skill whenever you store or retrieve persistent memory (Honcho tools:
`honcho_profile`, `honcho_search`, `honcho_context`, `honcho_reasoning`,
`honcho_conclude`) and more than one memory scope is available. It defines
WHICH scope to use.

## Your available scopes

The active memory scopes are determined by the x-on-behalf plugin from the
authenticated user's identity. They follow this naming scheme:

| Scope pattern | Meaning |
|---|---|
| `personal:user:<id>` | Private memory of the current user — never visible to others |
| `team:<group>` | Shared memory of one team/group |
| `org:<name>` | Organization-wide memory |

The current conversation's DEFAULT scope is announced in your context (memory
scope line / principal info). If no default is announced, treat `personal` as
the default and ask before writing team memory.

## Routing rules

1. **Default first**: For an ongoing conversation, write to and read from the
   announced DEFAULT scope unless the topic clearly belongs elsewhere. The
   default is determined deterministically:
   - **Nextcloud Talk**: a `[memory:team:<group>]` tag in the room's
     description sets the room's default scope — new rooms just need the tag,
     no configuration.
   - **Nextcloud Deck**: the default comes from the explicit
     `memory.conversation_scopes` list in the Hermes config (board titles are
     user-owned and never carry tags).
   - Untagged/new rooms and DMs default to the user's personal scope.
2. **Topic-based rerouting** (only to scopes the user is allowed to use — you
   can never gain access to a scope outside the announced list):
   - Technical infrastructure (server, network, DNS, deploy errors, credentials
     handling notes) → the technical team scope (e.g. `team:it-admin`)
   - Organizational/association topics (meetings, decisions, people, events) →
     the organizational team scope (e.g. `team:vorstand`)
   - Personal preferences, working style, 1:1 context → `personal:user:<id>`
   - Rules/procedures valid for the whole organization → `org:<name>`
3. **Never** store team or org topics in personal memory, and never store
   personal information about a user in team memory.
4. **Uncertain?** Ask the user which memory to use instead of guessing. One
   short clarifying question is cheaper than polluting the wrong scope.
5. **Reading**: You may search across all announced scopes; state which scope
   an answer came from when it matters ("aus dem IT-Team-Gedächtnis: …").
6. **One write per turn**: Write to exactly one scope per storage action;
   if content belongs in two scopes, write it twice, scoped appropriately
   (or ask).

## Anti-patterns

- Dumping everything into personal memory "because it's simplest" — team
  knowledge stays invisible to other team members.
- Writing credentials or secrets into any shared scope.
- Assuming a scope that was not announced — the announced list is the complete
  and authoritative set.
