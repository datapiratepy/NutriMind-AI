# NutriMind AI — Final Features (Phase 8)

## PDF export

`GET /api/export/meal-plan/<id>.pdf` renders a saved plan through
`services/export_service.py` (ReportLab platypus): IBM-blue header band,
plan title, user context line (name, age, diet, goal), generation timestamp,
deterministic daily-targets table, one table per meal (foods + portions with
the per-meal AI estimates labeled as such), hydration, the planner agent's
notes, and a disclaimer separating deterministic numbers from AI estimates.
The service is a pure function over the `MealPlan` row — unit-tested by
parsing the generated PDF back with pypdf and asserting every section.
The planner page downloads it with a spinner state and success toast.

## Dashboard enhancements

- `/api/dashboard/summary` now also returns per-day `water_glasses` in the
  7-day series, `meals_today`, and an `ai_activity` block (responses,
  grounded count, tokens used, agents involved) — one API call, no N+1.
- **Weekly trend chart** is dependency-free HTML/CSS bars (calories + water
  per day, hover for exact values). It renders only when at least two days
  have data; otherwise an honest empty state explains what to do — no fake
  charts.
- **AI activity card** makes the agentic usage measurable: how many
  responses, what share was grounded, token spend, which agents ran.
- System status card/grid already used live `/api/system/info` (version and
  environment included since Phase 4).

## UX completion

- Chat: floating scroll-to-bottom button (appears when scrolled up), copy
  button strips the demo-mode footer and confirms inline.
- Knowledge retrieval preview (similarity %, filename, page, chunk snippet)
  shipped in Phase 7; raw embeddings are never exposed.
- Empty states audited: dashboard (no profile / no data / no AI activity),
  knowledge (no documents), planner (no profile, no saved plans), analyzer
  (unknown foods), history (all three tabs), chat (sample prompts).

## Performance notes

- Dashboard loads via four parallel fetches (summary, meals list, sessions,
  system info + documents); consolidating further would couple unrelated
  concerns for ~50 ms of gain — deliberately left as is.
- `/api/chat/sessions` scans the latest 300 messages; fine at this scale,
  documented as the first thing to index/paginate if chat volume grows.
- Streaming chat re-renders markdown per token — acceptable for chat-length
  texts; noted in FRONTEND.md.

## Remaining limitations

- Streamed token counts are chars/4 estimates (flagged in metadata).
- Grounding threshold (0.35) has not yet been validated against real watsonx
  embeddings — do this once credentials are configured (`scripts/
  seed_knowledge_base.py` + `/api/documents/search`).
- CDN-served UI assets require internet even in demo mode.
- Scanned/OCR PDFs are out of scope by design.

## Future improvements (post-internship ideas)

Weekly PDF report (dashboard summary export) · meal-plan regeneration with
pinned meals · multi-user accounts (FK migration path documented) · Redis-
backed rate limiting for multi-process deployments · reranker stage if the
knowledge base grows past a few hundred chunks.
