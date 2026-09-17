# Agentbox Remote Tunnel (Household Tier)

Automated outbound tunnel connecting the local Agentbox appliance to the Agentbox Cloud Relay.

## Overview
- **Egress-only handshake:** Initiates outbound connection to `relay.agentbox.app:51820` / `relay.agentbox.app:443`.
- **Zero Port-Forwarding:** Traverses carrier-grade NAT (CGNAT) and dynamic residential IP blocks.
- **Dedicated Subdomain:** Bound cryptographically to `https://<family>.agentbox.app` with automated wildcard TLS.
- **Local Proxying:** Securely exposes local port `8771` (Agentbox Portal) to authenticated family sessions.
