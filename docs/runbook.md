# Runbook

> Paths below use the deployment variables rather than one machine's literal
> layout: `$HERMES_HOME` is the gateway profile, `$GATEWAY_USER_HOME` the
> gateway user's home, `$GATEWAY_VENV` its virtualenv, `$AGENTBOX_ENV_DIR` the
> operator's env files, `$AGENTBOX_REPO` this checkout. CI refuses literal home
> directories so the repo stays portable and free of one person's filesystem.


## The MCP gateway

One service, `agentbox-mcp` on `:3465`, serves every tool from five bridges.
It replaced five per-service MCPs that were each a tool registry plus an HTTP
proxy carrying its own copy of the policy gate.

```
curl -s localhost:3465/health   # tool and integration counts
curl -s localhost:3465/ready    # per-bridge readiness
```

`/ready` is not-ready if *any* bridge is unreachable, and names which — a
gateway fronting five bridges that reported a single upstream would show green
while a fifth of the tools were dead.

### Adding an integration

One module in `services/compose/agentbox-mcp/app/integrations/` exporting
`TOOLS` and `dispatch(name, args)`, one line in `INTEGRATIONS`, and a tool→
capability mapping in the policy. No second container, no second token, no
second compose file. Duplicate tool names refuse to start rather than
silently routing to whichever integration the dict happened to yield first.

### Identities

```
cli/agentbox identity list
cli/agentbox identity add sam
cli/agentbox identity remove sam
```

`add` generates a token, writes it into the gateway env, and prints the two
steps it will not do for you: store the token in 1Password, and redeploy. It
also warns when you add the *first* identity, because that is the moment
`AGENTBOX_MCP_SHARED_TOKEN` stops working — the Hermes config must be pointed
at a real identity token or every call 401s and it looks like a broken deploy.

Under the hood this is one env var:

```
AGENTBOX_IDENTITIES=alex:<token>,sam:<token>
```

**The token presented is the identity.** There is no way to ask to be someone
else, which is what stops an instruction embedded in an email from switching
accounts — switching would need a credential the process was never given.

Per-identity bridge routing, so each person's mail credential is in its own
container:

```
GOOGLE_BRIDGE_URL_SAM=http://sam-google-bridge:8080
GOOGLE_BRIDGE_TOKEN_SAM=...
```

Unset falls back to the shared bridge, which is right for genuinely shared
services like tasks and wrong for personal mail — `identity list` shows which
services each person reaches through their own bridge versus the shared one,
because that difference is easy to get wrong silently.

Grants can be scoped with `agentbox grant <tool> --for alex`; unscoped grants
cover anyone, as every pre-identity grant does.

### Onboarding somebody

```
cli/agentbox-invite create sam
AGENTBOX_INVITE_ORIGIN=http://agentbox.local:8770 \
AGENTBOX_GOOGLE_CLIENT_ID=<client id> \
  cli/agentbox-invite serve
cli/agentbox invite complete <id>
```

`AGENTBOX_INVITE_ORIGIN` must be the URL **she** opens, and
`<that origin>/google/callback` must be registered as a redirect URI on the
OAuth client — the same `redirect_uri_mismatch` that has already bitten once.
Without both set, the Google step is skipped and `serve` says so.

She opens the link on her phone, picks a name, ticks which services she wants
her own account for, and that is her whole involvement. She never sees Vikunja's
login page or a terminal.

**The link is a credential** — it authorises creating an identity on this box,
which buys more than a bridge token does. Single use, expires in 24h, and a
refusal never says whether the id or the secret was wrong. Send it directly, not
to a group chat.

**Run `serve` only while an invite is outstanding.** It binds to the LAN so a
phone can reach it, and it refuses to start when nothing is pending.

#### Why it is two commands and not one

The page has no privileges at all: no docker socket, no 1Password token, no
bridge tokens. It writes one spool file. `invite complete` does the privileged
work as you.

That split is forced, not stylistic. Registration is disabled on this Vikunja
(`VIKUNJA_SERVICE_ENABLEREGISTRATION=false`), so creating her account needs
`vikunja user create` *inside the container* — a docker-socket privilege. A
LAN-reachable web page holding the docker socket could read every bridge
credential with `docker inspect`.

#### What she gets automatically, and what she cannot

| | |
|---|---|
| Tasks | created for her — she gets a one-time password to change |
| Memory | her private scope exists the moment her identity does |
| The house | shared; nothing to set up |
| Gmail / Calendar | **she consents in the page**, then her bridge is built for her |

**Consent is the only part that is not automated**, and it cannot be: we can
neither create a Google account nor agree on her behalf. Everything after it
is. She clicks "Sign in with Google" in the invite page; Google returns an
authorisation code; `invite complete` exchanges it for a refresh token and
stands up `sam-google-bridge` holding only her credential, then wires
`GOOGLE_BRIDGE_URL_SAM` so her mail is reached with her token and nobody
else's.

