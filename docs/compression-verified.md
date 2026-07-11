# Compression techniques — empirically verified on our hardware

Tested on the laptop (CPU) against our own session corpus. The pattern that
keeps repeating: verify, because the marketing latency numbers are GPU numbers.

## LLMLingua-2 — real ~55% cut, but latency limits where it's usable

Ran the official `llmlingua` package (LLMLingua-2, XLM-RoBERTa encoder) on real
payloads from our corpus, rate=0.5 (target: keep ~50%):

| payload tokens | compressed | reduction | latency (CPU) |
|---:|---:|---:|---:|
| 122 | 56 | 54% | 0.4s |
| 202 | 92 | 54% | 0.4s |
| 425 | 187 | 56% | 0.7s |
| 1047 | 479 | 54% | 1.0s |
| 1775 | 831 | 53% | 2.7s |

Two things stand out versus Headroom (which got **1.4%** on our Claude-Code-like
traffic):

1. **LLMLingua-2 cuts ~55% on EVERY payload type** — prose, code, JSON alike —
   because it's a learned token-importance classifier, not a structural JSON
   compressor. On our mixed code/prose traffic that's ~40x more reduction than
   Headroom.
2. **But latency scales with size: ~1.5 ms/token on this CPU.** That's the
   binding constraint.

### What this rules in and out

- **RULED OUT — compressing the cold-prefill context.** At 1.5 ms/tok, a 200K
  context takes ~5 minutes just to *compress* — the same order as the prefill
  you're trying to avoid. Net loss.
- **RULED IN — compressing large, prose-y, reused tool results before they
  enter context.** A 1775-token email/doc/search dump → 831 tokens for 2.7s
  one-time. If that result is re-read across many turns (each re-read is
  cache-cheap but still occupies scarce window), halving it once is worth 2.7s.
- **QUALITY GATE NEEDED.** The 53-56% cut is uniform, which is a red flag for
  code/JSON — dropping ~half of code tokens will break syntax. LLMLingua-2 is
  known to degrade structured content. Restrict it to prose-kind tool results
  (the bridge already tags `kind`); never compress code/JSON/diffs.
- Model load is 2.3s warm / 65s cold — keep it resident if used at all.

Maturity: usable library, mature. Cold cost is real on CPU-only.

## Synthesis — the compression stack that actually fits us

Ordered by value/effort, drawing on everything verified (this doc,
`thinking-and-kv-cache.md`, `context-economy.md`, the Headroom report):

1. **Prefix stability + prompt caching (free, do first).** llama.cpp already
   prefix-caches; the win is *discipline* so we hit it: pin system prompt +
   sorted tool defs, append-only history, no per-turn timestamps in the prefix.
   Zero new infra, protects every warm turn. → `context-economy.md`.
2. **Projection/`view=lean` at bridges (cheap, high value).** Stop tool results
   being bloated in the first place — the source-side lever. This is the v1 we
   scoped. Beats any downstream compressor because the tokens never exist.
3. **Content-addressed tool-result store (cheap).** Large results stored by
   hash; context gets a handle + short summary; model fetches full on demand.
   Caps window bloat without lossy compression. Standard agent-CLI pattern.
4. **Slot KV persistence (high value IF the flash-attn tradeoff clears).**
   Skips the ~7-min cold prefill after restart. Blocked on: needs FA off (→ f16
   KV, ~2x memory at 200K) — must bench ornith at long context first.
   → `thinking-and-kv-cache.md`.
5. **LLMLingua-2 on prose tool results only (narrow, optional).** ~55% on
   emails/docs/search snippets, gated to `kind=prose`, only when the result is
   large and reused. Not for code/JSON, not for the main context.
6. **NOT Headroom** (1.4% on our traffic) and **NOT semantic response caching**
   (too risky for tool args). Both measured/assessed and rejected.

The through-line: because we own the producers, **preventing tokens (1-3) beats
compressing them (5)**, and **caching computation (4) beats re-doing it**. A
generic proxy sees none of these levers — which is why building our own,
source-aware mechanism wins.
