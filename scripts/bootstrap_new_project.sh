#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a clean project copy from current repository.
# Runtime artifacts and local secrets are excluded by default.

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <target_dir> [--init-git]"
  echo "Example: $0 ../ArcReel --init-git"
  exit 1
fi

TARGET_DIR="$1"
INIT_GIT="false"
if [[ "${2:-}" == "--init-git" ]]; then
  INIT_GIT="true"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -e "${TARGET_DIR}" ]]; then
  echo "Error: target path already exists: ${TARGET_DIR}"
  exit 1
fi

mkdir -p "${TARGET_DIR}"

if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '.git' \
    --exclude '.idea' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    --exclude '.env' \
    --exclude 'projects/*' \
    --exclude 'vertex_keys/*' \
    "${SOURCE_DIR}/" "${TARGET_DIR}/"
else
  # Fallback copy without rsync.
  cp -R "${SOURCE_DIR}/." "${TARGET_DIR}/"
  rm -rf "${TARGET_DIR}/.git" \
         "${TARGET_DIR}/.idea" \
         "${TARGET_DIR}/.venv" \
         "${TARGET_DIR}/vertex_keys" \
         "${TARGET_DIR}/projects"
  rm -f "${TARGET_DIR}/.env"
  mkdir -p "${TARGET_DIR}/projects"
  touch "${TARGET_DIR}/projects/.gitkeep"
fi

mkdir -p "${TARGET_DIR}/projects"
touch "${TARGET_DIR}/projects/.gitkeep"
rm -f "${TARGET_DIR}/projects/.api_usage.db"

if [[ "${INIT_GIT}" == "true" ]]; then
  (
    cd "${TARGET_DIR}"
    git init >/dev/null
    git add .
    git commit -m "chore: initialize project from template" >/dev/null
  )
fi

echo "Done: ${TARGET_DIR}"
echo "Next steps:"
echo "  1) cd ${TARGET_DIR}"
echo "  2) cp .env.example .env"
echo "  3) uv sync"
