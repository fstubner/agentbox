# Memory Bridge

Local reviewed-memory and memory-proposal store for Agentbox.

Hermes should use this as a capability boundary instead of treating model
context as durable memory.

## Scope

Initial endpoints:

- `GET /health`
- `GET /schema`
- `POST /v1/proposals`
- `GET /v1/proposals`
- `POST /v1/proposals/{id}/approve` — operator only
- `POST /v1/proposals/{id}/reject` — operator only
- `POST /v1/memories`
- `GET /v1/memories`

Sensitive proposals, including health profile facts, should be approved before
promotion to durable memory.

## Exposure

- Default bind: `127.0.0.1:3471`
- Intended caller: local Hermes runtime or a future MCP wrapper
- Not exposed to LAN/Tailnet

## Runtime Secrets

Create:

```text
~/.config/agent-control-plane/memory-bridge.env
```

Recommended values:

```bash
MEMORY_BRIDGE_TOKEN=op://Agentbox/memory-bridge/bridge_token
LAN_BIND_IP=127.0.0.1
```

Data is stored in a Docker named volume at `/data/memory.json`.

## Review gate

The assistant may propose a memory. Approving one, or writing straight to
durable memory, requires `MEMORY_REVIEW_TOKEN` sent as
`X-Memory-Review-Token` — a second credential the assistant never holds.
With it unset, approval returns 503: durable memory stops accepting writes
rather than accepting them from anyone holding the bridge token.

Operators use `cli/agentbox memory list|approve|reject`.
