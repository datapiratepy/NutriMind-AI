# Runbook

Operational procedures for a running NutriMind AI deployment. `DEPLOYMENT.md`
explains *why* the deployment is shaped the way it is; this file is what you
follow at 2am.

Everything here assumes the Compose stack in the repository root.

---

## 0. First deploy

**Prerequisites:** a host with Docker, a DNS `A`/`AAAA` record pointing at it,
and ports 80 and 443 reachable.

```bash
git clone <repo> nutrimind && cd nutrimind
cp .env.example .env
```

Edit `.env`. The four that matter:

| variable | value |
|---|---|
| `FLASK_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `NUTRIMIND_DOMAIN` | your hostname, e.g. `nutrimind.example.com` |
| `NUTRIMIND_TLS_EMAIL` | an address you read — certificate expiry warnings |
| `TRUSTED_PROXY_HOPS` | `1` (already set in `docker-compose.yml`) |

Then:

```bash
docker compose build
docker compose run --rm migrate      # schema first, always
docker compose up -d
docker compose logs -f app           # watch for the startup lines below
```

**What a healthy start looks like.** Two lines you want to see, and one you do
not:

```
NutriMind AI v1.0.0 started — requested mode 'auto' (effective 'demo')
vector store ready: kb_lexical at /data/chroma (0 chunks)
```

```
ANOTHER PROCESS IS ALREADY USING THIS CHROMA DIRECTORY   <-- must NOT appear
```

That last one means two processes are sharing the vector store. It is not fatal
and the app will serve requests, but retrieval silently returns nothing on
whichever share of them lands on the second process. See `DEPLOYMENT.md`,
"Why not several workers".

---

## 1. Deploying a new version

Migrations are a separate, deliberate step. They are **never** run by the app
container: a container that migrates on boot re-runs migrations on every
restart, and a crash-looping container retries a failing migration against live
data forever.

```bash
# 1. Back up first. Always. It costs seconds.
docker compose run --rm -v "$PWD/backups:/backups" app scripts/backup.sh /backups

# 2. Get the new code and build it.
git pull
docker compose build

# 3. Apply schema changes and verify them. Exits non-zero on drift.
docker compose run --rm migrate

# 4. Replace the running app. SIGTERM drains in-flight indexing first.
docker compose up -d app

# 5. Confirm.
curl -fsS https://$NUTRIMIND_DOMAIN/api/health
docker compose logs --tail=50 app
```

Step 4 takes up to `stop_grace_period` (40s) if a document is being indexed.
That is the drain working, not a hang — the log says
`draining N background job(s) before shutdown`.

---

## 2. Rolling back

**If the schema did not change** (most releases), roll back the code alone:

```bash
git checkout <previous-tag>
docker compose build && docker compose up -d app
curl -fsS https://$NUTRIMIND_DOMAIN/api/health
```

**If the schema did change**, do not improvise. Alembic downgrades are written
per revision and are not always lossless — a migration that dropped a column
cannot invent the data back. Restore from the backup taken in step 1 above:

```bash
docker compose stop app
docker compose run --rm -v "$PWD/backups:/backups" app \
    scripts/restore.sh /backups/nutrimind-<stamp>.tar.gz
git checkout <previous-tag>
docker compose build && docker compose up -d app
```

This is why step 1 of every deploy is a backup. The rollback path for a schema
change *is* the restore path.

---

## 3. Backups

```bash
# Manual
docker compose run --rm -v "$PWD/backups:/backups" app scripts/backup.sh /backups

# Nightly at 03:15 — crontab -e on the host
15 3 * * * cd /srv/nutrimind && docker compose run --rm -T \
    -v /srv/nutrimind/backups:/backups app scripts/backup.sh /backups >> /var/log/nutrimind-backup.log 2>&1
