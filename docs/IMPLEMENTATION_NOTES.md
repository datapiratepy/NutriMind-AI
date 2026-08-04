# Implementation Notes

Running log of notable implementation decisions and deviations, per phase.
The architecture documents remain the source of truth; entries here explain
*how* it was realized and why, where a choice wasn't mechanical.

## Phase 3 — IBM integration

- **Default chat model is `ibm/granite-4-h-small`**, not a Granite 4.1 or 3.3
  model: official docs (2026-07-06) show it as the only current-generation
  Granite chat model on *multitenant* (token-billed) hosting, which is what
  the Lite plan can use. `scripts/check_watsonx.py` validates configured IDs
  against the live catalog, so catalog drift surfaces immediately.
- **Auto-fallback decision happens at client construction** (startup/first
  use) via a zero-token `ping()`, not per request — simpler, and `APP_MODE=live`
  intentionally never falls back (fail fast).

## Phase 4 — Backend core

- **`http_status` + `error_code` added to the exception hierarchy** so the
  Flask error handlers map domain errors to responses without a lookup table.
  Additive change; Phase-3 behavior unchanged.
- **CSRF**: `CSRFProtect` is initialized, JSON API blueprints are exempted.
  The API is same-origin JSON consumed by our own `fetch()` code; CSRF tokens
  guard server-rendered forms, which arrive with the Phase 7 UI.
- **No FKs to `user_profile`** on logs/plans/chat (per ARCHITECTURE §5
  single-profile decision). Multi-user later = add `profile_id` FKs + backfill.
- **Food table ships with 85 curated foods** (Indian + international, macros,
  fiber, iron/calcium/vitamin C, Hindi aliases). Values are approximations
  compiled from standard composition references (IFCT/USDA). Expanding the
  table is data entry, not code change. Marked as acceptable debt.
- **`/api/meals/estimate` accepts structured items only** (`[{name, quantity}]`).
  Free-text parsing ("today I ate 2 rotis") is deliberately the Meal Analyzer
  agent's job (Phase 6) — LLM extracts items, this endpoint's math prices them.
- **Health score** components return 0 with an explanatory `detail` string when
  data is missing, rather than skewing the average — an honest empty state the
  dashboard can render.
- **Chat/upload endpoints return HTTP 501** with a phase note instead of fake
  responses — honest stubs that keep the URL contract stable for Phases 5-6.
- **Water intake** is one row per calendar day, upserted and clamped to 0-30
  glasses; `POST /api/water` takes a delta (default +1) matching the UI's
  tap-to-add interaction.
- **BMI snapshots**: profile create/update with height/weight changes
  automatically appends a `BMIRecord`, feeding the dashboard trend chart
  without a separate user action.
- **Gender "other" in Mifflin-St Jeor** uses the male/female average — a
  documented convention; the equation itself is binary.

## Phase 5 — RAG pipeline

- **Chunks never cross page boundaries.** Citations stay exact (filename +
  page) at the cost of splitting paragraphs that straddle a page break —
  the right trade for guideline-style PDFs where citation accuracy matters.
- **Chunk defaults** (800 chars / 120 overlap / top-5 / threshold 0.35) are
  documented on ``RAGSettings`` in ``config.py`` and env-tunable. 800 chars ≈
  200 tokens keeps every chunk far under granite-embedding-278m's 512-token
  input window; overlap chunks may extend to size+overlap by design.
- **Three embedding providers, not two.** Besides watsonx and
  sentence-transformers, a dependency-free **hash** provider keeps the entire
  pipeline mechanically functional (and the test suite green) with zero
  credentials. It is never selected silently: resolution logs a warning and
  ``/api/system/info`` reports the active provider. Each provider owns its
  Chroma collection (``kb_watsonx`` / ``kb_local`` / ``kb_lexical``).
- **Seed documents are indexed by a script, not at boot**
  (``scripts/seed_knowledge_base.py``). Explicit beats implicit — startup stays
  fast and no watsonx tokens are consumed without an operator action. That path
  stays synchronous: a one-shot CLI should know whether it worked before it
  exits.
