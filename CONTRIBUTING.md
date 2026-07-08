# Contributing

This started as a single-operator personal project; contributions that keep
it useful for that use case are welcome.

- Run `cli/agentbox validate` and the test suite (`pytest tests/`) before
  opening a PR.
- Keep secrets out of the repo — bridges hold credentials, never the
  assistant or the router. New services should follow the `op://` +
  plain-`.env` fallback pattern used by existing `services/compose/*`.
- New mutating capabilities need an entry in `policies/approval-policy.yaml`
  (default tier is `approval_required` if you do not add one, so omitting an
  entry is safe but you should still be explicit).
- Match existing code style; CI runs `ruff check`.
