# Agentbox — Target Architecture & Port/Rewrite Verdicts (Phase 1)

Draft 2026-07-07. Will become `agentbox/docs/architecture.md`. Sources verified against the
live reference deployment (Strix Halo laptop), which supersedes the Windows snapshot everywhere.

## System overview (target)

```
                    ┌────────────────────────────┐
   Discord/UI ───▶  │  Hermes gateway            │  runtime state: ~/agentbox (private)
                    │  (isolated agentbox user)  │
                    └──────┬─────────────────────┘
                           │ MCP levers only (no shell)
      ┌────────────────────┼──────────────────────────┐
      ▼                    ▼                          ▼
  bridges (docker)     agentbox-router           policy engine
  google-workspace     :8765 role router         approval-policy.yaml
  memory / vikunja     /context/extract          (enforced, not prose)
  (localhost-bound)    /reason/check
                       /decide/orchestrate
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        llama-server   fastcontext  vibethinker
        ornith 35B     worker :1235 worker :1236
        :1234 (Vulkan or ROCm — Phase 4 decides)
```

Open design questions v1 does NOT implement but must not preclude (extension points):
- **Tier routing / cloud escalation**: escalate-on-failure hook after local attempt; policy gate first; no LLM-based tier classifier in v1.
- **Memory store review gates**: memory-bridge exists; review-queue workflow is future work.
- **Bridge isolation model**: bridges hold OAuth tokens, Hermes never sees raw credentials — preserved as a hard invariant; formalize as policy tests later.
- **Builder sandbox** (ad-hoc service scaffolding behind PR review): designed separately; the repo layout reserves `services/templates/` for it.

## Verdict table (validated against live sources)

| Component | Live source | Size | Verdict |
|---|---|---|---|
| agentbox-router | `~/agent-control-plane/services/agentbox-router/agentbox_router.py` | 209 lines | **Port as-is** + pytest (mocked upstreams) + type hints. Running in prod 6+ days. |
| deploy/ scripts | `~/agent-control-plane/deploy/` | 26 scripts, ~1.3k lines shell | **Rewrite as one CLI** (`agentbox` — deploy/validate/doctor/status/backup). Port the *patterns*: op:// wrapper, validate.sh checks, doctor probes. The 8 `patch-*.sh` and `migrate-*.sh` are applied history — drop. |
| policies/ | `approval-policy.md`, `network-policy.md`, `secrets-policy.md` | 3 prose docs | **Rewrite as `policies/*.yaml`** loaded by gateway/CLI with enforcement tests; keep md as generated/maintained mirror. |
| hermes/config | `~/agent-control-plane/hermes/config/config.example.yaml` | — | **Port with corrections**: model naming still `qwen36-35b-a3b-q4`; live alias is `ornith-35b-q6-mtp`. Ship generic example (`local-main` alias) so it isn't model-coupled again. |
| services/compose | 8 stacks (vikunja, vikunja-bridge/mcp, memory-bridge/mcp, google-workspace-bridge/mcp-lite, example-service) | — | **Port**. Declarative, localhost-bound, healthy in prod. Genericize env examples. |
| eval harness | `~/local-ai-evals/agentbox_matrix_eval.py` | 4,318 lines | **Rewrite as package** (`harness/`: fixtures, runners, scoring, reporting). Behavior-lock first: capture golden outputs from a real fixture run on the laptop before restructuring. The `.bak-*` forks are dead — ignore. |
| eval runner/job/watch | `agentbox_eval_runner.py` (544), `agentbox_eval_job.py` (315), `agentbox_eval_watch.py` (503) | ~1.4k lines | **Port with light cleanup** into `runner/` — small, single-purpose, in active use. |
| eval fixtures | `~/local-ai-evals/agentbox-eval/` | — | **Port sanitized subset** only (fixtures are @example.com-based; verify per file). |
| agentbox-control-plane (FastAPI + Astro UI) | `~/agentbox-control-plane` (not a git repo; deployed from tgz) | — | **Not in v1** (decided 2026-07-07): both API and UI stay private; evals repo is CLI-only for now. |
| Root loose scripts (both machines) | ~30 files each side | — | **Do not port.** `switch-live-model.sh` and `agentbox-current-status.sh` logic absorbed into the CLI. |
| Personal-admin skill kits (operator-specific) | various | — | **Private forever.** |

## Repo mapping

- `agentbox` (platform): router/, gateway/ (config examples), policies/ (yaml + md), services/ (compose + templates), cli/, docs/.
- `agentbox-evals`: harness/ (rewritten package), runner/ (ported), fixtures/ (sanitized), reports/ (ROCm benchmark etc.). CLI-only in v1; web layer stays private.

Build location: repos are built at `~/oss/agentbox` and `~/oss/agentbox-evals` on the laptop (decided 2026-07-07); Windows workspace holds planning docs only.

## Porting rules (restated)
1. Nothing enters by bulk copy; every file arrives via this table.
2. Fresh history, Apache-2.0 headers, env-var paths (`AGENTBOX_HOME`, `AGENTBOX_EVAL_ROOT`) from the first commit.
3. Ported code gets at least smoke-level tests in the same PR/commit that introduces it.
