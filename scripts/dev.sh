#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_PID=""

cleanup() {
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    echo
    echo "Stopping ArcReel backend..."
    kill "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

cd "${ROOT_DIR}"

if [[ ! -d "${ROOT_DIR}/frontend/node_modules" ]]; then
  echo "Error: frontend dependencies are not installed."
  echo "Run: cd frontend && pnpm install"
  exit 1
fi

echo "Applying database migrations..."
uv run alembic upgrade head

echo "Starting ArcReel backend on http://127.0.0.1:1241"
uv run uvicorn server.app:app \
  --reload \
  --reload-dir server \
  --reload-dir lib \
  --port 1241 &
BACKEND_PID=$!

echo "Starting ArcReel frontend on http://localhost:5173"
cd "${ROOT_DIR}/frontend"
pnpm dev
