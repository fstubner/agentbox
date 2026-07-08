# Security

This is a personal-assistant platform designed to run on a single trusted
operator's own hardware, not as a multi-tenant service.

- Report vulnerabilities via a private GitHub security advisory rather than
  a public issue.
- The assistant is expected to have narrow, policy-gated tool access only
  (see `policies/approval-policy.yaml`) — it should never receive raw shell
  or raw credentials. Reports of a path that grants either are treated as
  high severity.
- Services in `services/compose/` are expected to bind to localhost/LAN only
  (enforced by `cli/agentbox validate`); a service that binds `0.0.0.0` or
  exposes itself to the public internet without explicit operator action is
  a bug.