- **Uploads are indexed asynchronously** (Milestone 4). ``POST /api/documents``
  answers ``202 Accepted`` with the document ``pending``; the work runs on a
  bounded in-process thread pool and the UI polls until the state is terminal.
  Measured: 12.0s for a 400-page PDF on the lexical provider, and 125 sequential
  watsonx round-trips for the same document on the credentialed one. Before this,
  the polling branch in ``knowledge.js`` was unreachable — the upload response
  already said ``indexed``, so no document was ever in a non-terminal state.
- **Duplicate protection** is content-based (SHA-256), not filename-based;
  re-uploading identical bytes under a new name returns a 400 with a hint.
- **`NUTRIMIND_INSTANCE_DIR` override** added so tests isolate uploads,
  Chroma and the DB under pytest's tmp_path — nothing touches the repo's
  ``instance/`` during test runs.
- **Dev-doc verification** (ThaiRecipes.pdf, 14 pages): 28 chunks, avg 679 /
  max 919 chars; exact-text query grounded at similarity 1.0 with correct
  page citation; a semantic query under the hash provider scores 0.084 and is
  correctly labeled not-grounded — the threshold works as designed. Semantic
  retrieval quality requires the watsonx (or local) provider by nature.

## Phase 6 — Agentic AI layer

- **Agents are event generators** (`status`/`token`/`final`) so one code path
  serves SSE and blocking JSON; the chat route just serializes events.
- **Coordinator answers BMI and small talk itself** — deterministic, zero
  tokens, and a strong demo of "never call the LLM unnecessarily".
- **Analyzer extraction is mode-aware**: Granite JSON extraction live, the
  deterministic alias scanner in demo mode *and* as live-failure fallback.
  The scanner resolves overlaps longest-match-first ("veg fried rice" beats
  "rice") and understands word-numbers ("two chapatis" -> roti x2).
- **Planner numbers**: per-meal calories/protein in the plan are the model's
  estimates (labeled as such in the rendering); daily targets are always the
  deterministic ones and are shown alongside. Plans persist to `meal_plans`.
- **Meal quality score** is computed deterministically from macro shares
  (+fiber bonus) — the LLM only verbalizes it.
- **Streaming token counts are estimates** (chars/4, flagged
  `estimated: true`) because chat_stream chunks don't carry usage; zero-LLM
  paths report exact 0.
- **Routing regex gotcha fixed in review**: a trailing `\b` after an
  alternation silently blocked prefix terms ("diabet" never matched
  "diabetics"). Caught by the smoke test; rules now use explicit `\w*`.

## Phase 7 — Frontend

- See docs/FRONTEND.md for the full UI architecture. Highlights: no build
  step, ~250-line token-based design system, all AI metadata rendered
  verbatim from the API (nothing inferred client-side).
- **Real bug caught by the suite at UTC midnight**: dashboard "today" used
  local `date.today()` while logs store `utcnow` — buckets misaligned after
  00:00 UTC. All "today" logic now uses the UTC date (dashboard, WaterLog
  default, water endpoints).
- The planner/analyzer pages reuse the chat agent pipeline via
  `stream:false` calls instead of duplicating agent logic behind separate
  endpoints — one orchestration path, three UIs.

## Phase 8 — Final features

- PDF export is a pure service function (ReportLab) tested by parsing its own
  output with pypdf; the route stays 10 lines.
- Trend charts are hand-rolled HTML/CSS bars instead of Chart.js — the data
  (7 points) doesn't justify a 200 KB dependency, and empty states beat fake
  charts (requirement honored literally).
- `ai_activity` in the dashboard summary turns the agent layer into a
  measurable feature: grounded-share and token spend are now visible product
  metrics, not just logs.

## Phase 9 — Release preparation

- Version bumped to 1.0.0; ruff (pyflakes rules) clean; four unused imports
  removed; error-translation logic gained direct unit tests (+11 tests).
- Coverage: **88% overall, 156 tests**. Intentionally uncovered:
  `watsonx_client.py` network paths (34% — pure logic tested; live calls are
  exercised by `scripts/check_watsonx.py` against real credentials),
  sentence-transformers provider (optional heavy dep), live-mode branches of
  the LLM factory.
- Two development-session artifacts in the repo root could not be deleted in
  this environment (`.sync_probe.txt`, `.verify_phase3_bundle.py`) — both
  git-ignored; delete manually before or after `git init` if preferred.
- CI: GitHub Actions runs the full suite in demo mode on push/PR — no
  secrets required in CI by design.
