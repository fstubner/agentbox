# Approval Policy

Hermes may perform low-risk read-only work without approval.

## Allowed Without Approval

- Read repo files.
- Inspect service logs.
- Run validators and tests.
- Draft plans, issues, PR descriptions, and skill changes.
- Create branches.
- Generate Docker Compose proposals.

## Requires Approval

- Start, stop, restart, or remove services.
- Install packages on the host.
- Add a new MCP server.
- Enable a new skill bundle in production.
- Modify production Hermes config.
- Change network bindings or exposed ports.
- Access Gmail, Calendar, Drive, or other personal data beyond the requested task.
- Archive, mark read, label, or unsubscribe email.
- Create, rename, or remove Gmail labels.
- Place grocery orders or spend money.
- Store durable health, relationship, or sensitive household memory.
- Make health-sensitive diet, medication, exercise, or routine recommendations
  beyond low-risk planning suggestions.
- Spend money or use paid APIs beyond configured limits.
- Send messages, emails, calendar invites, or external communications.

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

- Assistant-created Gmail labels must use an `Assistant/` prefix.
- Hermes may remove only labels it created or owns under that prefix, subject to the configured bridge permissions.
- Hermes may write only to the configured assistant calendar.
- Hermes may read personal calendars when relevant, but must not edit the operator's personal calendar without explicit approval and a bridge that enforces that boundary.
