# The actual big-brain lever: entropy-class routing of context

Verified against prior art (soft-token-prior-art.md, frontier-critique.md).
The reframe survived contact with the literature; two of my earlier ideas did
not. Here is the honest, sharp version.

## What the literature actually says (so we stop chasing illusions)

Latent / soft-token compression is real but the headline numbers are a trap:

| Method | Claimed | HONEST production ratio | What it destroys |
|---|---|---|---|
| xRAG | 100–250x | works only on single-fact QA | exact detail, multi-hop |
| 500xCompressor | 500x | retains 62–74% capability | everything at 1 token |
| ICAE | 4–8x | **4–8x** | exact entity recall |
| AutoCompressor | 13–41x | detail erased past ~6x | numbers, timestamps, lists |
| LLMLingua-2 | 2–5x | **2–5x** (we measured ~2x usable) | syntax, negations |

**The universal failure mode is identical across every method: they destroy
exactly what an agent cannot lose** — tool-call argument fidelity (`user_id` vs
`userid`), exact recall (IDs, paths, hex), and multi-hop reasoning. And they all
need to fine-tune the model, which risks the intelligence you refuse to trade.

So the naive "compress the context into latent vectors" dream is a dead end for
an *agent*. Anyone who tells you 100x lossless is selling a QA benchmark.

## The insight everyone misses — and why only you can exploit it

Every method above applies **one compression to the whole context.** That's the
mistake. Context is not homogeneous — it's a mix of two entropy classes:

- **Fidelity-critical (high-entropy, must be exact):** tool schemas, IDs, the
  user's literal words, code, numbers, the current task. Lossy compression here
  = broken tool calls. Correct method: **lossless** (prompt cache — don't
  recompute) or **don't compress at all.**
- **Semantic-only (low-entropy, gist is enough):** background doc bodies, old
  conversation history, reference prose, "context for flavor." Losing exact
  wording costs nothing. Correct method: **aggressive lossy** (gist/soft tokens,
  4–8x) or drop-and-retrieve-on-demand.

A stateless cloud service **cannot tell these apart** — it receives an opaque
token blob and must guess (that's why LLMLingua guesses per-token and sometimes
deletes the "not"). **You own the producers.** The bridge that emits a tool
result *knows* the task-ID is fidelity-critical and the email body is semantic.
It can **tag each span with its entropy class at the source**, and the gateway
routes each span to the compression matched to it:

```
bridge emits span + entropy tag
        │
   ┌────┴─────────────────────────┐
   ▼                              ▼
 fidelity-critical            semantic-only
 → lossless prompt-cache      → soft-token gist (4-8x, fine-tuned)
   or verbatim                  or drop + retrieve-on-demand
 → 0% quality loss            → lossy where lossy is free
```

This is the thing no proxy, no cloud API, no generic tool can do: **compression
routing driven by producer knowledge of what matters.** It gets most of the
soft-token win (on the bulk semantic tokens) with none of the soft-token risk
(fidelity spans never touch the lossy path). Token reduction *and* no
intelligence loss — because we stopped applying one hammer to two nails.

## Ranked plan (honest about effort and ceiling)

1. **Entropy tagging at bridges (now, cheap).** Extend the `view=lean` work:
   every emitted span carries `fidelity: critical|semantic`. Zero model change.
   This is the enabling substrate for everything below and it's a small edit to
   code we already own.
2. **Lossless-first routing (now).** Fidelity spans → stable prefix → prompt
   cache (0% loss, already in llama.cpp). Semantic spans → droppable tail,
   fetched on demand from the content-addressed store. This alone cuts window
   pressure with zero fine-tuning and zero risk. Measure it with the eval
   harness before going further.
3. **Soft-token gist for semantic spans only (medium, fine-tune).** If step 2's
   measurement shows semantic prose still dominates the window, train an ICAE/
   AutoCompressor-style adapter (open code exists: `princeton-nlp/AutoCompressors`,
   Microsoft ICAE) — but apply it *only* to fidelity=semantic spans, gated by
   the eval harness proving no regression on tool-call and recall fixtures.
   Realistic 4–8x on that subset, which is a real token cut where it's safe.
4. **Procedural compilation (high ceiling, the System-1/2 split).** The critique
   is right that this is the deepest lever AND that naive DAG-replay is brittle
   (schema drift, fuzzy intent). So do it as **LLM-supervised**: compile a
   recurring intent to a skill, but keep a cheap novelty-detector that falls
   back to the 35B when inputs leave the skill's validated envelope. Gate every
   promotion behind the eval harness. This is where "cost falls with use" lives.

## Ideas the verification KILLED (so we don't waste time)

- **Weight-baking identity into a nightly LoRA (my earlier #2):** prior art
  (Anthropic context-distillation; Sakana Text-to-LoRA) confirms it works, but
  the critique's failure analysis is decisive for us: catastrophic forgetting +
  hallucinated tool args + nightly compute lockup + staleness. A system prompt
  in context is a *hard constraint*; baked into weights it becomes a soft
  statistical nudge the model can ignore. **Not worth the intelligence risk.**
- **Producers emit raw KV (my earlier #3):** RoPE re-rotation is
  bandwidth-bound and on this hardware can cost more than just prefilling; and
  isolated-chunk KV never attended to the query, breaking co-reference. Viable
  only for a truly-static pinned prefix, not general tool output.
- **Neural-IPC latent grafting (the critique's own steelman):** intellectually
  the boldest — bypass the model's first ~12 layers by injecting tool data as
  hidden states via a tiny per-tool encoder (~37% prefill saving on those
  tokens). But it's the same soft-token failure mode wearing a fancier hat:
  cross-attention discontinuity + per-tool encoders to train + brittle to any
  model swap. A research bet, not a build. Park it.

## The one-line thesis

Don't compress the context — **classify it, and route each class to the only
method that's lossless-where-it-must-be and lossy-where-it's-free.** Owning the
producers is what makes the classification trustworthy, and that is the edge no
stateless system can copy.
