---
name: adding-a-bridge
description: Use when adding a new credential bridge to the Agentbox platform — a narrow HTTP service that holds an upstream API/OAuth credential and exposes an allowlisted API to the assistant. Trigger when the user wants to connect a new external service (a new SaaS API, device, or data source) that requires a secret the assistant must not see directly.
---

# Adding a Bridge

A bridge isolates a credential: the assistant calls the bridge with a *bridge
token*; the bridge calls the upstream service with the real credential and
returns only allowlisted results. New bridges must reuse the shared base so
they inherit fail-closed auth — a past bug shipped a bridge that accepted an
empty token because auth was hand-rolled.

## Steps

1. **Copy the template.**
   `cp -r services/templates/bridge services/compose/<name>-bridge`

2. **Do NOT edit `app/bridge_base.py`.** It provides auth, error handling,
   body limits, health, readiness, request logging, and the server loop.
   Editing it per-bridge is how divergence bugs start.

   The file is *vendored* — each bridge has its own copy, because the
   Dockerfile only copies `app/*.py` and cannot reach outside the build
   context. When the template legitimately changes, copy it into every bridge
   in the same commit. `cli/agentbox validate` hashes the copies against the
   template and fails on drift, so a partial sync cannot ship.

3. **Write `app/bridge.py`** — the only file you author:
   - Read two secrets from env: the upstream credential and `*_BRIDGE_TOKEN`.
   - Write one function per route: `def route(handler, body) -> (status, payload)`.
     Raise `BridgeError(status, msg)` for expected failures; return only the
     safe subset of the upstream response.
   - Subclass `BridgeHandler`, set `bridge_token` and `routes`.
   - **If your bridge fronts a service you run** (rather than a remote SaaS
     API), override `upstream_status()` to probe it. Return
     `{"ok": bool, "upstream": {...}}`. This is what makes `/ready` meaningful.
     Do not add the upstream to `/health` — see below.
   - Consider a `view` parameter on list endpoints that return many objects.
     Emitting only the fields the agent acts on is the cheapest context saving
     available, because the tokens are never generated. See
     `docs/context-economy.md` and `vikunja-bridge` for the pattern.

   Four surface rules, all learned from getting them wrong here:

   - **Every list endpoint takes a bound.** Use `clamp_limit` from
     `bridge_base`. An unbounded list is a context problem before it is a
     performance one — the caller cannot know how much of its window a call
     will spend.
   - **Creates are idempotent where the domain allows it.** A retried call must
     not leave a second copy behind. Return the existing object instead
     (`create_gmail_label` by name, `propose_memory` by statement) or offer a
     `find_or_create_*` variant where the backing store has no natural key.
   - **Constrain writes symmetrically.** If creating an object forces a
     namespace, applying or referencing one must enforce the same namespace.
     Gmail label create was prefixed while label apply was not, which made the
     prefix decorative.
   - **Tools are primitives, not workflows.** If a tool is another tool plus a
     fixed argument, do not ship it — the agent can compose. Two vikunja tools
     were `add_task_comment` and `list_tasks` with a canned string, and a Gmail
     tool was `search` with a hardcoded vendor query. Workflow belongs in a
     skill or prompt, where it can change without an API change.

   Never encode policy in a tool description. "Do not use without approval" is
   documentation; the assistant is free to ignore it. Enforce it in the bridge
   or do not claim it.

4. **Know which probe is which.** `/health` is liveness and must never touch
   the upstream — the container healthcheck uses it, and a bridge that
   restart-loops because its backing service is down is strictly worse than
   one that stays up and reports honestly. `/ready` is readiness and does probe
   the upstream. A downed backing service should turn `/ready` red and leave
   `/health` green.

5. **Rename the env vars** in `compose.yaml` and `*.env.example` to match your
   service. Keep the `:?` guards so a missing secret fails the deploy loudly.
   Keep the host port bound to `127.0.0.1` (or an explicit LAN IP).

6. **Add the token to the deny-by-default policy if the bridge can mutate
   state.** A read-only bridge is `approval_required` at most; a bridge that
   sends/deletes/pays belongs in `always_denied` unless explicitly gated.
   Update `policies/approval-policy.yaml`.

7. **Register the bridge with the assistant** as an MCP lever (a matching
   `*-mcp` service), not by giving the assistant the bridge URL directly, if
   the assistant needs to call it.

8. **Add it to `doctor`.** A service nothing checks is a service that can be
   down for hours without anyone noticing — that has already happened once.
   Put the port in `BRIDGE_READY_PORTS` in `cli/agentbox` if it has an
   upstream, and in `ENDPOINTS` otherwise.

9. **Verify before deploy:**
   - `python3 -m pytest services/compose/<name>-bridge` (copy the base test).
   - `cli/agentbox validate` (bindings, resource limits, non-root, base drift).
   - `curl -s localhost:<port>/health` → 200; the same route without a token → 401.
   - `curl -s localhost:<port>/ready` → 200 with the upstream up.
   - **Run the outage drill if you implemented `upstream_status()`:** stop the
     backing service, confirm `/health` stays 200 while `/ready` returns 503
     and `cli/agentbox doctor` exits non-zero, then restart it. A readiness
     check nobody has seen fail is a readiness check that may not work — the
     first version of this one silently downgraded a real outage to a warning.

10. **Deploy:** `cli/agentbox deploy <name>-bridge`.

## Checklist (all enforced by base/template/validate — confirm you didn't undo them)

- [ ] `bridge_base.py` unchanged, and identical to the template in every bridge
- [ ] upstream credential and bridge token are distinct env vars
- [ ] every mutating route validates its input and returns only allowlisted fields
- [ ] host port bound to loopback/LAN, never `0.0.0.0` on the host
- [ ] policy entry added for any state-changing capability
- [ ] `upstream_status()` implemented if the bridge fronts a service you run
- [ ] `/health` does **not** touch the upstream
- [ ] every list endpoint takes a bound via `clamp_limit`
- [ ] creates are idempotent, or a `find_or_create_*` variant exists
- [ ] write constraints are symmetric between create and apply
- [ ] no tool is another tool plus a fixed argument
- [ ] no tool description states a rule the code does not enforce
- [ ] registered in `doctor` so an outage is visible
- [ ] no secret, request body, or free-text query param reaches the request log
- [ ] tests + `validate` pass
