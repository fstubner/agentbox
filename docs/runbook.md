# Runbook

How to run Agentbox day to day. Paths use deployment variables.
`$HERMES_HOME` is the gateway profile, `$GATEWAY_USER_HOME` the gateway user's
home, `$GATEWAY_VENV` its virtualenv, `$AGENTBOX_ENV_DIR` the operator's env
files (default `~/.config/agentbox`) and `$AGENTBOX_REPO` this checkout. CI
refuses literal home directories so the repository stays portable.

## Checking health

```
cli/agentbox doctor
cli/agentbox status
```

`doctor` checks that git, curl and docker are present, that the main model and
agentbox-mcp answer, disk usage, image age, deploy freshness, policy drift,
bridge readiness, the assistant's containment, each person's Google
credential, the portal's redirect URI and email delivery, which tools the
gateway can see, and finally runs `validate`. `status` prints one line per
endpoint.

### Smoke test

```
cli/agentbox smoke
```

`doctor` answers "is everything up". `smoke` answers "does anything work". It
drives real workflows through agentbox-mcp, the same path the assistant uses.
Its first run found that every tool published a list of required arguments
that nothing enforced, so a missing argument came back as
`internal error: KeyError`.

It is safe to run against live accounts.

- **Vikunja** holds test data, so it creates a task and completes it.
- **Google** holds real data, so it only reads. Nothing is drafted, labelled or
  archived.
- **Memory** proposals are inert, so it proposes one and rejects it as the
  operator. That also exercises the half of the review gate the assistant
  cannot reach.
- The **policy gate** check passes when the call is refused. A success there
  is the bug.

Run it after every deploy. Everything in `tests/` runs against fixtures, so
this is the only check that would notice a service that starts, passes
readiness and then refuses every call.

### Liveness and readiness

Each bridge has two unauthenticated probes.

- `/health` says whether the process answers. It never touches the upstream,
  and the container healthcheck must keep using it. If liveness depended on
  the upstream, one outage in a backing service would restart-loop every
  bridge in front of it.
- `/ready` says whether the bridge can do its job. It probes the backing
  service and returns 503 with the reason when it cannot.

Without `/ready`, a bridge that is up but cannot reach what it fronts looks
healthy, and `doctor` stays green while nothing works.

Bridges publish no host ports, except the memory bridge's review port, so they
cannot be probed from the host. That is part of keeping their tokens useless
outside the container network. Ask agentbox-mcp instead, which probes them all
over the container networks.

```
curl -s localhost:3465/health   # tool and integration counts
curl -s localhost:3465/ready    # names each bridge and its state
```

`/ready` is not ready if any bridge is unreachable, and it says which one.

### Request logs

Bridges write one JSON object per request to stdout and to a file.

```
tail -f ~/.local/state/agentbox/logs/vikunja-bridge.jsonl
{"service": "vikunja-bridge", "method": "GET", "path": "/v1/tasks", "status": 200, "bytes": 76, "ms": 5.9, "params": {"view": "lean"}}
```

Use the file, not `docker logs`. Every deploy recreates the container and
container logs do not survive that. The file rotates at 32 MB and keeps one
previous generation.

The bridges run as uid 65532, so the log directory needs group access once.

```
sudo chgrp 65532 ~/.local/state/agentbox/logs && sudo chmod 0775 ~/.local/state/agentbox/logs
```

To see where the bytes go across all bridges:

```
cat ~/.local/state/agentbox/logs/*.jsonl | python3 -c "
import json,sys,collections
n=collections.Counter(); b=collections.Counter()
for l in sys.stdin:
    r=json.loads(l); k=f\"{r['service']} {r['path']}\"
    n[k]+=1; b[k]+=r['bytes']
for k,v in b.most_common(15): print(f'{v:9d} B  {n[k]:4d} calls  {k}')"
```

`bytes` is the response size, so this shows where the model's context actually
goes, from real traffic.

The logs never contain the Authorization header, request or response bodies,
or any query parameter outside `bridge_base.LOGGED_QUERY_PARAMS`. A free-text
parameter such as a search string can carry personal data. Probe requests are
left out unless `BRIDGE_LOG_PROBES=1`.

## Deploying

