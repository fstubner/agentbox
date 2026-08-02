# Thinking support & KV-cache persistence — empirically verified

> **Update 2026-08-02.** The launch flags below were changed after this was
> written. `agentbox-production-model.service` now runs
> `--reasoning auto --reasoning-format deepseek --chat-template-kwargs --jinja`,
> so the server is configured to route thinking into a separate
> `reasoning_content` field rather than leaving `<think>` inline.
>
> Two things still suppress it in practice, verified live on 2026-08-02:
>
> 1. A plain completion returns `reasoning_content: absent` — the model reasons
>    inline in `content` instead, so `enable_thinking` is not reaching the
>    template.
> 2. The gateway config sets `agent.reasoning_effort: none`.
>
> So thinking is off by configuration at two layers on a model that supports it.
> Turning it on is a real trade — reasoning tokens cost latency and context on a
> 35B model — and should be measured, not flipped blind.

All findings below were tested on the live laptop (llama.cpp Vulkan build,
2026-07), not taken from docs. Two independent questions; both resolved.

## 1. Thinking / reasoning output — NOT a Hermes limitation

Hermes reads whatever the server returns; the behavior is entirely
llama.cpp-server + model-chat-template driven. llama.cpp fully supports
structured reasoning via `--reasoning [on|off|auto]`,
`--reasoning-format deepseek` (routes thoughts to a separate
`message.reasoning_content` field instead of leaving `<think>` in content),
`--reasoning-budget N` (cap thinking tokens) and `--reasoning-budget-message`.
Whether it *works* depends on the model's GGUF chat template declaring the
reasoning structure.

**Verified templates (read straight from GGUF metadata):**

| Model | Template has `<think>` structure? | Current launch | Result |
|---|---|---|---|
| **ornith 35B (production)** | **Yes** — full `<think></think>` handling + `enable_thinking` | `--reasoning auto --reasoning-format deepseek` (changed since this was written) | server-side reasoning is now enabled; see the update below |
| VibeThinker 3B (worker) | No reasoning markers at all | none | `<think>` leaks raw into content; budget can't be enforced (parser can't find the boundary) |

### Fixes

- **Production model (ornith): one-line change.** It already supports
  structured thinking. Launch it with `--reasoning on --reasoning-format
  deepseek` (optionally `--reasoning-budget 2048`) and Hermes receives a clean
  answer with thoughts separated into `reasoning_content`. Its template also
  honors `enable_thinking`, so `/no_think`-style toggling works *when the
  template is engaged* (that's why raw `/no_think` to VibeThinker did nothing —
  VibeThinker's template doesn't implement it).
- **VibeThinker worker: already handled.** The router's `strip_thinking()`
  (`router/agentbox_router.py`) splits on `</think>` and returns the answer, so
  the reason/check role is already clean downstream. To get *structured*
  `reasoning_content` + budget enforcement from it too, supply a corrected
  `--chat-template` that wraps output in the markers the deepseek parser wants.

**Bottom line:** thinking works; we had it switched off on the one model whose
template supports it properly.

## 2. Slot KV-cache persistence — works, but conflicts with flash-attention

The headline lever for the ~7-minute cold 200K prefill: `--slot-save-path` +
`POST /slots/{id}?action=save|restore` serializes a slot's KV cache to disk and
restores it across a *server restart* (survives reboot).

**Verified end-to-end** (3B model, 3,513-token prompt, process fully
restarted between save and restore):

| Phase | prompt_n | prompt_ms | cache_n |
|---|---:|---:|---:|
| Cold (first ever) | 3513 | 2158 | 0 |
| After restart + restore | **1** | **14** | **3512** |

Restore read a 129 MB slot file in 21 ms and skipped the entire prefill —
**~150x** on this prompt. Extrapolated, a restored 200K slot boots in low
single-digit seconds vs ~7 minutes cold.

### The catch (also verified): flash-attention breaks slot save

- `--flash-attn on` (current production config): save returns **`n_saved=0`** —
  silent no-op. Slot persistence is effectively OFF on production right now.
- `--flash-attn off`: save captures all tokens correctly.
- `--flash-attn off` + `--cache-type-k/v q8_0`: **llama-bench produced no
  result** — quantized KV cache does not work without flash-attn on this build.
  So turning FA off also forces **f16 KV**, which ~doubles KV memory at 200K.

### The tradeoff is not yet decided — needs a long-context bench on ornith

Short-context bench (3B, 2048 ctx) showed FA-off is roughly neutral there
(pp 1648 vs 1711 t/s; tg 87 vs 83 t/s). **This does NOT generalize to 35B at
200K** — flash-attention's speed and memory advantage grows with context, and
f16 KV at 200K may not even fit alongside the ~30 GB model on 46 GB unified
memory. Before any production change:

1. Bench ornith at 64K/128K/200K with FA-on/q8_0 vs FA-off/f16 — measure
   prefill t/s, generation t/s, and peak memory (does 200K f16 KV fit?).
2. If FA-off is too costly at long context, slot-persist is unusable for the
   main model as-is. Options then: use it only for a FA-off secondary/worker,
   or for shorter pinned-prefix contexts, or wait for llama.cpp to support
   slot save under flash-attn.

### If the tradeoff clears: highest-value use

Persist the *stable prefix* slot (system prompt + tool definitions + pinned
long-context docs) so a reboot or model-swap doesn't re-prefill it. Pairs
directly with the prefix-stability discipline in `context-economy.md`.
