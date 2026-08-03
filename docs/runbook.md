# Runbook

## Smoke test

```
cli/agentbox smoke
```

`doctor` answers "is everything up". This answers "does anything work" — it
drives real workflows through the MCPs, the same path the assistant uses, and
was worth writing immediately: it found on its first run that every tool
published a `required` argument list that nothing enforced, so an omitted
argument came back as `internal error: KeyError`.

Safe to run against live accounts, deliberately:

- **Vikunja** is test data, so it creates a task and completes it.
- **Google** is real data, so it only reads. Nothing drafts, labels or archives.
- **Memory** proposals are inert, so it proposes one and then rejects it as the
  operator — which also exercises the half of the review gate the assistant
  cannot reach.
- The **policy gate** check passes when the call is *refused*. A success there
  is the bug.

Run it after any deploy. Every other test in `tests/` runs against fixtures, so
this is the only thing that would notice a service that starts cleanly, passes
readiness, and refuses every call.

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

## Self-reflection

The assistant reviews its own outcome history every morning at 09:00, works
out what to do differently, and proposes durable lessons the operator approves
or rejects.

Because it runs daily over a rolling seven-day window, it reads mostly the same
activity each time. The skill therefore has it check `search_memories` and
`list_memory_proposals` *before* looking at the evidence, and a normal day ends
with no proposal at all. A queue filling with near-identical lessons means that
step is being skipped — it is the failure mode to watch for, because a review
queue nobody reads closes the assistant's only route to durable memory.

```
cli/agentbox memory list          # what it concluded, pending your review
cli/agentbox memory approve <id>
cli/agentbox memory reject <id> --reason "..."
```

The loop is: MCPs write an outcome journal → `review_own_activity` aggregates
it → the assistant reflects using `skills/self-reflection` → it proposes with
`propose_memory` → you approve. It cannot approve its own conclusions; that
needs a credential no container holds.

The schedule lives in the gateway's own scheduler, not cron(8). It runs as the
`agentbox` user under that gateway's profile — **the `HERMES_HOME` matters**, a
job created without it lands in a different profile and never fires:

```
sudo -u agentbox env HERMES_HOME=/home/agentbox/agentbox HOME=/home/agentbox \
  /home/agentbox/hermes-agent-test/.venv/bin/python -m hermes_cli.main cron list
```

The scheduler enumerates jobs at startup, so **restart the gateway after adding
one** (`sudo systemctl restart hermes-gateway-agentbox`). `cron list` prints
"Gateway is not running" even when it is; check `.tick.lock` in
`/home/agentbox/agentbox/cron/` for the real answer.

### What it can and cannot see

The journal records tool names, outcomes, timings, argument *names*, and an
allowlist of shape values (`view`, `limit`, …). It never records argument
values, result content, or exception messages — an upstream error routinely
quotes the input that caused it. `review_own_activity` returns counts only.

This is what makes a weekly reflection safe to run unattended against real
accounts.

### Never run the gateway by hand while the service is up

The unit starts it with `gateway run --replace`, which means a second instance
**takes over from the first**. Running it manually to see an error therefore
stops the real gateway, and the service then fails with nothing but a banner
and `status=1/FAILURE` — no message saying what happened or that you caused it.

```bash
sudo systemctl start hermes-gateway-agentbox    # the fix, once the manual one is gone
```

To see why it is failing, read the journal or check the wrapper's own
preconditions (`op whoami` and three `op read` calls, any of which exits 1
before Hermes starts). Do not reach for a manual `gateway run`.

### Adding a new skill

```
sudo install -d -o agentbox -g agentbox -m 0775 /home/agentbox/agent-control-plane/hermes/skills/<name>
sudo install -o agentbox -g agentbox -m 0664 skills/<name>/SKILL.md /home/agentbox/agent-control-plane/hermes/skills/<name>/SKILL.md
```

Skills in this repo are the source; that directory is what the gateway loads.
They are not synced automatically.

## Backup

```
cli/agentbox backup          # archive task + memory stores, keep 14
cli/agentbox backup list
```

Archives the two stores that hold anything the assistant cannot regenerate:
the Vikunja database (host bind mount) and durable memory (a docker volume,
read out through a throwaway container). Roughly 3 MB together.

The archive is verified with `tar -tzf` before old ones are pruned, so a broken
run cannot delete the last good copy.

Restore needs nothing but tar:

```
tar -xzf ~/.local/state/agentbox/backups/agentbox-<stamp>.tar.gz -C /tmp/restore
```

Then copy `vikunja/` back over `$AGENT_CONTROL_PLANE_STATE_DIR/vikunja` and
`memory/memory.json` into the `memory-bridge_memory_data` volume, with both
services stopped.

Not yet scheduled — run it from cron or a systemd timer if you want it
unattended.

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

### Approving from Discord

A refused call is recorded, so you are told rather than having to notice:

```
cli/agentbox approvals list      # what the assistant is waiting on
cli/agentbox approvals clear     # drop requests you are not going to grant
```

`cli/agentbox-approvals` posts those requests to Discord and writes the grant
when you reply `approve <tool>`. Run it as yourself, not as the agentbox user:

```
AGENTBOX_APPROVAL_CHANNEL_ID=<channel> cli/agentbox-approvals
```

It reads the bot token and the operator allowlist from
`op://Agentbox/discord`, and needs `~/.config/agent-control-plane/1password.env`
sourced or `OP_SERVICE_ACCOUNT_TOKEN` set.

**Why a separate process rather than the assistant asking.** The assistant is in
the same Discord, and an instruction embedded in an email can make it say
anything — including a convincing request for its own approval. So the loop
ignores every message a bot authored and every user outside the allowlist, and
it never writes grants itself; it shells out to `agentbox grant`, which the
assistant cannot run. MCP elicitation would do this natively, but the server
would need a streaming transport it does not have, and `2026-07-28` replaces
elicitation with MRTR anyway.

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
sudo install -d -o "$USER" -g 65532 -m 2775 ~/.local/state/agentbox/policy-state/pending
```

The `pending` directory needs creating explicitly, with setgid. If the container
creates it first it is owned by uid 65532, and the operator then cannot remove
requests from it — deleting a file needs write on the containing directory, not
the file. Setgid keeps the group on anything written later.

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
