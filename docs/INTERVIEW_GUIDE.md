# Interview Guide — design decisions, ready answers

**Why Flask (not Django/FastAPI)?** Right-sized: server-rendered pages +
JSON APIs + SSE with minimal ceremony. App-factory + blueprints give
structure and testability; Django's admin/ORM stack and FastAPI's
async-first design solve problems this project doesn't have. SSE streaming
works cleanly with Flask's generator responses.

**Why Bootstrap + vanilla JS (no React)?** No build step: clone → run. The
UI is mostly server-rendered with ~1,400 lines of focused JS across seven
modules. A SPA would add tooling, state management and hydration complexity
without adding evaluator-visible value.

**Why IBM Granite / which model?** Program requirement — but the specific
choice was researched: `granite-4-h-small` is the current-generation Granite
chat model on *multitenant* (token-billed) hosting, the only kind the Lite
plan can use; most Granite models are deploy-on-demand (dedicated, paid).
The ID is config-driven and `check_watsonx.py` validates it against the live
catalog, so catalog drift can't break the app silently.

**Why ChromaDB (not FAISS)?** Built-in persistence and metadata filtering.
FAISS is a similarity index; persistence, metadata and deletion bookkeeping
are DIY. Chroma gave page-level citation metadata and per-document deletes
for free. Trade-off accepted: slightly heavier dependency.

**Why deterministic calculations?** LLMs are unreliable at arithmetic and
nutrition numbers are the product's core trust surface. BMR/TDEE (Mifflin-St
Jeor), food math (curated composition table), BMI, and the health score are
pure Python — reproducible and unit-tested. The LLM writes language *around*
verified numbers. This also cut token usage dramatically.

**Why multiple agents instead of one prompt?** Separation of concerns for
prompts: each agent has a narrow role, its own constraints and its own
tools, which makes behavior predictable and testable. It also enables the
explainability story — routing reasons and per-agent tooling are visible in
every response. One mega-prompt can't offer that.

**Why RAG?** Nutrition guidance should be attributable. RAG grounds answers
in known documents with page-level citations and — critically — a similarity
threshold below which the app *refuses to pretend*: answers get an explicit
"general knowledge" label. Retrieval quality is also demonstrable standalone
via the knowledge page's search preview.

**Why not LangChain?** ~300 lines of custom pipeline bought full control,
fewer dependencies, and debuggability: the chunker, retriever and grounding
policy are plain Python I can defend line-by-line. Frameworks abstract
exactly the parts an evaluator wants to see understood.

**How does routing work?** Two stages. Ordered regex rules classify obvious
intents at zero token cost (including BMI and small talk, which the
coordinator answers itself deterministically). Only unmatched messages go to
Granite with a strict JSON classification prompt (~60 output tokens);
invalid output falls back to a safe default. Every decision ships with
intent, agent, reason and method in the response metadata.

**How does Demo Mode work?** One `LLMClient` interface, two implementations.
`DemoClient` returns curated deterministic responses (including valid plan
JSON via prompt-marker matching), simulates streaming, and generates
hash-based pseudo-embeddings so the entire RAG pipeline stays mechanically
functional. Selection: explicit (`APP_MODE=demo`), or automatic when
credentials are missing or a zero-token startup ping fails. `live` never
silently falls back — fail fast.

**Biggest engineering challenges?**
1. *Lite-plan token budget* → hybrid router, embedding cache, capped
   max-tokens, deterministic short-circuits; worst-case message ≈ 1,200
   tokens.
2. *Honest grounding* → threshold + labeled fallback + citations built from
   retriever metadata, never model output.
3. *A timezone bug caught by CI at UTC midnight* — local `date.today()`
   bucketing vs UTC-stored timestamps; fixed by standardizing on UTC dates.
   Good example of tests catching real-world drift.
4. *IBM catalog drift* — docs research revealed most Granite models aren't
   Lite-usable; solved with config-driven IDs + live catalog validation.

**Future improvements?** Multi-user auth (FK migration path documented),
retrieval reranking at scale, weekly PDF reports, vendored UI assets for
offline demos, Redis-backed rate limiting for multi-process deployment.
