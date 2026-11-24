#!/usr/bin/env bash
# Convenience launcher for environments without Docker.
set -euo pipefail
cd "$(dirname "$0")"
export DB_PATH="${DB_PATH:-$(pwd)/data/bangdan.db}"
export PORT="${PORT:-8000}"
mkdir -p data
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
