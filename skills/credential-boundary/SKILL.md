# Credential Boundary

Use this skill in all Discord, gateway, and personal-assistant sessions.

Rules:
- Never print, summarize, enumerate, or partially reveal raw credentials, tokens, API keys, OAuth secrets, private keys, session keys, cookie values, or secret file contents.
- Do not provide commands that would reveal secrets, such as env/printenv secret greps, cat .env, op read, reading mcp-token directories, or reading /proc/*/environ.
- If the operator asks to inspect credentials, tell them to use SSH or 1Password directly outside Hermes.
- You may discuss credential categories at a high level, rotation steps, and whether a secret should exist, without names or values.
- If a task needs a credential, call the appropriate bridge/service. Do not request or handle the raw value.
