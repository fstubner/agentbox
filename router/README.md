# Router (retired)

This is not part of the running system. It is kept because I expect to use it
again. The evaluation results below explain why it was stopped.

## What it did

The router sat between the assistant and a set of small local models, each
with one job. A context model pulled the relevant parts out of long input, and
a reasoning model checked an argument. Two assistant tools used it.
`triage_email` classified a message without reading its body into the
conversation, and `check_reasoning` had a second model look over the
assistant's own argument. Neither model had tools, so a worker that
misbehaved could only return a wrong answer.

The goal was to move work that a 3B or 4B model can do off the main model, on
a machine where all services share 16 GB of memory.

## Why it is off

Both workers failed the agent-capability baseline on 2026-08-18, with 80
failures each. Some failures came from role mismatch, which better framing can
fix. The others came from FastContext following an instruction embedded in
tool data.

A model that follows such instructions cannot be used here, however fast it
is. The security model of this system assumes that text from mail, calendars
and web pages is untrusted and may contain instructions. The rest of the
design exists to contain a model that follows them.

On 2026-09-16 I retired both workers and the router, and moved production to a
single model, Ornith 1.5 35B with multi-token prediction. I retired the vision
model at the same time, which removed the `look_at_camera` tool.

## What brings it back

The small-worker role is on hold. It needs an evaluation that measures
context extraction against a known correct answer, and that evaluation does
not exist yet. Until a worker passes one, routing to it saves memory but
depends on a model that has followed instructions found in untrusted text.

When that changes:

1. Move the tool definitions from `RETIRED_TOOLS` back into `TOOLS` in
   `services/compose/agentbox-mcp/app/integrations/harness.py`.
2. Add their mappings back to `policies/approval-policy.yaml`. The test
   `test_every_live_mcp_tool_is_mapped` fails until you do.
3. Restore the health checks in `ENDPOINTS` in `cli/agentbox_doctor.py` and
   `cli/agentbox_status.py`.
4. Install `agentbox-router.service` and the worker units again.
5. Restore `skills/worker-router` from git history and install it into the
   gateway.
