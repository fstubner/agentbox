# Vikunja Bridge

Narrow local bridge for task/project operations.

Hermes should call this bridge instead of receiving a raw Vikunja API token. The bridge stores the real Vikunja token in its own process environment and exposes only a small HTTP API.

## Exposure

- Default bind: `127.0.0.1:3466`
- Intended caller: local Hermes runtime or a local MCP wrapper
- Not exposed to LAN
- Not exposed to Tailnet

## Runtime Secrets

Create this host-private file:

```text
~/.config/agent-control-plane/vikunja-bridge.env
```

Required values:

```bash
VIKUNJA_URL=http://vikunja:3456
VIKUNJA_API_TOKEN=op://Agentbox/vikunja-bridge/api_token
VIKUNJA_BRIDGE_TOKEN=op://Agentbox/vikunja-bridge/bridge_token
LAN_BIND_IP=127.0.0.1
```

The values are resolved through 1Password by `deploy/deploy.sh`. The raw values should live in the `Agentbox` vault, not in this file.

Generate the bridge token value with:

```bash
openssl rand -hex 32
```

## API

All endpoints except `/health` require:

```http
Authorization: Bearer <VIKUNJA_BRIDGE_TOKEN>
```

Available endpoints:

- `GET /health`
- `GET /schema`
- `GET /v1/projects`
- `POST /v1/projects`
- `GET /v1/tasks`
- `GET /v1/tasks/{id}`
- `POST /v1/projects/{project_id}/tasks`
- `PATCH /v1/tasks/{id}`
- `POST /v1/tasks/{id}/comments`

## Deploy

Deploy after `vikunja` is already running:

```bash
./deploy/deploy.sh vikunja-bridge
```

The bridge joins the existing `agent-control-plane-vikunja_default` Docker network and talks to Vikunja at `http://vikunja:3456`.

## Sources

- Vikunja API docs: https://vikunja.io/docs/api-documentation/
- Vikunja Docker docs: https://vikunja.io/docs/full-docker-example/
