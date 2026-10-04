# Vikunja

The task and project service the assistant uses for tasks, lists and due
dates.

## Runtime

- Image: `vikunja/vikunja`
- Database: SQLite in `${AGENT_CONTROL_PLANE_STATE_DIR}/vikunja/db`
- Files: `${AGENT_CONTROL_PLANE_STATE_DIR}/vikunja/files`
- Port: `3456`, bound to `127.0.0.1` by default

`cli/agentbox deploy` sets `AGENT_CONTROL_PLANE_STATE_DIR` to
`~/.local/state/agentbox`.

For LAN access, set these in `~/.config/agentbox/vikunja.env`:

```bash
LAN_BIND_IP=<the box's LAN address>
VIKUNJA_SERVICE_PUBLICURL=http://agentbox.local:3456/
```

## Secret

```bash
openssl rand -hex 32
```

Add it to `~/.config/agentbox/vikunja.env` as `VIKUNJA_SERVICE_SECRET`.

## First login

Leave registration enabled to create the first user, then set
`VIKUNJA_SERVICE_ENABLEREGISTRATION=false` and redeploy. Later accounts are
created by `agentbox invite complete`.

## Backup

`cli/agentbox backup` includes the database and the files directory.

## Sources

- Install docs: https://vikunja.io/docs/installing/
- Docker example: https://vikunja.io/docs/full-docker-example/
