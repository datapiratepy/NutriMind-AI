# Deployment Guide

How to run NutriMind AI beyond the development server. **Documentation
only** — the internship deliverable runs locally; this records the
production path a real deployment would take.

## Local (development)

`python run.py` → Flask's dev server on 127.0.0.1:5000. Fine for demos and
evaluation; not for public exposure (single-threaded reloader, debug
features).

## Production process model

Use a WSGI server. The app factory makes this a one-liner:

```bash
pip install waitress
waitress-serve --host 127.0.0.1 --port 8000 --call nutrimind:create_app
```

(gunicorn on Linux: `gunicorn -w 2 -b 127.0.0.1:8000 "nutrimind:create_app()"`.)

Production `.env` changes: a generated `FLASK_SECRET_KEY` and `LOG_LEVEL=INFO`.
`FLASK_DEBUG` already defaults to off, so there is nothing to remember to turn
off — set it to `1` only on your own machine.

Setting `FLASK_SECRET_KEY` explicitly is still the right thing to do in
production, even though the app no longer refuses to start without it. If it is
unset, a random key is generated once and stored in `instance/secret_key`; that
is safe (it is never the placeholder value) but the key then lives outside your
secret store, is not rotatable through your normal process, and is lost if the
instance volume is ever recreated — which logs every user out.

## Reverse proxy + HTTPS

Terminate TLS in front of the WSGI server — nginx or Caddy:

- **Caddy** (simplest, automatic Let's Encrypt):
  `caddy reverse-proxy --from yourdomain.example --to 127.0.0.1:8000`
- **nginx**: proxy_pass to 127.0.0.1:8000; set `proxy_buffering off;` for
  the `/api/chat` SSE endpoint (streaming breaks behind buffering proxies —
  the app already sends `X-Accel-Buffering: no`).

Never expose the WSGI port directly; bind it to localhost.

## Security recommendations

- Secrets stay in `.env` / the platform's secret store — never in the image
  or repo. Rotate the IBM key if it ever leaks.
- Add authentication before any multi-user or public deployment (the app is
  single-profile by design); enable CSRF tokens on forms at the same time.
- Keep `MAX_UPLOAD_MB` conservative; uploads are validated but disk is
  finite.
- The in-memory rate limiter is per-process — put real limits at the proxy
  (nginx `limit_req`) for public exposure.

## Database migrations

The schema is owned by the versioned scripts in `migrations/`, not by the
application. `create_app()` does **not** create tables.

Applying migrations is an explicit deployment step, run once per release
*before* starting the new workers:

```bash
python scripts/upgrade_database.py
python scripts/check_schema.py     # release gate; exits 1 on drift
```

**Use the script, not bare `flask db upgrade`.** They differ on one case that
matters exactly once per database. `flask db upgrade` only works where Alembic
already tracks the schema; a database created before this project adopted
migrations has all the tables but no `alembic_version` row, so Alembic reads no
version marker, concludes the database is empty, replays the baseline revision
and fails with `table bmi_records already exists`. The script detects that state
and stamps the baseline first — recording that the existing schema is already
applied, without altering it — then upgrades through anything newer. On an empty
or already-tracked database it is an ordinary upgrade.

Never run it from the web workers. Several processes migrating the same database
at once is how a schema gets corrupted.

It is deliberately not automatic at startup: with several workers booting
together they would race each other to migrate the same database. The one
exception is `python run.py`, which applies migrations for local development
convenience — that path runs only under `__main__`, so a WSGI server never
triggers it.

After changing a model, generate the matching revision and commit it:

```bash
FLASK_APP=nutrimind flask db migrate -m "what changed"
FLASK_APP=nutrimind flask db upgrade
```

CI fails if the models and the migration scripts disagree, so a forgotten
revision is caught before it reaches a deployment.

Rollback is `flask db downgrade`. Note that downgrades which drop columns
destroy the data in them — for anything beyond a trivial revert, restoring
from backup is the safer path.

## SQLite limitations & PostgreSQL path

SQLite is deliberate for local development (zero-ops, no server to run). Its
limits: one writer at a time, no network access, file-on-disk durability story.
It also does not enforce `VARCHAR` lengths, so column-width bugs stay invisible
until they reach Postgres.

For production, set:

```
DATABASE_URI=postgresql+psycopg://user:pass@host:5432/nutrimind
```

`psycopg` is already in `requirements.txt`, and the same migrations apply
unchanged. CI runs them against a real PostgreSQL 16 server on every push and
asserts the resulting schema matches the models, so this path is verified
continuously rather than discovered on deployment day.

Timestamps are stored as naive UTC (`TIMESTAMP WITHOUT TIME ZONE`) on both
dialects — see `nutrimind/utils/time.py` for why that convention was chosen
over timezone-aware columns.

Multi-user additionally needs `user_id` foreign keys; that is the next
milestone, and it is what the migration tooling above exists to make routine.

## State to persist across deploys

`instance/` holds everything mutable: `nutrimind.db`, `uploads/`, `chroma/`
and logs. Back it up or mount it as a volume. `NUTRIMIND_INSTANCE_DIR` and
`CHROMA_DIR` relocate it if needed.

## Cloud options (future)

IBM Code Engine (fits the IBM story; containerize with a simple
`python:3.11-slim` + waitress image), Render, or a small VPS. Remember:
outbound HTTPS to `*.ml.cloud.ibm.com` and `iam.cloud.ibm.com` must be
allowed for live mode.
