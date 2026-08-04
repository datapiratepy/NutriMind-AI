# Deployment Guide

How and why NutriMind AI is deployed the way it is. The **step-by-step
procedures** — first deploy, upgrade, rollback, backup, restore, troubleshooting
— live in [RUNBOOK.md](RUNBOOK.md). This file explains the decisions behind
them.

The deployment target is a single small VPS running the Compose stack in the
repository root: the application behind Caddy, with one data volume.

```bash
cp .env.example .env          # then edit — see RUNBOOK.md §0
docker compose build
docker compose run --rm migrate
docker compose up -d
```

## Local (development)

`python run.py` → Flask's dev server on 127.0.0.1:5000. Fine for demos and
evaluation; not for public exposure (single-threaded reloader, debug
features).

## Production process model

**One process, many threads. Never multiple worker processes.**

The container does this for you:

```
CMD waitress-serve --host=0.0.0.0 --port=8000 --threads=8 --channel-timeout=120 wsgi:app
```

Outside the container, the same thing:

```bash
waitress-serve --host 127.0.0.1 --port 8000 --threads 8 wsgi:app
```

Three details in that command line are deliberate.

**`waitress`, and it is a pinned dependency** in `requirements.txt` — not a
`pip install` at deploy time. Everything else the application runs on is pinned
and scanned by `pip-audit` in CI; the one component directly exposed to the
internet must not be the exception. Waitress rather than gunicorn because it is
the same server on Windows and Linux, so what is tested locally is what runs.

**`wsgi:app`, not `--call nutrimind:create_app`.** The `wsgi` module installs the
SIGTERM handler that drains in-flight indexing. An app factory must not do that
as a side effect of being called — the test suite calls it hundreds of times,
and `signal.signal` raises outside the main thread.

**`--threads 8`, and exactly one process.** See below.

### Why not several workers

Because they cannot share the vector store, and the way they fail is silent.

ChromaDB keeps its data in two places with different sharing properties.
Metadata lives in SQLite and *is* coherent across processes. The HNSW vector
index is read through a **per-process cached reader that never refreshes**. Two
processes on one Chroma directory therefore diverge. Measured on the pinned
version (chromadb 1.5.9):

| Reader's client opened… | `count()` | `get()` | `query()` |
|---|---|---|---|
| before any vectors existed | correct | correct | **raises** `InternalError: Error creating hnsw segment reader: Nothing found on disk` |
| when it already held a segment | correct | correct | **silently cannot find** the peer's vectors |

The second row is the dangerous one. A worker that did not perform the indexing
reports the right chunk count — the knowledge page shows "indexed, 28 chunks",
because that number comes from the coherent metadata path — and then finds
nothing when it searches. Retrieval simply returns no results on whatever share
of requests happen to land on that worker. No error is logged. It looks exactly
like the retrieval bug fixed in Milestone 3.

Writes are **not** the problem, and the store is **not** corrupted: two processes
writing 120 interleaved records produced 120 correct, searchable rows. So
`-w 2` does not damage anything — it makes half your workers unable to search.

Threads are safe. One client under 8 concurrent writer threads and 3 concurrent
reader threads completed 200 adds with zero errors, nothing lost, nothing
unsearchable. That is why the answer is `--threads N` rather than `-w N`.

The application enforces this rather than trusting the reader. On startup it
takes an advisory lock on the Chroma directory; a second process finds the lock
held and logs an error explaining the above. It does **not** refuse to start —
a diagnostic that can prevent boot is worse than the problem it reports — so
check your logs for `ANOTHER PROCESS IS ALREADY USING THIS CHROMA DIRECTORY`
after a deploy.

If you genuinely outgrow one process, the fix is to move Chroma out of the
application — run it as a server (`chromadb.HttpClient`) or replace it — not to
add workers.

### Scaling within the one process

`--threads` handles concurrent requests. Document indexing does **not** run on
those threads; it runs on a separate bounded pool (`INGEST_WORKERS`, default 2)
so a large upload cannot occupy a request thread. See "Document indexing" below.

Production `.env` changes: a generated `FLASK_SECRET_KEY` and `LOG_LEVEL=INFO`.
`FLASK_DEBUG` already defaults to off, so there is nothing to remember to turn
off — set it to `1` only on your own machine.

Setting `FLASK_SECRET_KEY` explicitly is still the right thing to do in
production, even though the app no longer refuses to start without it. If it is
unset, a random key is generated once and stored in `instance/secret_key`; that
is safe (it is never the placeholder value) but the key then lives outside your
secret store, is not rotatable through your normal process, and is lost if the
instance volume is ever recreated — which logs every user out.

## Document indexing

Uploading a PDF returns **202 Accepted** with the document `pending`. Extraction,
chunking, embedding and indexing then run on a background pool inside the same
process; the knowledge page polls `GET /api/documents` until the document reaches
`indexed` or `failed`.

This is not premature optimisation. Measured end to end on the zero-credential
lexical provider:

| pages | chunks | extract+chunk+embed | Chroma add | total |
|---|---|---|---|---|
| 50 | 250 | 0.74s | 0.37s | 1.11s |
| 150 | 750 | 1.22s | 1.15s | 2.37s |
| 400 | 2000 | 5.70s | 6.30s | **12.00s** |

12s already sits inside the window where proxies start giving up, and the
credentialed path is far worse: watsonx embeddings go out in batches of 16, so
that same 400-page document is **125 sequential HTTP round-trips**. `MAX_UPLOAD_MB`
defaults to 15, which permits documents roughly twenty times larger than the
largest measured here.

