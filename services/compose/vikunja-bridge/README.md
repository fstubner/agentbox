# Vikunja bridge

Holds the Vikunja API token and exposes a small task and project API.
agentbox-mcp calls it with a bridge token. The assistant never sees the Vikunja
token.

## Exposure

The bridge publishes no host port. It joins the `vikunja_default` network to
reach Vikunja at `http://vikunja:3456`, and agentbox-mcp joins its network to
reach it.

## Secrets

`~/.config/agentbox/vikunja-bridge.env`:

```bash
VIKUNJA_URL=http://vikunja:3456
VIKUNJA_API_TOKEN=op://Agentbox/vikunja-bridge/api_token
VIKUNJA_BRIDGE_TOKEN=op://Agentbox/vikunja-bridge/bridge_token
```

`cli/agentbox deploy` resolves the references. Generate the bridge token with
`openssl rand -hex 32`.

## Routes

Every route except `/health` requires `Authorization: Bearer
<VIKUNJA_BRIDGE_TOKEN>`.

- `GET /health`, `GET /schema`
- `GET /v1/projects`, `POST /v1/projects`
- `GET /v1/tasks`, `GET /v1/tasks/{id}`
- `POST /v1/projects/{project_id}/tasks`
- `PATCH /v1/tasks/{id}`
- `POST /v1/tasks/{id}/comments`

## Deploy

Deploy after `vikunja` is running:

```bash
cli/agentbox deploy vikunja-bridge
```

## Sources

- Vikunja API docs: https://vikunja.io/docs/api-documentation/
