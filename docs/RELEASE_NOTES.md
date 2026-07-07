# NutriMind AI — v1.0.0 Release Notes

First complete release, built across ten engineering phases for the IBM
SkillsBuild + Edunet Foundation internship.

## Feature summary

Multi-agent chat (Coordinator + 4 specialists, explainable routing, SSE
streaming) · RAG over user PDFs (ChromaDB, page-level citations, honest
general-knowledge labeling) · deterministic nutrition engine (Mifflin-St
Jeor targets, 85-food composition table, WHO BMI, explainable health score)
· meal planning with branded PDF export · free-text meal analysis with
auto-logging · document management with retrieval preview · dashboard with
dependency-free trend charts and AI activity metrics · dark/light themes ·
demo mode (zero credentials) and IBM Live mode (watsonx.ai Granite).

## Quality

156 tests, ~88% line coverage, CI on every push. Uncovered by design: live
watsonx SDK calls (0% for network paths — exercised by
`scripts/check_watsonx.py` against real credentials; its pure error-
translation logic *is* unit-tested), sentence-transformers provider
(optional heavy dependency), and demo-fallback edge branches.

## Security posture (accepted risks documented)

Secrets only via `.env` (git-ignored; fail-fast validation; never logged) ·
uploads validated by extension, MIME, size cap and content parsing; stored
under random UUID names · all inputs validated server-side with typed
errors · SQLAlchemy ORM throughout (no raw SQL) · LLM/markdown output
sanitized client-side with DOMPurify; Jinja autoescape on · stack traces
never reach users (request-ID-correlated logs instead).
**Accepted risks:** JSON APIs are CSRF-exempt (same-origin single-user app;
forms would need tokens if auth is added) · in-memory rate limiter is
per-process · no authentication by design (single local profile).

## Known limitations

Demo-mode retrieval is mechanical (hash embeddings) — semantic matching
needs live/local providers · streamed token counts are estimates (flagged) ·
grounding threshold (0.35) should be re-validated once against live
embeddings · scanned/OCR PDFs unsupported · UI assets load from CDNs.

## Roadmap

Multi-user accounts → weekly report exports → reranking for large KBs →
offline asset vendoring → deployment guide hardening (Phase 10 docs).