```

The archive holds the database, uploaded PDFs, the Chroma index and the secret
key. Logs are excluded deliberately — large, low value.

The database is captured with SQLite's **online backup API**, not `cp`. A plain
copy of a live SQLite file can catch it mid-transaction, and in WAL mode the
`.db` alone is incomplete because recent commits still live in the `-wal`
sidecar.

Retention is the 14 most recent archives (`BACKUP_KEEP`). **Copy them off the
host** — a backup on the same disk as the thing it backs up survives a bad
deploy but not a dead server.

---

## 4. Restoring

**The app must be stopped.** Replacing the database and Chroma directory under a
running process leaves it holding handles to files that no longer exist, and the
vector store will disagree with the database in ways that look like corruption
rather than a bad restore.

```bash
docker compose stop app
docker compose run --rm -v "$PWD/backups:/backups" app \
    scripts/restore.sh /backups/nutrimind-<stamp>.tar.gz
docker compose start app
```

The previous `instance/` is moved aside as `instance.superseded-<stamp>` rather
than deleted, so restoring the wrong archive is recoverable.

**Then verify, in this order** — this sequence is the one that was actually
drilled, and each step covers a different part of the archive:

1. Sign in as a known account. *(database + secret key)*
2. Open **Profile** and confirm it is populated. *(relational data)*
3. Open **Knowledge**; the documents are listed with their chunk counts.
   *(database)*
4. Search for a phrase you know is in one of them; it must return a citation.
   *(Chroma index — the part a database-only backup would miss)*
5. Send a chat message referencing that document.

If documents show **needs re-index** in amber, the embedding provider changed
since the backup — expected, and the Re-index button rebuilds them.

---

## 5. Common problems

**Nobody can sign in; sign-in appears to do nothing.**
The session cookie is `Secure`, so it is only sent over HTTPS. If TLS is
misconfigured the browser silently drops it and the login form just redraws.
Check the certificate first: `docker compose logs caddy`.

**Every user is rate-limited as if they were one person.**
`TRUSTED_PROXY_HOPS` is 0. Behind Caddy the app sees only the proxy's address,
so ten failed sign-ins lock out the whole site. It must be `1` for this stack.

**Chat answers arrive all at once instead of streaming.**
Response buffering in front of `/api/chat`. The `Caddyfile` sets
`flush_interval -1` for that route; if you replaced Caddy with nginx, set
`proxy_buffering off`.

**Retrieval finds nothing, but documents say "indexed".**
Check for `ANOTHER PROCESS IS ALREADY USING THIS CHROMA DIRECTORY` in the logs.
If it is there, you are running more than one process — see §0.
If it is not, check whether the knowledge page shows **needs re-index**: the
embedding provider changed and the vectors are in the previous collection.

**A document is stuck on "processing".**
It cannot be, after a restart: startup recovery moves anything left `pending` or
`processing` to `failed` with a Re-index prompt. If you see one, the app has not
been restarted since the interruption — restart it.

**Upload returns 429.**
Working as intended: 10 uploads per 10 minutes per address. It is the only
endpoint that writes caller-controlled bytes to disk.

**Disk full.**
`docker compose run --rm app du -sh /data/*` shows the split. `uploads/` grows
with user documents; `chroma/` grows with the index. Rejected uploads are
deleted immediately and do not accumulate.

---

## 6. Health and inspection

```bash
curl -fsS https://$NUTRIMIND_DOMAIN/api/health      # 200 healthy, 503 degraded
docker compose ps                                   # container health status
docker compose logs --tail=100 app
docker compose exec app python scripts/check_schema.py
docker compose exec app python scripts/manage_users.py list
```

`/api/health` is public and unauthenticated so an uptime monitor can reach it.
It reports the database and vector store, and returns **503** when either is
unreachable — so a load balancer sees a failure rather than a 200 with bad news
in the body.

---

## 7. What is not covered yet

Honest list, so nobody assumes otherwise:

- **No error tracking or uptime monitoring.** A 500 is a line in a log file on
  this box. Next milestone.
- **No security headers (CSP, HSTS beyond Caddy's default, SRI).** Next
  milestone; CSP needs Report-Only observation against the real origin first.
- **Password reset emails are written to the log**, not sent. An operator can
  retrieve the link with `docker compose logs app | grep reset`.
- **Backups are local.** Copying them off-host is a manual step you must add.
