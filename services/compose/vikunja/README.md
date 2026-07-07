# Vikunja

Private task and lightweight project-management service.

## Why This Exists

Vikunja gives the assistant a practical place to manage tasks, projects, boards, due dates, and household/admin planning without inventing a custom task database first.

## Runtime

- Image: `vikunja/vikunja`
- Database: SQLite, stored in `${AGENT_CONTROL_PLANE_STATE_DIR}/vikunja/db`
- Uploads/files: `${AGENT_CONTROL_PLANE_STATE_DIR}/vikunja/files`
- Default port: `3456`
- Default bind: `127.0.0.1`

For LAN access on the AMD box, set:

```bash
LAN_BIND_IP=192.0.2.10
VIKUNJA_SERVICE_PUBLICURL=http://agentbox.local:3456/
```

Set these in the host-private file:

```text
~/.config/agent-control-plane/vikunja.env
```

Do not commit that file.

The deploy script defaults `AGENT_CONTROL_PLANE_STATE_DIR` to:

```text
~/.local/state/agent-control-plane
```

## Required Secret

Generate a service secret on the AMD box:

```bash
openssl rand -hex 32
```

Then add it to `~/.config/agent-control-plane/vikunja.env`:

```bash
VIKUNJA_SERVICE_SECRET=<generated value>
```

## First Login

Leave registration enabled for first setup. After creating the intended user, set:

```bash
VIKUNJA_SERVICE_ENABLEREGISTRATION=false
```

Then redeploy.

## Backup

Back up:

- `~/.local/state/agent-control-plane/vikunja/db`
- `~/.local/state/agent-control-plane/vikunja/files`

## Sources

- Official install docs: https://vikunja.io/docs/installing/
- Official Docker examples: https://vikunja.io/docs/full-docker-example/
