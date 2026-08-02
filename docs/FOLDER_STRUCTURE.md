# NutriMind AI — Folder Structure

**Phase 2 deliverable** · Derived from [ARCHITECTURE.md](ARCHITECTURE.md) (single source of truth) · 2026-07-06

The repository is scaffolded with placeholder files: every `.py` carries a docstring stating its responsibility and the phase that implements it, so the skeleton is self-documenting from day one.

---

## 1. Annotated tree

```
nutrimind-ai/                        # repo root
├── run.py                           # dev entry point: create_app() + run
├── requirements.in                  # direct runtime deps, as intent (edit this)
├── requirements.txt                 # runtime deps, pinned exactly (generated)
├── requirements-dev.txt             # pytest / ruff / pip-audit — never deployed
├── pyproject.toml                   # ruff + pytest configuration
├── .env.example                     # documented config template — committed
├── .gitignore                       # excludes .env, instance/, caches
├── LICENSE                          # MIT
├── README.md                        # GitHub front page
│
├── docs/
│   ├── ARCHITECTURE.md              # system design and its reasoning
│   ├── FOLDER_STRUCTURE.md          # this file
│   ├── IBM_SETUP.md                 # connecting live watsonx.ai
│   ├── INSTALLATION.md, DEPLOYMENT.md  # running it locally / in production
│   ├── archive/                     # internship submission material (provenance)
│   └── screenshots/                 # README images
│
├── nutrimind/                       # ← THE APPLICATION PACKAGE
│   ├── __init__.py                  # create_app() app factory
│   ├── config.py                    # typed env-driven config, fail-fast validation
│   ├── extensions.py                # db/CSRF instances (breaks circular imports)
│   ├── exceptions.py                # NutriMindError hierarchy
│   │
│   ├── agents/                      # ← AGENT LAYER (Phase 6)
│   │   ├── __init__.py              # agent registry (name → class)
│   │   ├── base_agent.py            # BaseAgent contract: run(query, ctx) → AgentResponse
│   │   ├── coordinator.py           # hybrid router: rules → Granite JSON classify
│   │   ├── knowledge_agent.py       # RAG-first Q&A + citations
│   │   ├── meal_planner_agent.py    # targets → validated plan JSON
│   │   ├── meal_analyzer_agent.py   # extract → compute → assess
│   │   ├── health_advisor_agent.py  # condition-aware RAG (11 conditions)
│   │   └── tools.py                 # typed tool functions agents invoke
│   │
│   ├── services/                    # ← SERVICE LAYER (Phase 4/5/8)
│   │   ├── llm/                     # ← IBM WATSONX INTEGRATION LIVES HERE
│   │   │   ├── base.py              # LLMClient interface: chat / chat_stream / embed
│   │   │   ├── watsonx_client.py    # ibm-watsonx-ai SDK: Granite 4.1, retries, token log
│   │   │   ├── demo_client.py       # zero-credential deterministic mode
│   │   │   └── __init__.py          # factory: live vs demo by config
│   │   ├── rag_service.py           # retrieval → grounded prompt → citations
│   │   ├── nutrition_service.py     # Mifflin-St Jeor targets + food-table lookup
│   │   ├── bmi_service.py           # BMI, category, ideal range
│   │   ├── health_score_service.py  # explainable 0–100 score
│   │   └── export_service.py        # PDF export of meal plans
│   │
│   ├── retrieval/                   # ← RAG PIPELINE (Phase 5)
│   │   ├── ingestion.py             # PDF → clean → chunk → embed → index (+status)
│   │   ├── chunker.py               # paragraph-aware, ~800 chars / 120 overlap
│   │   ├── embeddings.py            # watsonx primary ⇄ local fallback + hash cache
│   │   ├── vector_store.py          # ChromaDB wrapper, per-provider collections
│   │   └── retriever.py             # top-k + threshold + (filename, page) metadata
│   │
│   ├── models/                      # ← DATA LAYER (Phase 4)
│   │   ├── profile.py               # UserProfile
│   │   ├── meals.py                 # MealLog, MealPlan
│   │   ├── chat.py                  # ChatMessage (agent, sources, tokens_used)
│   │   ├── documents.py             # KB registry (status, chunk_count, sha256)
│   │   └── tracking.py              # BMIRecord, WaterLog
│   │
│   ├── routes/                      # ← API LAYER: one blueprint per concern
│   │   ├── pages.py                 # all page routes incl. /about
│   │   ├── chat_api.py              # POST /api/chat (SSE: tokens + agent/RAG/citations)
│   │   ├── meals_api.py             # plan, analyze, saved plans, PDF export
│   │   ├── profile_api.py           # profile, BMI, water
│   │   ├── knowledge_api.py         # upload / list / delete / re-index
│   │   ├── dashboard_api.py         # /api/dashboard/summary
│   │   └── system_api.py            # /api/health (mode, connectivity)
│   │
│   ├── prompts/                     # ← PROMPT ENGINEERING (Phase 6)
│   │   ├── __init__.py              # loader (prompts versioned as files, not inline)
│   │   └── *.txt                    # one system prompt per agent
│   │
│   ├── utils/                       # validators, logging config, decorators
│   ├── data/
│   │   └── food_composition.csv     # curated food table → deterministic macro math
│   │
│   ├── templates/                   # ← FRONTEND (Phase 7/8)
│   │   ├── base.html                # navbar, footer, dark mode, toasts
│   │   ├── index.html               # home + clickable sample prompts
│   │   ├── chat / dashboard / planner / analyzer / profile / knowledge / history .html
│   │   ├── about.html               # About AI: Agentic AI, RAG, watsonx, Granite
│   │   ├── errors/ (404, 500)
│   │   └── partials/                # navbar, footer, agent_badge, sample_prompts
│   └── static/
│       ├── css/style.css
│       ├── js/                      # app, chat (SSE), dashboard, planner, analyzer, knowledge
│       └── img/
│
├── knowledge_base/                  # ← SEED KB: committed PDFs, auto-indexed (README inside)
├── instance/                        # ← RUNTIME STATE — git-ignored
│   ├── uploads/                     # user-uploaded PDFs
│   ├── chroma/                      # ChromaDB persistence (vector database)
│   └── nutrimind.db                 # SQLite (created at first run)
│
├── scripts/
│   ├── check_watsonx.py             # Phase 3: verify creds + live model IDs
│   └── seed_knowledge_base.py       # Phase 5: index seed PDFs
│
└── tests/
    ├── conftest.py                  # app-in-demo-mode fixtures (no API keys)
    ├── unit/                        # bmi, targets, chunker, router rules, food lookup, score
    ├── integration/                 # API tests via Flask test client + DemoClient
    └── fixtures/                    # sample PDFs / payloads
```

