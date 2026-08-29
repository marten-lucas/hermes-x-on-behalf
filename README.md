# Hermes X-On-Behalf Plugin

A Hermes Agent Extension plugin that transparently propagates the human user identity and group memberships from platform channels (e.g., Nextcloud Talk, Nextcloud Deck) to outbound Model Context Protocol (MCP) requests and API Gateways (such as **Agentgateway**).

## Overview

When Hermes Agent acts on behalf of a human user in a chat or board session, outbound MCP tool calls are typically initiated by the agent process itself. This extension intercepts outgoing request metadata during the **Discovery Phase (`tools/list`)** and the **Execution Phase (`tools/call`)**, injecting the original requester's identity headers:

* `X-On-Behalf-Of`: The username or unique ID of the human initiator (e.g., `marten`).
* `X-User-Groups`: Comma-separated list of the user's groups (e.g., `admin,kiga_board`).

The plugin expects Hermes platform adapters to provide the identity in a stable contract:

* `source.user_id` or `source.user_name` for the human trigger
* `source.extra_headers["X-On-Behalf-Of"]` and `source.extra_headers["X-User-Groups"]` when the adapter already resolved group membership

This makes the plugin compatible with both the Nextcloud Talk adapter and the Deck adapter pattern, as long as the adapter sets the human-trigger user on the source object rather than the Hermes bot identity.

### Key Benefits
* **Dynamic RBAC:** Enables **Agentgateway** or downstream MCP servers to filter visible tools in `tools/list` based on the calling user's permissions.
* **Security & Non-Repudiation:** Prevents prompt injection risks where an LLM could try to manipulate or claim a fake `user_id` inside tool arguments.
* **Memory Isolation:** Uses the human trigger as the downstream identity, so memory and permission boundaries align with the triggering user instead of the Hermes bot account.
* **Subagent Inheritance:** Propagates parent session context when Hermes delegates tasks to subagents.

---

## Architecture Flow

```text
[ Platform Adapter (Talk / Deck) ]
  └── Sets: source.user_id = "marten"
  └── Sets: source.extra_headers = { "X-On-Behalf-Of": "marten", "X-User-Groups": "admin,kiga_board" }
         │
         ├───> [ Human-scoped memory / permission layer ]
         │
         ▼
[ Hermes X-On-Behalf Plugin ] (Hook + request mutation)
         │
         ├─ 1. tools/list  (Discovery: Agentgateway filters visible tools per user)
         └─ 2. tools/call  (Execution: Evaluates tool permissions)
         ▼
[ Agentgateway / MCP Server ] (Receives requests with authentic user headers)

```

---

## Installation

Clone or place this repository into your Hermes Agent plugins directory:

```bash
cd ~/.hermes/plugins/ # or your Hermes gateway plugins directory
git clone [https://github.com/marten-lucas/hermes-x-on-behalf.git](https://github.com/marten-lucas/hermes-x-on-behalf.git)

```

### Directory Structure

```text
hermes-x-on-behalf/
├── README.md
├── __init__.py
├── plugin.py
└── plugin.yaml

```

---

## Configuration

The plugin works with any platform adapter that populates the human identity on the active session context. The supported contract is:

- `source.user_id` or `source.user_name` for the human requestor
- `source.extra_headers["X-On-Behalf-Of"]` / `source.extra_headers["X-User-Groups"]` when the adapter already resolved user groups

For Talk, the existing adapter pattern already sets this on the source object.
For Deck, the same contract should be used for the human trigger / requestor, not the Hermes bot account.

### Optional Environment Variables

| Variable | Description | Default |
| --- | --- | --- |
| `MCP_IDENTITY_FALLBACK_USER` | Fallback user ID when no session context is present (e.g., system cronjobs). | *(empty)* |

---

## How It Works

1. **Context Extraction:** Inspects the active `SessionSource`, `MessageEvent`, or parent subagent context for identity headers.
2. **Header Injection:** Writes `X-On-Behalf-Of` and `X-User-Groups` back onto the current outbound request metadata, so downstream MCP servers see the human identity instead of the Hermes bot identity.
3. **Graceful Fallbacks:** If no active user session exists (e.g., automated cron tasks), it falls back to `MCP_IDENTITY_FALLBACK_USER` or continues without headers, logging a debug message without breaking execution.

---

## Compatibility Notes

### Nextcloud Talk

The Talk adapter is already compatible with this plugin when it sets the human sender on the message source and exposes the configured user groups.

### Nextcloud Deck

Deck should follow the same identity contract: the active source must represent the human trigger or requester, not the Hermes bot user. If a Deck card was triggered by a human user or a human comment, the plugin will propagate that identity to downstream MCP requests.

This keeps tool execution, memory boundaries, and auditing aligned with the human user rather than the Hermes service account.

## License

MIT
