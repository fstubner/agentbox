# Router (retired)

This is not part of the running system. It is kept because I expect to bring
it back, and the reason it stopped is worth more than the code.

## What it did

The router sat between the assistant and a set of small local models, each
with one job. A context model pulled the relevant parts out of long input, and
a reasoning model checked an argument. Two assistant tools used it.
`triage_email` classified a message without reading its body into the
conversation, and `check_reasoning` had a second model look over the
assistant's own argument. Neither model had tools, so the worst a confused
worker could do was return a bad answer.

The idea was to spend less of the main model on work a 3B or 4B model could
do, on a machine with 16 GB of memory shared between everything.

## Why it is off

Both workers failed the agent-capability baseline on 2026-08-18, with 80
failures each. Part of that was role mismatch, which is a fixable framing
problem. The other part was not. FastContext obeyed an instruction embedded in
tool data.

That disqualifies it here regardless of how fast it is. The whole security
model of this system assumes that text arriving from mail, calendars and web
pages is untrusted and may contain instructions. A model that follows them is
the exact failure the rest of the design exists to contain.

So on 2026-09-16 I retired both workers and the router with them, and moved
production to a single model, Ornith 1.5 35B with multi-token prediction. The
vision model went at the same time, which also took `look_at_camera` with it.

## What brings it back

The small-worker role is blocked, not abandoned. It needs an evaluation that
can actually measure context extraction against a known right answer, and that
does not exist yet. Until a worker passes something like that, routing to one
saves memory at the cost of trusting a model that has already shown it cannot
be trusted with untrusted text.

When that changes:

1. Move the tool definitions from `RETIRED_TOOLS` back into `TOOLS` in
   `services/compose/agentbox-mcp/app/integrations/harness.py`.
2. Add their mappings back to `policies/approval-policy.yaml`. The test
   `test_every_live_mcp_tool_is_mapped` fails until you do.
3. Restore the health checks in `cli/agentbox` and `cli/agentbox_status.py`.
4. Install `agentbox-router.service` and the worker units again.
5. Restore `skills/worker-router` from git history and install it into the
   gateway.