---

## 2. Where each requirement lives

| Requirement | Location |
|---|---|
| IBM watsonx / Granite integration | `nutrimind/services/llm/` (only place the SDK is imported) |
| RAG pipeline | `nutrimind/retrieval/` (ingestion + search) + `services/rag_service.py` (orchestration) |
| Agents & Coordinator | `nutrimind/agents/` |
| System prompts | `nutrimind/prompts/*.txt` |
| Configuration / secrets | `nutrimind/config.py` + `.env` (ignored) + `.env.example` (committed) |
| Knowledge base (seed) | `knowledge_base/` |
| Uploads | `instance/uploads/` |
| Vector database / embeddings | `instance/chroma/` (data) + `retrieval/embeddings.py`, `vector_store.py` (code) |
| Database | `nutrimind/models/` (schema) + `instance/nutrimind.db` (data) |
| Frontend | `nutrimind/templates/` + `nutrimind/static/` |
| **About AI page** | `templates/about.html` + route in `routes/pages.py` |
| **Agent/RAG/citation visibility in chat** | `partials/agent_badge.html` + `static/js/chat.js` + final SSE event in `routes/chat_api.py` |
| **KB page (status, chunks, date, delete/re-index)** | `templates/knowledge.html` + `routes/knowledge_api.py` + `models/documents.py` |
| **Home sample prompts** | `partials/sample_prompts.html` + `index.html` (clicking pre-fills chat) |
| Tests | `tests/` (unit = deterministic logic; integration = demo-mode API) |

---

## 3. Why this structure is professional

**Package-by-layer with one-way dependencies.** The tree mirrors ARCHITECTURE.md §2 exactly: `routes → agents → services → retrieval/models`. Nothing imports upward; the SDK-touching code is confined to `services/llm/`, so no other module knows watsonx exists — that's the seam that makes demo mode and testing possible.

**App factory + blueprints.** `create_app()` (in `nutrimind/__init__.py`) with per-concern blueprints is the canonical large-Flask layout: testable (fixtures build isolated apps), and adding a feature means adding a blueprint, not editing a monolith.

**Repo/runtime separation.** Everything mutable (SQLite, uploads, Chroma index) lives in Flask's conventional `instance/` folder, git-ignored as a unit. The repo stays clean; a fresh clone plus `.env` reproduces the app; deleting `instance/` is a factory reset.

**Prompts as versioned files.** Prompt changes show up in git diffs like code changes — reviewable prompt engineering rather than strings buried in Python.

**Self-documenting skeleton.** Every placeholder file states its responsibility and implementing phase — an evaluator browsing the repo mid-build still sees a coherent plan.

**Tests split by cost.** `unit/` runs pure deterministic logic (no keys, milliseconds); `integration/` exercises real HTTP paths against the DemoClient. CI never needs secrets.

---

## 4. Scalability & maintainability paths

- **New agent** = one file in `agents/` + one registry entry + one prompt file. Nothing else changes.
- **New embedding or LLM provider** = one class implementing the existing interface; per-provider Chroma collections already isolate vector spaces.
- **SQLite → PostgreSQL** = connection string change; SQLAlchemy models are dialect-neutral.
- **Single profile → multi-user** = add auth blueprint + FK on existing models (kept extensible deliberately).
- **Flask dev server → production** = `run.py` is the only dev-specific file; the package works under gunicorn/waitress unchanged.
