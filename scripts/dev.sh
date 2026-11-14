#!/usr/bin/env bash
# Starts the backend (port 8077) and the frontend dev server (port 5277) together.
set -euo pipefail
cd "$(dirname "$0")/.."
ls data/cache/horizon_db_Site01_*m.npz >/dev/null 2>&1 || { echo "No horizon database - run: .venv/bin/python scripts/build_horizon_db.py --site Site01 --spacing 50"; exit 1; }
.venv/bin/uvicorn app:app --app-dir backend --port 8077 &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
cd frontend && npm run dev
