# Approval policy

A readable version of `approval-policy.yaml`. The YAML is what the code loads,
in `cli/agentbox policy check` for operator actions and in `policy_gate.py` for
assistant tool calls. If the two disagree, the YAML is what happens, so fix this
file.

One policy covers the operator and the assistant. Capabilities are named here,
and the YAML's `tools` section maps each assistant tool to one of them, so a
tool call lands in the same tier as the matching operator action.

I prefer narrowing a capability to gating it. A constraint holds even when the
model is compromised, and an approval only helps if a person reads it
carefully. Several entries below are allowed because the bridge already limits
them.

## Allowed

Operator and development:

- Read repository files.
- Inspect service logs.
- Run validators and tests.
- Draft plans, issues, PR descriptions and skill changes.
- Create branches.
- Generate Docker Compose proposals.
- Scaffold a new service for review with `cli/agentbox scaffold`, which writes
  a branch and never deploys or merges.

Assistant:

- Manage tasks: list, search, create, update, complete and comment. Every write
  can be undone and nothing deletes.
- Read Gmail and Calendar: search, read, list labels and calendars, and check
  free/busy times.
- Propose a memory for review. Proposing is not storing.
- Create and apply Gmail labels under its own `agentbox/` namespace. The bridge
  refuses a label outside it.
- Write a Gmail draft. Drafts are never sent.
- Create an event on the configured assistant calendar. Attendees and
  conference links are refused and `sendUpdates=none` is forced, so an event
  cannot notify anyone.

## Needs approval

Operator and development:

- Start, stop, restart or remove services.
- Install packages on the host.
- Add a new MCP server.
- Enable a new skill bundle in production.
- Change the production gateway config.
- Change network bindings or exposed ports.
- Place grocery orders, spend money, or use paid APIs beyond configured limits.
- Make health-related recommendations on diet, medication, exercise or
  routines beyond low-risk planning suggestions.

Assistant:

- Archive an email or mark it read. Both change what the owner will notice in
  their inbox, and a correct archive and a wrong one are the same API call, so
  this cannot be designed out at the bridge.
- Store durable health, relationship or other sensitive memory. No tool can do
  this, because storing needs a review token the assistant does not hold.
- Send messages, emails, calendar invites or anything external. No tool can do
  this either, because no bridge exposes sending.

An approval is a grant that expires.

```
cli/agentbox grant archive_gmail --ttl 15m
```

Grants are single-use by default. A tool mapped to no capability is
`approval_required`, so a tool added without a mapping fails closed.

## Always denied

These can only be done by a person, outside the assistant.

- Merge its own PR.
- Disable approval gates.
- Expose services to the public internet.
- Read or print long-lived credentials.
- Delete emails, files, repositories, backups or production data.
- Change the gateway's own source code.
- Diagnose medical conditions, change medication guidance or override a
  clinician's advice.

## Labels and calendars

- Labels the assistant creates use the `agentbox/` prefix
  (`GOOGLE_OWNED_LABEL_PREFIX`). Creating a label forces the prefix, and
  applying one checks the label id and refuses anything outside the namespace.
- The assistant can only write to the configured assistant calendar
  (`GOOGLE_ALLOWED_WRITE_CALENDAR_ID`). Any other calendar is refused.
- It may read personal calendars when relevant, and may not edit them.
- No bridge exposes removing a label, so there is nothing to permit.

## Why reads are allowed

Whether a read goes beyond what the current task needs is a judgement for each
call, which a tier cannot express. Gating every read would make an assistant
whose job is your mail and calendar unusable. The real risk, personal data
leaving the account, is contained elsewhere. No tool sends mail, calendar
invites are refused, and drafts stay in the account until a person sends them.
