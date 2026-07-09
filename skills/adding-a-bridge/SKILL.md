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
   body limits, health, and the server loop. Editing it per-bridge is how
   divergence bugs start.

3. **Write `app/bridge.py`** — the only file you author:
   - Read two secrets from env: the upstream credential and `*_BRIDGE_TOKEN`.
   - Write one function per route: `def route(handler, body) -> (status, payload)`.
     Raise `BridgeError(status, msg)` for expected failures; return only the
     safe subset of the upstream response.
   - Subclass `BridgeHandler`, set `bridge_token` and `routes`.

4. **Rename the env vars** in `compose.yaml` and `*.env.example` to match your
   service. Keep the `:?` guards so a missing secret fails the deploy loudly.
   Keep the host port bound to `127.0.0.1` (or an explicit LAN IP).

5. **Add the token to the deny-by-default policy if the bridge can mutate
   state.** A read-only bridge is `approval_required` at most; a bridge that
   sends/deletes/pays belongs in `always_denied` unless explicitly gated.
   Update `policies/approval-policy.yaml`.

6. **Register the bridge with the assistant** as an MCP lever (a matching
   `*-mcp` service), not by giving the assistant the bridge URL directly, if
   the assistant needs to call it.

7. **Verify before deploy:**
   - `python3 -m pytest services/compose/<name>-bridge` (copy the base test).
   - `cli/agentbox validate` (checks bindings, resource limits, non-root).
   - `curl -s localhost:<port>/health` → 200; the same route without a token → 401.

8. **Deploy:** `cli/agentbox deploy <name>-bridge`.

## Checklist (all enforced by base/template/validate — confirm you didn't undo them)

- [ ] `bridge_base.py` unchanged
- [ ] upstream credential and bridge token are distinct env vars
- [ ] every mutating route validates its input and returns only allowlisted fields
- [ ] host port bound to loopback/LAN, never `0.0.0.0` on the host
- [ ] policy entry added for any state-changing capability
- [ ] tests + `validate` pass