```
cli/agentbox validate
cli/agentbox deploy <service>
cli/agentbox update <service>     # pull newer third-party images and redeploy
```

`validate` checks for committed secrets and keys, `0.0.0.0` bindings,
unqualified Docker port mappings, missing resource limits and
`no-new-privileges`, and that the approval policy has all three tiers. CI runs
the same checks.

`deploy` runs `validate`, loads `$AGENTBOX_ENV_DIR/<service>.env`, resolves any
secret references, and runs `docker compose -p <service> up -d --build`.
References can point at 1Password (`op://`), Infisical, Bitwarden or Doppler,
or at your own lookup command in `$AGENTBOX_ENV_DIR/secret-wrapper`. A plain
value works too.

The compose project name is the service name, so a service's default network
is `<service>_default`. agentbox-mcp joins the bridges' networks by that name.

Each service directory has an `.env.example` to copy into `$AGENTBOX_ENV_DIR`
and fill in before the first start.

### Looking up a policy tier

```
cli/agentbox policy check <action>
```

Prints the action's tier as JSON. It exits 2 for `always_denied`. An action the
policy does not list is `approval_required`.

### Scaffolding a new bridge

```
cli/agentbox scaffold todoist
```

This creates `services/compose/todoist-bridge` from the template, with both
credentials as separate secret references, a free port, the platform
guardrails in place and a starter test. It runs `validate`, commits to
`scaffold/todoist-bridge`, and stops. It does not deploy or merge.

Review it against `skills/adding-a-bridge/SKILL.md`, replace the placeholder
route, then run `cli/agentbox deploy todoist-bridge`.

### Adding an integration to agentbox-mcp

Add one module in `services/compose/agentbox-mcp/app/integrations/` that
exports `TOOLS` and `dispatch(name, args)`, add one line to `INTEGRATIONS`, and
map each tool to a capability in the policy. There is no second container,
token or compose file. If two integrations declare the same tool name the
server refuses to start.

## People

### Identities

```
cli/agentbox identity list
cli/agentbox identity add sam
cli/agentbox identity remove sam
```

`add` generates a token, writes it into the gateway env, and prints the two
steps it leaves to you, which are storing the token in your secret manager and
redeploying. It warns when you add the first identity, because that is when
`AGENTBOX_MCP_SHARED_TOKEN` stops working. The gateway config has to point at
a real identity token, or every call gets a 401 and it looks like a broken
deploy.

Underneath it is one variable.

```
AGENTBOX_IDENTITIES=alex:<token>,sam:<token>
```

The token presented is the identity. There is no way to ask to be someone
else, so an instruction hidden in an email cannot switch accounts. Switching
would need a credential the process was never given.

Each person's Google credential can live in its own bridge container.

```
GOOGLE_BRIDGE_URL_SAM=http://sam-google-bridge:8080
GOOGLE_BRIDGE_TOKEN_SAM=...
```

Without these, calls go to the shared bridge. That is right for shared
services like tasks and wrong for personal mail. `identity list` shows which
services each person reaches through their own bridge, because that is easy to
get wrong without noticing.

Grants can be scoped to one person with `agentbox grant <tool> --for alex`. An
unscoped grant covers anyone.

There is no limit on the number of people. Each one gets a private memory
scope plus the shared household one, and each person who connects Google gets
their own bridge.

With `AGENTBOX_IDENTITIES` empty, the gateway uses `AGENTBOX_MCP_SHARED_TOKEN`
with no identity, which is single-operator mode. Once identities exist the
shared token stops working on purpose. Otherwise it would be an unnamed extra
identity that every per-person check ignores.

### Onboarding someone

Every step happens in the browser.

1. **Operations > Invite somebody new.** Enter a short account name and an
   email address or Discord id. With a delivery channel set up the invite is
   sent. Without one it appears under "Waiting to be opened" for you to hand
   over. Only one invite per name can be outstanding, because each one is a
   credential.
2. **They open the link and fill in the form.** A display name, and which
   services they want their own account for. They never see Vikunja's login
   page or a terminal.
3. **Operations > Waiting to be set up > Finish setting them up.** This writes
   an approval into a spool. `agentbox-onboarding.path` notices it and
   `agentbox invite drain` does the privileged part. It creates the identity,
   the Vikunja account, and a Google bridge holding only their credential.

