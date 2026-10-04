# Memory bridge

Stores memory proposals and approved memories. The assistant can propose a
memory. Only a person can approve one.

## Routes

- `GET /health`, `GET /schema`
- `POST /v1/proposals`, `GET /v1/proposals`
- `POST /v1/proposals/{id}/approve` and `.../reject`, operator only
- `POST /v1/memories`, operator only, and `GET /v1/memories`
- `GET /v1/whoami`, `GET /v1/activity`, `GET /v1/feedback`

## Exposure

agentbox-mcp reaches it on the compose network. It also publishes
`127.0.0.1:3471` on the host, so `cli/agentbox memory` and the portal can use
the review token. That is the one bridge port the platform allows.

## Secrets

`~/.config/agentbox/memory-bridge.env`:

```bash
MEMORY_BRIDGE_TOKEN=op://Agentbox/memory-bridge/bridge_token
MEMORY_REVIEW_TOKEN=op://Agentbox/memory-bridge/review_token
```

Data is stored in a Docker named volume at `/data/memory.json`.

## Review gate

Approving a proposal, or writing directly to durable memory, requires
`MEMORY_REVIEW_TOKEN` in the `X-Memory-Review-Token` header. The assistant
never holds it. If it is unset, approval returns 503 and durable memory
refuses all writes, so the bridge token alone can never write to it.

Operators use `cli/agentbox memory list|approve|reject` or the portal.
