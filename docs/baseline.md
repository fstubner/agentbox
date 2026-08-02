# Platform baseline

A known-good reference for the whole architecture, captured 2026-07-31. Its
purpose is to make "is the platform working as expected?" answerable by
comparison rather than by memory.

Reproduce it with:

```
cli/agentbox doctor      # every service below, plus freshness and readiness
cli/agentbox status      # one line per endpoint
```

`doctor` is the source of truth. This document explains what it checks and
records the numbers a probe cannot express.

## Services

| Component | Endpoint | Severity if down |
|---|---|---|
| Production model — `ornith-35b-q6-mtp` | `:1234/v1/models` | fail |
| Context worker — `fastcontext-worker` | `:1235/v1/models` | fail |
| Reason worker — `vibethinker-worker` | `:1236/v1/models` | fail |
| Vision model — `local-qwen25-vl-3b` | `:1240/v1/models` | warn |
| Role router | `:8765/health` | fail |
| vikunja-bridge | `:3466/health`, `/ready` | fail |
| google-workspace-bridge | `:3470/health`, `/ready` | fail |
| memory-bridge | `:3471/health`, `/ready` | fail |
| vikunja-mcp | `:3467/health` | fail |
| memory-mcp | `:3472/health` | fail |
| google-workspace-mcp-lite | `:3473/health` | fail |
| Vikunja (task backend) | `:3456`, via bridge `/ready` | fail |
| Control plane API | `:8000/api/evals/health` | warn |
| Control plane UI | `:4321/` | warn |
| Assistant gateways | `hermes-gateway-agentbox`, `hermes-assistant-gateway` | not probed |

**Why two severities.** The role workers are core: the router hands
`/context/extract` and `/reason/check` to them, so a stopped worker is a broken
capability. Vision and the control plane sit outside the assistant's request
path, and the evaluator legitimately stops model servers while benchmarking.
If those were failures, `doctor` would be red during normal work — and a check
that is normally red is a check people stop reading.

The gateways run as system units under a separate user and are not probed by
`doctor`; check them with
`systemctl is-active hermes-gateway-agentbox hermes-assistant-gateway`.

## Measured behaviour

Role routing is deterministic — each endpoint reaches its declared worker:

| Route | Worker | Latency |
|---|---|---|
| `/context/extract` | fastcontext (:1235) | ~1.9 s |
| `/reason/check` | vibethinker (:1236) | ~44.6 s |
| `/decide/orchestrate` | main model (:1234) | ~0.5 s |

`/reason/check` at ~45 s is the outlier. It is a reasoning model doing real
work, not a fault, but it is slow enough that any interactive path through it
needs a timeout budget set deliberately.

Approval policy, `always_denied` exits 2 and unknown actions default to
`approval_required`:

```
policy check merge_own_pr              -> exit 2
policy check disable_approval_gates    -> exit 2
policy check expose_services_to_internet -> exit 2
policy check read_repo_files           -> exit 0
policy check send_email                -> approval_required (unknown_action_default)
```

Payload sizes across both bridges, measured against real accounts:

| Endpoint | Bytes | Items | Per item |
|---|---|---|---|
| `POST /v1/calendar/events` | **23022** | 10 | ~2302 |
| `GET /v1/projects` (vikunja) | 3450 | — | — |
| `POST /v1/calendar/list` | 1818 | 3 | ~606 |
| `POST /v1/gmail/labels/list` | 1764 | 18 | ~98 |
| `GET /v1/tasks` (1 task, full) | 829 | 1 | 829 |
| `POST /v1/gmail/search` | 682 | 10 | ~68 |
| `GET /v1/tasks?view=lean` | 76 | 1 | 76 |

**Calendar events dominate everything else by a wide margin** — 23 KB, roughly
7,000 tokens, for ten events. That is 28× the full vikunja task list, and it
lands in context every time the assistant looks at a schedule.

The v1 proposal in `docs/context-economy.md` picked `vikunja/v1/tasks` as the
first projection target. On measured traffic that is the *second smallest*
payload in the system. `calendar/events` is where projection actually pays.

Field breakdown of one event (19 fields, 924 B):

| Field | Bytes | Share |
|---|---|---|
| `attendees` | 242 | 26.2% |
| `htmlLink` | 100 | 10.8% |
| `organizer` | 82 | 8.9% |
| `creator` | 82 | 8.9% |
| `location` | 69 | 7.5% |

### Measured after shipping the lean views

| Path | full | lean | reduction |
|---|---|---|---|
| `calendar/events` at the bridge | 23022 B | 3258 B | **85.8%** |
| `list_calendar_events` via MCP | 27002 B | 4105 B | **84.8%** |
| `list_tasks` via MCP | 989 B | 100 B | **89.9%** |

The calendar saving is ~5,600 tokens per schedule lookup. It beat the 74%
estimate because dropping the response envelope (`defaultReminders`,
`timeZone`, `accessRole`, `description`, `etag`, `kind`) stacks on top of the
per-event projection. `nextPageToken` is deliberately retained — dropping it
would silently truncate a multi-page calendar.

`status` is kept in the lean event even though a scheduling answer does not
need it: without it a cancelled event is indistinguishable from a live one,
which is an accuracy loss rather than a saving.

**The MCP layer inflates every tool result by ~17%** (23022 B at the bridge
becomes 27002 B through MCP) because `tool_result` serialises with
`json.dumps(..., indent=2)`. Pretty-printing costs tokens and buys a model
nothing. Not yet changed — it affects every tool on every MCP, so it wants its
own before/after rather than riding along with this change.

Disk: root 43%, models on a dedicated volume at 20% (312 GB free).

## Invariants this baseline assumes

Verified by tests, not by inspection — see `services/templates/bridge/`:

- Unset bridge token ⇒ 503, never silently open. Constant-time compare.
- `/health` never touches the upstream, so one backing-service outage cannot
  restart-loop the bridges in front of it.
- `/ready` does probe the upstream and returns 503 with the reason.
- Request logs never contain the Authorization header, request or response
  bodies, or any query parameter outside `LOGGED_QUERY_PARAMS`.
- `bridge_base.py` is byte-identical across the template and every bridge;
  `validate` fails on drift.

## Known gaps

- **No traffic baseline yet.** The request log now persists across deploys, but
  the numbers above are still from synthetic calls rather than a representative
  day of use.
- **The gateways are not probed** by `doctor`; they are system units owned by
  a different user.
- **The role router and both role workers are unused by the assistant.** The
  gateway talks directly to the main model and the MCPs. The router serves the
  evaluator. `doctor` treats the workers as core and fails when they are down,
  which is currently stricter than the assistant's actual dependencies.

## Restoring from cold

Everything is `enabled`, so a reboot brings the stack up on its own. If it does
not, order matters: model servers first (they are what the router needs), then
router and control plane, then the gateways. Docker services restart on their
own unless explicitly `docker stop`ped — an explicit stop overrides
`unless-stopped` and survives a reboot, which is how Vikunja stayed down on
2026-07-31.
