# NutriMind AI — production image.
#
# Multi-stage: dependencies are built in a stage that has a compiler, and the
# runtime stage copies only the installed packages. chromadb and psycopg pull
# native wheels; when a wheel is missing for the platform, pip falls back to
# building from source and needs gcc. Keeping that toolchain out of the shipped
# image removes a large amount of attack surface and roughly halves the size.
#
# Pinned to a major.minor tag rather than a digest. The stronger form is a
# digest, which cannot be invented — resolve it once on the deploy host and
# replace the FROM lines:
#
#     docker pull python:3.13-slim-bookworm
#     docker inspect --format='{{index .RepoDigests 0}}' python:3.13-slim-bookworm
#
# 3.13 is not arbitrary: ibm-watsonx-ai declares Requires-Python >=3.11,<3.15
# and each release moves that ceiling, so the interpreter and the SDK are chosen
# together (see requirements.in). CI runs the same version.

# ---------------------------------------------------------------- build stage
FROM python:3.13-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential for source fallbacks; libpq-dev is NOT needed because
# psycopg[binary] ships its own libpq.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt ./
# --prefix keeps the install relocatable so the runtime stage can copy it whole.
RUN pip install --prefix=/install -r requirements.txt

# -------------------------------------------------------------- runtime stage
FROM python:3.13-slim-bookworm AS runtime

# PYTHONDONTWRITEBYTECODE: the filesystem is read-only for the app user, and
# .pyc files would be written on every import attempt.
# PYTHONUNBUFFERED: without it, logs sit in a pipe buffer and `docker logs`
# shows nothing until the buffer fills — which looks exactly like a hung app.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    NUTRIMIND_INSTANCE_DIR=/data

COPY --from=builder /install /usr/local

# Non-root. A fixed uid/gid so the bind-mounted or named volume has predictable
# ownership; matching them on the host is what stops "permission denied" on
# /data after the first deploy.
RUN groupadd --gid 10001 nutrimind \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin nutrimind

WORKDIR /app
COPY --chown=nutrimind:nutrimind . .

# /data holds everything mutable: SQLite database, uploaded PDFs, the Chroma
# store and the generated secret key. It is a volume, not image content — see
# docker-compose.yml.
RUN mkdir -p /data && chown nutrimind:nutrimind /data
VOLUME ["/data"]

USER nutrimind
EXPOSE 8000

# Liveness only. /api/health returns 503 when the database or vector store is
# unreachable, so the container is marked unhealthy for the reason that matters
# rather than merely "the port is open". start-period covers the measured 3.4s
# cold start (import 1.8s + create_app 1.2s) with room to spare on slower disks.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# wsgi:app, not run.py and not --call nutrimind:create_app.
#
#   * run.py is the development server and applies migrations at startup;
#     schema changes are a deliberate deploy step here (see docs/RUNBOOK.md).
#   * wsgi.py installs the SIGTERM handler that drains in-flight indexing, which
#     an app factory must not do as a side effect of being called.
#
# --threads 8, and exactly one process: ChromaDB caches its HNSW reader per
# process, so a second worker reports correct chunk counts and finds nothing
# when it searches (measured in Milestone 4; see nutrimind/services/runtime.py).
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8000", "--threads=8", \
     "--channel-timeout=120", "wsgi:app"]
