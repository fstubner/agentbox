---
name: context-packer
description: Build compact, source-grounded context packets for long-running tasks.
version: 0.1.0
metadata:
  hermes:
    tags: [context, retrieval, summarization]
    category: agent-ops
---

# Context Packer

## When to Use

Use this skill before long-running research, coding, planning, or memory reconciliation tasks.

## Procedure

1. Gather only the files, notes, messages, or records needed for the task.
2. Keep source references attached to each fact.
3. Remove repeated text, boilerplate, and stale context.
4. Produce a packet with:
   - objective
   - relevant facts
   - constraints
   - open questions
   - source list
5. Do not invent facts to fill gaps.

## Output Shape

```text
Objective:
Facts:
Constraints:
Open Questions:
Sources:
```

## Verification

The packet is good only if another agent can act on it without rereading the full source corpus.

