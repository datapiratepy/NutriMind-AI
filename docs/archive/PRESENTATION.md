# IBM SkillsBuild Presentation — Slide Content

Structured to match the **official AICTE/Edunet template** in `_reference/`
(15 slides). Where the template's sample assumes LangFlow, slides 6-7 adapt
to this project's engineered agent layer — address it directly (slide 6
notes) since it's a strength, not a deviation. Font per template: Arial,
28 pt headings / 20 pt content. *Italics = speaker notes.*

---

## Slide 1 — Title

**IBM University Engagement Project Submission**
"Exploring the Power of Agentic AI with IBM Granite"

**NutriMind AI — AI-Powered Nutrition Assistant**
Harsh Kamat · IBM SkillsBuild + Edunet Foundation Internship · 2026

*15 seconds: name, program, one sentence — "a multi-agent nutrition
assistant on IBM watsonx.ai with retrieval-augmented, citation-backed
answers."*

## Slide 2 — Domain & Title

- **Domain:** Healthcare / Nutrition
- **Project title:** NutriMind AI — AI-Powered Nutrition Assistant using
  IBM watsonx.ai, Granite and RAG

## Slide 3 — Problem Statement

Good nutrition is the foundation of health, but individuals struggle to get
guidance that is **personalized** (age, diet preference, medical conditions,
location), **trustworthy** (not hallucinated), and **actionable** (real meal
plans, real numbers). Generic chatbots answer confidently without evidence
and are unreliable at the arithmetic nutrition depends on.

*Anchor on the official brief: "a Personalized Nutrition Agent that
collects user preferences such as age, food preferences, health and medical
conditions, and location." Then add the two failure modes of naive
chatbots: hallucinated facts and wrong math.*

## Slide 4 — Proposed Solution

A **multi-agent AI web application** where:
- a **Coordinator** routes each request to a specialist agent (Knowledge,
  Meal Planner, Meal Analyzer, Health Advisor) and explains the decision;
- factual answers use **RAG** over nutrition PDFs with page-level citations
  — or are explicitly labeled "general knowledge";
- **every number is deterministic Python** (Mifflin-St Jeor targets, curated
  85-food composition table, WHO BMI, explainable health score) — IBM
  Granite writes the language around verified numbers.

## Slide 5 — Technology Used

- **IBM watsonx.ai** (Cloud Lite) — `ibm/granite-4-h-small` chat +
  `ibm/granite-embedding-278m-multilingual`, official Python SDK
- **ChromaDB** — persistent vector store (per-provider collections)
- **Flask + SQLAlchemy + SQLite** — backend, 7 data models
- **Bootstrap 5 + vanilla JS** — streaming SSE chat UI, no build step
- **pypdf, ReportLab, pytest** — ingestion, PDF export, 156 tests

*One line on why granite-4-h-small: the current-generation Granite chat
model available on multitenant (token-billed) hosting — verified against
IBM's live catalog, which the project checks programmatically.*

## Slide 6 — Agent Components Used *(template: "Langflow components")*

The template's sample uses LangFlow; NutriMind implements the same concepts
as engineered, testable Python components:

1. **Chat Input** → Flask SSE endpoint `/api/chat`
2. **Coordinator** → hybrid router (rules first — 0 tokens; Granite JSON
   classification only for ambiguous requests)
3. **Agents** → 4 specialists, each with a versioned system prompt and a
   typed toolbox (retriever, nutrition service, food table, BMI)
4. **IBM Granite** → generation + classification via `ModelInference`
5. **Knowledge store** → ChromaDB retriever with similarity threshold
6. **Chat Output** → streamed tokens + metadata (agent, grounding,
   citations, tools, tokens)

*Say explicitly: "I chose code over a visual builder so every routing rule
and prompt is version-controlled and unit-tested — 156 tests prove it."*

## Slide 7 — Agent Workflow *(template: "Langflow workflow")*

User message → Coordinator (intent + explanation) → Specialist agent →
Tools (retrieve / calculate / lookup / log) → IBM Granite → Response with
agent badge, grounded indicator, citations, tool list, token count.

*Use the screenshot of the chat "How this was answered" panel here — the
workflow visualization is generated from live metadata.*

## Slide 8 — Architecture Blueprint

Layered architecture diagram (use docs/ARCHITECTURE_DIAGRAMS.md §1 or a
screenshot of the About AI page): Presentation → Flask API → Agent layer →
Services (LLM client with demo fallback, RAG, deterministic nutrition) →
ChromaDB + SQLite.

## Slide 9 — Role of Agentic AI

Agentic AI makes NutriMind autonomous *and* accountable:
- **decides** which specialist handles each request — and says why;
- **uses tools** instead of guessing: retrieval for facts, Python for math;
- **degrades honestly**: below the similarity threshold it labels answers
  "general knowledge"; without credentials it switches to a deterministic
  demo engine.

*Contrast with a single-prompt chatbot: no routing, no tools, no evidence
trail.*

## Slide 10 — Token Usage

- Rule-routed requests (BMI, small talk, clear intents): **0 routing tokens**
- Ambiguous requests: ~60-token Granite JSON classification
- Typical grounded answer: ≤ 900 tokens end-to-end; worst case ≈ 1,200
- Connectivity check: < 100 tokens; embeddings cached by content hash
- **[Insert your watsonx.ai usage screenshot + total after live testing]**

*The template shows before/after prompt totals — screenshot your watsonx
resource usage page after the live demo run and add the real number.*

## Slide 11 — Novelty & Uniqueness

1. **Explainable routing** — every response shows agent, reason, method.
2. **Honest grounding** — citations with filename + page, or an explicit
   general-knowledge label; evidence is never invented.
3. **Deterministic nutrition engine** — LLM never computes calories.
4. **Zero-credential demo mode** — same architecture, runs anywhere.
5. **Engineering depth** — 156 tests / 88% coverage, CI, 15 documentation
   files, live IBM catalog validation.

## Slide 12 — GitHub Link

`https://github.com/<your-username>/nutrimind-ai`
Public repo · README with screenshots · MIT license · CI badge

*Template asks for a public repo with README enabled — verify before
submitting.*

## Slide 13 — Future Scope

Wearable/health-device integration · multi-user accounts (migration path
prepared) · weekly PDF nutrition reports · retrieval reranking for larger
knowledge bases · regional-language support via Granite multilingual models.

## Slide 14 — Certificates Earned

[Your Credly / IBM SkillsBuild certificate]

## Slide 15 — Certificates Earned

[Your IBM BOB certificate]

---

### Optional appendix slides (if time allows)

- **Testing & Quality:** 156 automated tests, ~88% coverage, CI on every
  push, demo-mode test suite needs no secrets.
- **Results:** all 18 brief requirements implemented + 9 enhancements
  (docs/SUBMISSION_CHECKLIST.md).
- **Challenges:** Lite-plan token budget → hybrid router; IBM catalog drift
  → live validation; UTC-midnight date bug caught by tests.
