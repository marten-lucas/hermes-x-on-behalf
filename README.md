# Hermes X-On-Behalf Plugin (v1.0)

Identity / **Principal-Context-Propagation**-Plugin für Hermes Agent. Es löst den menschlichen Principal (User, Gruppen, Conversation, Channel) aus den Plattform-Adaptern (Nextcloud Talk, Nextcloud Deck) auf, berechnet daraus **serverseitig** Memory-Scopes und propagiert die Identität via ContextVars und HTTP-Headern an MCP-Tools und Gateways.

## Architektur

```
hermes-x-on-behalf/
├── principal.py      # PrincipalContext (user, groups, org, conversation, channel, kind)
├── context.py        # current_principal ContextVar + principal_context() (Token-Reset)
├── scopes.py         # MemoryScopeResolver: Group→Scope-Mapping, Memory-Tags, Default-Scope
├── headers.py        # Header-Ableitung + X-Adapter-Secret-Validierung
├── config.py         # ~/.hermes/config.yaml (x_on_behalf:) als einzige Quelle + Env-Overrides
├── honcho.py         # Optional: Wrapt Hermes' Honcho-Provider (Principal → peer/session)
├── interceptor.py    # httpx/aiohttp Header-Injektion aus current_principal
├── skills/
│   └── memory-routing/SKILL.md   # Bündelt die Routing-Heuristik für den Agent
└── plugin.py         # Hermes Lifecycle-Hooks (register, pre_tool_call, Skill-Registrierung)
```

### Kernprinzipien (Security)

1. **Scopes werden serverseitig berechnet** — niemals aus Client-Headern oder LLM-/Tool-Argumenten übernommen. `X-Memory-Scopes` wird bewusst nicht als Header transportiert.
2. **Token-basierter Context-Reset**: Identität wird ausschließlich über `principal_context(principal)` gesetzt und im `finally` zurückgesetzt — kein Identity-Leak bei parallelen Requests.
3. **Drei Principal-Arten**: `interactive` (echter Mensch), `system` (Cron/Fallback — bekommt niemals Personal-/Team-Memory), `anonymous`.
4. **Gruppen ≠ Memory-Teams**: Nur im YAML-Mapping als `team` eingestufte Nextcloud-Gruppen werden zu Memory-Scopes; `admin`/`employees` etc. bleiben Berechtigungs- bzw. Org-Gruppen.
5. **Anti-Spoofing**: Ist `HERMES_X_ON_BEHALF_ADAPTER_SECRET` gesetzt, akzeptiert `build_principal_from_context` Identity-Header (`X-On-Behalf-Of` etc.) aus dem Session-Kontext nur mit gültigem `X-Adapter-Secret` — andernfalls wird der Request `anonymous`. Ohne konfiguriertes Secret ist die Prüfung deaktiviert (offen).

## Verwendung in Adaptern

```python
principal = identity.build_principal(user_id, groups, room_id=room_id, is_group_chat=True)
if principal is not None:
    with identity.principal_context(principal):
        await self.handle_message(event)

source["extra_headers"] = identity.principal_headers(principal)
```

## Konfiguration

**Einzige Quelle:** Abschnitt `x_on_behalf:` in `~/.hermes/config.yaml` (Hermes-Standard, wie `memory.provider:` etc.). Es gibt keine separate Plugin-YAML-Datei. Ein alternativer Pfad ist nur via `HERMES_X_ON_BEHALF_CONFIG` (z. B. für Tests) möglich.

Env-Flags (in `~/.hermes/.env`):
- `HERMES_X_ON_BEHALF_DEBUG` — Debug-Logging (Principal + Scopes, nie Memory-Inhalte)
- `HERMES_X_ON_BEHALF_ADAPTER_SECRET` — Shared Secret (Anti-Spoofing, siehe Kernprinzip 5)
- `MCP_IDENTITY_FALLBACK_USER` — erzeugt `kind=system`-Principals (kein Personal-Memory)
- `MCP_IDENTITY_SERVICE_USER` — Service-Identity-User (Default z. B. `ki-assistent`)
- `MCP_IDENTITY_SERVICE_GROUPS` — kommagetrennte RBAC-Gruppen (Default z. B. `it-admin`)

## Service-Identity (Nicht-interaktive Requests)

