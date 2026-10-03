# Security

Agentbox runs on one household's own hardware. It is not a multi-tenant
service.

Please report vulnerabilities through a private GitHub security advisory
rather than a public issue.

These are treated as high severity.

- **A path to a shell or a raw credential.** The assistant should only reach
  narrow, policy-gated tools (`policies/approval-policy.yaml`) and should never
  get a shell or an upstream credential.
- **Acting as another person.** Identity comes from the session token. Any way
  for a tool argument or tool output to change who the assistant acts for is a
  bug.
- **Exposure beyond the LAN.** Services in `services/compose/` bind to
  localhost or the LAN, and `cli/agentbox validate` enforces it. A service that
  binds `0.0.0.0` or reaches the internet without the operator choosing that is
  a bug.
