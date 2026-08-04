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

### 1. Self-reflection and memory reconciliation — **built**

Verified end to end on 2026-08-03: the assistant read its own history, drew a
correct conclusion from it, and proposed a durable lesson the operator can
approve.

Four parts:

- **The outcome journal** (`services/templates/mcp/outcome_log.py`). One record
  per tool call at the MCP layer, where tool names and policy decisions exist.
  The bridge request log could not do this job: it sits below the MCP, so it
  sees `GET /v1/tasks` and never learns the tool was `list_tasks` — and a call
  refused by the gate never reaches a bridge at all, so the most interesting
  events were invisible in it.
- **`review_own_activity`** — the tool, gated by `inspect_service_logs`, which
  was already `allowed` and had no tool behind it. Returns counts and rates,
  never journal lines.
- **`skills/self-reflection/SKILL.md`** — what to look for and what to do about
  it, installed into the gateway's skills directory.
- **A daily cron job** (09:00) in the gateway's own scheduler, with the skill
  attached and results delivered to Discord. Daily needs the deduplication step
  above; verified by running it twice with a proposal already queued, and the
  second run correctly proposed nothing.

Operator decisions are recorded too, including denials — which used to vanish,
since an approval went through `agentbox grant` and saying no just deleted a
file. A journal that remembers every yes and no no would make any tier argument
read from it wrong in one direction.

**What the first real run taught us.** It proposed two lessons. One was right.
The other said "do not retry archive_gmail" — wrong, because `archive_gmail` is
`approval_required` and available with a grant, not `always_denied`. The
summary gave it no way to tell "ask for this" from "never do this", and the
safe-looking reading is the one that silently discards a capability. Each tool
now carries its tier and a note; on the re-run it proposed only the correct
lesson. Worth recording because it is the failure mode to watch for: reflection
that quietly narrows what the assistant will attempt.

Still to do: reflection reads outcomes but nothing yet records whether an
*answer* was right — only whether a call succeeded. Operator corrections in
conversation are still invisible.

### 2-4. Building services, self-extension by PR, and a version trail — **built**

One system, because all three reduce to "the assistant writes code, a human
decides whether it ships". `services/compose/builder-{bridge,mcp}`, verified
end to end on 2026-08-03.

Git is the trail: every proposal is a branch with a commit attributed to
`agentbox-assistant`, so nothing it produces is untracked and reviewing it is
`git diff`. Five tools — read the repo, run the validators, propose a change,
list proposals — and `cli/agentbox proposals list|show|merge` on the operator
side.

**Why this is the most dangerous service here.** Every other bridge holds a
credential to somebody else's API. This one can write the source of the system
that constrains it. If it could propose an edit to `approval-policy.yaml`, it
could propose granting itself everything and then need one distracted approval
to get it — a failure of review, not of policy.

So the containment is not "the operator will read carefully":

- **Protected paths are refused outright** — the policy, both gates, the
  operator CLI, CI, and the builder's own source. A path check, not a tier
  lookup, because a tier cannot express "may edit any file except the ones that
  govern it". Path traversal is normalised before the check, and a protected
  file anywhere in a proposal rejects the whole thing before any git runs.
- **It never pushes.** Branches stay in its own clone and the operator fetches
  them, so the container needs no write access to the real repository. The
  operator's repo is mounted read-only, which makes "pull, never push" a
  property of the filesystem rather than a promise in the code.
- **No merge, no deploy.** The permitted git subcommands are asserted in tests.
- **`run_repo_checks` runs a fixed command**, not a supplied one — `allowed` is
  only safe for that capability while what runs cannot be chosen.

Still to do: it writes whole files rather than patches, so editing a large file
means round-tripping the whole thing. A diff-based proposal would be better and
is harder to validate safely.

### 5. Voice — **partly built**

Local speech in and out works and is verified: faster-whisper for STT, Piper for
TTS, libopus and ffmpeg for Discord audio. Nothing spoken leaves the box. See
`docs/voice.md`.

**Still to do: microphones and speakers around the house** — Raspberry Pis or
ESP32 boards acting as satellites.

Do not build an audio pipeline for this. The hard parts — wake-word detection,
always-on streaming, echo cancellation, device discovery, and firmware for
cheap microcontrollers — are exactly what Home Assistant's Assist stack already
does, with ESPHome firmware for ESP32-S3 voice boards and Wyoming satellite for
a Pi. Reimplementing it here would be months of work to arrive somewhere worse.

The split that makes sense: **Home Assistant owns the audio, Agentbox owns the
thinking.** A satellite wakes, streams to HA, HA transcribes, HA posts the text
to a Hermes webhook, and the reply comes back to be spoken on that satellite.

`hermes webhook subscribe` already provides the entry point, with HMAC secrets,
prompt templates, skill attachment and per-target delivery — so the Agentbox
side is a route and a policy decision rather than a new service.

Two things to settle before building:

- **Which room heard it.** The satellite id has to survive the round trip or
  every reply comes back everywhere at once.
- **What a voice request is allowed to do.** A spoken request has no operator
  reading carefully before it lands, so the approval loop is a worse fit than
  it is in Discord. Voice probably wants a narrower capability set rather than
  the same one with approvals in front of it.

This merges with the next item; doing them separately would mean building the
Home Assistant connection twice.

### 6. Home Assistant — **built and live**

`services/compose/homeassistant-{bridge,mcp}`. Five tools: two read, three act,
and none of them is a general `call_service`.

The tiering was decided before building, as the previous version of this entry
insisted:

