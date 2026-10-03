# Speedup frontier — divergent mechanisms, not just "compress harder"

Goal: raise tokens/sec, cut tokens/interaction, and cut prefill latency —
**without lowering model intelligence.** The last clause is the discipline:
every idea here is either lossless, or quality-gated so the smart path is
untouched. Organized by the resource each attacks, because they are different
physics.

Legend: [now] shippable on our stack this month · [fork] needs a llama.cpp/
gateway patch · [research] promising, unproven here.

---

## A. PREFILL is compute-bound — and we recompute a cacheable function

Prefill recomputes `KV = f(weights, tokens)`, deterministic, then discards it.
Every idea here is "stop recomputing what we already computed."

1. **Speculative (idle-time) prefill.** [now] The box is idle ~20h/day; the
   assistant's near future is predictable (fixed system prompt + tool schemas;
   the morning-briefing context; overnight emails). A cron job pre-bakes those
   KV slots to disk overnight (we proved restore = ms). The user's "cold"
   morning turn is served from last night's compute. *We own the producers, so
   we know what to pre-bake — a cloud API cannot.* Converts idle watts into
   latency, like pumped-hydro stores off-peak power.
2. **KV transplant / position-shifted reuse.** [fork/research] Prefix caching
   only reuses *identical* prefixes. But RoPE is a rotation and llama.cpp's
   context-shift already re-rotates KV to new positions. Research (PromptCache,
   CacheBlend, EPIC) computes a document's KV *once* and splices it in at any
   offset with a small seam-recompute. Upgrade the content-addressed store from
   "cache the text" to "cache the attention." The shifting machinery already
   exists in-tree — this is the highest-leverage fork target.
3. **Agent fork().** [now-ish] A sub-agent re-prefills what it shares with its
   parent. Slot state serializes (proven) — save parent slot, restore into the
   child's slot: it inherits the warm shared context free. `fork()` for agents.
4. **Prefill-cost-aware context assembly.** [now] The gateway orders context so
   the *stable* prefix (system, tools, pinned docs) is byte-identical across
   turns and the volatile tail is last — maximizing the cache-hit prefix. This
   is prefix-stability turned into an active layout optimizer, not just a rule.

## B. GENERATION is memory-bandwidth-bound — read less, or accept more per read

Every token streams the active weights + KV through ~256 GB/s. Two honest
levers: read fewer bytes/token, or verify more tokens per read.

5. **Speculative decoding — already on, tune it.** [now] ornith runs
   `--spec-type draft-mtp --spec-draft-n-max 1`. MTP is lossless (the big model
   verifies every drafted token). `n-max 1` is conservative; sweeping it to
   2-4, and trying `ngram`/prompt-lookup drafts for input-grounded turns
   (summarizing a doc that's already in context), can lift tok/s with **zero**
   quality change. Cheap eval, pure upside. *First thing to measure.*
6. **Self-speculation / layer-skip drafting.** [research] Draft with a subset
   of the model's own layers, verify with the full stack (LayerSkip, Medusa,
   EAGLE). Lossless by construction (full model verifies). If a future
   llama.cpp build exposes it for this arch, it's free tok/s.
7. **Heterogeneous expert residency (MoE-aware).** [fork/research] ornith is a
   35B *MoE* with ~3B active/token. On unified memory we could keep hot experts
   in the fastest tier and cold experts in slower RAM, or even predict the next
   token's experts and prefetch — MoE routing is somewhat predictable per
   domain. Bandwidth saved = tok/s gained, no quality loss (same experts, just
   staged smarter).
8. **Bigger batch = free throughput when concurrent.** [now] Voice + chat +
   background eval hitting the model together: `--parallel` batching amortizes
   the weight read across requests. Doesn't speed a lone turn, but raises whole-
   system tok/s at zero quality cost.

## C. TOKENS-PER-INTERACTION is an information problem — carry less entropy

The window holds far more than the decision needs. Losslessly or quality-gated.

9. **Elastic intelligence: route the *turn*, not just the worker.** [now] Not
   every turn needs 35B. "What's my next meeting" → the 3B worker or a rule.
   Deciding *which brain* per turn (escalate-on-failure, our prior design) cuts
   both prefill and generation on the easy majority, and — key — *raises*
   effective intelligence by reserving the big model for hard turns. The one
   lever that improves speed AND quality simultaneously.
10. **Thinking as a controllable dial, not on/off.** [now — testing today]
    ornith's template supports per-request `enable_thinking`. Let Hermes spend
    reasoning tokens only where value-of-information is high (a refactor, a
    conflicting-constraint plan) and skip them on lookups. Thinking is the most
    expensive tokens per answer; spending them selectively is pure efficiency
    with *higher* quality where it matters.
11. **Semantic dedup of the transcript.** [now] Across a long session the same
    fact recurs (the user's timezone, a file's contents). A gateway pass keeps
    the first occurrence, replaces later verbatim repeats with a short
    reference. Lossless to meaning; shrinks the window.
12. **Output-shape contracts.** [now] Much generation is wasted on prose
    scaffolding around a small decision. Constrained/grammar-based decoding
    (llama.cpp supports GBNF) forces terse structured output where a tool call
    is the real payload — fewer generated tokens, *and* more reliable parsing.

## D. Cross-cutting — free intelligence, not just free speed

13. **Test-time compute where it pays.** [now] The inverse of "go faster":
    sometimes spend *more* tokens (self-consistency, a verify pass) but only on
    the few turns where being wrong is expensive — funded by the tokens saved
    on the easy majority (#9). Net: same budget, higher accuracy. Speed and
    intelligence aren't a single axis; owning the loop lets us move them
    independently per turn.
14. **The eval harness is the moat.** [now] Every one of these has a quality
    knob. The reason we can push aggressively *without* dumbing the assistant is
    that a separate evaluation harness, kept private, measures the
    intelligence cost of each. Speculation
    and slot-reuse are provably lossless (assert identical output); LLMLingua,
    quant, layer-skip are quality-gated by eval. The discipline is what makes
    "faster without dumber" a measurement, not a hope.

---

## Priority (value × low-risk × lossless-first)

1. **Sweep MTP `n-max` + prompt-lookup drafts (#5)** — lossless tok/s, one eval.
2. **Per-turn brain routing + thinking dial (#9, #10)** — cuts the easy
   majority, sharpens the hard minority. Testing #10 on ornith today.
3. **Idle-time speculative prefill (#1)** — kills cold-start for predictable
   contexts; needs the flash-attn/slot question settled.
4. **Prefix-stability layout + semantic dedup (#4, #11)** — steady window
   savings, no infra.
5. **KV transplant (#2)** — biggest ceiling, biggest effort; the fork project
   to scope next.

The unifying thesis: a compression proxy operates on the *token stream* and
can only ever do #11-ish. Owning the model, the KV, the router, and the
producers lets us attack compute (A), bandwidth (B), and information (C)
*independently* — and move the speed/intelligence dials in opposite directions
per turn when that's what the task wants. That is the advantage no external
tool can copy.
