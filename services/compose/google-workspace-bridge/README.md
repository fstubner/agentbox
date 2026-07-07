# Google Workspace Bridge

Narrow local bridge for Gmail/Calendar operations. This service owns Google
OAuth credentials and exposes only assistant-safe operations to Hermes.

Hermes should call this bridge or its future MCP wrapper. Hermes should not
receive raw OAuth refresh tokens, Google client secrets, or broad Google API
credentials.

## Scope

- `GET /health`
- `GET /schema`
- `POST /v1/gmail/search`
- `POST /v1/gmail/read`
- `POST /v1/gmail/search_grocer_orders`
- `POST /v1/gmail/labels/list`
- `POST /v1/gmail/labels/create`
- `POST /v1/gmail/modify`
- `POST /v1/calendar/list`
- `POST /v1/calendar/events`
- `POST /v1/calendar/freebusy`
- `POST /v1/calendar/events/create`

Gmail modification is deliberately limited to marking read, archiving, and
adding labels. Calendar writes are limited to the configured assistant-owned
calendar.

## Exposure

- Default bind: `127.0.0.1:3470`
- Intended caller: local Hermes runtime or a local MCP wrapper
- Not exposed to LAN
- Not exposed to Tailnet

## Runtime Secrets

Create this host-private file:

```text
~/.config/agent-control-plane/google-workspace-bridge.env
```

Required values:

```bash
GOOGLE_CLIENT_ID=op://Agentbox/google-workspace-mcp/client_id
GOOGLE_CLIENT_SECRET=op://Agentbox/google-workspace-mcp/client_secret
GOOGLE_REFRESH_TOKEN=op://Agentbox/google-workspace-bridge/refresh_token
GOOGLE_BRIDGE_TOKEN=op://Agentbox/google-workspace-bridge/bridge_token
GOOGLE_ALLOWED_WRITE_CALENDAR_ID=your-agent-calendar-id@group.calendar.google.com
GOOGLE_OWNED_LABEL_PREFIX=agentbox/
LAN_BIND_IP=127.0.0.1
```

The raw values are resolved by `deploy/deploy.sh` through 1Password. Do not put
raw values in the repo or model-visible config.

## Gmail Search Examples

Search recent Grocer emails:

```json
{
  "query": "from:(grocer.ie OR grocer.com) newer_than:30d",
  "max_results": 10
}
```

Search order confirmations:

```json
{
  "query": "(grocer order confirmation OR grocer receipt OR grocer delivery) newer_than:60d",
  "max_results": 10
}
```

## Safety

This bridge should never expose Gmail send or delete endpoints. Any future
draft creation, spending, external communication, or health-sensitive action
must remain approval-gated above this bridge.