The approval card names the account being created and shows separately what
the person called themselves. You are approving the account. The display name
is whatever they typed.

**The link is a credential.** It allows creating an identity on this box,
which is worth more than a bridge token. It is single use and expires after 48
hours, and a refusal never says whether the id or the secret was wrong. Send it
to the person directly, not to a group chat.

The Google step in the invite form only appears when `AGENTBOX_INVITE_ORIGIN`
is set. Google will not redirect to a private IP or a `.local` name, and
loopback on someone's phone means their phone. If it is not set, they finish
onboarding and connect Google afterwards from their own portal under "Your
accounts", which sets up the same bridge. Set it if people will fill the form
in on the box itself, where `http://127.0.0.1:8770` works.

The same thing works from a terminal.

```
cli/agentbox-invite create sam
cli/agentbox invite complete <id>
```

`agentbox-invite serve` runs as a unit with `--unattended`, so the form is up
whenever someone opens a link. It refuses every request without a valid invite
token.

#### Why the page cannot do it alone

The invite page has no privileges. It has no Docker socket, no secret manager
token and no bridge tokens, and it writes one spool file. The worker does the
privileged work as the operator.

That split is forced. Registration is off in Vikunja
(`VIKUNJA_SERVICE_ENABLEREGISTRATION=false`), so creating an account needs
`vikunja user create` inside the container, which needs the Docker socket. A
web page on the LAN holding the Docker socket could read every bridge
credential with `docker inspect`.

#### What a new person gets

| | |
|---|---|
| Tasks | an account, with a one-time password to change |
| Memory | a private scope, which exists as soon as the identity does |
| The house | shared, nothing to set up |
| Gmail and Calendar | they consent in the page, then their bridge is built |

Consent is the one step that cannot be automated, because nobody else can
agree to Google's terms for them. Everything after it is automatic. They click
"Sign in with Google", Google returns an authorisation code, and
`invite complete` exchanges it for a refresh token. It then starts
`sam-google-bridge` holding only their credential and sets
`GOOGLE_BRIDGE_URL_SAM`, so their mail is reached with their token and nobody
else's.

Authorisation codes expire after about ten minutes and work once, so finish
the setup while they are still there. A stale code fails with `invalid_grant`
and says so.

They get no writable calendar by default. `GOOGLE_ALLOWED_WRITE_CALENDAR_ID` is
left blank in their env file, because copying yours would let their assistant
write to your calendar. Set it if they want one.

If they skip the Google step they use the shared bridge, which is fine for
shared services and wrong for mail. `invite complete` warns about it.

### Memory scopes

There are two levels rather than per-item sharing.

| scope | who reads it |
|---|---|
| `<identity>` | only that person, and the default |
| `household` | everyone |

Memories are private by default. A memory that ends up shared because nobody
said otherwise is a disclosure nobody chose. Filtering happens inside the
memory bridge, not in agentbox-mcp, so a confused caller cannot leak another
person's memories by asking the wrong way.

I rejected per-item sharing. "Sam can see this one thing of Alex's" makes
"what can Sam see?" impossible to answer without reading every row.

The assistant calls `whoami` to find out which person it is acting for and
which scopes it can read. The tool is `allowed`, because uncertainty about who
it is serving causes exactly the cross-account mistakes it prevents. Memories
from before scopes existed have no scope and count as household.

#### What a private scope protects

A scope controls what the assistant shows to whom. Sam's assistant cannot read
Alex's private memories, which stops one person's context leaking into
another's conversation.

A scope does not hide anything from the operator. Whoever runs the box can
read `/data/memory.json` with one `docker exec`, so a review screen that hid
rows from them would only be for show. The operator's review token lists every
scope, which also means the one account that can approve a private proposal
can see it.

Privacy from the operator would need per-person encryption at rest with keys
the operator does not hold. That is not built.

`identity remove` does not delete that person's memories. They stay under the
removed name and become unreachable. Deleting them is a separate step.

### The portal

`cli/agentbox-portal serve` runs a self-service page on port 8771. Members
review the memories the assistant proposed about them and manage their own
accounts. Admins also get Operations.

```bash
systemctl --user status agentbox-portal
```

People sign in by entering their email address, and a link arrives. For
someone whose address is not set up yet, the operator can make a link
directly.

