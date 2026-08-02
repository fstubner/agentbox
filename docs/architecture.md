# Architecture

Agentbox is a self-hosted personal AI assistant platform for a single
operator on local hardware. The assistant (a Hermes-style gateway) never gets
raw shell access or raw credentials — it gets narrow, policy-gated levers.

```
                    ┌────────────────────────────┐
   Chat client ───▶ │  Assistant gateway         │  runtime state: $AGENTBOX_HOME
                    │  (isolated system user)    │  (private; never in this repo)
                    └──────┬─────────────────────┘
                           │ MCP levers only (no shell)
      ┌────────────────────┼──────────────────────────┐
      ▼                    ▼                          ▼
  bridges (docker)     role router (:8765)       policy engine
  google-workspace     /context/extract          policies/approval-policy.yaml
  memory / vikunja     /reason/check             (deny-by-default;
  (localhost-bound)    /decide/orchestrate        cli/agentbox policy check)
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        main model     context      reasoning
        llama-server   worker       worker
        :1234          :1235        :1236
```

## Design principles

- **Levers, not shell.** Every capability is an explicit endpoint with a
  contract, not terminal access.
- **Bridges hold credentials.** OAuth tokens and API secrets live inside
  bridge containers, injected at deploy time (1Password `op://` references or
  plain env files). The assistant and router never see them.
- **Deterministic routing.** Which worker handles which role is code
  (`router/agentbox_router.py`), not model judgment.
- **Deny by default.** Actions resolve against `policies/approval-policy.yaml`
  in tier order `always_denied` → `approval_required` → `allowed`; unknown
  actions require approval.
- **Local-only network posture.** Compose services bind to localhost or an
  explicit LAN IP; `cli/agentbox validate` rejects `0.0.0.0` bindings and
  unqualified port mappings.

## Components

| Dir | Role |
|---|---|
| `router/` | Stdlib-only role router + systemd user units for the two workers |
| `gateway/` | Example gateway configuration (model aliases, MCP endpoints) |
| `policies/` | Machine-readable approval policy + human-readable mirrors |
| `services/compose/` | One directory per service: task backend (Vikunja) and bridge/MCP pairs for memory and Google Workspace |
| `cli/` | Operator CLI: `deploy`, `validate`, `doctor`, `status`, `policy check` |
| `docs/` | This document and the runbook |

## Policy enforcement

Two files, two audiences, same semantics (`always_denied` → `approval_required`
→ `allowed`, unknown defaults to `approval_required`):

- `policies/approval-policy.yaml` — **operator and development** actions
  (`service_lifecycle`, `host_package_install`, `merge_own_pr`). Enforced by
  `cli/agentbox policy check`.
- `policies/runtime-actions.yaml` — **assistant tool calls** (`list_tasks`,
  `archive_gmail`). Enforced in every MCP by `policy_gate.py` at the single
  `tool_call` dispatch point.

They are separate because the vocabularies do not overlap: no tool name appears
in the operator policy, so checking tools against it would send every one of
them to `approval_required` by deny-by-default and stop the assistant doing
anything. Merging them would also put "install a package on the host" and
"list my tasks" in one list answering to one command.

`approval_required` tools need an operator grant (`cli/agentbox grant`), which
is time-boxed and single-use by default. See the runbook.

## Extension points

- **Builder sandbox** — implemented: `cli/agentbox scaffold <name>` generates a
  complete bridge from `services/templates/bridge`, runs `validate`, and commits
  it to a `scaffold/*` branch. It never deploys and never merges, because
  `merge_own_pr` is `always_denied` and a generated service that deployed itself
  would route around that.
- **Memory review gates** — implemented: the assistant proposes, an operator
  approves via `cli/agentbox memory` using a credential the assistant does not
  hold.
- **Cloud escalation** (not implemented): the intended pattern is
  escalate-on-failure (try the local model, escalate when validation fails),
  gated by the approval policy — not an LLM-based tier classifier.

## Reference deployment

AMD Strix Halo (Ryzen AI Max+ 395, Radeon 8060S iGPU, gfx1151, unified
memory) running Ubuntu Server with llama.cpp (Vulkan) serving a ~35B MoE
model at up to 200K context, plus two small worker models. Any machine that
can serve an OpenAI-compatible endpoint works; the router and CLI are
stdlib-only Python.
