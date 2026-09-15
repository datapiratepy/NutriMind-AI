#!/bin/sh
set -e

python scripts/upgrade_database.py

exec waitress-serve --host=0.0.0.0 --port="${PORT:-8000}" --threads=8 --channel-timeout=120 wsgi:app