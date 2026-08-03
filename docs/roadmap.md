# Roadmap

What was asked for, and what actually exists. `docs/architecture.md` records
what is built; this file records the gap. They were the same document for a
while, which is how six requested capabilities went missing — the architecture
doc has no way to describe something that isn't there yet.

Status is verified against the repo, not remembered.

## The tell

`policies/approval-policy.yaml` already tiers seven capabilities that no tool
implements:

| Capability | Tier | Tool |
|---|---|---|
| `read_repo_files` | allowed | none |
| `inspect_service_logs` | allowed | none |
| `run_validators_and_tests` | allowed | none |
| `draft_plans_issues_prs_skills` | allowed | none |
| `create_branches` | allowed | none |
| `generate_compose_proposals` | allowed | none |
| `scaffold_service_for_review` | allowed | none |

The policy is the older and more faithful record of intent. It describes an
assistant that reads the repo, runs the tests, drafts a PR and scaffolds a
service. None of that is reachable — `agentbox scaffold` is an operator
command. When these land, each needs a tool mapped in the same file, or it
fails closed and looks broken rather than absent.

## Outstanding

### 1. Self-reflection and memory reconciliation

Asked for repeatedly. Not started. No capability, no tool, no store.

The permission shape is already settled and correct: the assistant proposes,
the operator disposes (`propose_memory_for_review` is allowed,
`store_sensitive_memory` needs approval, `modify_upstream_agent_source` is
denied outright). Reflection does not need new authority — it needs an
**outcome signal**, which nothing currently records. The request log knows what
was called and how many bytes came back. It does not know whether the answer
was right, whether it was corrected, or whether the same question was asked
twice because the first attempt was useless.

So the ordering is: record outcomes → reflect over them → propose → operator
approves. Skipping to the reflection step gives a loop with nothing to read.

### 2. Building services on demand

Partly there. `agentbox scaffold` generates a complete bridge, validates it and
commits it to a `scaffold/*` branch without deploying or merging — but only the
operator can run it, and it only makes bridges. This is what the
containerisation was for, and the container story is the part that works.

Needs: a tool that reaches it, and a generator that isn't bridge-shaped.

### 3. Self-extension by pull request, never self-merge

`merge_own_pr` is `always_denied` and `modify_upstream_agent_source` is
`always_denied` — the guardrails exist. What is missing is the permitted half:
no tool creates a branch, opens a PR, or proposes a diff. The assistant cannot
currently propose a change to its own source at all, which is a stricter
posture than was asked for, arrived at by omission rather than decision.

### 4. Version control trail for generated services

Half done. Scaffold commits to a branch, so generated bridges have history.
Nothing else the assistant produces does — memory proposals, plans and future
generated services have no trail.

### 5. Home Assistant integration

Not started, and not previously recorded anywhere in this repo. A bridge is the
right shape: it holds the long-lived HA token, exposes narrow endpoints, and
the MCP in front of it maps tools onto capabilities. `agentbox scaffold
homeassistant` generates most of it.

Tiering to decide before building, not after: reading sensor state is
plausibly `allowed`; actuating anything physical (locks, heating) is not, and
"constrain rather than gate" argues for exposing a fixed set of safe entities
rather than a general `call_service` endpoint with an approval on it.

### 6. Harness dispatch — farming work out to other models

Sound, and less new than it looks. `router/agentbox_router.py` already routes
deterministically to a context worker (`:1235`) and a reasoning worker
(`:1236`). It serves the evaluator; the gateway config points only at `:1234`
and `:1240`, so the assistant has never used it.

Wiring it in generalises cloud escalation from a special case into one entry in
a dispatch table — try local, escalate on validation failure, gated by policy.

One constraint carries over from `docs/architecture.md`: FastContext-4B obeyed
an instruction embedded in tool data in 10 of 10 attempts. Dispatching to the
reasoner for verification is fine. Dispatching **externally-authored content**
to the extractor is the exact path that document warns against, and it is also
the most obvious use for it. Answer that before wiring the summarise case.

### 7. Cloud escalation

Explicitly deferred by the operator. Subsumed by item 6 if that lands first.

### 8. MRTR (`resultType: "input_required"`)

Blocked upstream. Hermes ships `mcp` 1.28.1, ceiling `2025-11-25`, with no
elicitation handling at all. Until then approval runs out of band through
`cli/agentbox-approvals`.

## Also outstanding, smaller

- **No end-to-end test.** All tests are unit-level against fixtures; nothing
  exercises MCP → bridge → upstream against the running system.
- **Backup is not scheduled.** `agentbox backup` works and is restore-verified;
  nothing runs it.
- **No traffic.** The request log holds only test calls, which blocks the
  `view=lean` A/B and every measurement that depends on real use.
