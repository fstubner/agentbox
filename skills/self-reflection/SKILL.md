---
name: self-reflection
description: Use when reviewing your own performance and deciding what to do differently: on a scheduled reflection, when the operator asks how things have been going, or when you notice you have repeated a mistake. Reads your own outcome history with review_own_activity and records durable lessons with propose_memory.
---

# Self-reflection

`review_own_activity` gives you a record of what you did. Use it instead of
your recollection of conversations. Recollection is biased toward what went
well, does not survive a restart, and cannot count. `review_own_activity`
returns counts.

The goal is **at most one or two concrete changes** to how you work, recorded
where they persist across sessions. On a quiet day, end with none.

## How to do it

1. **Check what you already know first.** Call `search_memories` and
   `list_memory_proposals` before anything else.

   This runs daily over a rolling seven-day window, so most of the activity
   you review is the same as yesterday's. If you skip this step you will
   propose the same lesson every day. The operator may stop reading a review
   queue full of duplicates, and that queue is your only route to durable
   memory.

   A lesson that is already stored or already in the queue is **done**. Do not
   restate it, sharpen it, or propose a near-identical variant.

2. **Get the evidence.** Call `review_own_activity` (default 7 days; use 30 for
   a monthly look). You get counts per tool (calls, successes, errors,
   refusals, malformed calls, median duration, and each tool's policy tier)
   plus what the operator approved or rejected.

3. **Look for the patterns below.** Each needs a different response, so name
   the pattern before you conclude anything.

   | What you see | What it usually means | What to do |
   |---|---|---|
   | A tool with many `invalid` calls | You are calling it wrong, usually with a missing required argument | Record the correct argument shape as a workflow rule |
   | A tool with many `error` calls | The tool is broken, or you are using it for something it does not do | Say so plainly to the operator; do not keep retrying it |
   | A tool `denied`, tier `approval_required` | You are allowed this, but you need a grant first | Record *how to ask* for a grant. Do not record "never use it" |
   | A tool `denied`, tier `always_denied` | You may never do this | Record that plainly and stop trying |
   | `view` never appears in `shape` | You are pulling full payloads when lean would do | Record a rule to prefer `view: lean` for picking, counting and ranking |
   | Operator `reject` on your memory proposals | You are proposing the wrong kind of thing | Look at what you proposed and narrow it |
   | Few calls, all successful | Nothing to learn yet | Say that. Do not invent a finding |

4. **Write down what you conclude.** `propose_memory` with
   `type: workflow_rule` is the only durable record you can create. A good one
   is specific enough to change a future decision:

   - Good: "find_or_create_task requires project_id; call list_projects first
     when the user names a project by title rather than id."
   - Useless: "be more careful with tool arguments."

   Propose one or two, and on most days **zero**. Because this runs daily over
   a rolling window, a new lesson must be something yesterday's run did not
   already cover. If you propose many rules, you have not prioritised, and the
   operator has to read all of them.

5. **Report briefly.** Say what you looked at, what you found, and what you
   proposed. If nothing needed changing, say that instead. A window with
   nothing to change is a valid result. Do not invent a finding to appear
   thorough.

### Read the tier before concluding anything about a refusal

Every tool in the summary carries its `tier`. A refusal means different
things depending on the tier. Do not confuse them.

- `approval_required`: **you are allowed to do this.** It needs an operator
  grant first. Record how to ask for the grant. If you record "never call it",
  you lose a capability you were given.
- `always_denied`: you may never do this, and no approval exists. Record that
  and stop.

If the tier is absent, say the summary did not include it. Do not guess which
case applies.

## What you cannot do, and why

You propose memories and the operator approves them. Your proposals have no
effect until they are approved with a credential you do not hold. If you could
approve your own conclusions about your own behaviour, there would be no
review. `merge_own_pr` is always denied for the same reason.

- **Do not** try to change `approval-policy.yaml`, issue yourself a grant, or
  work around a refusal.
- **Do** tell the operator if you think a tier is wrong. Say it once, with the
  counts that support it, for example "set_home_climate was refused six
  times this week and approved every time you saw it". Do not repeat it every
  day.

## Reporting accurately

`review_own_activity` returns counts only, never past content, so you cannot
reconstruct what you did from it. Do not claim to. If the window is empty it
says so. An empty window means no data. It does **not** mean you behaved
well. Do not report it as a clean record.

If the evidence contradicts your memory, trust the evidence.
