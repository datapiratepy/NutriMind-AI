#!/usr/bin/env sh
# NutriMind AI — back up everything mutable.
#
#   scripts/backup.sh [DEST_DIR]
#
# Inside the container stack:
#   docker compose run --rm -v "$PWD/backups:/backups" app scripts/backup.sh /backups
#
# What matters here is the SQLite copy. `cp` on a live SQLite database can
# capture a file mid-transaction, and in WAL mode the .db alone is incomplete
# because recent commits are still in the -wal sidecar. `sqlite3 .backup` uses
# the online backup API, which takes a consistent snapshot of a database that is
# being written to. That distinction is the difference between a backup and a
# file that looks like one.

set -eu

INSTANCE_DIR="${NUTRIMIND_INSTANCE_DIR:-instance}"
DEST="${1:-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
STAGE="$WORK/nutrimind-$STAMP"

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

if [ ! -d "$INSTANCE_DIR" ]; then
    echo "backup: no instance directory at '$INSTANCE_DIR'" >&2
    exit 1
fi

mkdir -p "$STAGE" "$DEST"

# ---- database -------------------------------------------------------------
DB="$INSTANCE_DIR/nutrimind.db"
if [ -f "$DB" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$DB" ".backup '$STAGE/nutrimind.db'"
        echo "backup: database captured with the SQLite online backup API"
    else
        # Python ships the same API, and the runtime image always has Python
        # even when it has no sqlite3 CLI.
        python -c "
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
src.close(); dst.close()
" "$DB" "$STAGE/nutrimind.db"
        echo "backup: database captured via python sqlite3.backup"
    fi
else
    echo "backup: no SQLite database found (PostgreSQL deployment?) — skipping" >&2
fi

# ---- everything else ------------------------------------------------------
# uploads/  the source PDFs; without them Re-index cannot rebuild anything
# chroma/   the vector index; rebuildable from uploads, but slowly
# secret_key  losing it signs every user out and invalidates reset links
for item in uploads chroma secret_key; do
    if [ -e "$INSTANCE_DIR/$item" ]; then
        cp -R "$INSTANCE_DIR/$item" "$STAGE/"
    fi
done

# Logs are deliberately not backed up: large, low value, and they would
# dominate the archive.

ARCHIVE="$DEST/nutrimind-$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$WORK" "nutrimind-$STAMP"

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo "backup: wrote $ARCHIVE ($SIZE)"

# ---- retention ------------------------------------------------------------
# Keep the 14 most recent. A backup disk that fills silently stops producing
# backups, which is discovered at exactly the wrong moment.
KEEP="${BACKUP_KEEP:-14}"
ls -1t "$DEST"/nutrimind-*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -f "$old"
    echo "backup: pruned $old"
done

echo "backup: OK"