Beim Gateway-Start und der MCP-Discovery ist noch kein interaktiver Principal aktiv — der HTTP-Interceptor würde ohne Gegenmaßnahme kein `X-User-Groups` setzen, und Agentgateway würde die Tool-Sicht auf einen leeren/kleinen Katalog filtern. Dafür gibt es eine **Service-Identity**:

```yaml
# ~/.hermes/config.yaml → x_on_behalf:
service_identity:
  user: ki-assistent
  groups:
    - it-admin
```

Alternativ als Env: `MCP_IDENTITY_SERVICE_USER`, `MCP_IDENTITY_SERVICE_GROUPS` (kommagetrennt). Der daraus gebaute `system`-Principal trägt die RBAC-Gruppen (für Agentgateway-Sichtbarkeit), hat aber **niemals** Personal-/Team-Memory-Zugriff. Ein explizit gesetzter Principal (auch `anonymous`) hat immer Vorrang und wird nie eskaliert.


## HTTP-Propagation (Interzeptoren)

`register()` patcht `httpx.AsyncClient.send` und `aiohttp.ClientSession._request`: Jeder ausgehende Request erhält die Header des **aktiven** `PrincipalContext` (`X-On-Behalf-Of`, `X-User-Groups`, `X-Conversation-Id`, `X-Source-Adapter`) — ContextVar-basiert, also auch bei parallelen Requests isoliert. Ohne aktiven Principal werden keine Identity-Header gesetzt; die Header-Ableitung ist fail-soft und lässt Requests niemals scheitern. `system`-Principals propagieren nur ihre `user_id` (keine Gruppen/Conversation), `anonymous` nichts.

## Memory-Routing für Conversations

Team-Memory ist kontextabhängig: Der Default-Scope einer Conversation wird **deterministisch** ermittelt, in dieser Priorität:

1. **Memory-Tag in der Talk-Raum-Beschreibung**: `[memory:team:it-admin]` (auch `[memory:personal]`, `[memory:org]`, Kurzform `[memory:it-admin]`). Skaliert mit neuen Räumen — Raum anlegen, Description taggen, fertig. **Deck-Board-Titel werden bewusst nicht getaggt** (Titel gehören dem User).
2. **Explizites Mapping** `memory.conversation_scopes` in der `x_on_behalf:`-Sektion — exakte conversation_id (`talk:room:<token>`, `deck:board:<id>`, `deck:board:<id>:card:<id>`) oder Präfix-Match mit Segment-Grenze (`deck:board:3` matcht `deck:board:3:card:44`, aber **nicht** `deck:board:30`). **Das ist der Weg für Deck-Boards**, da Deck über eine konfigurierte Board-Liste verfügt.
3. **Fallback** `memory.fallback_scope` (Standard: `personal`) — neue ungetaggte Talk-Räume und DMs landen im Personal-Memory.

Sicherheitsgate: Ein deterministisch ermittelter Scope wird nur verwendet, wenn der Principal ihn auch nutzen darf (Gruppenmitgliedschaft). Der Agent kann sich keine Scopes erschließen.

Der gebündelte Skill **`memory-routing`** (automatisch via `ctx.register_skill` registriert) vermittelt dem Agent die Routing-Heuristik: Default-Scope der Conversation, themenbasiertes Umrouten nur innerhalb der erlaubten Scopes, Nachfragen bei Unsicherheit.

## Honcho (optional)

Ist in der YAML `honcho.enabled: true` gesetzt, wrapt das Plugin Hermes' Honcho-Memory-Provider (`hermes.plugins.memory.honcho.provider`) und leitet Workspace/Peer/Session aus dem `PrincipalContext` ab:

| PrincipalContext | Honcho |
|---|---|
| `organization` | `workspace_id` |
| `user_id` | `peer_id` = `user:<id>` |
| `conversation_id` | `session_id` (`talk:room:<token>` / `deck:board:<id>:card:<id>`) |
| berechnete Scopes | personal / team / org |

Ohne Honcho (oder wenn Hermes' Provider nicht gefunden wird) läuft alles andere normal — die Integration scheitert weich (fail-soft).

## Memory-Modell

```
Honcho Workspace (org)
├── Peers: user:alice, user:bob, hermes
└── Sessions: talk:room:42, deck:board:12:card:44

Memory-Scopes (serverseitig berechnet):
  personal:user:alice     → nur alice
  team:erzieher           → alle Mitglieder der NC-Gruppe "erzieher"
  org:kiga                → alle interaktiven Nutzer der Instanz
```
