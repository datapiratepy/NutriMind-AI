# Resume Content

## One-line description

Multi-agent AI nutrition assistant on IBM watsonx.ai (Granite) with
retrieval-augmented, citation-backed answers and a deterministic nutrition
engine — 156 tests, ~88% coverage.

## Two-line description

Built a Flask web application where a coordinator agent routes requests to
four specialized AI agents, grounds factual answers in user-uploaded PDFs
via ChromaDB with page-level citations, and computes all nutrition math
deterministically. IBM Granite via the official watsonx.ai SDK, streaming
SSE chat, PDF export, CI, 156 automated tests.

## Resume bullet points (pick 3-4)

- Architected and built **NutriMind AI**, a multi-agent nutrition assistant
  (Flask, IBM watsonx.ai Granite, ChromaDB): a coordinator agent routes
  requests to 4 specialist agents with **explainable routing** shown in
  every response.
- Implemented a **RAG pipeline** from first principles (no LangChain):
  page-bounded chunking, hybrid embedding providers, similarity-threshold
  grounding with **page-level citations** and an honest general-knowledge
  fallback label.
- Enforced **LLM-free arithmetic**: Mifflin-St Jeor targets, a curated
  85-food composition table, and an explainable 0-100 health score computed
  in pure Python — the LLM only writes language around verified numbers.
- Shipped production-quality engineering: **156 automated tests (~88%
  coverage)**, GitHub Actions CI, streaming SSE chat protocol, branded
  ReportLab PDF export, typed error handling with request-ID logging, and a
  zero-credential demo mode.
- Optimized for IBM Cloud Lite's token budget: **0-token rule-based routing**
  for common intents, capped generation, embedding caching — worst-case
  ≈1,200 tokens per message.

## Technologies

Python · Flask · IBM watsonx.ai · IBM Granite · RAG · ChromaDB · SQLAlchemy
· SQLite · SSE · Bootstrap 5 · JavaScript · ReportLab · pypdf · pytest ·
GitHub Actions

## Quantifiable metrics

156 tests · ~88% coverage · 5 AI agents · 36 HTTP endpoints (27 API + 9
pages) · 7 database models · 47 Python modules (~4,100 lines) + 1,100 test
lines · 16 templates · 7 JS modules · 15 documentation files · 85-food
curated nutrition table · < 100 tokens for full IBM connectivity validation
