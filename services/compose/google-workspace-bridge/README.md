# Google Workspace bridge

Holds one person's Google OAuth credential and exposes a narrow set of Gmail,
Calendar and Drive routes. agentbox-mcp calls it with a bridge token. The
assistant never sees the refresh token or the client secret.

## Routes

- `GET /health`, `GET /ready`, `GET /schema`
- Gmail: `search`, `read`, `clean`, `drafts/create`, `labels/list`,
  `labels/create`, `modify`
- Calendar: `list`, `events`, `freebusy`, `events/create`
- Drive: `search`, `list`, `recent`, `read`, `activity`, `sharing`, `create`

All are `POST /v1/<service>/<route>`.

## Limits

- Gmail `modify` accepts two changes, mark read and archive, plus labels in
  the assistant's own `agentbox/` namespace. A label outside it is refused.
- Composing is limited to drafts. There is no send route.
- Calendar writes go only to `GOOGLE_ALLOWED_WRITE_CALENDAR_ID`. Attendees and
  conference data are refused and `sendUpdates=none` is forced, so an event
  cannot notify anyone.
- Drive writes go only to the assistant's own folder, in text formats. `sharing`
  lists who can see a file and never changes it. There is no delete route.

## Exposure

The bridge publishes no host port. It is reachable only on its compose
network, which agentbox-mcp joins.

## Secrets

`~/.config/agentbox/google-workspace-bridge.env`:

```bash
GOOGLE_CLIENT_ID=op://Agentbox/google-workspace-mcp/client_id
GOOGLE_CLIENT_SECRET=op://Agentbox/google-workspace-mcp/client_secret
GOOGLE_REFRESH_TOKEN=op://Agentbox/google-workspace-bridge/refresh_token
GOOGLE_BRIDGE_TOKEN=op://Agentbox/google-workspace-bridge/bridge_token
GOOGLE_ALLOWED_WRITE_CALENDAR_ID=your-agent-calendar-id@group.calendar.google.com
GOOGLE_OWNED_LABEL_PREFIX=agentbox/
```

`cli/agentbox deploy` resolves the references. Keep raw values out of the
repository and out of any config the model can read.

## Re-authorising

`GOOGLE_REFRESH_TOKEN` is the only credential here that expires or is revoked.
When it is, every route returns 500 and `cli/agentbox doctor` reports the
bridge as not ready with `invalid_grant`.

```
python3 services/compose/google-workspace-bridge/oauth-setup.py
```

This runs the loopback consent flow and prints a new refresh token to stdout.
It never writes the token to disk. Store it and redeploy:

```
op item edit google-workspace-bridge refresh_token='<token>' --vault Agentbox
cli/agentbox deploy google-workspace-bridge
cli/agentbox doctor
```

Over SSH, forward the callback port first, because the redirect goes to the
server's loopback address.

```
ssh -L 8899:127.0.0.1:8899 <user>@<host>
```

If the token expires again within a week, the OAuth consent screen is still in
"Testing" status, where Google expires refresh tokens after 7 days. Publish the
app under APIs & Services > OAuth consent screen. A new token alone only
restarts the 7-day expiry.

## Scopes

`oauth-setup.py` requests `gmail.modify`, `calendar`, `drive.file`,
`drive.activity.readonly`, `contacts.readonly` and `directory.readonly`. Each
one is explained in that file. `drive.readonly` is added only when
`GOOGLE_ENABLE_DRIVE_READ_ALL=1`.

## Gmail search example

```json
{
  "query": "(order confirmation OR receipt) newer_than:60d",
  "max_results": 10
}
```