| | |
|---|---|
| `home_read_state` | allowed — knowing the kitchen is 19°C is not worth gating, and a sensor allowlist would need editing every time a battery is changed |
| `home_control_comfort` | allowed — lights, scenes, switches; the *allowlist* is the constraint, not an approval |
| `home_control_climate` | approval_required — costs money, and a household may be asleep |
| `home_control_security` | **always_denied**, and no tool maps to it at all |

**Why there is no `call_service`.** HA's REST API is one endpoint from total
control: `POST /api/services/<domain>/<service>` unlocks a door as readily as
it turns on a lamp. Exposing it behind an approval would be the wrong shape —
an approval asked every time someone wants a light is granted unread within a
week, and by then it grants nothing. Constrain rather than gate.

Two independent refusals, and the order matters: an entity in a security domain
is refused **before** the allowlist is consulted and regardless of what it
says, because the allowlist is the thing most likely to be wrong.
`lock.front_door` and `light.front_door` differ by two characters and a human
edits that list. Climate is bounded 5–30 °C whatever the grant says — an
extreme is a burst pipe or a heat risk to someone asleep, and that should not
depend on the model being sensible.

Default `HA_CONTROLLABLE_ENTITIES` is empty: it can read the house and change
nothing until the operator says otherwise.

**Update 2026-08-04: deployed and live.** The instance runs on this box
(`services/compose/homeassistant`, host networking for mDNS/SSDP discovery,
declared `agentbox.exposure: lan`), the operator has onboarded, tokens are in
1Password under `op://Agentbox/homeassistant/`, and the bridge/MCP pair is up —
verified end to end with 19 discovered entities. All three lists
(`HA_CONTROLLABLE_ENTITIES`, `HA_VIEWABLE_CAMERAS`, `HA_PRIVATE_SCREENS`)
remain empty: it reads the house and changes nothing until the operator fills
them. The original note below stands as the record of why deployment waited. Deploying a
service whose upstream does not exist would leave `doctor` permanently red,
which is how a check stops being read; the readiness entries report "refused;
is it deployed?" as a warning instead. 26 tests cover the refusals against the
real module. The HA REST API is stable, but nothing has been exercised against
a live instance — that is the one thing outstanding.

### 7. Two accounts — household shared, personal private

Decided 2026-08-03. **Order: after Home Assistant, before harness dispatch.**

Today the system has no notion of who is asking — zero references to a
requester anywhere in the MCP or bridge path, one `GOOGLE_REFRESH_TOKEN`, one
flat memory store, and grants recorded as `{tool, expires_at, single_use}`. A
second person does not get an error; they get **the first person's data,
presented as their own**. A wrong answer that looks right is the worst
available failure.

The split already exists in the services, which is why this is tractable:
Vikunja is genuinely shared and works for two people today; Google is genuinely
personal and is the broken part; memory is ambiguous and currently wrong.

**Decisions taken:**

- **Household plane plus private planes.** Shared: tasks, shopping, joint
  scheduling. Private: each person's mail, personal memory, calendar detail.
  Two fully separate stacks were rejected — a household assistant that cannot
  answer "when are we both free" gives up most of its value and doubles the
  surface to run and patch.
- **Approvals are own-account by default, with an explicit allowlist** for
  named cross-account actions. Neither person can approve arbitrary actions on
  the other's data; specific exceptions are configured, not assumed.

**The design constraint that matters more than any of the above:** identity
must be bound to the *session*, not passed per call. If the assistant chooses
which account to act as, then an instruction embedded in an email can choose
too — and one compromised context reaches both accounts instead of one.
Injection blast radius doubling is the real cost of multi-user, and
session-scoped credential selection is what prevents it. The bridge must
receive an identity assertion it cannot be argued out of, derived from the
gateway session rather than from tool arguments.

Unknown identity fails closed: refuse, never guess.

This touches every layer — bridges, MCPs, policy, grants, approvals, memory —
and is larger than the builder. A half-implementation is worse than none here,
because partial isolation reads as isolation.

### 8. Harness dispatch — farming work out to other models

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

### 9. Cloud escalation

Explicitly deferred by the operator. Subsumed by item 6 if that lands first.

### 10. MRTR (`resultType: "input_required"`)

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

### 11. Cross-service rules — a designed grammar, not n8n

Decided 2026-08-04, and the reasoning is the platform's own principle stated
sharply by the operator: **tools are designed surfaces, not wrappers.** We
control the grammar of what the assistant can do; expressiveness is a budget
spent only on what we are willing to verify.

n8n was evaluated seriously. `NODES_INCLUDE` is real — verified in its loader
source, a load-time allowlist, with `executeCommand` excluded by default — so a
locked-down instance on an `internal: true` network holding only bridge tokens
was buildable. Rejected anyway, because it is the subtractive shape: renting a
general-purpose surface and auditing the subtraction on every upgrade. The
additive shape is a rules primitive we own:

    rule = when <bridge-observed event | schedule>
           if   <literal predicates — no templates, no code>
           do   <allowlisted calls to tools the assistant already has>

Deliberately not Turing-complete, so statically checkable — the template-free
automation validator generalised platform-wide. The model is involved only at
authoring time; evaluation is deterministic code. Every `do` is an ordinary
bridge call, so policy gates, grants and the outcome journal apply with no new
machinery, and the daily reflection sees what rules did.

Already covered elsewhere: time triggers (Hermes cron), house triggers (HA
automations). The new work is bridge-observed events plus a small evaluator,
and rule proposals should flow through the builder's git trail like everything
else the assistant authors.

Ordering: after the MCP consolidation (the evaluator belongs behind the same
gateway) and after two accounts (a rule fires as *someone*; building it
single-user first would mean retrofitting identity into stored rules).
