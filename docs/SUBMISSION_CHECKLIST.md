# IBM SkillsBuild Submission Checklist

Requirement-to-implementation map for evaluators. ✅ = mandatory brief item,
⭐ = beyond the original brief.

## Mandatory requirements

| Brief requirement | Where it lives | Status |
|---|---|---|
| IBM Cloud Lite + watsonx.ai + Granite | `services/llm/watsonx_client.py`, official SDK, granite-4-h-small (verified multitenant/Lite-compatible), docs/IBM_SETUP.md | ✅ |
| RAG (KB, embeddings, retriever, pipelines) | `retrieval/` package + ChromaDB, per-provider collections, `RAGService` | ✅ |
| Agentic AI, multiple agents + Coordinator | `agents/`: Coordinator + Knowledge/Planner/Analyzer/Advisor, explainable routing | ✅ |
| Retrieval before generation, honest citations | threshold-gated grounding, filename+page citations, labeled general-knowledge fallback | ✅ |
| Knowledge base + PDF upload + auto indexing | /knowledge UI, upload→chunk→embed→index with status machine, seed script | ✅ |
| User profile (all listed fields) | `UserProfile` model + 3-step wizard | ✅ |
| Meal plans (meals, macros, water, Indian+intl) | Meal Planner agent + deterministic targets + planner page | ✅ |
| Meal analyzer from free text | Meal Analyzer agent + 85-food table + analyzer page | ✅ |
| Health advisor (11 conditions) | Health Advisor agent + condition-enriched retrieval | ✅ |
| BMI calculator (auto, category, ideal, tips) | `bmi_service` + profile auto-snapshots + coordinator direct answers | ✅ |
| Dashboard (profile, calories, macros, water, history, charts, health score) | /dashboard + `/api/dashboard/summary` | ✅ |
| SQLite storage (users, meals, BMI, plans, chat) | 7 SQLAlchemy models | ✅ |
| .env secrets, never hardcoded | `config.py`, `.env.example`, fail-fast validation | ✅ |
| Error handling, logging, validation | typed exceptions + JSON envelope + request-ID logs + `validators.py` | ✅ |
| Modern Bootstrap UI, dark mode, chat, navbar/footer | Phase 7 frontend, Carbon-inspired | ✅ |
| Tests + sample prompts + edge cases | 156 tests (~88% coverage), docs/MANUAL_TESTING.md, home-page sample prompts | ✅ |
| Documentation (README, install, architecture, IBM setup, deployment) | README + 12 docs/ files | ✅ |
| GitHub-ready repo (license, structure, README) | MIT license, .gitignore, CI workflow | ✅ |

## Optional enhancements delivered

| Enhancement | Notes |
|---|---|
| ⭐ Demo mode | Full app with zero credentials; deterministic engine; evaluators can run instantly |
| ⭐ Streaming SSE chat with progress statuses | documented protocol, docs/AGENTS.md §6 |
| ⭐ Explainable routing + AI workflow panel | routing reason/method + tool invocations in every response |
| ⭐ Retrieval preview on the knowledge page | similarity %, source, page — RAG demonstrable without chat |
| ⭐ Branded PDF export | ReportLab service, tested by parsing its own output |
| ⭐ Explainable health score | 5 components, each with points and reason |
| ⭐ Connectivity check script | validates credentials + model IDs against the live IBM catalog |
| ⭐ AI activity metrics | grounded-share and token spend on the dashboard |
| ⭐ CI workflow | pytest on every push/PR |

## Known scope boundaries

Single local profile (multi-user path documented) · text-based PDFs only ·
CDN-served UI assets require internet · streamed token counts are estimates.
