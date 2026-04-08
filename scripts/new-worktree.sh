#!/usr/bin/env bash
# new-worktree.sh — Create an isolated git worktree and sync local environment files
#
# Usage: scripts/new-worktree.sh <branch-name> [base-ref]
#   branch-name: local branch name for the new worktree (also used as directory name)
#   base-ref:    optional, which ref to base on (e.g. origin/feature/xxx), defaults to HEAD

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <branch-name> [base-ref]"
  echo "  branch-name: worktree directory name and local branch name"
  echo "  base-ref:    which ref to base on (default: HEAD)"
  exit 1
fi

BRANCH_NAME="$1"
BASE_REF="${2:-HEAD}"
ROOT=$(git rev-parse --show-toplevel)
TARGET="$ROOT/.worktrees/$BRANCH_NAME"

# --- Create worktree ---
if [ -d "$TARGET" ]; then
  echo "ERROR: Directory already exists: $TARGET"
  exit 1
fi

if [ "$BASE_REF" = "HEAD" ]; then
  git worktree add "$TARGET" -b "$BRANCH_NAME"
else
  git worktree add "$TARGET" --track -b "$BRANCH_NAME" "$BASE_REF"
fi
echo "Created worktree: $TARGET"

# --- Sync local environment files ---
echo ""
echo "Syncing local environment files..."

# .claude/settings.local.json
if [ -f "$ROOT/.claude/settings.local.json" ]; then
  mkdir -p "$TARGET/.claude"
  cp "$ROOT/.claude/settings.local.json" "$TARGET/.claude/settings.local.json"
  echo "  .claude/settings.local.json"
else
  echo "  - .claude/settings.local.json does not exist, skipped"
fi

# .env
if [ -f "$ROOT/.env" ]; then
  cp "$ROOT/.env" "$TARGET/.env"
  echo "  .env"
else
  echo "  - .env does not exist, skipped"
fi

# projects/ — symlink to share data
if [ -d "$ROOT/projects" ]; then
  rm -rf "$TARGET/projects"
  ln -s "$ROOT/projects" "$TARGET/projects"
  git -C "$TARGET" ls-files projects/ | xargs -r git -C "$TARGET" update-index --skip-worktree
  echo "  projects/ -> $ROOT/projects (symlink)"
else
  echo "  - projects/ does not exist, skipped"
fi

# .vscode/
if [ -d "$ROOT/.vscode" ]; then
  cp -r "$ROOT/.vscode" "$TARGET/.vscode"
  echo "  .vscode/"
else
  echo "  - .vscode/ does not exist, skipped"
fi

# --- Install dependencies ---
echo ""
echo "Installing project dependencies..."

if [ -f "$TARGET/pyproject.toml" ]; then
  (cd "$TARGET" && uv sync)
  echo "  Python dependencies (uv sync)"
fi

if [ -f "$TARGET/frontend/package.json" ]; then
  (cd "$TARGET/frontend" && pnpm install)
  echo "  Frontend dependencies (pnpm install)"
fi

# --- Done ---
echo ""
echo "========================================="
echo "Worktree ready: $TARGET"
echo "Branch: $BRANCH_NAME"
echo "========================================="
