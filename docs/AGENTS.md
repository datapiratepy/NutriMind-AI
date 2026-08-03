# NutriMind AI — Agent Architecture

Phase 6 deliverable · companion to [ARCHITECTURE.md](ARCHITECTURE.md) §3

## 1. Overview

Every chat message flows: `POST /api/chat` → **Coordinator** (routes) →
one specialist agent (executes, using **tools**) → SSE events → persisted
`ChatMessage` with agent, grounded flag and citations. Nothing about the
agentic behavior is hidden: routing reasons, tool invocations, grounding
status and citations are all first-class response metadata.

## 2. Routing strategy (hybrid, token-frugal)

**Stage 1 — deterministic rules (0 tokens).** An ordered regex table in
`agents/coordinator.py` classifies obvious intents. First match wins. Two
intents are answered by the Coordinator itself with **zero LLM calls**:
small talk (canned) and BMI checks (deterministic math via `compute_bmi`).

**Stage 2 — Granite JSON classification (~60 output tokens).** Only messages
no rule matched are sent to Granite with `prompts/coordinator.txt`, which
must return `{"agent", "intent", "reason"}`. Invalid output → safe default
(Knowledge Agent). In demo mode this stage is skipped (default applies).

Every decision is a `RoutingDecision {intent, agent, reason, method}`,
included in the `routing` SSE event and in `meta.routing` — the UI shows
*why* an agent was chosen, making the multi-agent design visible.

## 3. Agents and their tools

| Agent | Tools used | Numbers come from | LLM's job |
|---|---|---|---|
| Coordinator | `compute_bmi` | bmi_service | classification only (ambiguous cases) |
| Knowledge | `retrieve_knowledge` | — | grounded answer w/ [n] citations |
| Meal Planner | `calculate_targets` | Mifflin-St Jeor | compose plan JSON honoring targets |
| Meal Analyzer | `lookup_foods`, `log_meal` | curated food table | extract items; assess computed numbers |
| Health Advisor | `retrieve_knowledge`, `calculate_targets` | both | condition-aware guidance w/ safety framing |

Tools live in `agents/tools.py` behind a request-scoped `Toolbox` that
records every invocation; the list appears in `meta.tools_used` — evaluators
can see "lookup_foods: 4 matched, 445 kcal total" on each response.

## 4. Grounding policy

`retrieve_knowledge` applies the similarity threshold (config, default 0.35).
Above threshold → the agent must ground its answer in the numbered passages
and the response carries `grounded: true` + `citations: [{filename, page}]`.
Below threshold → the answer is generated from general knowledge and is
**visibly labeled** both in the text ("*General knowledge — not from your
documents*") and in metadata (`response_source: "general_knowledge"`).
Citations only ever come from the retriever — the prompts forbid invented
sources, and the metadata is assembled from `RetrievalResult`, not from
model output.

## 5. Prompt design

One file per agent under `nutrimind/prompts/` (versioned, reviewable in git
diffs), each structured as: ROLE → RESPONSIBILITIES → ALLOWED TOOLS →
FORBIDDEN → OUTPUT FORMAT → SAFETY → REASONING STRATEGY. Structured-output
prompts (coordinator, planner, analyzer extraction) demand *JSON only* and
are parsed by `parse_llm_json` (fence-tolerant) with **one retry carrying
the validation error back to the model**, then a friendly failure.

## 6. Streaming protocol (SSE)

`POST /api/chat` (default `stream: true`) emits, in order:

```
event: status    {"message": "Analyzing your request…"}
event: routing   {"intent", "agent", "reason", "method"}
event: status    {"message": "Searching the knowledge base…"}   (agent-specific)
event: token     {"text": "…"}                                   (many)
event: final     {"message_id", "session_id", "text", "meta"}
event: error     {"code", "message", "hint"?, "request_id"}      (on failure)
```

Status events arrive *before* generation so the UI is never silently
waiting. `stream: false` returns one JSON object with the same `meta`.

### meta contract (used verbatim by the Phase-7 UI)

`agent · grounded · response_source · embedding_provider · llm_mode ·
retrieved_chunks · citations[] · tools_used[] · generation_ms · tokens
{total, estimated} · routing {intent, agent, reason, method}` — plus
agent-specific extras (`plan_id`, `targets`, `totals`, `quality_score`,
`extraction`).

## 7. Demo mode behavior

All five agents work with zero credentials: routing uses rules+default;
the planner receives a valid canned plan JSON (marker-matched by the demo
backend); the analyzer switches to the deterministic alias scanner
(`naive_extract_items`) — which also serves as the live-mode fallback when
Granite extraction fails; knowledge/advisor answer **from the retrieved
passages themselves** when a turn is grounded, and from scripted content
correctly labeled as general knowledge. Every demo answer is marked.

## 8. Token budget summary

Per message: routing 0 tokens (rules) or ~60 (ambiguous, live only);
knowledge/advisor ≈ prompt + passages + ≤600-700 output; planner ≤900
output + ≤200 retry; analyzer ≤200 extraction + ≤300 assessment; BMI and
small talk always 0. History forwarded only to conversational agents
(6 turns max).