```bash
cli/agentbox-portal link sam --base-url http://127.0.0.1:8771
```

There are two roles, `admin` and `member`, taken from `AGENTBOX_ADMINS`. The
capability table is in the portal source. It is deliberately not a general
role system, because a second policy vocabulary next to
`approval-policy.yaml` would mean two systems that can disagree about who may
do what. An empty `AGENTBOX_ADMINS` makes nobody an admin, which is the safe
way for a misread env file to fail.

An admin decides `household` proposals, because those affect everyone. An
admin does not decide another member's private proposals.

The relevant settings are `AGENTBOX_IDENTITY_EMAILS="alex:alex@example.com,sam:sam@example.com"`,
`AGENTBOX_ADMINS=alex`, and `AGENTBOX_SMTP_*` for email delivery. Without an
SMTP host the portal still works and links still get made, but the operator
has to hand them over.

#### Why a sign-in link only works in one browser

A magic link is usually a bearer token, so whoever holds it is you. That fits
badly here, because the assistant can read the inbox the link is sent to. A
model following an injected instruction could search for the email, open the
link, and approve its own memory proposals. That would defeat the review gate,
which is its only route to lasting memory.

Hiding the email from the assistant's searches would be an allowlist the model
could reason its way around. So requesting a link sets a nonce cookie, and
using the link requires it.

On a phone, Discord and most mail apps open links in their own browser, which
has different cookies. So the binding decides privilege rather than access.

| where you open it | what you get |
|---|---|
| the browser that asked | everything |
| anywhere else | read your memories and accounts, but not approve or disconnect |

Only a full sign-in uses up the link. A limited one leaves it valid, so
someone who reads the message cannot lock the real person out by opening it
first.

#### Getting a link to someone

All three channels send the same link.

| channel | needs | notes |
|---|---|---|
| Operator hands it over | nothing | `cli/agentbox-portal link <name>` |
| Discord DM | a paired Discord account | no SMTP or sending address needed |
| Email | `AGENTBOX_SMTP_*` | sent from whichever account you authenticate as |

Discord delivery works like the connector flow. The portal writes a delivery
request, and `cli/agentbox-approvals` sends the DM. That process runs as the
operator, holds the bot token, never passes anything through the model, and
drops the URL once it is sent. The portal never holds the bot token.

The Operations tab lists which channels will actually deliver. It is the only
place to find out, because the sign-in form answers the same way for a known
and an unknown address. Anything else would let strangers find out who lives
here.

`cli/agentbox identity list` shows each person's role, how they can sign in,
and which services they reach through their own bridge.

#### Pairing Discord

On the Accounts tab, *Connect Discord* shows a six-character code. Send
`link ABC123` to the Agentbox bot as a direct message and the account is
paired. Sending from that account is the proof, since anyone can type a user id
into a form but only its owner can send a message from it. Messages from bots
are ignored, so the assistant cannot pair an identity to itself.

The pairing is stored in the portal's state directory, which no container
mounts. It decides where sign-in links go, so the assistant must not be able
to write it. `AGENTBOX_DISCORD_IDENTITIES` is still read for older setups.

### Google consent and hostnames

The portal works on a LAN address for sign-in links, memory review and account
status. Google consent is the exception. Google only accepts a redirect URI on
loopback over HTTP, or on a real public domain over HTTPS. `agentbox.local` and
a bare private IP are both rejected with "must end with a public top-level
domain".

- **Loopback.** Register `http://127.0.0.1:8771/google/callback` and do the
  consent in a browser on the box. This is the default and needs nothing else.
- **A real name over HTTPS.** Point `AGENTBOX_PORTAL_URL` and
  `AGENTBOX_OAUTH_REDIRECT_BASE` at it, and consent works from anywhere.

`agentbox validate` rejects a portal URL Google would refuse, so this fails
when you configure it rather than halfway through someone's consent screen.

Neither option below needs a domain purchase or exposes the box to the
internet.

**Tailscale**, if everyone who needs remote consent can install it. MagicDNS
gives the machine a real name and Tailscale issues a certificate for it.

```bash
sudo tailscale up
# then in the admin console, enable MagicDNS and HTTPS
sudo tailscale serve --bg --https=443 http://127.0.0.1:8771
tailscale status --json | grep -o '"DNSName":"[^"]*"'   # the name to register
```

