# Agentbox

> **Reconstructed from the repository. Users answered 2026-08-18; Success and
> the MVP cut were reviewed and framed by the household 2026-08-19.**
> Every claim below is cited to a file in this repo or to a decision recorded
> here. Nothing was invented to fill a heading. Written by the same agent that
> built much of the system it describes, so the status markers in Success are
> measurements to re-check, not a standing guarantee.

## Purpose

Give one household an assistant that runs on hardware they own, reaches their
real accounts through narrow levers rather than a shell, and remembers only
what a person has approved.

Its bet is stated in `docs/the-different-axis.md`: this is **"one person, a
million times, for years"** — the same people, the same data sources, the same
few hundred recurring intents — rather than a million strangers once each.
Everything expensive is worth amortising across a lifetime of use.

## Users

Two people in one home, with different privileges:

- **Operator/admin** (`alex`) — runs the box, sees Operations, decides
  household memories, invites people.
- **Household member** (`sam`) — manages their own memories and accounts, and
  cannot reach Operations.

`docs/roadmap.md` records the model as **"household plane plus private
planes"**: tasks, shopping and joint scheduling are shared; each person's mail,
calendar detail and personal memory are private. Two separate stacks were
explicitly rejected, because "a household assistant that cannot answer 'when
are we both free' gives up most of its value".

**Decided 2026-08-18: a household of two.** `README.md`,
`docs/architecture.md` and `CONTRIBUTING.md` had described a "single operator";
those were stale and now say household. The code already followed this reading.

Distinct from that, and still true: a box with **no identities configured**
runs in a single-operator mode — one shared token, no identity, every memory
implicitly the one operator's. That is a supported deployment with tests behind
it (`services/compose/agentbox-mcp/app/server.py`, `test_memory_scopes.py`),
not stale framing. The product is designed around the household; the fallback
exists for a box that has not been set up as one.

## Success

**Decided 2026-08-19: these are targets, not a status report.** Success says
what the product is for; each line then says honestly how far off it is. A
claim may sit here unmet for as long as it is still the goal — what it may not
do is read as achieved when it is not. Status measured 2026-08-19.

- **Partial — the operator only.** A household member can read every memory
  proposed about them and approve or reject each one before it is stored.
  *Works today for `alex`. `sam` has no way to sign in, so the proposals
  waiting for them cannot be reached. See the delivery gap below.*
- **Not yet — built but never run.** A household member can ask the assistant
  a question about their own mail, calendar or tasks and get an answer without
  an operator doing anything. *The machinery is automatic:
  `provision_google_bridge()` stands up a bridge holding only that person's
  credential, writes per-identity routing, and recreates the gateway so the
  routing takes effect. It has never run for `sam` because no invite has ever
  been created — the spool is empty, and she exists only because her name was
  added to the identity list by hand.*
- **Not yet.** An operator can add a second person and get them **fully
  onboarded** — signed in, with their own accounts and data plane — without
  opening a terminal. *Decided 2026-08-19: onboarding is the unit, not
  sign-in. The portal invite does the first half already. The second half,
  `agentbox invite complete`, is a terminal command today, and that is not a
  gap in the flow — it is where `cli/agentbox-invite` deliberately put the
  privilege, arguing that a LAN-reachable page holding the docker socket is
  the worst thing that could run on this box. Closing this means moving where
  completion is triggered from without moving where its privilege lives; the
  argument against a privileged web page stands and is not being overruled.*
- **Met.** An operator can see which services are running, and which are not,
  from a page rather than a shell. *Verified live 2026-08-19 during an
  evaluation stand-down: four up, five down, reported accurately.*
- **Met.** The assistant cannot send mail, delete a file, unlock a door, or
  approve its own memory — verified by tests, not by instruction.
- **Partial — Discord only.** A person can speak to the assistant and hear a
  reply without any audio leaving the box (`docs/voice.md`). *Round-tripped
  2026-08-03 through Piper and faster-whisper. Reaches only the Discord path:
  the push-to-talk TUI needs `sounddevice`, which is not installed, and
  house-wide microphones are not built. Remains a full MVP item — see
  committed work below.*

## MVP

1. **Memory with consent** — the assistant proposes; a person approves,
   edits or rejects; nothing is stored unapproved. *(Working.)*
2. **Personal data, read-mostly** — mail, calendar, files and tasks reachable
   through per-identity bridges that hold the credentials. *(Working for one
   identity of two.)*
