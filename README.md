# Hermes X-On-Behalf Plugin

A Hermes Agent Extension plugin that transparently propagates the human user identity and group memberships from platform channels (e.g., Nextcloud Talk, Nextcloud Deck) to outbound Model Context Protocol (MCP) requests and API Gateways (such as **Agentgateway**).

## Overview

When Hermes Agent acts on behalf of a human user in a chat or board session, outbound MCP tool calls are typically initiated by the agent process itself. This extension intercepts outgoing MCP HTTP/SSE transport calls during both the **Discovery Phase (`tools/list`)** and the **Execution Phase (`tools/call`)**, injecting the original requester's identity headers:

* `X-On-Behalf-Of`: The username or unique ID of the human initiator (e.g., `marten`).
* `X-User-Groups`: Comma-separated list of the user's groups (e.g., `admin,kiga_board`).

### Key Benefits
* **Dynamic RBAC:** Enables **Agentgateway** or downstream MCP servers to filter visible tools in `tools/list` based on the calling user's permissions.
* **Security & Non-Repudiation:** Prevents prompt injection risks where an LLM could try to manipulate or claim a fake `user_id` inside tool arguments.
* **Memory Isolation:** Works seamlessly alongside Honcho for isolated user/team memory representations.
* **Subagent Inheritance:** Propagates parent session context when Hermes delegates tasks to subagents.

---

## Architecture Flow

```text
[ Platform Adapter (Talk / Deck) ]
  └── Sets: source.user_id = "marten"
  └── Sets: source.extra_headers = { "X-On-Behalf-Of": "marten", "X-User-Groups": "admin,kiga_board" }
         │
         ├───> [ Honcho Long-Term Memory ] (Peer Memory Isolation)
         │
         ▼
[ Hermes X-On-Behalf Plugin ] (Middleware Hook)
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

The plugin works out of the box with any platform adapter that populates `source.extra_headers` or `source.user_id`.

### Optional Environment Variables

| Variable | Description | Default |
| --- | --- | --- |
| `MCP_IDENTITY_FALLBACK_USER` | Fallback user ID when no session context is present (e.g., system cronjobs). | *(empty)* |

---

## How It Works

1. **Context Extraction:** Inspects the active `SessionSource`, `MessageEvent`, or parent subagent context for identity headers.
2. **Header Injection:** Appends `X-On-Behalf-Of` and `X-User-Groups` to all outgoing MCP transport requests.
3. **Graceful Fallbacks:** If no active user session exists (e.g., automated cron tasks), it falls back to `MCP_IDENTITY_FALLBACK_USER` or continues without headers, logging a debug message without breaking execution.

---

## License

MIT

```

```