**One timing constraint:** authorisation codes expire in about ten minutes and
are single use, so run `invite complete` while she is still in the room. A
stale code fails with `invalid_grant` and says so.

She gets **no writable calendar** by default —
`GOOGLE_ALLOWED_WRITE_CALENDAR_ID` is left blank in her env file, because
inheriting yours would let her assistant write to your calendar. Set it if she
wants one.

If she skips the Google step she falls back to the shared bridge, which is
correct for shared services and wrong for mail — `invite complete` says so
rather than leaving it silent.

### Memory scopes

Two planes, not per-item sharing:

| scope | who reads it |
|---|---|
| `<identity>` | only that person — **the default** |
| `household` | everyone |

Private by default, because a memory landing in the shared plane because nobody
said otherwise is a disclosure nobody chose. Filtering happens inside the
memory bridge, not in the gateway: a caller that asked politely for only its
own memories would leak the moment anything upstream got confused about who it
was serving.

Per-item ACLs were rejected deliberately — "Sam can see this one thing of
Alex's" makes "what can she see?" unanswerable without reading every row.

The assistant calls **`whoami`** to find out which identity it is acting for
and which scopes it can read. It is `allowed` and ungated, because being unsure
is what causes the cross-account mistakes it prevents. Memories written before
scopes existed have none and read as household.

`identity remove` does **not** delete that person's memories. They stay scoped
to the removed name and become unreachable; deleting them is a separate,
deliberate act.

Leaving `AGENTBOX_IDENTITIES` empty falls back to `AGENTBOX_MCP_SHARED_TOKEN`
with no identity — the single-operator behaviour. Note that once identities
exist the shared token stops working, deliberately: otherwise it would be an
unnamed sixth identity that every per-identity check ignores.

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

Bridges publish no host ports (memory-bridge excepted, for the review CLI), so
they cannot be probed from the host — that is the credential mitigation, not an
inconvenience. Ask the gateway, which probes them over the container networks:

```
curl -s localhost:3465/ready    # names each bridge and its state
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
sudo -u agentbox env HERMES_HOME=$HERMES_HOME HOME=$GATEWAY_USER_HOME \
  $GATEWAY_VENV/bin/python -m hermes_cli.main cron list
```

The scheduler enumerates jobs at startup, so **restart the gateway after adding
one** (`sudo systemctl restart hermes-gateway-agentbox`). `cron list` prints
"Gateway is not running" even when it is; check `.tick.lock` in
`$HERMES_HOME/cron/` for the real answer.

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

### Assistant containment

`doctor` verifies five properties the assistant's confinement rests on, all of
which fail silently if broken:

```
modify_production_gateway_config enforced (config.yaml not writable)
enable_skill_bundle_production   enforced (skills not writable)
modify_upstream_agent_source     enforced (hermes-agent-test not writable)
docker socket unreachable to the assistant
operator credential directory unreadable to the assistant
```

The first three are policy tiers that were bypassable until 2026-08-05, because
the assistant had a shell and could rewrite its own config. The last two are
what stop that shell — were it ever re-enabled — from reading a real
credential. **Never add `agentbox` to the `docker` group**: that is
root-equivalent and would undo the whole model.

### Adding a new skill

```
sudo install -d -o root -g agentbox -m 0750 $GATEWAY_HOME/hermes/skills/<name>
sudo install -o root -g agentbox -m 0640 skills/<name>/SKILL.md $GATEWAY_HOME/hermes/skills/<name>/SKILL.md
```

Skills in this repo are the source; that directory is what the gateway loads.
They are not synced automatically. Root-owned deliberately — the assistant
reads its skills and must not be able to rewrite them, since a skill shapes its
behaviour as directly as its config does.

## Reviewing the assistant's code proposals

The assistant can read this repository and propose changes. It cannot merge and
cannot deploy; proposals are branches in its own clone, which you fetch.

```
cli/agentbox proposals list
cli/agentbox proposals show <name>
cli/agentbox proposals merge <name>     # checks it out on review/<name>
```

`merge` deliberately lands the work on a `review/` branch rather than on main.
Merging straight to main here would reduce approval to one keystroke on an
unread diff, which is the thing the review gate exists to prevent. Test it, run
`validate` and the tests, then merge to main yourself.

### What it cannot touch

Refused outright, whatever the proposal claims to be for: `policies/`, both
policy gates, `cli/`, `.github/`, and the builder's own source. That is a path
check rather than a policy tier, because a tier cannot express "may edit any
file except the ones that govern it". `cli/agentbox smoke` exercises the
refusals on every run.

### First-time setup

