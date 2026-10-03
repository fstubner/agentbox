# Agentbox

I designed Agentbox. AI agents wrote most of the code, working from my
architecture and decisions.

Because agents write the code, the safety rules are enforced in code and
checked by tests. Dangerous actions have no tool at all. Who the assistant acts
for comes from the session and never from a tool argument. Acceptance runs in a
separate session from the build, and a CI job follows the README on a fresh
machine every week.

Each line under Success has a status. **Met** means I checked it on a running
system. **Partial** means it works for some of the people it names. **Not
yet** means it is built but nobody has used it for real.

## Purpose

Give one household an assistant that runs on hardware they own, reaches their
real accounts through narrow tools rather than a shell, and remembers only what
a person has approved.

## Users

Everyone in one household. Each person has one of two roles, and there can be
more than one of each.

- **Admin.** Runs the box, sees Operations, decides household memories and
  invites people.
- **Member.** Manages their own memories and accounts, and cannot reach
  Operations.

Some things are shared and some are private. Tasks, shopping and joint
scheduling are shared. Each person's mail, calendar detail and personal
memories are private. I decided against a separate stack per person, because
then it could not answer "when are we both free".

A box with no identities configured runs in single-operator mode, with one
shared token and every memory belonging to that one operator. It is supported
and tested (`services/compose/agentbox-mcp/app/server.py`,
`tests/test_memory_scopes.py`), for a box that has not been set up for a
household.

## Success

Status last checked 2026-08-19.

- **Partial, admin only.** A household member can read every memory proposed
  about them and approve or reject each one before it is stored. This works
  for the admin. No other member has signed in yet, so proposals waiting for
  them cannot be reached.
- **Not yet, built but never run.** A household member can ask about their own
  mail, calendar or tasks and get an answer without an admin doing anything.
  `provision_google_bridge()` creates a bridge holding only that person's
  credential, writes the routing for their identity and restarts the gateway
  so it takes effect. It has never run, because no invite has been created.
- **Not yet, built but never run.** An admin can add a person and get them
  fully onboarded, signed in and with their own accounts, without opening a
  terminal. Every step is a web action. The admin creates an invite on
  Operations, the person fills in a form, the admin approves it, and a systemd
  path unit does the privileged part. The approval is a file the worker reads,
  not an action the portal takes, so no new privilege moved onto the page.

  That is weaker than a real privilege boundary, and
  `cli/agentbox-onboarding.service` says so. The portal runs as the operator,
  who is in the `docker` group, so the Docker socket is reachable from that
  process. What stops it is that no code path uses it. That will stay true
  until the portal has its own user.

  I drove the flow end to end against the running portal with a test invite,
  except for approval, which provisions real accounts. It also needs a delivery
  channel (see Committed work). Without one, the admin hands the link over in
  person.
- **Met.** An admin can see which services are running from a page rather than
  a shell. I checked this during an evaluation with four services up and five
  down, and it reported them correctly.
- **Met.** The assistant cannot send mail, delete a file, unlock a door or
  approve its own memory. Tests check this.
- **Partial, Discord only.** A person can speak to the assistant and hear a
  reply without any audio leaving the box (`docs/voice.md`). This works through
  Discord with Piper and faster-whisper. The services for microphones around the
  house are built but not deployed, and the route from Home Assistant back to
  the assistant is not built.

## MVP

1. **Memory with consent.** The assistant proposes, and a person approves,
   edits or rejects. Nothing is stored without approval. *Working.*
2. **Personal data, mostly read-only.** Mail, calendar, files and tasks through
   per-person bridges that hold the credentials. *Working for the admin.*
3. **The house.** Lights and scenes can be controlled. Locks, alarms, covers
   and cameras are refused in code. *Working.*
4. **Self-service access.** Sign-in links, per-person accounts, and an invite
   flow with no terminal. *Built. Nobody has been through it yet.*
5. **Operations you can see.** Service health, staleness, disk, and what the
   assistant is allowed to do. *Working.*
6. **Local speech.** Voice in and out, on the box only. *Working through
   Discord. The rest is committed work.*

## Constraints

- **One machine.** Everything, including the model, runs on the household's
  own box.
- **The assistant never holds an upstream credential.** OAuth tokens live in
  bridge containers, and secret references resolve at deploy time.
- **No shell.** MCP tools with explicit contracts and no terminal
  access (`docs/architecture.md`).
- **Identity is bound to the session, never passed per call.** If the
  assistant could choose which account to act as, an instruction hidden in an
  email could choose too.
- **Constrain rather than gate.** A constraint holds when the model is
  compromised. An approval only helps if a person reads it carefully.
- **Nothing spoken leaves the box.** I traded speech quality for this on
  purpose (`docs/voice.md`).
- **Google consent away from the box needs a real hostname.** Google rejects a
  private IP or a `.local` name as an OAuth redirect and only accepts HTTPS on
  a public name, or loopback. Google imposes this, and it affects no other part
  of the system.

  Two settings keep it to that one step. `AGENTBOX_PORTAL_URL` is where people
  reach the portal, and has to resolve from a phone.
  `AGENTBOX_OAUTH_REDIRECT_BASE` is what Google is told, and defaults to
  loopback. A household with no extra infrastructure gets onboarding, tasks,
  memory and the house over plain LAN HTTP, and connects Google once in a
  browser on the box. Pointing both at one `https://` name removes the limit.
  `docs/runbook.md` explains how to get one without buying a domain or exposing
  the box.

## Anti-goals

- **Not a general agent.** No shell, no arbitrary code execution, no merging
  or deploying its own changes.
- **Not multi-tenant.** One household on one box, not a service for strangers.
- **Not cloud-first.** Remote models are an exception, not the default.
- **No tool for irreversible actions.** Sending, deleting, unlocking and
  sharing have no tool, because a compromised model cannot call a tool that
  does not exist.

## Acceptance

- `python -m pytest` passes. `cli/agentbox validate` and `cli/agentbox doctor`
  report no failures.
- Every MVP capability has a test that fails when the capability is removed.
- Refusals are logged as refusals, not errors, so a working guardrail never
  looks like a broken tool in the outcome journal.
- Someone other than the person who built a change runs its acceptance.

## Committed work

The gap between the targets above and where things are.

1. **Set up a delivery channel**, either SMTP or a Discord pairing. Three
   Success lines are blocked on it, and none can be checked until a link
   actually arrives.
2. **Onboard a second person through the invite flow.** Everything it needs is
   built. `agentbox invite create <name>`, they fill in the form including
   Google consent, and `agentbox invite complete` sets up their Vikunja account
   and their own Google bridge. If they skip consent they share the household
   bridge, which is handled and warned about but wrong for mail.
3. **Voice beyond Discord.** Deploy the Wyoming services, set up a satellite,
   and build the webhook route from Home Assistant back to the assistant
   (`docs/voice.md`).

### Known gaps

- **Sign-in link delivery has never been tested end to end.** Neither SMTP nor
  Discord has delivered a real link. Both are covered only by tests and by the
  fallback when no channel is set.
- **A planned stand-down looks like a crash.** Both gateway units exit 1 when
  they are stopped for an evaluation, so systemd marks them `failed`.
  Operations correctly reports them as down but does not say an evaluation is
  the reason.
- **`docs/roadmap.md` and this file can disagree.** The roadmap tracks what was
  asked for against what exists and covers features nobody has started. Both
  are maintained by hand, and either can be the stale one.
