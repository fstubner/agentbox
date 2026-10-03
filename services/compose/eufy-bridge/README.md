# eufy-bridge (not wired up)

A bridge for Eufy cameras. It is built and tested (`tests/test_eufy_bridge.py`)
but the assistant cannot reach it. No integration in agentbox-mcp exposes its
routes, and this household never deployed it.

The design is worth keeping. Eufy has no public API, so every route to these
cameras is reverse-engineered. The usual one is a Home Assistant add-on, which
would put unreviewed code inside the most privileged container on the box.
Instead `eufy-security-ws` runs in its own container on a private network with
no host ports, and this bridge is the only thing that talks to it. The bridge
has two routes, a device list and a single snapshot from an allowlisted camera,
and nothing that moves, arms or records.

To use it, add an integration in `services/compose/agentbox-mcp/app/integrations/`
with tools mapped in `policies/approval-policy.yaml`, then deploy both.
