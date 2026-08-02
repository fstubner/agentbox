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

Bridges emit one JSON object per request, to stdout and to a persisted file:
```
tail -f ~/.local/state/agentbox/logs/vikunja-bridge.jsonl
{"service": "vikunja-bridge", "method": "GET", "path": "/v1/tasks", "status": 200, "bytes": 76, "ms": 5.9, "params": {"view": "lean"}}
```

Use the file, not `docker logs`. Container logs do not survive a recreate and
every `cli/agentbox deploy` recreates, so stdout loses the record each time
anything ships. The file rotates at 32 MB keeping one previous generation.

First-time setup, since the bridges run as uid 65532:

```
sudo chgrp 65532 ~/.local/state/agentbox/logs && sudo chmod 0775 ~/.local/state/agentbox/logs
```

Where the bytes go, across all bridges:
```
cat ~/.local/state/agentbox/logs/*.jsonl | python3 -c "
import json,sys,collections
n=collections.Counter(); b=collections.Counter()
for l in sys.stdin:
    r=json.loads(l); k=f\"{r['service']} {r['path']}\"
    n[k]+=1; b[k]+=r['bytes']
for k,v in b.most_common(15): print(f'{v:9d} B  {n[k]:4d} calls  {k}')"
```
`bytes` is the response size, which is what makes "where does the context
budget actually go" answerable from real traffic rather than estimated.

Never logged: the Authorization header, request bodies, response bodies, and
any query parameter outside the allowlist in `bridge_base.LOGGED_QUERY_PARAMS`
(free-text params such as a search string can carry personal data). Probe
requests are suppressed; set `BRIDGE_LOG_PROBES=1` to include them.

## Scaffolding a new bridge

```
cli/agentbox scaffold todoist
```

Generates `services/compose/todoist-bridge` from the template with both
credentials as distinct `op://` references, a free host port, the platform
guardrails already in place, and a starter test. It runs `validate`, commits to
`scaffold/todoist-bridge`, and stops.

It does not deploy and does not merge. Review it against
`skills/adding-a-bridge/SKILL.md`, replace the placeholder echo route, then
`cli/agentbox deploy todoist-bridge` when you are satisfied.

## Runtime policy grants

Assistant tool calls are gated by `policies/approval-policy.yaml` — the same
file that governs operator actions — enforced in every MCP at the single
`tool_call` dispatch point. Each tool maps to a capability, and the capability
carries the tier. `allowed` tools run freely;
`approval_required` tools are refused until an operator issues a grant; a tool
with no capability mapping defaults to `approval_required`, so a tool added
without being mapped fails closed.

```
cli/agentbox grant archive_gmail --ttl 15m   # single-use by default
cli/agentbox grants list
cli/agentbox grants revoke archive_gmail
```

Add `--repeatable` for a grant that survives repeated use until it expires.

Two directories back this, and the split is the security property:

- `~/.local/state/agentbox/policy` → mounted **read-only** at `/policy`. Holds
  the grants. The assistant side must never be able to issue itself permission.
- `~/.local/state/agentbox/policy-state` → mounted **writable** at
  `/policy-state`. Holds spent-grant markers only. Writing here can only
  *remove* permission, so it carries no authority.

First-time setup: the MCP containers run as uid 65532, so the writable
directory needs group access.

```
sudo chgrp 65532 ~/.local/state/agentbox/policy-state && sudo chmod 0775 ~/.local/state/agentbox/policy-state
```

If that is missed, single-use grants are refused with an explicit message
rather than silently degrading to unlimited-until-expiry.

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
