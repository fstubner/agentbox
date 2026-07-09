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

## Extension points (deliberately not implemented in v1)

- **Cloud escalation**: the intended pattern is escalate-on-failure (try the
  local model, escalate when validation fails), gated by the approval policy —
  not an LLM-based tier classifier.
- **Memory review gates**: the memory bridge stores proposals; a review-queue
  workflow in front of durable memory is future work.
- **Builder sandbox**: scaffolding new services behind PR review; the
  `services/templates/` directory is reserved for it.

## Reference deployment

AMD Strix Halo (Ryzen AI Max+ 395, Radeon 8060S iGPU, gfx1151, unified
memory) running Ubuntu Server with llama.cpp (Vulkan) serving a ~35B MoE
model at up to 200K context, plus two small worker models. Any machine that
can serve an OpenAI-compatible endpoint works; the router and CLI are
stdlib-only Python.