Set `AGENTBOX_PORTAL_URL` to `https://<that name>` and register
`https://<that name>/google/callback` on the OAuth client. Leave
`AGENTBOX_OAUTH_REDIRECT_BASE` on loopback if you would rather keep consent on
the box.

**A Cloudflare tunnel**, if they cannot. A named tunnel on a domain you own
gives a stable HTTPS name with no port forwarding. A quick tunnel
(`cloudflared tunnel --url http://127.0.0.1:8771`) gives one in seconds, but
the name changes on every restart. That is fine for one consent and useless as
a registered redirect URI.

**Or neither.** For people who live together, consent at the box means
sitting down at it once.

### Reconnecting or switching a Google account

Scopes change. A token issued before a scope was added returns
`ACCESS_TOKEN_SCOPE_INSUFFICIENT` for it, and the person has to consent again.

They start it themselves in the portal under **Your accounts > Reconnect or
switch account**, which takes them through Google's consent screen and
captures an authorisation code. A timer picks it up within thirty seconds and
finishes the job.

```bash
systemctl --user status agentbox-connectors.timer
cli/agentbox connectors sync          # or do it now, by hand
```

It takes two processes because the portal must not hold the Google client
secret, write access to the env directory, or the Docker socket. A web page on
the LAN that can run containers is the worst thing that could exist on this
box. The timer means nobody has to wait for the operator to fix their own
account.

Authorisation codes expire after about ten minutes. The timer runs well within
that, and a stale code is refused with a plain message.

Consent is tied to the person who asked for it. A callback whose `state` does
not match is refused, so a crafted link cannot put someone else's authorisation
code into this person's account.

To disconnect someone:

```bash
cli/agentbox identity disconnect sam
```

This revokes the credential at Google before removing the local copy. Deleting
only the local copy is not disconnecting, because the grant stays listed in
their Google account and anyone who copied the token could still use it. If
revocation cannot be confirmed, the command says so and tells you to check the
account's connected apps by hand.

`doctor` checks each person's Google credential and warns when one looks
revoked. Otherwise the container stays healthy and the first sign is an
unexplained 403 days later.

## Approvals and grants

Every assistant tool call is checked against `policies/approval-policy.yaml`,
the same file that covers operator actions. Each tool maps to a capability and
the capability has a tier. `allowed` tools run. `approval_required` tools are
refused until the operator issues a grant. A tool with no mapping is
`approval_required`, so a tool added without one fails closed.

```
cli/agentbox grant archive_gmail --ttl 15m   # single use by default
cli/agentbox grants list
cli/agentbox grants revoke archive_gmail
```

Add `--repeatable` for a grant that can be used until it expires.

### Approving from Discord

A refused call is recorded, so you are told about it rather than having to
notice.

```
cli/agentbox approvals list      # what the assistant is waiting on
cli/agentbox approvals clear     # drop requests you will not grant
```

`cli/agentbox-approvals` posts those requests to Discord and writes the grant
when you reply `approve <tool>`. Run it as yourself, not as the agentbox user.

```
AGENTBOX_APPROVAL_CHANNEL_ID=<channel> cli/agentbox-approvals
```

It reads the bot token and the list of allowed approvers from your secret
manager (`op://Agentbox/discord` with 1Password).

It is a separate process because the assistant is in the same Discord, and an
instruction hidden in an email can make it say anything, including a
convincing request for its own approval. So the process ignores every message
written by a bot and every user not on the list, and it never writes grants
itself. It calls `agentbox grant`, which the assistant cannot run.

Two directories back this, and the split between them is the security
property.

- `~/.local/state/agentbox/policy` is mounted read-only at `/policy` and holds
  the grants. The assistant's side must never be able to give itself
  permission.
- `~/.local/state/agentbox/policy-state` is mounted writable at
  `/policy-state` and holds only markers for used grants. Writing there can
  only remove permission.

The containers run as uid 65532, so the writable directory needs group access
once.

```
sudo chgrp 65532 ~/.local/state/agentbox/policy-state && sudo chmod 0775 ~/.local/state/agentbox/policy-state
sudo install -d -o "$USER" -g 65532 -m 2775 ~/.local/state/agentbox/policy-state/pending
```

