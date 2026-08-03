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

## Authentication and sessions

Every route that touches user data requires a signed-in account. Sessions are
signed cookies, so `FLASK_SECRET_KEY` is what protects them: set it explicitly
in production (see above). Changing it signs everyone out, which is the
emergency lever if a session is ever suspected of being stolen.

Cookie flags are set automatically: `HttpOnly` always, `SameSite=Lax` always,
and `Secure` whenever `FLASK_DEBUG` is off — so production sessions are only
ever sent over HTTPS. Terminate TLS in front of the app (see above) or browsers
will refuse to send the cookie at all.

`SESSION_DAYS` (default 14) sets how long a session lasts.

### Behind a reverse proxy: set `TRUSTED_PROXY_HOPS`

```
TRUSTED_PROXY_HOPS=1     # one proxy (nginx/Caddy) in front of the app
```

Without it, `request.remote_addr` is the *proxy's* address, so every visitor
shares one identity and the login rate limiter counts them together — ten failed
attempts by anybody locks out the whole site.

Set it to the number of proxies that actually sit in front of the app, and no
more. Trusting more hops than exist lets a client send its own
`X-Forwarded-For` and be treated as any address it likes.

### Account management

```bash
python scripts/manage_users.py list
python scripts/manage_users.py create you@example.com
python scripts/manage_users.py set-password you@example.com
python scripts/manage_users.py disable someone@example.com
```

Passwords are prompted for, never passed as arguments — command lines end up in
shell history and in the process list.

### Password reset needs a mail transport

**Not yet configured.** `POST /forgot-password` generates a signed, expiring,
single-use link and writes it to the application log rather than emailing it.
That is workable for a single-operator deployment and useless for a public one:
users cannot read your logs.

Wiring SMTP is a prerequisite for public launch. The seam is
`_deliver_reset_link()` in `nutrimind/routes/auth.py`.

## Changing embedding provider

Each provider owns its own Chroma collection (`kb_watsonx`, `kb_local`,
`kb_lexical`) because their vector spaces are not comparable — mixing them
returns confident nonsense.

So **adding IBM credentials, or installing `sentence-transformers`, switches
collections**, and documents indexed under the previous provider stop being
searchable. They are not lost: the knowledge page marks them **needs re-index**
with the reason, and the re-index button rebuilds them into the active
collection. Nothing changes until you do it.

Similarity thresholds move with the provider too. Each supplies the value
calibrated for its own space (semantic ≈ 0.35, lexical ≈ 0.12). Leave
`RAG_SIMILARITY_THRESHOLD` unset unless you have measured a better number for
your own corpus — one value applied across providers silently breaks whichever
it was not chosen for.

## Security recommendations

- Secrets stay in `.env` / the platform's secret store — never in the image
  or repo. Rotate the IBM key if it ever leaks.
- Keep `MAX_UPLOAD_MB` conservative; uploads are validated but disk is
  finite.
- The in-memory rate limiter is per-process, so with several workers the
  effective limit is `workers × max_calls`. Put real limits at the proxy
  (nginx `limit_req`) for public exposure.
- Security headers (CSP, HSTS) and Subresource Integrity on the CDN assets are
  still outstanding — tracked as the security-hardening milestone.

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

### Upgrading a database that predates accounts

The tenancy migration adds an owner to every row. Data that already existed has
no owner, so it is adopted into a placeholder account
(`legacy@nutrimind.invalid`) that has no usable password and cannot be signed
into. **Nothing is deleted**, but the data is unreachable until you claim it:

```bash
python scripts/manage_users.py set-password legacy@nutrimind.invalid
```

Then sign in as that address. Rename it afterwards if you prefer a real one.

**On an empty database no placeholder is created** — a fresh install should not
inherit a ghost account. The upgrade prints an `adopted N existing row(s)…`
line only when it actually adopted something; if you did not see one, there was
nothing to adopt and `legacy@nutrimind.invalid` will not exist. Confirm with
`python scripts/manage_users.py list` and register normally.

If the upgrade stops with *"Found N rows in user_profile"*, the database holds
several profiles from before a profile belonged to one account. Keep the row you
want, delete the rest, and re-run.

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
