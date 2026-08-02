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

## SQLite limitations & PostgreSQL path

SQLite is deliberate for this scope (zero-ops, single user). Its limits:
one writer at a time, no network access, file-on-disk durability story.

Migration path (already prepared): models are SQLAlchemy 2.x and
dialect-neutral; set `DATABASE_URI=postgresql+psycopg://user:pass@host/db`,
`pip install psycopg`, run `db.create_all()` once. JSON columns map to
Postgres JSONB automatically via SQLAlchemy. Multi-user would additionally
add `profile_id` foreign keys (documented in IMPLEMENTATION_NOTES.md).

## State to persist across deploys

`instance/` holds everything mutable: `nutrimind.db`, `uploads/`, `chroma/`
and logs. Back it up or mount it as a volume. `NUTRIMIND_INSTANCE_DIR` and
`CHROMA_DIR` relocate it if needed.

## Cloud options (future)

IBM Code Engine (fits the IBM story; containerize with a simple
`python:3.11-slim` + waitress image), Render, or a small VPS. Remember:
outbound HTTPS to `*.ml.cloud.ibm.com` and `iam.cloud.ibm.com` must be
allowed for live mode.
