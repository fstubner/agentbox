# Contributing

Agentbox is built for one household on one machine. Contributions that keep it
useful for that are welcome.

- Run `cli/agentbox validate` and the test suite (`pytest tests/`) before
  opening a PR.
- Keep secrets out of the repository. Bridges hold credentials and the
  assistant never does. New services should follow the pattern in
  `services/compose/*`, where each `.env` value is either plain or a secret
  reference resolved at deploy time.
- A new capability that changes state needs an entry in
  `policies/approval-policy.yaml`. Without one it defaults to
  `approval_required`, which is safe, but be explicit anyway.
- Match the existing code style. CI runs `ruff check`.

## Example names

Use `alex` and `sam` for household members in tests, docs and config samples.
The tests need two distinct people to check that one person's memories, mail
and bridges are not another's, and a third invented name tends to collide with
a fixture in a way that looks like a real failure. For someone who is neither,
such as a newcomer or a person who cannot sign in, use `newcomer` or `taylor`.

They are names rather than role words because `admin` and `member` are already
role constants in `cli/agentbox-portal`. An identity called `member` would make
a test about one person read like a claim about the whole role.
