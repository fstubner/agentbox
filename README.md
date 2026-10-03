# Agentbox

[![ci](https://github.com/fstubner/agentbox/actions/workflows/ci.yml/badge.svg)](https://github.com/fstubner/agentbox/actions/workflows/ci.yml)
[![clean-room onboarding](https://github.com/fstubner/agentbox/actions/workflows/onboarding.yml/badge.svg)](https://github.com/fstubner/agentbox/actions/workflows/onboarding.yml)
[![licence: MIT OR Apache-2.0](https://img.shields.io/badge/licence-MIT%20OR%20Apache--2.0-blue)](#licence)

Agentbox is an AI assistant for a household, running on a machine in the
house. It uses a local model, and it reaches tasks, email, calendars, the
house itself and a shared memory through a small set of tools that each do
one narrow thing.

I designed it and directed AI agents to build most of it. [PRODUCT.md](PRODUCT.md)
says what it is for and how far along each part really is.

Everyone in the household can use it. Tasks, shopping and joint scheduling are
shared. Each person's mail, calendar detail and memories are private to them.
Who the assistant is acting for comes from the signed-in session, and the model
never gets to pass it as a tool argument. So an instruction hidden in an email
cannot make it act as somebody else.

It also works for one person with no identities set up.

The second badge runs the quickstart below on a fresh machine on every change.
If these instructions stop working, it goes red.

## How it is kept safe

- The assistant has no shell. It can only call tools with fixed schemas, over
  MCP.
- Anything irreversible has no tool at all. It cannot send mail, delete files,
  unlock doors or approve its own memories.
- Credentials live in separate bridge containers, and the assistant never sees
  a token.
- Every tool call is checked against `policies/approval-policy.yaml`. Most
  tools are allowed because the bridge behind them already limits what they can
  do. A few need the operator's approval, and a tool the policy does not list
  needs approval by default.
- Code the assistant proposes goes into its own repository as a branch. It
  cannot change the code it runs on.
- Agent processes run in a Bubblewrap sandbox. The root filesystem is
  read-only, the operator's credentials are hidden, and the only writable place
  is `~/.local/state/agentbox`.

## What is in the repository

| Directory | What it holds |
| :--- | :--- |
| `cli/` | The `agentbox` command, the sandbox launcher, and the web portal |
| `services/` | Docker Compose stacks for the bridges, the tool server and Vikunja |
| `policies/` | The approval policy, plus network and secrets policies |
| `gateway/` | Example gateway configuration |
| `router/` | Retired. It routed work to small local models until they failed evaluation. See `router/README.md` |
| `docs/` | Architecture, runbook, and evaluation notes |

## Getting started

### What you need

- A Linux machine with systemd, such as Ubuntu 24.04
- Docker with Compose v2
- Python 3.12 or later
- Bubblewrap (`sudo apt install bubblewrap`)
- Git

### Set up the machine

```bash
./cli/agentbox setup
```

This creates the config and state directories with tight permissions, sets up
the repository the assistant proposes changes into, installs example config
files, and installs the sandbox wrapper. Running it again is safe.

### Add secrets

Each service reads `~/.config/agentbox/<service>.env`. A value can be written
in plain, or as a reference that your secret manager resolves when you deploy.
1Password (`op://`), Infisical (`infisical://`), Bitwarden (`bws://`) and
Doppler (`doppler://`) work out of the box. For anything else, copy
`~/.config/agentbox/secret-wrapper.example` to `secret-wrapper`, make it
executable, and put your own lookup command in it.

### Check it

```bash
./cli/agentbox validate
```

This checks the repository itself, including that the policy is consistent,
the tool definitions fit their size budget, and the code passes lint.

```bash
./cli/agentbox doctor
```

This checks the running machine. It reports which services answer, whether
the assistant is really cut off from credentials and the Docker socket, and
whether anything is listening on the network that should not be.

### Deploy

```bash
./cli/agentbox deploy <service>
```

## Day to day

```bash
./cli/agentbox status                 # which services are answering
./cli/agentbox update <service>       # pull newer images and redeploy
./cli/agentbox proposals list         # changes the assistant has proposed
./cli/agentbox proposals show <name>
./cli/agentbox backup                 # back up state and memory, then test the archive
./cli/agentbox smoke                  # run real workflows end to end
```

Backups are readable only by you, and each one is restored as a test before
older ones are pruned. They are not encrypted, so keep them somewhere that
matters.

## Licence

Dual licensed under either of

- MIT ([LICENSE-MIT](LICENSE-MIT))
- Apache License 2.0 ([LICENSE-APACHE](LICENSE-APACHE))

at your option.
