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
carefully. Many entries below are allowed because the bridge already limits
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

- Manage tasks. Every write can be undone and nothing deletes.
- Read Gmail and Calendar.
- Archive an email or mark it read. Both can be undone in one click and the
  mail stays in the account. The bridge accepts only those two changes.
- Create and apply Gmail labels under its own `agentbox/` namespace.
- Write a Gmail draft. Drafts are never sent.
- Create an event on the assistant calendar. Attendees and conference links
  are refused and `sendUpdates=none` is forced, so an event cannot notify
  anyone.
- Read Drive, and write text files into its own Drive folder. It cannot
  delete or share.
- Propose a memory, a rule or an invite. Each one does nothing until a person
  approves or sends it.
- Read the state of the house, control lights, scenes and media on an
  allowlist, and look at one frame from an allowlisted camera.
- Speak through the household speakers. The speaker service refuses during
  quiet hours.
- Send someone their own sign-in link. A link the assistant asks for cannot
  approve memories, change accounts, invite people or change settings.

## Needs approval

Operator and development:

- Start, stop, restart or remove services.
- Install packages on the host.
- Add a new MCP server.
- Enable a new skill bundle in production.
- Change the production gateway config.
- Change network bindings or exposed ports.
- Spend money, including grocery orders and paid APIs over their limits.
- Make health-related recommendations beyond low-risk planning suggestions.

Assistant:

- Change the heating or cooling. It costs money and affects people asleep.
- Write a Home Assistant automation, which runs later with nobody watching.
- Store sensitive memory. No tool can do this, because storing needs a review
  token the assistant does not hold.
- Send messages, emails or calendar invites. No tool can do this either,
  because no bridge can send.

An approval is a grant that expires.

```
cli/agentbox grant <tool> --ttl 15m
```

Grants are single-use by default. A tool mapped to no capability is
`approval_required`, and `cli/agentbox validate` fails on an unmapped tool.

## Always denied

Only a person can do these, outside the assistant.

- Merge its own PR.
- Disable approval gates.
- Expose services to the public internet.
- Read or print long-lived credentials.
- Delete emails, files, repositories, backups or production data.
- Change the gateway's own source code.
- Diagnose medical conditions, change medication guidance or override a
  clinician's advice.
- Control locks, alarms, garage doors or covers.

## Labels and calendars

- Labels the assistant creates get the `agentbox/` prefix
  (`GOOGLE_OWNED_LABEL_PREFIX`). Applying a label checks its id and refuses
  anything outside that namespace.
- The assistant can only write to the assistant calendar
  (`GOOGLE_ALLOWED_WRITE_CALENDAR_ID`). It can read personal calendars but not
  edit them.
- No bridge can remove a label.

## Why reads are allowed

Whether a read goes beyond what the current task needs has to be judged per
call, and a tier cannot do that. Gating every read would make an assistant
whose job is your mail and calendar unusable. The risk that matters is
personal data leaving the account, and that is handled elsewhere. No tool
sends mail, calendar invites are refused, and drafts stay in the account until
a person sends them.
