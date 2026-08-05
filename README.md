# NutriMind AI

**Multi-user AI Nutrition Assistant · IBM watsonx.ai · Granite · RAG · Specialist Agents with Deterministic Routing**

[![ci](https://github.com/datapiratepy/NutriMind-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/datapiratepy/NutriMind-AI/actions/workflows/ci.yml)
![release](https://img.shields.io/badge/release-v2.0.0-success)
![python](https://img.shields.io/badge/python-3.13-blue)
![flask](https://img.shields.io/badge/Flask-3.x-000000)
![tests](https://img.shields.io/badge/tests-472-brightgreen)
![coverage](https://img.shields.io/badge/coverage-91%25-brightgreen)
![docker](https://img.shields.io/badge/Docker-Compose%20%2B%20Caddy-2496ED)
![postgres](https://img.shields.io/badge/PostgreSQL-compatible-336791)
![watsonx](https://img.shields.io/badge/IBM-watsonx.ai-0f62fe)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

NutriMind AI is a **production-ready, multi-user AI nutrition assistant** built
with Flask, IBM watsonx.ai integration, retrieval-augmented generation, secure
authentication, asynchronous document processing, and production deployment
infrastructure.

A Coordinator routes every request to one of four specialist agents; factual
answers are grounded in each user's own nutrition PDFs with page-level citations;
and every number the app shows — calories, BMI, macro targets, health score — is
computed deterministically in Python, never by the LLM.

The project began as an IBM SkillsBuild + Edunet Foundation internship
deliverable and has since been taken through five engineering milestones to a
tagged production release. Each milestone was audited before implementation,
verified at runtime afterwards, and shipped with its own regression tests — the
history is in [Release history](#release-history).

> **On the word "agent."** Each of the four specialists is an agent in the sense
> that it encapsulates a role, its own version-controlled system prompt, and its own
> tools, and streams a typed event protocol. Routing between them is **deterministic
> rules first**, with a Granite JSON classification step only for messages no rule
> matches. There is **no autonomous planning loop** — no ReAct-style
> reason/act/observe cycle, no self-directed tool selection, no inter-agent
> negotiation. The accurate description is *specialist agents with deterministic
> routing and an LLM classification fallback*. See [Routing](#routing-what-selects-an-agent)
> below and [docs/AGENTS.md](docs/AGENTS.md).

> **Runs with zero setup.** Without IBM credentials the app starts in a fully
> functional demo mode; with an IBM Cloud Lite account it uses live Granite
> models through the official watsonx.ai SDK.

## Screenshots

| Chat — streamed, with routing and citation metadata | Dashboard — trends and explainable health score | Knowledge base — upload, async indexing, retrieval preview |
|---|---|---|
| ![Chat](docs/screenshots/chat.png) | ![Dashboard](docs/screenshots/dashboard.png) | ![Knowledge](docs/screenshots/knowledge.png) |

## Key features

**Agentic AI** — a Coordinator classifies each request (deterministic rules
first, Granite JSON classification only for ambiguous cases) and routes it to
the Knowledge, Meal Planner, Meal Analyzer or Health Advisor agent. Every
response shows which agent answered, the routing reason, and the exact tools
that ran.

**RAG with honest grounding** — upload nutrition PDFs; they are chunked
(page-bounded, overlapping: 800 chars with 120-char overlap), embedded, and indexed
in ChromaDB (cosine, top-k 5). Indexing runs in the background — the upload
returns immediately and the page reports progress — because a 400-page document
takes 12s on the built-in provider and far longer against watsonx. Answers above
the provider's similarity threshold cite sources by filename and page; anything
else is visibly labeled *general knowledge*. No invented evidence, ever.

**Deterministic nutrition engine** — Mifflin-St Jeor BMR/TDEE, macro targets,
an 85-food curated composition table (Indian + international, Hindi aliases,
micronutrients), WHO BMI categories, and an explainable 0-100 health score
whose five components are shown with their reasons.

**Multi-user by construction** — email/password accounts with registration,
login, logout, password reset and remember-me. Every profile, meal, plan, chat
message and document belongs to exactly one account, enforced by foreign keys
*and* by an ownership filter on vector retrieval. **Data belonging to one user is
never visible to another user** — including in search results, where the vector
store has no `user_id` column and retrieval is therefore restricted to document
ids resolved from the database first. A dedicated tenancy suite exists to prove
it, in both directions.

**Asynchronous document processing** — uploads return `202 Accepted`
immediately and are indexed on a bounded background pool, so **document uploads
never block HTTP requests**. Documents move through an honest
`pending → processing → indexed | failed` lifecycle that the UI polls. Work
interrupted by a restart is recovered at startup rather than left stuck, and a
runtime lock detects an unsupported multi-process deployment — the cause of a
subtle retrieval-consistency problem found during audit, where a second worker
reported correct chunk counts and silently found nothing. That is now prevented
and explained rather than merely documented.

**Account lifecycle** — export everything as JSON, or delete the account
outright. Deletion removes relational rows, uploaded files **and** vector
embeddings, then ends the session; a partial delete that orphaned vectors would
be invisible, so it is asserted against the vector store directly.

**Production database foundation** — the schema is owned by versioned Alembic
migrations, never by `create_all()`. Databases created before migrations existed
are adopted safely, upgrades are an explicit deployment step, and a schema-drift
check is a release gate. SQLite by default, **PostgreSQL-compatible**, with the
migration path exercised against a real PostgreSQL server in CI.

**Product-grade UX** — streaming chat (SSE) with live progress, an AI workflow
panel built from response metadata, dark/light themes, dashboard with
dependency-free trend charts, drag-and-drop document management with retrieval
preview, wizard profile with live BMI, per-user timezones so daily totals reset
at the user's midnight rather than UTC, paginated history, and branded PDF export
of meal plans.

## Architecture

```
Caddy (TLS · HTTP/3 · SSE-safe proxy)
        │
Waitress — ONE process, 8 threads  (wsgi:app)
        │
Flask API (blueprints · auth · CSRF · validation · JSON errors · request IDs
           · security headers + CSP nonce)
        │
Coordinator Agent ──► Knowledge │ Meal Planner │ Meal Analyzer │ Health Advisor
        │                    │            │             │            │
        │              Retriever   Nutrition Svc   Food Table   Retriever+Targets
        │                    │            │             │            │
LLMClient (watsonx.ai Granite ⇄ deterministic demo engine)
        │
        ├── background job pool ──► ingestion (extract · chunk · embed · index)
        │
ChromaDB (per-provider collections) · SQLite/PostgreSQL · curated food CSV
```

**One process, many threads — deliberately.** ChromaDB caches its vector index
reader per process, so a second worker reports the correct chunk count and then
finds nothing when it searches, with no error logged. Threads are safe; worker
processes are not. The application takes an advisory lock on the vector store
directory at startup and explains this if a second process appears. The
measurements behind the decision are in
[DEPLOYMENT.md](docs/DEPLOYMENT.md#production-process-model).

Rendered diagrams (system, routing, RAG, database, request & chat flows):
[ARCHITECTURE_DIAGRAMS.md](docs/ARCHITECTURE_DIAGRAMS.md)

Deep dives: [ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[AGENTS.md](docs/AGENTS.md) · [FRONTEND.md](docs/FRONTEND.md) ·
[IMPLEMENTATION_NOTES.md](docs/IMPLEMENTATION_NOTES.md)

## Getting started

```bash
git clone https://github.com/datapiratepy/NutriMind-AI.git
cd NutriMind-AI

# Create a virtual environment
python -m venv .venv

# Activate it
# Windows
.venv\Scripts\activate

# Linux/macOS
# source .venv/bin/activate

pip install -r requirements.txt

python run.py
```
Open **http://127.0.0.1:5000** in your browser.

By default, NutriMind AI starts in **Demo Mode**, providing deterministic AI responses and a fully functional RAG pipeline so that every feature can be explored without IBM credentials.

To enable **IBM Live Mode** with Granite foundation models, follow the instructions in **docs/IBM_SETUP.md**.

### Routing — what selects an agent

`Coordinator.route()` (`nutrimind/agents/coordinator.py`) has exactly two stages:

1. **Deterministic rules — 0 tokens.** An ordered table of six regex rules; first
   match wins. Two of them (`Small Talk`, `BMI Check`) the Coordinator answers
   *itself*, with no LLM call at all — BMI is arithmetic, so it is computed in
   Python.
2. **Granite JSON classification — only if no rule matched.** The message goes to
   Granite with a strict-JSON prompt at `temperature=0.0`, `max_tokens=80`; the
   returned agent id is validated against the allow-list. Any failure falls back to
   the default agent — routing never raises.

**In demo mode stage 2 is skipped entirely** (the demo backend cannot classify
arbitrary text), so unmatched messages fall through to the Knowledge Agent with
`method: "default"` and a reason string that says so.

Every decision carries `intent`, `agent`, `reason` and `method`
(`rules` | `llm` | `default`), returned in the API response and rendered in the UI
badge — so the routing path is always visible, never inferred.

### Embedding provider matrix

Three providers, resolved by `EMBEDDINGS_PROVIDER` (`nutrimind/retrieval/embeddings.py`):

| Provider | Model | Dim | Semantic? | Selected when |
|---|---|---|---|---|
| `watsonx` | `ibm/granite-embedding-278m-multilingual` | 768 | yes | `EMBEDDINGS_PROVIDER=watsonx`, or `auto` **with** IBM credentials |
| `local` | `sentence-transformers/all-MiniLM-L6-v2` | 384 | yes | `EMBEDDINGS_PROVIDER=local`, or `auto` with no credentials **and** `sentence-transformers` installed |
| `lexical` | hashing trick over word tokens (signed, sublinear TF) | 2048 | keyword only | `auto`, no credentials, `sentence-transformers` **not** installed |

`sentence-transformers` is **commented out** in `requirements.txt` (it pulls
PyTorch), so a default install with no IBM credentials resolves to **`lexical`**.

**Be clear about what the lexical provider is:** it is keyword search expressed as
vectors — the hashing trick over word tokens, with signed buckets so collisions
cancel rather than accumulate. It finds passages that *share words* with your
question, so uploading a cookbook and asking for "pad thai" works. It cannot
connect "aubergine" to "eggplant"; that needs a real embedding model. It exists so
the full pipeline — ingest → chunk → embed → index → search → threshold → cite —
is genuinely usable with zero credentials and zero heavy dependencies. It is never
selected silently: resolution logs a warning, `/api/system/info` reports it, every
chat response carries `meta.embedding_provider`, and the search UI says plainly
when a miss is due to keyword-only matching.

**Similarity thresholds belong to a vector space, not to the application.** A short
query against a long chunk cannot reach the same cosine in a sparse lexical space
as in a dense semantic one, so each provider supplies its own default (semantic
0.35, lexical 0.12, both overridable with `RAG_SIMILARITY_THRESHOLD`). Applying one
number to both is how "retrieval returns nothing" happens.

Each provider gets **its own Chroma collection** (`kb_watsonx`, `kb_local`,
`kb_lexical`) because the vector spaces are not comparable — a 384-dim hash vector must
never be searched against 768-dim Granite vectors.

**What does *not* change between modes:** chunking, the vector store, the
similarity threshold, citation construction, the SSE event protocol, and every
nutrition calculation (BMR/TDEE, macros, BMI, health score) — those are pure Python
and identical in both modes.

### IBM Live mode

1. Follow **[docs/IBM_SETUP.md](docs/IBM_SETUP.md)** (IBM Cloud Lite account,
   watsonx.ai project, IAM API key — ~20 minutes, no credit card).
2. `copy .env.example .env` and fill in your credentials.
3. Verify: `python scripts/check_watsonx.py` (color-coded connectivity check,
   validates your model IDs against the live catalog).
4. Seed the knowledge base: `python scripts/seed_knowledge_base.py` after
   placing nutrition PDFs in `knowledge_base/`.

### Environment variables

All configuration is environment-driven (see [.env.example](.env.example)):

- **IBM** — `WATSONX_APIKEY`, `WATSONX_PROJECT_ID`, `WATSONX_URL`,
  `WATSONX_MODEL_ID`, `WATSONX_EMBEDDING_MODEL_ID`, `APP_MODE` (live/demo/auto),
  `EMBEDDINGS_PROVIDER`
- **Application** — `FLASK_SECRET_KEY`, `FLASK_DEBUG`, `SESSION_DAYS`,
  `DATABASE_URI`, `LOG_LEVEL`
- **Uploads and jobs** — `MAX_UPLOAD_MB`, `MAX_USER_STORAGE_MB`,
  `INGEST_WORKERS`, `INGEST_QUEUE_LIMIT`
- **Deployment** — `TRUSTED_PROXY_HOPS` (must be `1` behind Caddy, or the rate
  limiter counts every visitor as one), `NUTRIMIND_DOMAIN`, `NUTRIMIND_TLS_EMAIL`
- **RAG tuning** — `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP`, `RAG_TOP_K`,
  `RAG_SIMILARITY_THRESHOLD`

Secrets live only in `.env` (git-ignored) or the platform's secret store.

## Security

Full policy — including what is *not* implemented — in
[SECURITY.md](SECURITY.md). Data handling is in [PRIVACY.md](PRIVACY.md).

**Authentication.** Passwords hashed with scrypt; minimum length 10 with no
composition rules, following NIST SP 800-63B. Session identity embeds the
password hash, so changing a password invalidates every existing session
*including* remember-me tokens. Sign-in and password reset never reveal whether
an address has an account. Reset links are signed, single-use and expire in an
hour.

**Per-user isolation.** `user_id` foreign keys on every owned table, all queries
routed through one chokepoint, and access control that is default-deny per
blueprint — so a new endpoint is protected before anyone remembers to protect it.
Another account's record reports "does not exist" rather than 403, so ids cannot
be enumerated.

**CSRF protection.** Enforced on every state-changing endpoint; the browser
client sends the token as `X-CSRFToken`. Only the unauthenticated read-only
health probe is exempt. CSRF stays *enabled* in the test suite — disabling it is
the usual shortcut and silently voids every CSRF assertion.

**Secure cookies.** Both credential cookies carry `Secure` (whenever debug is
off), `HttpOnly` and `SameSite=Lax`, with matching lifetimes. Flask-Login keeps
remember-me on separate settings that do not inherit from the session cookie;
leaving them unset gives a 365-day cookie with no `Secure` flag, so they are set
explicitly and asserted from real `Set-Cookie` headers.

**Security headers.** Content-Security-Policy, HSTS (HTTPS only),
`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
`Permissions-Policy`, `Cross-Origin-Opener-Policy` and
`Cross-Origin-Resource-Policy`.

**Content Security Policy.** Nonce-based: `script-src` does **not** allow
`'unsafe-inline'`, because that would permit both the app's own inline script and
every injected one, making the header decorative. A per-request nonce is issued
instead, and a static test fails the build if a template gains an inline script
without one — that failure is otherwise invisible, since the page renders and the
script simply never runs.

**Prompt injection boundaries.** Retrieved passages are fenced as untrusted data
and the fence markers are neutralised inside passage text, so an uploaded
document cannot close its own fence. The agent prompts state that fenced content
is data and cannot issue instructions. This is mitigation, not a solution, and
`SECURITY.md` says so plainly.

**DOM sanitisation.** Model output is rendered through DOMPurify before it
reaches `innerHTML`; Jinja autoescaping is on everywhere and no template uses
`|safe`. `scripts/vendor_cdn_assets.py` copies the third-party assets locally,
after which the CSP allows no external origin at all — run it before exposing an
instance publicly.

**Upload validation.** Files are validated by **content**, not by filename or
`Content-Type` — both of which the caller supplies. Header, trailer and a
structural parse must all pass. Anything rejected is deleted immediately, and a
document that parses but can never be indexed is discarded too, while a
*transient* failure keeps the file so Re-index can recover it.

**Rate limiting and abuse resistance.** Authentication, chat and upload
endpoints are rate limited. The tracking table is bounded, evicts idle clients,
and groups IPv6 callers by /64 so address rotation cannot multiply identities. A
per-account storage quota bounds total uploaded bytes, which one request limit
alone does not.

**Privacy-aware logging.** User messages, search queries and email addresses are
never written to logs. They are replaced by a length and a per-process salted
fingerprint — enough to correlate a retry or match routing against retrieval,
with nothing recoverable. Passwords, tokens and secrets are never logged, and log
volume is bounded in the application and at the container runtime.

## Testing

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -q                                   # 472 tests
pytest tests/ --cov=nutrimind --cov-report=term    # 91% coverage
ruff check .                                       # lint, as CI runs it
```

The suite runs entirely in demo mode — no API keys required — and covers the
deterministic services, models, RAG pipeline, agents, routing rules, chat SSE
protocol, configuration defaults, PDF export, authentication, and — most
importantly — that no account can reach another account's data.

**Every milestone added its own regression tests**, each written against a
defect that had been reproduced first:

| Area | What it pins |
|---|---|
| Migrations | Upgrade from empty, pre-Alembic and managed databases; no schema drift |
| Auth & tenancy | Access control, sign-in, CSRF, cookie flags, reset flow, and that neither account can read the other's rows *or* vectors |
| Concurrency | Upload answers while indexing runs, concurrent uploads stay isolated, nothing is left non-terminal, interrupted work recovers |
| Upload validation | Nine disguised file types rejected, each proven to leave nothing on disk; quota and rate limit enforced |
| Rate-limit bounds | IPv6 grouping, the tracked-client ceiling, idle eviction — driven directly, because the suite's own fixture resets that state |
| Log privacy | Real requests, then a grep of everything logged for conditions, medications, weight, emails and passwords |
| Security headers | Every header, no `'unsafe-inline'`, nonce uniqueness, and every inline template script carrying one |
| Account lifecycle | Export completeness and isolation; deletion of rows, files *and* vectors |
| Timezone | Day boundaries across zones, and that an unset zone behaves exactly as before |

CI runs on every push (GitHub Actions) in three jobs: ruff → suite with a
coverage floor → advisory `pip-audit`; migrations applied and schema verified
against a real **PostgreSQL** server; and the **Docker image built**, asserted to
run as a non-root user, booted, health-checked and stopped cleanly on SIGTERM.

**Test isolation:** the suite must never pick up a real `.env`. `load_settings()`
calls `load_dotenv(..., override=False)`, which protects variables already present in
the environment but *repopulates ones a test deleted* — so tests that clear
`WATSONX_APIKEY`/`WATSONX_PROJECT_ID` to exercise the credential-free path would
otherwise get live credentials back and resolve to the watsonx provider. A
session-scoped autouse fixture in `tests/conftest.py` neutralises the `.env` read and
clears the credential variables, so the suite behaves identically in CI, in a fresh
clone, and on a configured developer machine.

**Intentionally uncovered:** `watsonx_client.py` network paths (the pure logic
is tested; live calls are exercised by `scripts/check_watsonx.py` against real
credentials), the optional `sentence-transformers` provider, and the live-mode
branches of the LLM factory. CI enforces a coverage floor of 88% — a ratchet
against erosion rather than a target to chase.

> Note: `chromadb` is required to run the suite — roughly 30 tests construct a
> `VectorStore`. Without it those tests fail with
> `ConfigurationError: ChromaDB is not installed`. Install the full
> `requirements.txt` before running.

## Folder structure

```
nutrimind/          application package
  agents/           coordinator + 4 specialists + tools
  services/         llm/ (watsonx + demo) · rag · jobs · runtime · nutrition · bmi · score · export
  retrieval/        chunker · embeddings · vector store · ingestion · retriever
  routes/           pages · auth · chat · meals · profile · knowledge · account · system
  security.py       response headers + per-request CSP nonce
  utils/            redaction · time · decorators · validators · logging
  models/ prompts/ templates/ static/
migrations/         Alembic revisions — the schema is owned here, not by create_all()
knowledge_base/     seed PDFs (indexed by scripts/seed_knowledge_base.py)
instance/           runtime data (SQLite, uploads, ChromaDB, secret_key) — git-ignored
scripts/            upgrade_database · check_schema · backup · restore ·
                    vendor_cdn_assets · manage_users · check_watsonx · seed_knowledge_base
tests/ docs/

Dockerfile          multi-stage, pinned base, non-root runtime
docker-compose.yml  app + Caddy + data volume, with an explicit migrate step
Caddyfile           TLS, HTTP/3, SSE-safe proxying for /api/chat
wsgi.py             production entry point (installs the SIGTERM drain)
SECURITY.md PRIVACY.md
```

Full map with rationale: [docs/FOLDER_STRUCTURE.md](docs/FOLDER_STRUCTURE.md)

## Technology stack

Python 3.13 (supported 3.11–3.14) · Flask 3 · SQLAlchemy 2 · **ibm-watsonx-ai** (Granite 4 chat +
Granite embeddings) · ChromaDB · pypdf · ReportLab · Bootstrap 5.3 · vanilla
JS (no build step) · pytest

## Documentation

| Doc | Purpose |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design and the reasoning behind it |
| [AGENTS.md](docs/AGENTS.md) | Agent contracts and the streaming event protocol |
| [INSTALLATION.md](docs/INSTALLATION.md) | Local setup |
| [IBM_SETUP.md](docs/IBM_SETUP.md) | Zero-to-connected IBM walkthrough |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Why the deployment is shaped the way it is |
| [SECURITY.md](SECURITY.md) | What is protected, what is not, how to report |
| [PRIVACY.md](PRIVACY.md) | What is collected, where it goes, how to delete it |
| [RUNBOOK.md](docs/RUNBOOK.md) | Deploy, upgrade, roll back, back up, restore, troubleshoot |
| [IMPLEMENTATION_NOTES.md](docs/IMPLEMENTATION_NOTES.md) | Decisions and trade-offs made during the build |
| [MANUAL_TESTING.md](docs/MANUAL_TESTING.md) | Step-by-step verification guide |
| [RELEASE_NOTES.md](docs/RELEASE_NOTES.md) | Release summary, limitations, roadmap |
| [FOLDER_STRUCTURE.md](docs/FOLDER_STRUCTURE.md) | Full repository map with rationale |

Internship submission material (presentation, demo script, checklists) is kept
for provenance in [docs/archive/](docs/archive/).

## Deployment

The project ships with the infrastructure to run it, not just instructions for
it. The production stack is one application container behind Caddy with a single
data volume:

```bash
cp .env.example .env          # set FLASK_SECRET_KEY and NUTRIMIND_DOMAIN
docker compose build
docker compose run --rm migrate      # schema changes are an explicit step
docker compose up -d
```

- **Docker** — multi-stage build, pinned base image, non-root runtime, container
  health check.
- **Docker Compose** — app + Caddy + named volume, with a `migrate` profile so
  `up` can never trigger a schema change.
- **Caddy** — automatic TLS certificates and renewal, HTTP/3, and response
  buffering disabled on `/api/chat` so streamed answers arrive token by token
  rather than in one burst at the end.
- **Migrations as a deploy step** — never from the app container, so a
  crash-looping container cannot retry a failing migration against live data.
  `scripts/check_schema.py` exits non-zero on drift and acts as a release gate.
- **Backup and restore** — `scripts/backup.sh` captures the database with
  SQLite's online backup API (not `cp`, which can catch a live file
  mid-transaction), plus uploads, vectors and the secret key.
  `scripts/restore.sh` displaces the existing instance rather than deleting it,
  so restoring the wrong archive is recoverable. **The restore has been performed
  end to end**, not merely scripted.
- **Graceful shutdown** — SIGTERM drains in-flight indexing before exit; anything
  it cannot finish is recovered on the next start.
- **Production configuration** — environment-driven, with a documented runbook
  for deploy, upgrade, rollback, backup, restore and common failures.

Full procedures: [RUNBOOK.md](docs/RUNBOOK.md). The reasoning behind the shape of
the deployment: [DEPLOYMENT.md](docs/DEPLOYMENT.md).

> The application is production-ready and deployable from this repository. It is
> **not currently hosted at a public URL** — running it is a `docker compose up`
> away, on your own infrastructure.

## Release history

Each milestone was audited before implementation and verified at runtime after
it, and every one corrected at least one assumption that turned out to be wrong
when measured.

| Tag | Focus |
|---|---|
| `v1.1.0-milestone1` | Production hardening, dependency pinning, CI quality gates, safer configuration defaults |
| `v1.2.0-milestone2` | Alembic migrations, PostgreSQL compatibility, safe adoption of pre-existing databases, schema verification |
| `v1.3.0-milestone3` | Authentication, user accounts, per-user ownership and multi-tenant isolation, CSRF on the JSON API, retrieval and demo-mode RAG fixes |
| `v1.4.0-milestone4` | Asynchronous ingestion, background job system, single-process runtime model with a startup guard, interrupted-work recovery |
| `v1.5.0-milestone5` | Docker, Compose, Caddy, production WSGI entry point, backup/restore, cookie hardening, WAL, graceful shutdown |
| **`v2.0.0`** | **Production release** — abuse resistance, security headers and CSP, prompt-injection fencing, privacy-safe logging, account export and deletion, timezones, pagination, `SECURITY.md` and `PRIVACY.md` |

## Future improvements

Weekly PDF reports · retrieval reranking for large knowledge bases · threshold
auto-tuning against live embeddings · error tracking and uptime monitoring ·
transactional email for password resets (reset links are currently written to the
application log) · email verification · a seeded public knowledge base so a new
account sees grounded retrieval before uploading anything.

## License

[MIT](LICENSE) © 2026 Harsh Kamat

NutriMind AI provides general nutrition information and is **not medical
advice**. It does not diagnose conditions and is not a substitute for a qualified
clinician.
