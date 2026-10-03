# Platform baseline

A reference for what a healthy deployment looks like, so "is it working?" can
be answered by comparing against it.

```
cli/agentbox doctor      # every check below, plus freshness and readiness
cli/agentbox status      # one line per endpoint
```

`doctor` is the source of truth. This document explains what it checks and
records the numbers a probe cannot express.

## Services

| Component | Check | Severity if down |
|---|---|---|
| Main model | `:1234/v1/models` | fail |
| agentbox-mcp | `:3465/health` | fail |
| Bridges and their backing services | agentbox-mcp `/ready` | fail |
| Control plane API | `:8000/api/evals/health` | warn |
| Control plane UI | `:4321/` | warn |
| Gateways | `hermes-gateway-agentbox`, `hermes-assistant-gateway` | not probed |

`fail` is for what the assistant needs to answer a request. That is the model
and the tool chain behind it. Everything else warns. People stop reading a
check that is red during normal operation, so nothing optional can fail.

Bridges publish no host ports, so they cannot be probed from the host. Instead
agentbox-mcp's `/ready` asks every bridge over the container networks and names
any that are not ready. Each bridge's own `/ready` probes the service behind
it, so a stopped Vikunja shows up here even while the bridge in front of it is
healthy.

The gateways run as system units under a separate user. Check them with

```
systemctl is-active hermes-gateway-agentbox hermes-assistant-gateway
```

## Approval policy

`always_denied` exits 2, and an action the policy does not list needs
approval.

```
policy check merge_own_pr                -> exit 2
policy check disable_approval_gates      -> exit 2
policy check expose_services_to_internet -> exit 2
policy check read_repo_files             -> allowed
policy check send_email                  -> approval_required (unknown_action_default)
```

## Payload sizes

Measured against real accounts on 2026-07-31.

| Endpoint | Bytes | Items | Per item |
|---|---|---|---|
| `POST /v1/calendar/events` | **23022** | 10 | ~2302 |
| `GET /v1/projects` (vikunja) | 3450 | | |
| `POST /v1/calendar/list` | 1818 | 3 | ~606 |
| `POST /v1/gmail/labels/list` | 1764 | 18 | ~98 |
| `GET /v1/tasks` (1 task, full) | 829 | 1 | 829 |
| `POST /v1/gmail/search` | 682 | 10 | ~68 |
| `GET /v1/tasks?view=lean` | 76 | 1 | 76 |

Calendar events are the largest. Ten events came to 23 KB, about 7,000 tokens,
which is 28 times the full task list. `vikunja/v1/tasks` is the second smallest
payload. Projection saves the most on `calendar/events`.

The biggest fields in one event (19 fields, 924 B):

| Field | Bytes | Share |
|---|---|---|
| `attendees` | 242 | 26.2% |
| `htmlLink` | 100 | 10.8% |
| `organizer` | 82 | 8.9% |
| `creator` | 82 | 8.9% |
| `location` | 69 | 7.5% |

### With the lean views

| Path | full | lean | reduction |
|---|---|---|---|
| `calendar/events` at the bridge | 23022 B | 3258 B | **85.8%** |
| `list_calendar_events` via MCP | 27002 B | 4105 B | **84.8%** |
| `list_tasks` via MCP | 989 B | 100 B | **89.9%** |

That saves about 5,600 tokens per schedule lookup. The reduction is larger
than the 74% estimate because the response envelope (`defaultReminders`,
`timeZone`, `accessRole`, `description`, `etag`, `kind`) is dropped as well as
the per-event fields. `nextPageToken` is kept, because dropping it would cut
off a calendar longer than one page with no error.

`status` stays in the lean event even though a scheduling answer rarely needs
it. Without it a cancelled event looks the same as a live one.

The MCP column was measured when tool results were pretty-printed, which added
about 17%. They are now serialised compactly (`tool_result` in
`services/templates/mcp/mcp_base.py`).

## Invariants

Each of these is checked by tests in `services/templates/bridge/`.

- A bridge with no token set returns 503 and never runs open. The token
  comparison is constant-time.
- `/health` never touches the upstream, so an outage in one backing service
  cannot restart-loop the bridges in front of it.
- `/ready` does probe the upstream, and returns 503 with the reason.
- Request logs never contain the Authorization header, request or response
  bodies, or any query parameter outside `LOGGED_QUERY_PARAMS`.
- There is one `bridge_base.py`, in `services/templates/bridge/`. Every bridge
  image copies it at build time, so bridges cannot drift apart.

## Known gaps

- The payload numbers come from synthetic calls, not a representative day of
  use. The request log persists across deploys, so a traffic baseline can be
  taken once the stack has run for a while.
- `doctor` does not probe the gateways, because they are system units owned by
  a different user.

## Starting from cold

Everything is enabled, so a reboot brings the stack up on its own. If it does
not, start the model server first, then the control plane, then the gateways.
Docker services restart on their own unless they were stopped with
`docker stop`. An explicit stop overrides `unless-stopped` and survives a
reboot.