The clone is owned by you with group 65532 and setgid, the same shape as
`policy-state` and `logs` — the container writes it by group.

```
git clone $AGENTBOX_REPO ~/.local/state/agentbox/builder-repo
sudo chown -R "$USER":65532 ~/.local/state/agentbox/builder-repo
sudo find ~/.local/state/agentbox/builder-repo -type d -exec chmod 2775 {} +
sudo find ~/.local/state/agentbox/builder-repo -type f -exec chmod 664 {} +
git -C ~/.local/state/agentbox/builder-repo config core.sharedRepository group
git -C ~/.local/state/agentbox/builder-repo remote set-url origin /origin
```

`origin` is `/origin` because that is where this repo is mounted **read-only**
inside the container. Chowning the clone to the container uid instead looks
simpler and then breaks: git refuses to read a repo it does not own, and that
cannot be waived with `-c safe.directory` — git ignores that from the command
line on purpose, so an attacker-controlled argv cannot disable the check.

## Home Assistant

Not deployed by default — it needs an HA instance. To bring it up:

```
cp services/compose/homeassistant-bridge/homeassistant-bridge.env.example ~/.config/agentbox/homeassistant-bridge.env
```

Fill in `HA_URL`, a long-lived access token from your HA profile page as
`HA_TOKEN`, and a generated `HA_BRIDGE_TOKEN`. Then decide what the assistant
may actually touch:

```
HA_CONTROLLABLE_ENTITIES=light.kitchen,light.hall,scene.evening
```

**Empty means it can read the house and change nothing**, which is the default
and the right starting point. Add entities one at a time.

```
cli/agentbox deploy homeassistant-bridge
cli/agentbox deploy homeassistant-mcp
```

Then add the five tools to the gateway's `mcp_servers` allowlist — see
"Adding a new skill" above for the pattern, and remember `doctor` will tell you
if a tool is invisible.

### Presence, cameras and screens

**Presence needs no configuration.** Reading `binary_sensor.*` is an ordinary
read, so once occupancy sensors exist in Home Assistant the assistant can
already tell which room someone is in, and route a response there. That is the
better answer to "know where I am" than a camera: no video, nobody else's
privacy, and no injection surface.

**Cameras are opt-in one at a time** via `HA_VIEWABLE_CAMERAS`, and separately
from control — `camera` stays in the never-actuate list, so nothing pans, tilts
or records. A look fetches one frame, sends it to the local vision model, and
discards it.

**Both ends are closed vocabularies**, which is what makes a camera safe to
point at an assistant that reads email:

- the *question* is an enum (`occupancy` or `activity`), not free text. A
  caller-composed question is a question an injected instruction can compose —
  "transcribe everything you can see" would turn the camera into a reader
  pointed at whatever is in frame.
- the *answer* is `{people, posture, text_visible}` with `posture` intersected
  against a fixed vocabulary. Unknown keys are dropped, and a reply that is not
  valid JSON — which is what a successful injection looks like — is discarded
  rather than passed through as prose.

Text in the room is reported as `text_visible: true` and **never transcribed**.
Knowing a whiteboard has writing on it is the useful part; reading it aloud is
the vulnerability.

An earlier version returned a prose description flagged `untrusted: true`. A
flag is a hint the model may ignore; a schema is not. Looking on request also
bounds exposure to moments somebody asked, which is why there is no continuous
mode.

**Screens have two levels**, like a phone lock screen:

```
HA_PRIVATE_SCREENS=media_player.office_monitor
```

A private screen gets the summary and the detail. Every other screen — including
any you have not classified — gets the summary only, and the detail is dropped
in the bridge rather than left to the assistant's judgement. A summary over 80
characters is refused, because otherwise it quietly becomes a second detail
field.

### What it will not do

- **No arbitrary service calls.** There is no `call_service` tool. HA's REST
  API is one endpoint from total control of the house, and putting an approval
  in front of that would be an approval asked so often it gets granted unread.
- **Locks, alarms, covers, garage doors and cameras are never actuated**, even
  if you put them in `HA_CONTROLLABLE_ENTITIES`. That check runs first and does
  not consult the list — `lock.front_door` and `light.front_door` differ by two
  characters, and the list is edited by a tired human.
- **Climate is bounded 5–30 °C** regardless of any grant.

Setting a temperature is `approval_required`, so it goes through the Discord
approval loop. Lights and scenes are `allowed`, because the allowlist is
already the constraint.

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

Scheduled nightly at 03:30 by `cli/agentbox-backup.timer` (a systemd *user*
timer, so it runs as the operator). `Persistent=true` catches up after the
machine has been off — without it a box that sleeps overnight silently never
backs up.

```
systemctl --user list-timers agentbox-backup.timer
```

Needs `sudo loginctl enable-linger $USER`, or it only runs while you are logged
in.

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