| variable | default | meaning |
|---|---|---|
| `INGEST_WORKERS` | 2 | threads available for indexing |
| `INGEST_QUEUE_LIMIT` | 32 | documents that may be queued before uploads are refused |

`INGEST_WORKERS` is deliberately small. Extraction and lexical embedding are pure
Python and hold the GIL, so extra ingest threads compete with the threads serving
requests instead of adding throughput. Raise it when embeddings go to watsonx,
where the work is network I/O and releases the GIL. Beyond `INGEST_QUEUE_LIMIT`
an upload is accepted, stored, and immediately marked `failed` with a message
telling the user to retry — a bounded queue that refuses is better than an
unbounded one that accepts work it will not start for hours.

### Interrupted indexing

`pending` and `processing` exist only in the memory of the process running the
job. If that process stops — deploy, restart, OOM kill — nothing remains to
finish the work or to record that it stopped.

On startup, any document still in `pending` or `processing` is therefore moved to
`failed` with "Indexing was interrupted when the application stopped. Use
Re-index to run it again." A document can never be left in a state that nothing
will ever change, which matters because the UI polls non-terminal states
indefinitely.

This runs only when the process holds the Chroma lock, so it cannot fail work
that another live process is still doing.

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

Cookie flags are set automatically on **both** credential cookies: `HttpOnly`
always, `SameSite=Lax` always, and `Secure` whenever `FLASK_DEBUG` is off — so
production credentials are only ever sent over HTTPS. Terminate TLS in front of
the app (see above) or browsers will refuse to send them at all.

"Both" is load-bearing. Flask-Login keeps the "remember me" cookie on its own
`REMEMBER_COOKIE_*` settings, which do **not** inherit from `SESSION_COOKIE_*`.
Left unset — as they were until Milestone 5 — its defaults applied, and the
measured result was:

```
remember_token -> Expires=+365 days, HttpOnly, Path=/   (no Secure, no SameSite)
session        -> Secure, HttpOnly, Path=/, SameSite=Lax
```

The longest-lived credential the application issues was the least protected one,
and it silently overrode the documented session policy by a factor of 26. Both
cookies are now configured together, and
`tests/unit/test_cookie_policy.py` asserts the real `Set-Cookie` headers rather
than the config dictionary — reading config would not have caught this.

`SESSION_DAYS` (default 14) sets how long a session lasts, and now also bounds
"remember me". Changing a password invalidates both, because the session
identity embeds the password hash.

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
- Keep `MAX_UPLOAD_MB` conservative. It bounds one request; the rate limit on
  `POST /api/documents` (10 per 10 minutes) is what bounds a sequence of them.
  Uploads are validated by their **contents**, not by filename or the
  `Content-Type` header — both of which the caller supplies. Before Milestone 5
  an ELF binary named `report.pdf` was accepted with HTTP 202, written to disk
  and left there; it is now rejected with 400 and the file is removed
  immediately. This is the only endpoint that writes caller-controlled bytes to
  disk, and a full volume takes the database and the vector store with it.
- The in-memory rate limiter is per-process. Since the supported model is a
  single process (see "Production process model"), the configured limit is the
  effective limit — but it is also lost on restart, so put real limits at the
  proxy (nginx `limit_req`) for public exposure.
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

Multi-user support shipped in Milestone 3: every owned table carries a `user_id`
foreign key, and the migration tooling above is what made that change routine
against existing data.

## SQLite journal mode

The database runs in **WAL** with `synchronous=NORMAL`, set per connection in
`nutrimind/__init__.py`. Under the default `delete` journal a writer blocks
readers and a reader blocks the writer, which matters when eight request threads
share one process.

Measured on this codebase, and reported in both directions because the result
was not uniformly positive:

```
write-only, 8 threads x 15 writes
    delete : p50  34ms  p95 246ms  max 975ms  0 errors
    wal    : p50  77ms  p95 250ms  max 591ms  0 errors      <- p50 worse

mixed, 3 writers + 5 readers (the realistic shape)
    delete : read p50 122ms  p95 271ms   wall 1.70s
    wal    : read p50  92ms  p95 197ms   wall 1.36s
                  -25%       -27%          -20%
```

WAL is not free everywhere: for purely serialised writes it costs a little. It
is enabled because the real workload is read-dominated, and because there were
**zero errors in either mode** — this is a latency choice, not a correctness one.

`journal_mode` is a persistent property of the database file, so an existing
deployment switches over on the first connection after upgrading, with no
migration.

## State to persist across deploys

`/data` in the container (`instance/` outside it) holds everything mutable:
`nutrimind.db`, `uploads/`, `chroma/`, the generated `secret_key`, and logs.
Compose mounts it as the named volume `nutrimind-data`.

Losing it loses accounts, documents and the vector index. `secret_key` in
particular signs every session, so losing it signs everyone out and invalidates
outstanding password-reset links.

Backup and restore are `scripts/backup.sh` and `scripts/restore.sh`; procedures
are in [RUNBOOK.md](RUNBOOK.md) §3–§4. The restore has been performed end to
end, not just written.

## Cloud options

The Compose stack runs anywhere Docker does — a small VPS is the intended
target. IBM Code Engine also fits the IBM story and takes the same image.

For live watsonx mode, outbound HTTPS to `*.ml.cloud.ibm.com` and
`iam.cloud.ibm.com` must be allowed.

One constraint applies everywhere: **the platform must give the container
persistent storage.** `/data` is a volume, and a platform with an ephemeral
filesystem destroys every account on each deploy.
