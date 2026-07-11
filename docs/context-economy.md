# A context-economy for Agentbox — beyond post-hoc compression

Design note. Motivated by the Headroom replay (Cursor 22.9% / Claude-Code
1.4% token reduction, ~2.8% of spend in the independent study). The point of
this doc is not to tune a compressor — it's to notice that Headroom's ceiling
comes from a structural constraint we do not share, and to design around that.

## The key asymmetry

Headroom is a **proxy**: it sees opaque traffic after it exists and can only
squeeze redundancy out of bytes already generated. It has three permanent
disadvantages that are architectural, not fixable by better compressors:

1. It can only *compress* tokens, never *prevent* them.
2. It has no domain knowledge of what a payload means or what the agent wanted.
3. It sees content only at consumption time, so re-reads look new to it.

**Agentbox owns the whole loop** — the bridges that PRODUCE tool results, the
gateway the model reads through, and a local model whose tokens are ~free
(electricity, not API bills). That ownership lets us move reduction *upstream*,
from "compress what was produced" to "don't produce the waste, and never send
the same thing twice." Cross-domain, this is the shift from **peephole
optimization** (Headroom) to **whole-program + source-level optimization**.

Empirically this matters because the Headroom study found the wins concentrated
in structured JSON tool results — exactly the payloads *our bridges emit and
therefore control*. Headroom compresses them after the fact (~34% on Cursor's
JSON); we can elide them at the source (potentially 80-95%, losslessly, because
the tokens are never generated).

## Three mechanisms, in leverage order

### 1. Projection pushdown at the bridge (databases / GraphQL)

The largest structural waste in agent traffic: a tool returns 50 objects × 30
fields when the agent needed `id, title, status`. Headroom can only compress
the excess; the bridge can decline to emit it.

Borrow **query pushdown** (SQL projection, GraphQL field selection, columnar
scan pruning): every bridge list/read endpoint accepts an optional `fields`
(or named `view`) parameter and returns only those. Default to a lean view;
let the agent opt into detail. This is:

