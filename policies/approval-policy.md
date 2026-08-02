# Approval Policy

Human-readable mirror of `approval-policy.yaml`. The YAML is what the code
loads — `cli/agentbox policy check` for operator actions, and `policy_gate.py`
in every MCP for assistant tool calls. If the two disagree, the YAML is what
actually happens; fix this file.

One policy covers both actors. Capabilities are named here; the YAML's `tools`
section maps each assistant tool onto one of them, so a tool call resolves to
the same tier as the equivalent operator action.

**Prefer narrowing a capability over gating it.** A constraint holds even when
the model is compromised; an approval only helps if a human reads carefully
before saying yes. Several entries below are allowed *because* the bridge
already contains them, not because the risk was waved through.

## Allowed Without Approval

Operator and development:

- Read repo files.
- Inspect service logs.
- Run validators and tests.
- Draft plans, issues, PR descriptions, and skill changes.
- Create branches.
- Generate Docker Compose proposals.
- Scaffold a new service for review (`cli/agentbox scaffold`) — writes a
  branch, never deploys and never merges.

Assistant:

- Manage tasks in its own backend: list, search, create, update, complete,
  comment. Every write is reversible and nothing deletes.
- Read Gmail and Calendar: search, read, normalise, list labels and calendars,
  check free/busy.
- Propose a memory for review. Proposing is not storing.
- Create and apply Gmail labels **within the assistant's own namespace**
  (`agentbox/`). Applying a label outside that namespace is refused by the
  bridge, not merely discouraged.
- Compose a Gmail draft. Drafts are never sent.
- Create an event on the configured assistant calendar. Attendees and
  conference data are refused and `sendUpdates=none` is forced, so creating an
  event cannot notify anyone.

## Requires Approval

Operator and development:

- Start, stop, restart, or remove services.
- Install packages on the host.
- Add a new MCP server.
- Enable a new skill bundle in production.
- Modify production Hermes config.
- Change network bindings or exposed ports.
- Place grocery orders, spend money, or use paid APIs beyond configured limits.
- Make health-sensitive diet, medication, exercise, or routine recommendations
  beyond low-risk planning suggestions.

Assistant:

- Archive an email or mark it read. Both change what the operator will notice
  in their own inbox, and a correctly-archived message and a wrongly-archived
  one are the same API call — so this cannot be designed out at the bridge.
- Store durable health, relationship, or sensitive household memory. No tool
  reaches this: durable writes need an operator review token the assistant does
  not hold.
- Send messages, emails, calendar invites, or external communications. No tool
  reaches this either — sending is not exposed by any bridge.

Approval at runtime is an explicit, time-boxed grant:

```
cli/agentbox grant archive_gmail --ttl 15m
```

Grants are single-use by default. A tool that maps to no capability defaults to
`approval_required`, so a tool added without being mapped fails closed.

## Always Denied Unless Manually Done Outside Hermes

- Merge its own PR.
- Disable approval gates.
- Expose services to the public internet.
- Read or print raw long-lived credentials.
- Delete emails, files, repositories, backups, or production data.
- Modify upstream Hermes Agent source code.
- Diagnose medical conditions, change medication guidance, or override clinician
  advice.

## Assistant-Owned Labels And Calendars

For Google Workspace integrations:

- Assistant-created Gmail labels use the `agentbox/` prefix
  (`GOOGLE_OWNED_LABEL_PREFIX`). Creating a label forces the prefix, and
  applying one resolves the id against the account and refuses anything outside
  that namespace.
- Hermes may write only to the configured assistant calendar
  (`GOOGLE_ALLOWED_WRITE_CALENDAR_ID`); any other calendar id is refused.
- Hermes may read personal calendars when relevant, and may not edit the
  operator's personal calendar.
- Label *removal* is not exposed by any bridge, so there is nothing to permit.

## Where reads sit, and why

An earlier version of this file put "access Gmail, Calendar, Drive... beyond the
requested task" behind approval. The qualifier was the part that mattered, and
a tier cannot express it — whether a read exceeds what the current task needs
is a per-call judgement. Gating all reads instead would make an assistant whose
job is the operator's mail and calendar unusable.

The narrower risk, personal data leaving the account, is contained elsewhere:
no tool sends mail, calendar invites are refused, and drafts stay in the
account until a human presses send.
