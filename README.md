# Agentbox

A self-hosted personal AI assistant platform for a single operator on local
hardware. Runs a local LLM behind a gateway with narrow, policy-gated levers:
task management, email/calendar bridges, and role-routed local worker models.

Design principles:

- **Levers, not shell.** The assistant gets MCP tools with explicit contracts,
  never raw terminal access.
- **Bridges hold credentials.** OAuth tokens live in bridge containers; the
  assistant never sees raw secrets (1Password `op://` references resolve at
  deploy time).
- **Deterministic routing.** Worker-role routing is code, not model judgment.
- **Approval gates.** Mutating and personal-data actions require approval per
  `policies/`.

## Layout

| Dir | Contents |
|---|---|
| `router/` | Role router exposing `/context/extract`, `/reason/check`, `/decide/orchestrate` over local llama.cpp workers |
| `gateway/` | Hermes gateway config examples |
| `policies/` | Approval / network / secrets policies |
| `services/` | Docker Compose stacks: task backend (Vikunja) + bridge/MCP pairs for memory and Google Workspace |
| `cli/` | Operator CLI (deploy / validate / doctor / status) |
| `docs/` | Architecture and runbook |

Reference deployment: AMD Strix Halo (Ryzen AI Max+ 395, 128GB unified
memory) running llama.cpp with a ~35B MoE model at 200K context.

License: Apache-2.0
