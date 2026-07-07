# Secrets Policy

Hermes should use capabilities, not raw credentials.

## Rules

- Do not commit secrets.
- Do not paste secrets into prompts.
- Do not print secrets in logs.
- Do not expose secrets to the model context.
- Prefer OAuth where possible.
- Prefer a secret manager for static credentials.

## Preferred Patterns

- OAuth MCP for Google/GitHub-style integrations.
- 1Password service account scoped only to the `Agentbox` vault for static service credentials.
- `op://` secret references resolved by the deploy layer, not by Hermes.
- Environment variables injected only into the specific service or tool that needs them.
- Narrow service/tool bridges that consume secrets without returning them to Hermes.

## 1Password Rules

- Hermes must not receive the 1Password service account token.
- Hermes must not call broad `op item list`, `op item get`, or `op read` directly.
- Agent-created services may request named credentials through controlled deploy scripts.
- Secret values should be created, rotated, or injected by the deploy layer or a future secrets broker.
- Do not print resolved `op://` values in dry-runs, logs, or chat.

## Agent Language

Use names like:

- `gmail_readonly`
- `calendar_household_write`
- `discord_bot_sender`
- `qwen_local_endpoint`

Do not reveal the underlying token values.
