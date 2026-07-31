# Runbook

## Health check
```
cli/agentbox doctor
```
Checks: git/curl/docker present, main model endpoint (`AGENTBOX_MAIN_BASE`, default `127.0.0.1:1234`) and router (`127.0.0.1:8765/health`) responding, disk usage, image freshness, bridge readiness, repo validation.

### Liveness vs readiness

Each bridge exposes two unauthenticated probes:

- `/health` — liveness. Is the process answering? Never touches the upstream.
  The container healthcheck uses this, and must keep using it: if liveness
  depended on the upstream, one backing-service outage would restart-loop every
  bridge in front of it.
- `/ready` — readiness. Can the bridge do its job? Probes the backing service,
  returns 503 with the reason when it cannot. `doctor` checks this.

The split exists because on 2026-07-31 Vikunja was down for hours while all six
bridges reported healthy and `doctor` was green. A bridge that is up but cannot
reach what it fronts is not serving anyone.

```
curl -s localhost:3466/ready    # vikunja-bridge
```

### Request logs

Bridges emit one JSON object per request on stdout:
```
docker logs vikunja-bridge-vikunja-bridge-1 | grep '^{'
{"service": "vikunja-bridge", "method": "GET", "path": "/v1/tasks", "status": 200, "bytes": 76, "ms": 5.9, "params": {"view": "lean"}}
```
`bytes` is the response size, which is what makes "where does the context
budget actually go" answerable from real traffic rather than estimated.

Never logged: the Authorization header, request bodies, response bodies, and
any query parameter outside the allowlist in `bridge_base.LOGGED_QUERY_PARAMS`
(free-text params such as a search string can carry personal data). Probe
requests are suppressed; set `BRIDGE_LOG_PROBES=1` to include them.

## Repo validation (CI-equivalent, run locally)
```
cli/agentbox validate
```
Checks for committed secrets/keys, insecure `0.0.0.0` bindings, unqualified Docker port mappings, missing compose resource limits / `no-new-privileges`, and that `policies/approval-policy.yaml` has all three tiers populated.

## Policy lookup
```
cli/agentbox policy check <action>
```
Returns the action's tier (`allowed` / `approval_required` / `always_denied`) as JSON; exit code 2 on `always_denied`. Unknown actions default to `approval_required` (deny-by-default).

## Bringing up services
```
cli/agentbox deploy <service>
```
`deploy` validates the repo, loads `$AGENTBOX_ENV_DIR/<service>.env`
(default `~/.config/agentbox`, falling back to `~/.config/agent-control-plane`),
resolves `op://` secrets through the 1Password CLI when present, and runs
`docker compose -p <service> up -d`. The compose project name is the service
name, so a service's default network is `<service>_default` — that is the name
the bridge/MCP pairs join as an external network.
Each service directory ships a `*.env.example` or `*.op.env.example` — copy and fill in before first start. Bridges resolve 1Password `op://` references at deploy time if you use 1Password; otherwise populate the plain `.env` directly.

## Router
```
router/agentbox_router.py
```
Configure via env: `AGENTBOX_MAIN_BASE`, `AGENTBOX_CONTEXT_BASE`, `AGENTBOX_REASON_BASE` (upstream llama-server URLs), `AGENTBOX_MAIN_MODEL` / `_CONTEXT_MODEL` / `_REASON_MODEL` (aliases), `AGENTBOX_ROUTER_HOST` / `_PORT`.