- **Lossless** (the agent chose what it doesn't need).
- **Free** (fewer tokens generated, not compressed — zero CPU at read time).
- **Ours alone** — a proxy literally cannot do this; it doesn't control the API.

Our bridges already gesture at this (`gmail_read` truncates body to 50k,
`gmail_clean` to 12k). Formalize it: `fields`/`view` on every list endpoint,
plus a `summary` view that returns counts + top-N instead of full sets.
Estimated impact is highest of the three because it attacks token *creation*.

### 2. Content-addressed tool-result store (git / Merkle / rsync)

The Headroom study's biggest number was the **27× cache-re-read multiplier**:
agents re-read the same file/result many times per session. A proxy sees each
re-read as new. The gateway, which we own, can remember.

Borrow **content addressing** (git blobs, Nix store, rsync): the gateway hashes
every tool result. On a repeat, instead of re-injecting the content it injects
a stable handle + "unchanged since «handle»", and hands the model a
`fetch(handle)` lever to rematerialize on demand. First read full; re-reads
near-free. Compose with **delta encoding** (video keyframes / operational
transforms): when a file the agent edited is re-read, send the diff from the
version it last saw, not the whole file.

This targets the re-read multiplier directly — the part of the bill Headroom
barely touched because those are cache-read tokens to it but *fresh context
injections* to us.

### 3. Local-model level-of-detail summarization (graphics LOD / mip-maps)

We have a local model with ~free tokens. Use it as a **pre-processor** that
spends local compute to shrink what reaches an expensive tier (cloud
escalation, or just the main model's context window).

Borrow **level-of-detail** (mip-maps, progressive JPEG, hierarchical memory):
keep the full artifact in the content store, feed the model a task-aware
summary produced by the local worker (fastcontext/vibethinker already exist for
exactly this role), and expose a "zoom in" lever. Unlike Headroom's generic
prose compressor, ours is **goal-conditioned** — the summarizer knows the
current task, so it keeps what's relevant and drops what isn't. This is the one
mechanism Headroom also attempts; our edge is the free local model and the
task context.

## Two more, surfaced by an independent divergent review

A fan-out to three independent models (Claude, Codex, Gemini/Antigravity) to
stress-test this design converged — unprompted, all three — on a pattern
stronger than my mechanism #2, plus one genuinely new one. Both exploit
"owning both ends" even harder.

### 4. Stateful coherence with invalidations (MESI / cache coherence)

All three reviewers independently named this. My content-addressed store (§2)
is stateless dedup; the sharper form is a **coherent shared view**: the gateway
keeps a verified local model of the entities the agent cares about (this email
thread, this week's calendar), each fact **versioned**. Bridges then emit
*invalidations and deltas* — `meeting moved +30m`, `thread T has 1 new reply` —
never fresh snapshots. Borrowed from multi-core CPU cache coherence (MESI) and
change-data-capture / event-sourcing in databases. A stateless proxy cannot do
this: it has no synchronized view to invalidate against. This subsumes and
upgrades §2.

### 5. Predictive coding — escalate only the residual (neuroscience)

Two reviewers, same idea, and it's the most novel. The cortex sends only
*prediction error* up the hierarchy, not the raw signal. Apply it: the free
local model predicts the tool result (or the answer); we escalate to the
expensive tier (cloud, or the big local pass) **only the residual where the
cheap prediction is uncertain or the bridge's real result diverges**. If the
local model predicted the calendar was free and it was, the escalated context
carries one token (`confirmed`), not the full event list. This is speculative
execution (CPU) + rate-distortion (send the surprise, not the data). A proxy
has no model, so it cannot predict — this is only possible because we own a
free local model at the producer side.

## The governing principle the review sharpened: value-of-information

Underneath all five mechanisms is one economic gate (named by two reviewers as
"value-of-information" / "option value"): **never spend tokens on context that
cannot change the agent's next action.** Projection, coherence, and predictive
residuals are all ways of computing "what is the minimal information sufficient
for the decision at hand" — the info-theoretic *minimal sufficient statistic*,
not the full serialization. That reframes the whole system: not a compressor
bolted on, but a context economy where every token must earn its place by
potentially changing an outcome.

## Why this composes better than a proxy

Because we own the eval harness, these are **independently measurable and
independently deployable** — the opposite of Headroom's all-or-nothing proxy.
Build projection pushdown first (cheapest, biggest, lossless), measure it with
`ab_endpoint_eval.py` against real bridge traffic, keep it if it pays. Add the
content store only if re-reads dominate our sessions. Add LOD only where cloud
escalation is real. Each is a lever with its own on/off switch and its own
number — the same discipline that told us to *not* ship Headroom.

## v1 proposal (buildable now, ~1 bridge)

1. Add `view` param to `vikunja-bridge` list endpoints: `lean` (id, title,
   done, priority) vs `full`. Default `lean`.
2. Extend `ab_endpoint_eval.py` to drive a real agent task ("list my overdue
   tasks and pick the top 3") against `view=lean` vs `view=full`, measuring
   tokens AND task success (does lean lose the agent any accuracy?).
3. If lean wins on tokens without hurting success, roll the pattern to the
   gmail/memory bridges and make `lean` the default.

This is the honest, us-shaped answer: not a smarter compressor, but an
architecture where the components that generate context also take
responsibility for its cost — measured, not assumed.

## Explicitly considered and parked

- **Profile-guided field pruning** (compiler PGO): instrument which fields the
  model actually attends to across runs, then make those the default `lean`
  view automatically. This is the feedback loop that makes §1 self-tuning —
  strong second step once projection pushdown exists and we have attention/usage
  logs to learn from.
- **Semijoin retrieval** (databases): send IDs + predicates first; hydrate full
  objects only for the IDs the agent's next step proves relevant. A concrete
  implementation of demand paging; folds into §1+§2.
- **Shared dynamic dictionary** (HPACK/QUIC): both ends maintain a synchronized
  codebook of recurring schema keys/boilerplate; results reference indices.
  Real but modest win; revisit if schema boilerplate dominates after §1.
- **Approximate query processing** (databases): return counts/bounds/top-k
  first, refine only if the decision is uncertainty-sensitive. Pairs naturally
  with the value-of-information gate.
- **Rate-distortion token budgeting** (JPEG bit-allocation): allocate a context
  budget, spend per-payload by relevance. Needs a relevance model; revisit
  after projection + coherence land.
- **Whole-conversation dead-context elimination** (compiler DCE/CSE): defer;
  high value for very long sessions, higher complexity.