Create `pending` yourself, with setgid. If the container creates it first it
is owned by uid 65532, and you cannot remove requests from it, because
deleting a file needs write access to its directory. If this step is missed,
single-use grants are refused with a clear message rather than quietly
becoming unlimited until they expire.

### Reviewing memories from Discord

The same process handles memory proposals, so the protections are shared
rather than reimplemented. Pending proposals are posted as they appear.

```
Memory proposal `520a4fcf`
> Bin day is usually Wednesday
Shall I remember this?
Reply `remember 520a4fcf`, `feedback 520a4fcf` to file it as something to
fix, or `forget 520a4fcf`.
```

`memories` lists what is stored, and `forget <id>` removes one.

Replies are only accepted from `AGENTBOX_APPROVAL_USER_IDS`, and bot messages
are ignored before any command is read. The assistant reads the same channel
and still cannot approve its own memory, even if an injected instruction makes
it type the words.

A grant is about a tool and is nobody's secret, but a memory proposal can be
private to one person. Household proposals go to the channel. Private ones go
only to that person's DM, which needs their Discord account paired. Without a
pairing a private proposal is not posted anywhere and stays in the portal.

### When a fact changes

Deleting an old fact loses the shape of the change, so there are three states.

| status | meaning | assistant reads it |
|---|---|---|
| `approved` | current | yes |
| `superseded` | was true until something replaced it | as history |
| `forgotten` | should never have been stored | no |

```
cli/agentbox memory add "Bin day is Wednesday" --supersedes <old-id>
cli/agentbox memory history <any-id-in-the-chain>
```

`history` works from any id in the chain, because the one you have is usually
from an old answer rather than the current one.

`search_memories` returns the current version of each fact, with the versions
it replaced nested under it as `previously`, each with the date it stopped
being true. Nesting costs a sentence and a date per old version and leaves no
doubt about which one is current. It keeps at most
`MEMORY_MAX_PRIOR_VERSIONS` (default 3), so a fact revised fifty times does not
add fifty lines to every search.

From Discord:

```
remember <id> replaces <old-id>    approve it, retiring what it replaces
replaces <new-id> <old-id>         link two that are already stored
```

The second form is for memories stored before anyone noticed they were
related.

The portal's Memories tab lists what is stored under **What I remember**, with
earlier versions folded under each one and a Forget button. Sessions the
assistant created cannot forget, for the same reason they cannot approve.
Deleting the inconvenient parts is the same power as writing memory.

Adding a memory that looks like it replaces an existing one prints a
suggestion instead of acting on it. A wrong automatic replacement hides a true
memory behind a false one without saying so.

## Self-reflection

Every morning at 09:00 the assistant reviews its own outcome history, works out
what to do differently, and proposes lessons for the operator to approve or
reject.

It reads a rolling seven-day window, so it sees mostly the same activity each
day. The skill has it check `search_memories` and `list_memory_proposals`
before looking at the evidence, and most days end with no proposal. A queue
filling up with near-identical lessons means that step is being skipped. Watch
for it, because a review queue nobody reads closes the assistant's only route
to lasting memory.

```
cli/agentbox memory list          # proposals waiting for review
cli/agentbox memory approve <id>
cli/agentbox memory reject <id> --reason "..."
```

agentbox-mcp writes an outcome journal. `review_own_activity` summarises it,
the assistant reflects using `skills/self-reflection`, proposes with
`propose_memory`, and you approve. It cannot approve its own conclusions,
because that needs a credential no container holds.

The journal records tool names, outcomes, timings, argument names and a short
list of shape values such as `view` and `limit`. It never records argument
values, results or exception messages, because an upstream error often quotes
the input that caused it. `review_own_activity` returns counts only. That is
what makes it safe to run unattended against real accounts.

The schedule lives in the gateway's own scheduler, not cron. It runs as the
`agentbox` user under the gateway's profile, and `HERMES_HOME` matters. A job
created without it lands in a different profile and never runs.

```
sudo -u agentbox env HERMES_HOME=$HERMES_HOME HOME=$GATEWAY_USER_HOME \
  $GATEWAY_VENV/bin/python -m hermes_cli.main cron list
```

