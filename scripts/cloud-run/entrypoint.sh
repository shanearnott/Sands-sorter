#!/usr/bin/env sh
# Cloud Run container entrypoint.
#
# Modes:
#   serve   (default) — apply alembic migrations, then run uvicorn on $PORT.
#   migrate           — apply alembic migrations and exit (used as a Cloud Run job).
#   poll              — invoke `python -m app.worker poll` and exit.
#   summarize         — invoke `python -m app.worker summarize` and exit.
#
# Cloud Run sets $PORT for the HTTP service. We default to 8080 if it isn't set
# (e.g. when running the image locally).
set -eu

mode="${1:-serve}"
port="${PORT:-8080}"

case "$mode" in
  serve)
    echo "[entrypoint] alembic upgrade head" >&2
    alembic upgrade head
    echo "[entrypoint] uvicorn app.main:app --host 0.0.0.0 --port $port" >&2
    exec uvicorn app.main:app --host 0.0.0.0 --port "$port"
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  poll)
    exec python -m app.worker poll
    ;;
  summarize)
    shift || true
    exec python -m app.worker summarize "$@"
    ;;
  *)
    echo "[entrypoint] unknown mode: $mode" >&2
    exit 64
    ;;
esac
