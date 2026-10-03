# Agentbox

[![ci](https://github.com/fstubner/agentbox/actions/workflows/ci.yml/badge.svg)](https://github.com/fstubner/agentbox/actions/workflows/ci.yml)
[![clean-room onboarding](https://github.com/fstubner/agentbox/actions/workflows/onboarding.yml/badge.svg)](https://github.com/fstubner/agentbox/actions/workflows/onboarding.yml)
[![licence: MIT OR Apache-2.0](https://img.shields.io/badge/licence-MIT%20OR%20Apache--2.0-blue)](#licence)

A self-hosted, privacy-first AI assistant for a household running on local hardware. Agentbox runs a local LLM behind a gateway with narrow, policy-gated levers: task management, calendar/email bridges, and role-routed worker models.

Two people share it, with a shared household plane (joint tasks, shopping, shared scheduling) and isolated private planes (private mail, personal calendar, personal memory). Identity is bound to the authenticated session, never passed as an LLM tool argument.

A single-operator deployment is fully supported without configuring multiple identities.

The second badge is worth clicking. It runs the quickstart below on a fresh
machine that has never seen this repository. It installs the prerequisites,
runs setup, validates, and deploys a service with no secrets configured. If any
step breaks, the badge goes red. The instructions are checked on every change
rather than just written down.

---

## Core Security & Architectural Principles

- **Levers, not shell:** The assistant interacts exclusively through Model Context Protocol (MCP) tools with explicit schemas and contracts — never raw terminal access.
- **Process Sandboxing (`agentbox-sandbox`):** Agent processes run inside a Bubblewrap container that mounts `/` read-only, hides operator credentials by mounting an empty tmpfs over `~/.config`, and confines write access to `~/.local/state/agentbox`.
- **Credential Containment:** OAuth tokens and API credentials live in isolated bridge containers. The assistant never inspects raw secrets.
- **Pluggable Secret Management:** Deploy-time secret resolution is delegated ephemerally in RAM. Out of the box, Agentbox supports Infisical (`infisical://`), Bitwarden Secrets Manager (`bws://`), Doppler (`doppler://`), 1Password (`op://`), or custom secret managers via `~/.config/agentbox/secret-wrapper`. Plain `.env` files are supported for fully-contained environments.
- **Non-Root Proposal Sandbox:** Code modifications proposed by the assistant write to an isolated setgid repository (`builder-repo`, GID 65532) preventing direct modifications to host code.
- **Deterministic Approval Gates:** Mutating and private-data actions require explicit operator approval defined in `policies/approval-policy.yaml`.

---

## Directory Layout

| Directory | Purpose |
| :--- | :--- |
| `cli/` | Operator CLI (`agentbox`) and sandbox launcher (`agentbox-sandbox`) |
| `services/` | Docker Compose service stacks (Vikunja, bridges, MCP servers) |
| `policies/` | Declarative approval, network, and tool capability policies |
| `router/` | Role router dispatching over local llama.cpp worker endpoints |
| `gateway/` | Gateway configuration and integration definitions |
| `docs/` | Architecture records, runbooks, and baseline evaluations |

---

## Quickstart

### 1. Prerequisites

- Linux host (Ubuntu 24.04+ or modern systemd distribution)
- Docker & Docker Compose v2
- Python 3.12+
- Bubblewrap (`sudo apt install bubblewrap`)
- Git

### 2. Automated Machine Setup

Run `agentbox setup` to configure directories, file permissions, and templates idempotently:

```bash
./cli/agentbox setup
```

This automatically:
- Creates and locks `~/.config/agentbox` to mode `0700`.
- Initialises state directories (`backups`, `logs`, `invites`, `portal`) to mode `0750`.
- Clones and hardens the proposal sandbox repository with group setgid.
- Installs default templates (`.env.example` and `secret-wrapper.example`).
- Installs the Bubblewrap sandboxing wrapper at `cli/agentbox-sandbox`.

### 3. Configure Secrets

Place service environment files in `~/.config/agentbox/<service>.env`. You can use secret reference URIs or standard environment variables.

To connect your secret manager:
- **Infisical, Bitwarden, Doppler, or 1Password:** Use URIs such as `op://vault/service/key`, `infisical://service/key`, `bws://secret-id`, or `doppler://token`.
- **Custom Secret Managers (Vault, SOPS, CyberArk):** Copy `~/.config/agentbox/secret-wrapper.example` to `~/.config/agentbox/secret-wrapper`, make it executable (`chmod +x`), and define your resolution command.

### 4. Verification & Diagnostics

Validate repository contracts, tool schema budgets, and policy consistency:

```bash
./cli/agentbox validate
```

Audit runtime daemon health, container bridge endpoints, and containment boundaries:

```bash
./cli/agentbox doctor
```

### 5. Deploying Services

Deploy any service stack with ephemeral secret injection:

```bash
./cli/agentbox deploy <service>
```

---

## Everyday Operator Commands

```bash
# View system status, bridge connections, and pending approvals
./cli/agentbox status

# Pull updated container images and redeploy
./cli/agentbox update <service>

# Review proposed changes submitted by the assistant
./cli/agentbox proposals list
./cli/agentbox proposals show <name>

# Create an encrypted archive of state and memory stores
./cli/agentbox backup

# Run integration workflows end-to-end
./cli/agentbox smoke
```

---

## License

Apache-2.0

## Licence

Dual licensed under either of

- MIT ([LICENSE-MIT](LICENSE-MIT))
- Apache License 2.0 ([LICENSE-APACHE](LICENSE-APACHE))

at your option.
