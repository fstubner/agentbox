# Runbook

## Health check
```
cli/agentbox doctor
```
Checks: git/curl/docker present, main model endpoint (`AGENTBOX_MAIN_BASE`, default `127.0.0.1:1234`) and router (`127.0.0.1:8765/health`) responding, disk usage, repo validation.

## Repo validation (CI-equivalent, run locally)
```
cli/agentbox validate
```
Checks for committed secrets/keys, insecure `0.0.0.0` bindings, unqualified Docker port mappings, missing compose resource limits / `no-new-privileges`, and that `policies/approval-policy.yaml` has all three tiers populated.

## Policy lookup
```
cli/agentbox policy check <action>
```
Returns the action's tier (`allowed` / `approval_required` / `always_denied`) as JSON; exit code 2 on `always_denied`. Unknown actions default to `approval_required` (deny-by-default).

## Bringing up services
```
cd services/compose/<service>
docker compose up -d
```
Each service directory ships a `*.env.example` or `*.op.env.example` — copy and fill in before first start. Bridges resolve 1Password `op://` references at deploy time if you use 1Password; otherwise populate the plain `.env` directly.

## Router
```
router/agentbox_router.py
```
Configure via env: `AGENTBOX_MAIN_BASE`, `AGENTBOX_CONTEXT_BASE`, `AGENTBOX_REASON_BASE` (upstream llama-server URLs), `AGENTBOX_MAIN_MODEL` / `_CONTEXT_MODEL` / `_REASON_MODEL` (aliases), `AGENTBOX_ROUTER_HOST` / `_PORT`.