The scheduler reads its jobs at startup, so restart the gateway after adding
one (`sudo systemctl restart hermes-gateway-agentbox`). `cron list` prints
"Gateway is not running" even when it is. Check for `.tick.lock` in
`$HERMES_HOME/cron/` instead.

## The gateway

### Never run it by hand while the service is up

The unit starts the gateway with `gateway run --replace`, so a second instance
takes over from the first. Running it by hand to see an error stops the real
one, and the service then fails with only a banner and `status=1/FAILURE`.

```bash
sudo systemctl start hermes-gateway-agentbox    # once the manual one is gone
```

To find out why it is failing, read the journal or check the wrapper's
preconditions. The secret manager login and the secret reads all run before
the gateway starts, and any of them can exit 1.

### Containment

`doctor` checks five things the assistant's confinement depends on. Each of
them would fail without any error if broken.

```
modify_production_gateway_config enforced (config.yaml not writable)
enable_skill_bundle_production   enforced (skills not writable)
modify_upstream_agent_source     enforced (hermes-agent-test not writable)
docker socket unreachable to the assistant
operator credential directory unreadable to the assistant
```

The first three keep the assistant from rewriting its own config, skills or
source. The last two keep credentials out of reach even if a shell were ever
turned back on. **Never add `agentbox` to the `docker` group.** That is
equivalent to root and undoes the whole model.

### Adding a skill

```
sudo install -d -o root -g agentbox -m 0750 $HERMES_HOME/skills/<name>
sudo install -o root -g agentbox -m 0640 skills/<name>/SKILL.md $HERMES_HOME/skills/<name>/SKILL.md
```

Skills in this repository are the source, and that directory is what the
gateway loads. They are not synced automatically. They are owned by root
because a skill shapes the assistant's behaviour as much as its config does,
so the assistant can read them but not change them.

## Reviewing the assistant's code proposals

The assistant can read this repository and propose changes. It cannot merge or
deploy. Proposals are branches in its own clone, which you fetch.

```
cli/agentbox proposals list
cli/agentbox proposals show <name>
cli/agentbox proposals merge <name>     # checks it out on review/<name>
```

`merge` puts the work on a `review/` branch, not on main. Merging straight to
main would make approval one keystroke on an unread diff. Test it, run
`validate` and the tests, then merge to main yourself.

Some paths are refused whatever the proposal says it is for. These are
`policies/`, both policy gates, `cli/`, `.github/`, and the builder's own
source. That is a path check rather than a policy tier, because a tier cannot
say "may edit any file except the ones that govern it". `cli/agentbox smoke`
checks the refusals on every run.

### First-time setup

The clone is owned by you, with group 65532 and setgid, so the container can
write to it through the group.

```
git clone $AGENTBOX_REPO ~/.local/state/agentbox/builder-repo
sudo chown -R "$USER":65532 ~/.local/state/agentbox/builder-repo
sudo find ~/.local/state/agentbox/builder-repo -type d -exec chmod 2775 {} +
sudo find ~/.local/state/agentbox/builder-repo -type f -exec chmod 664 {} +
git -C ~/.local/state/agentbox/builder-repo config core.sharedRepository group
git -C ~/.local/state/agentbox/builder-repo remote set-url origin /origin
```

`origin` is `/origin` because that is where this repository is mounted,
read-only, inside the container. Giving the clone to the container's uid looks
simpler but breaks. Git refuses to read a repository it does not own, and
`-c safe.directory` cannot override that from the command line, by design.

## Home Assistant

Not deployed by default, because it needs a Home Assistant instance.

```
cp services/compose/homeassistant-bridge/homeassistant-bridge.env.example ~/.config/agentbox/homeassistant-bridge.env
```

Fill in `HA_URL`, a long-lived access token from your Home Assistant profile as
`HA_TOKEN`, and a generated `HA_BRIDGE_TOKEN`. Then decide what the assistant
may change.

```
HA_CONTROLLABLE_ENTITIES=light.kitchen,light.hall,scene.evening
```

**Empty means it can read the house and change nothing.** That is the default
and the right place to start. Add entities one at a time.

Put the same `HA_BRIDGE_TOKEN` in `agentbox-mcp.env`, then deploy both.

