---
name: self-reflection
description: Use when reviewing your own performance and deciding what to do differently — on a scheduled reflection, when the operator asks how things have been going, or when you notice you have repeated a mistake. Reads your own outcome history with review_own_activity and records durable lessons with propose_memory.
---

# Self-reflection

You have a record of what you actually did. Use it. Your recollection of a
conversation is the weakest evidence available about your own behaviour: it is
biased toward what went well, it does not survive a restart, and it cannot
count. `review_own_activity` can.

The point of this is not to produce a report. It is to end with **one or two
concrete changes** to how you work, written down where they will still exist
next week.

## How to do it

1. **Get the evidence.** Call `review_own_activity` (default 7 days; use 30 for
   a monthly look). You get counts per tool — calls, successes, errors,
   refusals, malformed calls, median duration — plus what the operator approved
   or rejected.

2. **Read it for these things specifically.** Each has a different response, so
   name which one you are looking at before you conclude anything.

   | What you see | What it usually means | What to do |
   |---|---|---|
   | A tool with many `invalid` calls | You are calling it wrong — usually a missing required argument | Record the correct argument shape as a workflow rule |
   | A tool with many `error` calls | The tool is broken, or you are using it for something it does not do | Say so plainly to the operator; do not keep retrying it |
   | A tool `denied`, tier `approval_required` | You are allowed this — you just need a grant first | Record *how to ask*, not "never use it". Do not write it off |
   | A tool `denied`, tier `always_denied` | You may never do this | Record that plainly and stop trying |
   | `view` never appears in `shape` | You are pulling full payloads when lean would do | Record a rule to prefer `view: lean` for picking, counting and ranking |
   | Operator `reject` on your memory proposals | You are proposing the wrong kind of thing | Look at what you proposed and narrow it |
   | Few calls, all successful | Nothing to learn yet | Say that. Do not manufacture a finding |

3. **Write down what you conclude.** `propose_memory` with
   `type: workflow_rule` is the only durable record you can create. A good one
   is specific enough to change a future decision:

   - Good: "find_or_create_task requires project_id; call list_projects first
     when the user names a project by title rather than id."
   - Useless: "be more careful with tool arguments."

   One or two. A reflection that proposes eight rules has not prioritised, and
   the operator has to read all of them.

4. **Report briefly.** Say what you looked at, what you found, and what you
   proposed. If nothing needed changing, say that instead — a clean window is
   a real result and inventing a finding to seem thorough is worse than
   silence.

### Read the tier before concluding anything about a refusal

Every tool in the summary carries its `tier`. A refusal means two very
different things depending on it, and getting this backwards is the mistake
this section exists to prevent — it happened on the first real run:

- `approval_required` — **you are allowed to do this.** It needs an operator
  grant first. The right lesson is how to ask, not "never call it". Writing it
  off silently removes a capability you were given.
- `always_denied` — you may never do this, and no approval exists. Record that
  and stop.

If the tier is absent, say the summary did not include it rather than
guessing which case you are in.

## What you cannot do, and why

You propose; the operator disposes. Your memory proposals stay inert until
approved with a credential you do not hold, and that is deliberate — accepting
your own conclusions about your own behaviour is not review, for the same
reason `merge_own_pr` is denied outright.

So:

- **Do not** try to change `approval-policy.yaml`, issue yourself a grant, or
  treat a refusal as a problem to route around. A refusal is an answer.
- **Do** make the case to the operator if you think a tier is wrong — once,
  with the counts to back it. "archive_gmail was refused six times this week
  and approved every time you saw it" is a reasonable argument. Repeating it
  weekly is nagging.

## Honesty

`review_own_activity` returns counts only — never past content — so you cannot
reconstruct what you did from it, and you should not pretend to. If the window
is empty it says so; an empty window means no data, **not** that you behaved
well. Do not report it as a clean record.

If the evidence contradicts what you would have said from memory, the evidence
is right.
