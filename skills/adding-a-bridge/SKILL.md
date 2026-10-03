---
name: adding-a-bridge
description: Use when adding a new credential bridge to the Agentbox platform, a narrow HTTP service that holds an upstream API or OAuth credential and exposes an allowlisted API to the assistant. Trigger when the user wants to connect a new external service (a SaaS API, a device, or a data source) that needs a secret the assistant must not see.
---

# Adding a bridge

A bridge keeps a credential away from the assistant. agentbox-mcp calls the
bridge with a bridge token, and the bridge calls the upstream service with the
real credential and returns only what it allows. Every bridge uses the shared
base so its auth fails closed. A hand-written auth check can accept an empty
token.

## Steps

1. **Scaffold it.** `cli/agentbox scaffold <name>` generates the service with
   the guardrails in place, runs `validate` and commits to a branch. It never
   deploys or merges.

2. **Leave `services/templates/bridge/app/bridge_base.py` alone.** It provides
   auth, error handling, body limits, health, readiness, request logging,
   policy enforcement and the server loop. Every bridge's Dockerfile copies it
   at build time, so there is one copy. Changing it changes every bridge, and
   `doctor` marks them all stale until they are redeployed.

3. **Write `app/bridge.py`**, the only file you author.
   - Read two secrets from the environment, the upstream credential and
     `*_BRIDGE_TOKEN`.
   - Write one function per route, `def route(handler, body) -> (status,
     payload)`. Raise `BridgeError(status, msg)` for expected failures, and
     return only the safe part of the upstream response.
   - Subclass `BridgeHandler` and set `bridge_token` and `routes`.
   - **Override `capability_for()` for anything gated.** Return the policy
     capability a request uses, or None. agentbox-mcp also checks tool calls,
     but it holds your bridge token, so the bridge's check is the
     authoritative one.
   - **If the bridge fronts a service you run**, override `upstream_status()`
     to probe it and return `{"ok": bool, "upstream": {...}}`. `/ready`
     reports this result. Never add the upstream to `/health`.
   - Consider a `view` parameter on list endpoints that return many objects.
     Returning only the fields the assistant acts on saves context at little
     cost. `vikunja-bridge` shows the pattern.

   Follow four rules for the API.

   - **Every list endpoint takes a limit.** Use `resolve_limit` from
     `bridge_base`. Without one the caller cannot know how much of the model's
     context a call will use.
   - **Creating is idempotent where the domain allows.** A retried call must
     not leave a second copy. Return the existing object, as
     `create_gmail_label` does by name and `propose_memory` by statement, or
     offer a `find_or_create_*` variant.
   - **Constrain writes the same way in every direction.** If creating
     something forces a namespace, applying or referencing it must enforce the
     same namespace, or the namespace means nothing.
   - **Do not add a tool that is another tool plus a fixed argument.** Put
     workflow in a skill or prompt, where it can change without an API
     change.

   Never put policy in a tool description. "Do not use without approval" is
   only text, and the assistant can ignore it. Enforce the rule in the bridge,
   or leave it out of the description.

4. **Know which probe is which.** `/health` says whether the process answers
   and never touches the upstream, because the container healthcheck uses it.
   When the backing service is down, the bridge should stay up and report it.
   It should not restart-loop. `/ready` probes the upstream. A stopped backing
   service should turn `/ready` red and leave `/health` green.

5. **Check the compose file and env example.** Keep the `:?` guards so a
   missing secret fails the deploy with an error. The scaffold publishes a
   port on `127.0.0.1` with the label `agentbox.exposure: operator`, so the
   bridge can be tested on its own during review. Remove that `ports` block
   and set the label to `private` once the bridge is wired into agentbox-mcp.
   A bridge with a host port lets any local process use a stolen bridge
   token.

6. **Map capabilities in `policies/approval-policy.yaml`.** Reads are usually
   `allowed`. Anything that changes state needs an explicitly chosen tier, and
   anything that sends, deletes or pays belongs in `always_denied` unless a
   bridge constrains it. A tool with no mapping fails closed.

7. **Wire it into agentbox-mcp.**
   - Add an integration module in
     `services/compose/agentbox-mcp/app/integrations/` exporting `TOOLS` and
     `dispatch(name, args)`, and add it to `INTEGRATIONS` in `server.py`.
   - Add `<PREFIX>_BRIDGE_URL` and `<PREFIX>_BRIDGE_TOKEN` to the agentbox-mcp
     compose file and env example.
   - Join the bridge's compose network (`<name>-bridge_default`) in the
     agentbox-mcp compose file.
   - Add the bridge to the readiness list in `server.py`, so agentbox-mcp's
     `/ready`, and therefore `doctor`, reports it.

8. **Check before deploying.**
   - The scaffolded test, `python3 -m pytest tests/test_<name>_bridge.py`.
   - `cli/agentbox validate`, which covers bindings, resource limits, non-root
     and tool mapping.
   - While the review port exists, `curl -s localhost:<port>/health` gives 200,
     and a route without a token gives 401.
   - Once wired in, `curl -s localhost:3465/ready` names the bridge.
   - If you implemented `upstream_status()`, stop the backing service and
     confirm `/health` stays 200 while `/ready` fails and
     `cli/agentbox doctor` exits non-zero. A readiness check that has never
     been seen to fail may not work.

9. **Deploy** with `cli/agentbox deploy <name>-bridge`, then
   `cli/agentbox deploy agentbox-mcp`.

## Checklist

- [ ] `bridge_base.py` unchanged
- [ ] the upstream credential and the bridge token are separate variables
- [ ] every route that changes state validates its input and returns only
      allowlisted fields
- [ ] no host port once integrated, and the label says `private`
- [ ] a policy mapping for every tool
- [ ] `upstream_status()` implemented if the bridge fronts a service you run
- [ ] `/health` does not touch the upstream
- [ ] `capability_for()` returns a capability for every gated route
- [ ] every list endpoint takes a limit through `resolve_limit`
- [ ] creating is idempotent, or a `find_or_create_*` variant exists
- [ ] write constraints match between creating and applying
- [ ] no tool is another tool plus a fixed argument
- [ ] no tool description states a rule the code does not enforce
- [ ] the bridge is in agentbox-mcp's readiness list
- [ ] no secret, request body or free-text query parameter reaches the log
- [ ] tests and `validate` pass
