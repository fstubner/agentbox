# Agentbox

> **I designed this system. AI agents wrote most of the implementation,
> working to my architecture and my decisions.**
>
> That is why so much of this repository is scaffolding rather than features.
> Capabilities are absent instead of gated. Identity is bound to a session and
> never to a tool argument. Tests hold the invariants and have caught me
> breaking them. An acceptance pass runs in a separate session so the builder
> never signs off its own work, and a clean-room check follows the README on a
> machine that has never seen it. None of that would be interesting on a
> codebase one person typed by hand. It is what makes it reasonable to point
> agents at a system holding real credentials.
>
> **Every line under Success carries its real status.** *Met* means I verified
> it against a running system. *Partial* means it works for some of the people
> it names and not others. *Not yet* means the mechanism exists and nothing
> has used it in anger.
>
> I would rather the document be checkable than flattering. So the
> unflattering lines stay in, they carry the date I took the decision, and
> every claim points at a file in this repository or at a decision recorded
> in it. Nothing here was invented to fill a heading.

## Purpose

Give one household an assistant that runs on hardware they own, reaches their
real accounts through narrow levers rather than a shell, and remembers only
what a person has approved.

The bet is stated in `docs/the-different-axis.md`. This is **"one person, a
million times, for years"**. The same people, the same data sources, the same
few hundred recurring intents, rather than a million strangers once each.
Everything expensive is worth amortising across a lifetime of use.

## Users

Two people in one home, with different privileges.

- **Operator and admin** (`alex`). Runs the box, sees Operations, decides
  household memories, invites people.
- **Household member** (`sam`). Manages their own memories and accounts, and
  cannot reach Operations.

**`alex` and `sam` are the canonical example names**, in tests, docs and
config samples alike. Use them rather than inventing a placeholder. The suite
needs two *distinct* identities to assert that one person's memories, mail and
bridges are not another's. A third invented name tends to collide with one of
these and break a fixture in a way that looks like a real failure. Where a
test needs somebody who is deliberately neither of them, a newcomer or a
person with no way to sign in, `newcomer` and `taylor` are already used.

They are names rather than role words on purpose. `admin` and `member` are
already role constants in `cli/agentbox-portal`, so an identity called
`member` makes a test about one person read as a claim about the whole role.

`docs/roadmap.md` records the model as **"household plane plus private
planes"**. Tasks, shopping and joint scheduling are shared. Each person's
mail, calendar detail and personal memory are private. Two separate stacks
were explicitly rejected, because "a household assistant that cannot answer
'when are we both free' gives up most of its value".

**Decided 2026-08-18. A household of two.** `README.md`,
`docs/architecture.md` and `CONTRIBUTING.md` had described a "single
operator". Those were stale and now say household. The code already followed
this reading.

Distinct from that, and still true. A box with **no identities configured**
runs in a single-operator mode, with one shared token, no identity, and every
memory implicitly the one operator's. That is a supported deployment with
tests behind it (`services/compose/agentbox-mcp/app/server.py`,
`test_memory_scopes.py`), not stale framing. The product is designed around
the household. The fallback exists for a box that has not been set up as one.

## Success

**Decided 2026-08-19. These are targets, not a status report.** Success says
what the product is for. Each line then says honestly how far off it is. A
claim may sit here unmet for as long as it is still the goal. What it may not
do is read as achieved when it is not. Status measured 2026-08-19.

- **Partial, the operator only.** A household member can read every memory
  proposed about them and approve or reject each one before it is stored.
  *Works today for `alex`. `sam` has no way to sign in, so the proposals
  waiting for them cannot be reached. See the delivery gap below.*
- **Not yet, built but never run.** A household member can ask the assistant
  a question about their own mail, calendar or tasks and get an answer without
  an operator doing anything. *The machinery is automatic.
  `provision_google_bridge()` stands up a bridge holding only that person's
  credential, writes per-identity routing, and recreates the gateway so the
  routing takes effect. It has never run for `sam` because no invite has ever
  been created. The spool is empty, and they exist only because their name was
  added to the identity list by hand.*
- **Complete, never run.** An operator can add a second person and get them
  **fully onboarded**, signed in, with their own accounts and data plane,
  without opening a terminal. *Decided 2026-08-19. Onboarding is the unit,
  not sign-in. Every step is now a web action. Create the invite on
  Operations, they fill in a form, an admin approves it, and a path unit does
  the privileged half. No new privilege moved onto the page. The approval is a
  file the worker reads, not an action the portal takes.*

  *That is weaker than a privilege boundary, and
  `cli/agentbox-onboarding.service` says so in its own words.
  `agentbox-portal.service` runs as the operator, who is in the `docker`
  group, so the socket is already reachable to that process. What stops it is
  the absence of a code path, not a wall. Real for the code and aspirational
  for the process until the portal gets its own user.*

  *Not counted as met, because no part of it has carried a real person. The
  flow was driven end to end against the running portal with a test invite.
  The one step deliberately not exercised is approval, which provisions real
  accounts. It also still depends on gap 1 below. With no delivery channel, an
  admin has to hand the link over rather than send it.*
- **Met.** An operator can see which services are running, and which are not,
  from a page rather than a shell. *Verified live 2026-08-19 during an
  evaluation stand-down. Four up, five down, reported accurately.*
- **Met.** The assistant cannot send mail, delete a file, unlock a door, or
  approve its own memory. Verified by tests, not by instruction.
- **Partial, Discord only.** A person can speak to the assistant and hear a
  reply without any audio leaving the box (`docs/voice.md`). *Round-tripped
  2026-08-03 through Piper and faster-whisper. Reaches only the Discord path.
  The push-to-talk TUI needs `sounddevice`, which is not installed, and
  house-wide microphones are not built. Remains a full MVP item, see committed
  work below.*

