# The different axis: amortization over a lifetime, not optimization of a request

## Why the known playbook feels "too close to what others suggest"

Because it *is* everyone else's playbook — and that's not an accident. Prompt
caching, KV compression, speculative decoding, LLMLingua: every one of them is a
**stateless-system optimization.** They assume each request is independent and
try to make an independent request cheaper. That assumption is baked in because
the people who invented them serve **millions of strangers, once each.** Their
economics reward making the average anonymous request cheaper.

You are building the structural opposite: **one person, a million times, for
years.** The same user, the same data sources, the same ~few hundred recurring
intents ("what's on my calendar", "summarize overnight email", "add a task",
"what did I decide about X"). Optimizing each of those as if it were the first
time you'd ever seen it is leaving the entire structure of the problem on the
table.

The reframe: **stop optimizing the request. Amortize the lifetime.** The right
objective isn't "make this inference cheap" — it's "make the system learn from
its own history so recurring work stops touching the model at all." Cost per
interaction should *fall the more the assistant is used.* Every stateless
system has flat or rising marginal cost. A lifetime-amortized one has
**falling** marginal cost — that's the axis nobody serving strangers can walk.

## Three mechanisms that only exist on this axis

### 1. Procedural memory — compile the assistant's own behavior

The first time the assistant handles "summarize overnight email and flag
urgent," the LLM produces a specific *trace*: a DAG of tool calls
(gmail.search → gmail.read×N → classify → calendar.check → synthesize) plus the
decision logic between them. **Record that trace.** On the next occurrence,
replay the DAG deterministically — no model call to decide *what to do* — and
invoke the LLM only for the irreducibly novel slot (synthesizing *today's*
actual emails), or not at all if a learned output template fits.

This is not caching (caches store outputs; a new day's emails are new outputs).
It is **caching the procedure, not the product.** Analogies that are load-bearing,
not decorative:
- **Neuroscience:** a task moves from deliberate prefrontal reasoning (slow,
  expensive, flexible) to automatic basal-ganglia habit (fast, cheap, fixed)
  after repetition. The assistant should form habits.
- **JIT compilation:** an interpreter runs everything slowly; a JIT detects hot
  paths and compiles them to native code. The LLM is the interpreter; recurring
  intents are hot paths; the compiled skill is native code.

Payoff: recurring work — which for a personal assistant is *most* work — stops
requiring the 35B at all. Cost falls with use. This is the single biggest lever
and it is barely in anyone's product because stateless services can't reuse one
stranger's trace for the next stranger.

Failure mode to design against: silent staleness (the world changed but the
compiled skill didn't). Mitigation: skills carry invalidation triggers (the
coherence idea from context-economy) and periodically re-derive under the LLM to
check drift — cheap because it's occasional, not per-request.

### 2. Weight-baked identity — move the fixed prefix from context to parameters

The system prompt + tool schemas + stable core memory are a few thousand tokens
paid as prefill on every cold start, re-attended forever. If they rarely change,
**why are they context at all?** Distill them into a small LoRA overnight (the
box is idle 20h/day) so the assistant's identity, tools, and durable
preferences live in *weights*, not the window. The "context" becomes free
parameters; the largest fixed prefill disappears — not cached, *eliminated.*

This is context-distillation / gisting taken to its logical end, and it's
tractable at 35B on this hardware precisely because there's exactly one identity
to bake and a nightly window to bake it in. Re-distill as durable memory
evolves. Nobody serving many users can do this — they'd need a LoRA per user and
can't retrain nightly per person. You have one user. It's free.

### 3. Producers emit representation, not prose

You own the bridges *and* the model. A tool result today is text → tokenized →
prefilled → attended, every turn it stays in context. But the wire format
between a producer you own and a consumer you own doesn't have to be English.
For stable spans (the task list, the calendar skeleton, pinned docs) the bridge
can emit **precomputed KV** the model splices in at zero prefill — the
generalized, productized form of KV transplant, made reliable because the
producer controls exactly what it emits and can precompute it once and reuse.

## The deeper point — is "minimize tokens" even the right objective?

Probably not, on its own. The real objective is **minimize (latency + energy)
per unit of user value, over the assistant's lifetime, subject to no loss of
intelligence.** Tokens are a proxy that the stateless world adopted because
tokens are what they bill. You don't bill yourself. That frees you to optimize
the thing that actually matters — *time-to-useful-answer amortized over years* —
which sometimes means spending MORE tokens now (bake a skill, distill a LoRA,
precompute a KV) to spend near-zero later. Investment, not compression.

The unifying test for any idea: **does its marginal cost fall as the assistant
is used more?** Caching/compressing a request: no (flat). Amortizing a lifetime:
yes (falling). Optimize for the second derivative.

## What I'd build, in order

1. **Trace recorder (now, cheap, compounding).** Log every completed task's
   tool-call DAG + inputs/outputs. Zero behavior change; it just accumulates the
   raw material. Without this, none of #1 is possible later — and it's the
   "start logging before you need it" lesson from the Headroom eval.
2. **Skill compiler (the big win).** Detect recurring traces; propose a
   deterministic skill; gate promotion behind the eval harness (does the skill
   reproduce the LLM's output on held-out instances?). Human-review before a
   skill goes live — matches the approval-policy philosophy.
3. **Nightly LoRA distillation of identity (high ceiling, more effort).**
   Prove the win on the fixed system-prompt+tools prefix first; expand to
   durable memory once the pipeline exists.
4. KV-emitting bridges (#3) and the earlier prefill/generation levers as the
   *substrate* — they make the residual novel work (the part that still needs
   the model) as cheap as the known playbook allows.

The known playbook makes the model cheaper. This makes the model *unnecessary
for most of what a personal assistant repeatedly does* — and cheaper for the
rest. That's the axis worth betting on, and it's the one only you can walk.
