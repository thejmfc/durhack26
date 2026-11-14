#!/usr/bin/env bash
# Starts the global explorer and the optional high-resolution site workflows.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${SKYLINE_PYTHON:-}" ]]; then
  if [[ -x backend/.venv/bin/python ]]; then SKYLINE_PYTHON=backend/.venv/bin/python
  elif [[ -x .venv/bin/python ]]; then SKYLINE_PYTHON=.venv/bin/python
  elif [[ -x scripts/.venv/bin/python ]]; then SKYLINE_PYTHON=scripts/.venv/bin/python
  else SKYLINE_PYTHON=python3; fi
fi
[[ -f frontend/public/data/moon.json ]] || { echo "Prepare terrain first: $SKYLINE_PYTHON scripts/build_moon.py"; exit 1; }
"$SKYLINE_PYTHON" -c 'import uvicorn, multipart, rasterio, pyproj, certifi' 2>/dev/null || { echo "Sync backend dependencies with: (cd backend && uv sync), or install requirements.txt into $SKYLINE_PYTHON."; exit 1; }
BACKEND_PORT="${SKYLINE_BACKEND_PORT:-8077}"
FRONTEND_PORT="${SKYLINE_FRONTEND_PORT:-5277}"
WATCHFILES_FORCE_POLLING="${WATCHFILES_FORCE_POLLING:-true}" "$SKYLINE_PYTHON" -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port "$BACKEND_PORT" --reload --reload-dir backend &
BACK=$!
trap 'kill "$BACK" 2>/dev/null || true' EXIT
cd frontend
SKYLINE_API_TARGET="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --port "$FRONTEND_PORT"
