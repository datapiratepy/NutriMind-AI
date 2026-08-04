#!/usr/bin/env sh
# NutriMind AI — restore from a backup archive.
#
#   scripts/restore.sh backups/nutrimind-20260804T120000Z.tar.gz
#
# Inside the container stack (stop the app first — see docs/RUNBOOK.md):
#   docker compose stop app
#   docker compose run --rm -v "$PWD/backups:/backups" app \
#       scripts/restore.sh /backups/nutrimind-<stamp>.tar.gz
#   docker compose start app
#
# This half is the one that usually does not exist. A backup nobody has restored
# is a file with a hopeful name: it is the restore that proves the archive holds
# every piece the application needs, and that they still fit together.
#
# The app MUST be stopped. Replacing the database and the Chroma directory
# underneath a running process gives it open handles to files that no longer
# exist, and the vector store will disagree with the database in ways that look
# like data corruption rather than a bad restore.

set -eu

ARCHIVE="${1:-}"
INSTANCE_DIR="${NUTRIMIND_INSTANCE_DIR:-instance}"

if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "usage: scripts/restore.sh <archive.tar.gz>" >&2
    [ -n "$ARCHIVE" ] && echo "restore: no such archive '$ARCHIVE'" >&2
    exit 1
fi

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

tar -xzf "$ARCHIVE" -C "$WORK"
SRC="$(find "$WORK" -maxdepth 1 -type d -name 'nutrimind-*' | head -n 1)"
if [ -z "$SRC" ]; then
    echo "restore: archive does not contain a nutrimind-<stamp> directory" >&2
    exit 1
fi

if [ ! -f "$SRC/nutrimind.db" ]; then
    echo "restore: archive contains no database — refusing to continue" >&2
    exit 1
fi

# Displace rather than delete. If this restore turns out to be the wrong
# archive, the previous state is still on disk instead of gone.
if [ -d "$INSTANCE_DIR" ]; then
    ASIDE="$INSTANCE_DIR.superseded-$(date -u +%Y%m%dT%H%M%SZ)"
    mv "$INSTANCE_DIR" "$ASIDE"
    echo "restore: previous instance moved to $ASIDE"
fi

mkdir -p "$INSTANCE_DIR"
cp "$SRC/nutrimind.db" "$INSTANCE_DIR/nutrimind.db"
for item in uploads chroma secret_key; do
    [ -e "$SRC/$item" ] && cp -R "$SRC/$item" "$INSTANCE_DIR/"
done

# The archive holds a checkpointed database with no -wal/-shm sidecars, which is
# correct: those are rebuilt on the next connection. A stale sidecar copied in
# beside a restored database would be actively harmful.
rm -f "$INSTANCE_DIR/nutrimind.db-wal" "$INSTANCE_DIR/nutrimind.db-shm"

echo "restore: restored into $INSTANCE_DIR"
echo "restore: verifying schema ..."
python scripts/check_schema.py

cat <<'EOF'
restore: OK

Next:
  1. Start the app.
  2. Sign in as a known account.
  3. Open Knowledge and confirm the documents are listed AND searchable.
     Documents may show "needs re-index" if the embedding provider changed
     since the backup — that is expected, and Re-index rebuilds them.
EOF
