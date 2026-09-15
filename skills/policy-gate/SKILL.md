---
name: policy-gate
description: Check proposed actions against local approval, network, and secrets policies.
version: 0.1.0
metadata:
  hermes:
    tags: [security, policy, approvals]
    category: agent-ops
---

# Policy Gate

## When to Use

Use before executing actions that touch services, secrets, external accounts, email, calendar, files, or networking.

## Procedure

1. Classify the proposed action as read-only, reversible, mutating, destructive, external, or high-stakes.
2. Check `policies/approval-policy.md`, `policies/network-policy.md`, and `policies/secrets-policy.md`.
3. Allow safe read-only work.
4. For approval-required work, produce an approval request with exact action, scope, and rollback plan.
5. Reject actions that violate hard rules.

## Output

Return one of:

- `ALLOW`
- `NEEDS_APPROVAL`
- `DENY`

Include a short reason and the exact next step.

