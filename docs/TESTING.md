# Testing

How NutriMind AI is tested, what each suite is there to prove, and what is deliberately
left uncovered. The short version is in the [README](../README.md#testing-and-ci).

## Running the suite

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -q                                   # full suite
pytest tests/ --cov=nutrimind --cov-report=term    # with line coverage
ruff check .                                       # lint, exactly as CI runs it
```

Last measured at commit `d70ae9b` (Python 3.13, demo mode): **472 passed** — 274 in
`tests/unit/`, 198 in `tests/integration/` — at **91% line coverage** of the `nutrimind`
package, with `ruff check .` clean.

The suite runs entirely in demo mode, so no API keys are needed. `chromadb` is required:
roughly 30 tests construct a `VectorStore`, and without it they fail with
`ConfigurationError: ChromaDB is not installed`. Install the full `requirements.txt` first.

## What the suites pin

Every milestone added regression tests, each written against a defect that had been
reproduced first:

| Area | What it pins |
|---|---|
| Migrations | Upgrade from an empty, a pre-Alembic and a managed database; no schema drift |
| Auth and tenancy | Access control, sign-in, CSRF, cookie flags, the reset flow, and that neither of two accounts can read the other's rows *or* vectors |
| Concurrency | Upload answers while indexing runs, concurrent uploads stay isolated, nothing is left non-terminal, interrupted work recovers |
| Upload validation | Disguised file types rejected by content, each proven to leave nothing on disk; quota and rate limit enforced |
| Rate-limit bounds | IPv6 /64 grouping, the tracked-client ceiling and idle eviction, driven directly because the suite's own fixture resets that state |
| Log privacy | Real requests, then a search of everything logged for conditions, medications, weight, emails and passwords |
| Security headers | Every header, no `'unsafe-inline'` in `script-src`, nonce uniqueness, and every inline template script carrying a nonce |
| Account lifecycle | Export completeness and isolation; deletion of rows, files *and* vectors |
| Timezones | Day boundaries across zones, and that an unset zone behaves exactly as before |

The rest of the suite covers the deterministic services (targets, BMI, health score,
food lookup), models, chunking, embeddings and retrieval, the agents and routing rules,
the chat SSE protocol, configuration defaults and PDF export.

## Test isolation from a local `.env`

The suite must never pick up a real `.env`. `load_settings()` calls
`load_dotenv(..., override=False)`, which protects variables already present in the
environment but *repopulates ones a test deleted*. Tests that clear `WATSONX_APIKEY` and
`WATSONX_PROJECT_ID` to exercise the credential-free path would otherwise get live
credentials back and resolve to the watsonx provider. A session-scoped autouse fixture in
`tests/conftest.py` neutralises the `.env` read and clears the credential variables, so the
suite behaves identically in CI, in a fresh clone, and on a configured developer machine.

CSRF protection stays **enabled** in the test suite. Disabling it is the usual shortcut,
and it silently voids every CSRF assertion.

## Intentionally uncovered

- `nutrimind/services/llm/watsonx_client.py` network paths. The pure logic around them
  (error translation) is unit-tested; live calls are exercised by
  `scripts/check_watsonx.py` against real credentials, not in CI.
- The optional `sentence-transformers` embedding provider, which pulls in PyTorch.
- The live-mode branches of the LLM factory.

CI enforces a coverage floor of 88%: a ratchet against erosion, not a target to chase.

## CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs three jobs on every push
and pull request:

1. **Lint, tests, audit.** `ruff check .`, then the suite in demo mode with
   `--cov-fail-under=88`, then `pip-audit` as an advisory step (a newly disclosed CVE
   should be visible without blocking an unrelated change).
2. **Migrations on PostgreSQL.** Applies the migrations to a real PostgreSQL 16 service
   with `scripts/upgrade_database.py` (the same command the runbook gives operators) and
   asserts with `scripts/check_schema.py` that the schema matches the models.
3. **Production image.** Builds the Docker image, asserts it does not run as root, applies
   migrations and checks the schema inside it, boots the real entry point, waits for
   `/api/health`, and checks that `docker stop` (SIGTERM) ends it inside the grace period.