## MVP

1. **Memory with consent.** The assistant proposes. A person approves, edits
   or rejects. Nothing is stored unapproved. *(Working.)*
2. **Personal data, read-mostly.** Mail, calendar, files and tasks reachable
   through per-identity bridges that hold the credentials. *(Working for one
   identity of two.)*
3. **The house.** Lights and scenes controllable. Locks, alarms, covers and
   cameras refused in code. *(Working.)*
4. **Self-service access.** Sign-in links, per-person accounts, and an invite
   flow that needs no terminal. *(Complete as of 2026-08-19. No real person
   has been through it.)*
5. **Operations you can see.** Service health, staleness, disk, and what the
   assistant is allowed to do. *(Working.)*
6. **Local speech.** Voice in and out, on-box only. *(Discord path working.
   The rest is committed work.)*

## Constraints

- **One machine, 16 GB of unified memory.** `agentbox-host`, `vgm_gb = 16`
  in the evaluator config. A benchmark and the production model cannot both be
  resident, which is why evaluations stop the stack.
- **The assistant never holds a raw upstream credential.** OAuth tokens live
  in bridge containers. `op://` references resolve at deploy time
  (`README.md`).
- **Levers, not shell.** MCP tools with explicit contracts, never raw terminal
  access (`README.md`). See also the 2026-08-05 correction in
  `docs/architecture.md`, where this was once claimed and was not true.
- **Identity is bound to the session, never passed per call.** The roadmap
  calls this "the design constraint that matters more than any of the above".
  If the assistant could choose which account to act as, an instruction
  embedded in an email could choose too.
- **Constrain rather than gate.** A constraint holds when the model is
  compromised. An approval only helps if a human reads carefully.
- **Nothing spoken leaves the box.** A deliberate trade of speech quality for
  locality (`docs/voice.md`).
- **Granting Google consent away from the box needs a real hostname.** Google
  refuses a private IP or a `.local` name as an OAuth redirect URI and accepts
  only HTTPS on a resolvable name, or loopback. Imposed by Google, not chosen
  here, and it applies to no other part of the system.

  It is a limit on one step rather than a barrier to adoption, and the code is
  arranged so it stays that way. `AGENTBOX_PORTAL_URL` is where people reach
  the portal and must resolve from a phone. `AGENTBOX_OAUTH_REDIRECT_BASE` is
  what Google is told, and it defaults to loopback. A household with no
  infrastructure at all gets onboarding, tasks, memory and the house over
  plain LAN HTTP, and connects Google in a browser on the box. For people who
  live together that is sitting down at it once. Pointing both variables at
  one `https://` name lifts the limit, and is the only thing a hostname buys.
  `docs/runbook.md` has concrete steps for getting one without buying a domain
  or exposing the box.

  These were a single setting until 2026-08-19, which made the two
  requirements mutually exclusive. A value a phone could reach broke consent,
  and loopback made every delivered link point at the recipient's own device.

## Anti-goals

- **Not a general agent.** No shell, no arbitrary code execution, no
  self-merge, no self-deploy.
- **Not multi-tenant.** One household on one box, not a service for
  strangers.
- **Not cloud-first.** Remote models are a deliberate exception, not the
  default path.
- **No tool for irreversible acts.** Sending, deleting, unlocking and sharing
  are absent rather than gated. Absence survives a compromised model.

## Acceptance

- `python -m pytest` green. `cli/agentbox validate` and `cli/agentbox doctor`
  report zero failures.
- Every capability in MVP is exercised by a test that fails when the
  capability is removed.
- Refusals are recorded as refusals, not errors. A working guardrail must not
  read as a broken tool in the outcome journal.
- Acceptance is run by someone other than whoever built the change.

## Committed work

Named here because a Success line above depends on it. This is the gap between
target and status.

1. **Configure a delivery channel.** SMTP or one Discord pairing. Three
   Success lines are blocked behind it, and none of them can be proven until a
   link actually arrives. Nothing else here matters as much.
2. **Run an invite for `sam`.** Everything this needs is already built.
   `agentbox invite create sam`, they fill in the form including the Google
   consent step, then `agentbox invite complete` provisions their Vikunja
   account and their own Google bridge. Skipping the consent step is handled
   and warned about, but leaves them sharing the household bridge, which is
   wrong for mail. This is the same root cause as gap 1, not a second one.
   Nobody has ever been onboarded through the flow.
3. **Voice beyond Discord**, kept as MVP item 6, decided 2026-08-19. Install
   `sounddevice` for the push-to-talk TUI path, and supply the thinking half
   of house-wide speech through a Home Assistant webhook rather than
   reimplementing an audio pipeline (`docs/voice.md`).

### Known gaps at time of writing

- **Sign-in link delivery has never been proven end-to-end.** Neither SMTP nor
  Discord has actually delivered a link. Both paths are covered only by tests
  and by the no-channel fallback.
- **A planned stand-down reads as a crash.** Both gateway units exit 1 when
  keepalive SIGTERMs them for an evaluation, so systemd marks them `failed`.
  Operations correctly reports the services as down, measuring state before
  evaluation state, deliberately. It does not say the evaluation is why.
- **`docs/roadmap.md` tracks what was asked for against what exists.** It is
  broader than this file on features nobody has started. It is not
  automatically fresher. On 2026-08-20 an acceptance pass found it still
  describing onboarding as a terminal flow, days after that stopped being
  true, while this file said to trust it over itself. Both are
  hand-maintained, and either can be the stale one.