```
cli/agentbox deploy homeassistant-bridge
cli/agentbox deploy agentbox-mcp
```

`doctor` reports any tool the gateway cannot see.

### What it will not do

- **No general service calls.** There is no `call_service` tool. Home
  Assistant's REST API is one endpoint away from full control of the house, and
  an approval in front of that would be asked so often it would get granted
  without reading.
- **Locks, alarms, covers, garage doors and cameras are never actuated**, even
  if they are in `HA_CONTROLLABLE_ENTITIES`. That check runs first and ignores
  the list, because `lock.front_door` and `light.front_door` differ by two
  characters and the list is edited by a tired person.
- **Climate stays between 5 and 30 °C** whatever is granted.

Setting a temperature is `approval_required`, so it goes through Discord
approval. Lights and scenes are `allowed`, because the allowlist already limits
them.

### Presence

Presence needs no setup. Reading `binary_sensor.*` is an ordinary read, so once
Home Assistant has occupancy sensors the assistant can tell which room someone
is in and answer there. That beats a camera for "know where I am". There is no
video, nobody else's privacy is involved, and there is nothing to inject.

### Cameras

`look_at_camera` is retired along with the local vision model, and its code
is kept in `RETIRED_TOOLS`. The design is worth keeping. Cameras were opt-in
one at a time through `HA_VIEWABLE_CAMERAS`, separately from control, and both
ends were fixed vocabularies.

- The question was an enum (`occupancy` or `activity`), not free text. A
  free-text question is one an injected instruction can write, and "transcribe
  everything you can see" would turn the camera into a reader.
- The answer was `{people, posture, text_visible}`, with `posture` checked
  against a fixed list. Unknown keys were dropped, and a reply that was not
  valid JSON, which is what a successful injection looks like, was discarded.

Text in the room was reported as `text_visible: true` and never transcribed.

### Screens

Screens have two levels, like a phone's lock screen.

```
HA_PRIVATE_SCREENS=media_player.office_monitor
```

A private screen gets the summary and the detail. Every other screen, including
any you have not classified, gets only the summary, and the bridge drops the
detail rather than leaving it to the assistant. A summary over 80 characters is
refused, so it cannot become a second detail field.

### Speakers

Bluetooth speakers paired to the host are not Home Assistant devices. Home
Assistant's Bluetooth support is for sensors, not audio, so no `media_player`
entity can exist for them. `cli/agentbox-speaker` runs on the host instead,
and two things reach it.

- the assistant, through the `speak_aloud` tool
- Home Assistant, through `rest_command.agentbox_speak` and the
  `script.announce_*` wrappers in `docs/reference/ha-announce-scripts.yaml`

Both go through the same quiet-hours limit, so an automation at 3am is refused
just like a tool call. That is why the limit lives in the service rather than
in `approval-policy.yaml`, which only governs one of the two callers.

Each per-room script can be given a Home Assistant area, which is the only
place a room name can live for a device Home Assistant cannot see. Assign them
under Settings > Areas. Check each one by playing a test announcement, because
a Bluetooth name such as `Echo-7UP` says nothing about which room it is in.

## Backup

```
cli/agentbox backup          # archive the task and memory stores, keep 14
cli/agentbox backup list
```

This archives the two stores holding anything the assistant cannot
regenerate. These are the Vikunja database (a host bind mount) and durable
memory (a Docker volume, read out through a short-lived container). Together
they are about 3 MB.

Each archive is restored as a test before old ones are pruned, so a broken run
cannot delete the last good copy. Archives are readable only by you and are
not encrypted.

Restoring needs nothing but tar.

```
tar -xzf ~/.local/state/agentbox/backups/agentbox-<stamp>.tar.gz -C /tmp/restore
```

Then, with both services stopped, copy `vikunja/` back over
`$AGENT_CONTROL_PLANE_STATE_DIR/vikunja` and `memory/memory.json` into the
`memory-bridge_memory_data` volume.

`cli/agentbox-backup.timer` runs it nightly at 03:30 as a systemd user timer.
`Persistent=true` makes it catch up after the machine has been off, so a box
that sleeps overnight still gets backed up.

```
systemctl --user list-timers agentbox-backup.timer
```

It needs `sudo loginctl enable-linger $USER`, or it only runs while you are
logged in.