3. **The house** — lights and scenes controllable; locks, alarms, covers and
   cameras refused in code. *(Working.)*
4. **Self-service access** — sign-in links, per-person accounts, and an
   invite flow that needs no terminal. *(Built, never completed end to end.)*
5. **Operations you can see** — service health, staleness, disk, and what the
   assistant is allowed to do. *(Working.)*
6. **Local speech** — voice in and out, on-box only. *(Discord path working;
   the rest is committed work.)*

## Constraints

- **One machine, 16 GB of unified memory.** `agentbox-host`,
  `vgm_gb = 16` (evaluator config). A benchmark and the production model
  cannot both be resident, which is why evaluations stop the stack.
- **The assistant never holds a raw upstream credential.** OAuth tokens live in
  bridge containers; `op://` references resolve at deploy time (`README.md`).
- **Levers, not shell.** MCP tools with explicit contracts, never raw terminal
  access (`README.md`; and see the 2026-08-05 correction in
  `docs/architecture.md`, where this was once claimed and was not true).
- **Identity is bound to the session, never passed per call.** The roadmap
  calls this "the design constraint that matters more than any of the above":
  if the assistant could choose which account to act as, an instruction
  embedded in an email could choose too.
- **Constrain rather than gate.** A constraint holds when the model is
  compromised; an approval only helps if a human reads carefully.
- **Nothing spoken leaves the box** — a deliberate trade of speech quality for
  locality (`docs/voice.md`).

## Anti-goals

- **Not a general agent.** No shell, no arbitrary code execution, no
  self-merge, no self-deploy.
- **Not multi-tenant.** One household on one box; not a service for strangers.
- **Not cloud-first.** Remote models are a deliberate exception, not the
  default path.
- **No tool for irreversible acts.** Sending, deleting, unlocking and sharing
  are absent rather than gated — absence survives a compromised model.

## Acceptance

- `python -m pytest` green; `cli/agentbox validate` and `cli/agentbox doctor`
  report zero failures.
- Every capability in MVP is exercised by a test that fails when the capability
  is removed.
- Refusals are recorded as refusals, not errors — a working guardrail must not
  read as a broken tool in the outcome journal.
- Acceptance is run by someone other than whoever built the change.

## Committed work

Named here because a Success line above depends on it. This is the gap between
target and status, not a wishlist.

1. **Configure a delivery channel** — SMTP or one Discord pairing. Three
   Success lines are blocked behind it, and none of them can be proven until a
   link actually arrives. Nothing else here matters as much.
2. **Run an invite for `sam`** — not a thing to build; a thing to run.
   `agentbox invite create sam`, she fills in the form including the Google
   consent step, then `agentbox invite complete` provisions her Vikunja
   account and her own Google bridge. Skipping the consent step is handled and
   warned about, but leaves her sharing the shared bridge, which is wrong for
   mail. This is the same root cause as gap 1, not a second one: nobody has
   ever been onboarded through the flow.
3. **Onboarding without a terminal** (decided 2026-08-19) — the operator's
   half of `agentbox invite complete` needs a trigger that is not a shell,
   while the privilege it needs stays off any LAN-reachable page. The
   constraint to preserve is the one `cli/agentbox-invite` already states: the
   collecting surface has no docker socket, no 1Password token, and no bridge
   tokens. Note that `ops:invite` is already withheld from agent-origin
   sessions, and must stay withheld from whatever replaces the shell.
4. **Voice beyond Discord** (kept as MVP item 6, decided 2026-08-19) —
   install `sounddevice` for the push-to-talk TUI path, and supply the
   thinking half of house-wide speech through a Home Assistant webhook rather
   than reimplementing an audio pipeline (`docs/voice.md`).

### Known gaps at time of writing

- **Sign-in link delivery has never been proven end-to-end.** Neither SMTP nor
  Discord has actually delivered a link; both paths are covered only by tests
  and by the no-channel fallback.
- **A planned stand-down reads as a crash.** Both gateway units exit 1 when
  keepalive SIGTERMs them for an evaluation, so systemd marks them `failed`.
  Operations correctly reports the services as down — measured state before
  evaluation state, deliberately — but does not say the evaluation is why.
- **`docs/roadmap.md` is the live gap record** — it tracks what was asked for
  against what exists, and is more current than this file on feature status.
