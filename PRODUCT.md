# Agentbox

> **Draft, reconstructed from the repository — not yet confirmed by the household.**
> Every claim below is either cited to a file in this repo or marked `TBD`.
> Nothing here was invented to fill a heading. The one place the sources
> disagree with each other is flagged under Users; that needs a decision, not
> a guess. Written by the same agent that built much of the current system, so
> it should be read as a proposal to correct, not as a contract already agreed.

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

> ⚠️ **Sources conflict — needs your decision.** `README.md` and
> `docs/architecture.md` both say "for a **single operator**". The roadmap, the
> role system, invites, and per-identity bridges all describe a household of
> two. The code follows the household reading. Either the two docs are stale,
> or the household work outran the stated scope. **`TBD` until you say which.**

## Success

- A household member **can read every memory proposed about them and approve or
  reject each one** before it is stored.
- A household member **can ask the assistant a question about their own mail,
  calendar or tasks and get an answer** without an operator doing anything.
- An operator **can add a second person and get them signed in** without
  opening a terminal.
- An operator **can see which services are running, and which are not**, from
  a page rather than a shell.
- The assistant **cannot send mail, delete a file, unlock a door, or approve
  its own memory** — verified by tests, not by instruction.
- A person **can speak to the assistant and hear a reply without any audio
  leaving the box** (`docs/voice.md`).

## MVP

1. **Memory with consent** — the assistant proposes; a person approves,
   edits or rejects; nothing is stored unapproved.
2. **Personal data, read-mostly** — mail, calendar, files and tasks reachable
   through per-identity bridges that hold the credentials.
3. **The house** — lights and scenes controllable; locks, alarms, covers and
   cameras refused in code.
4. **Self-service access** — sign-in links, per-person accounts, and an
   invite flow that needs no terminal.
5. **Operations you can see** — service health, staleness, disk, and what the
   assistant is allowed to do.
6. **Local speech** — voice in and out, on-box only.

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

### Known gaps at time of writing

- **Sign-in link delivery has never been proven end-to-end.** Neither SMTP nor
  Discord has actually delivered a link; both paths are covered only by tests
  and by the no-channel fallback.
- **`docs/roadmap.md` is the live gap record** — it tracks what was asked for
  against what exists, and is more current than this file on feature status.
