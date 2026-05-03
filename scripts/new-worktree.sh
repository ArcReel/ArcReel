#!/usr/bin/env bash
# new-worktree.sh — 创建隔离 git worktree 并同步本地环境文件
#
# Usage: scripts/new-worktree.sh <branch-name> [base-ref]
#   branch-name: 新 worktree 的本地分支名（也用作目录名）
#   base-ref:    可选，基于哪个 ref 创建（如 origin/feature/xxx），默认 HEAD

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <branch-name> [base-ref]"
  echo "  branch-name: worktree 目录名及本地分支名"
  echo "  base-ref:    基于哪个 ref 创建（默认 HEAD）"
  exit 1
fi

BRANCH_NAME="$1"
BASE_REF="${2:-HEAD}"
ROOT=$(git rev-parse --show-toplevel)
TARGET="$ROOT/.worktrees/$BRANCH_NAME"

# --- 创建 worktree ---
if [ -d "$TARGET" ]; then
  echo "✗ 目录已存在: $TARGET"
  exit 1
fi

if [ "$BASE_REF" = "HEAD" ]; then
  git worktree add "$TARGET" -b "$BRANCH_NAME"
else
  git worktree add "$TARGET" --track -b "$BRANCH_NAME" "$BASE_REF"
fi
echo "✓ 已创建 worktree: $TARGET"

# --- 同步本地环境文件 ---
echo ""
echo "同步本地环境文件..."

# .claude/settings.local.json
if [ -f "$ROOT/.claude/settings.local.json" ]; then
  mkdir -p "$TARGET/.claude"
  cp "$ROOT/.claude/settings.local.json" "$TARGET/.claude/settings.local.json"
  echo "  ✓ .claude/settings.local.json"
else
  echo "  - .claude/settings.local.json 不存在，已跳过"
fi

# .env
if [ -f "$ROOT/.env" ]; then
  cp "$ROOT/.env" "$TARGET/.env"
  echo "  ✓ .env"
else
  echo "  - .env 不存在，已跳过"
fi

# projects/ — 不再链接，避免 alembic migration 跨 worktree 污染共享 DB
# 各 worktree 首次运行时各自生成 projects/.arcreel.db，互不影响
echo "  - projects/ 不链接（DB 隔离）；如需共享数据请手动复制具体子目录"

# .vscode/
if [ -d "$ROOT/.vscode" ]; then
  cp -r "$ROOT/.vscode" "$TARGET/.vscode"
  echo "  ✓ .vscode/"
else
  echo "  - .vscode/ 不存在，已跳过"
fi

# --- 安装依赖 ---
echo ""
echo "安装项目依赖..."

if [ -f "$TARGET/pyproject.toml" ]; then
  (cd "$TARGET" && uv sync)
  echo "  ✓ Python 依赖 (uv sync)"
fi

if [ -f "$TARGET/frontend/package.json" ]; then
  (cd "$TARGET/frontend" && pnpm install)
  echo "  ✓ 前端依赖 (pnpm install)"
fi

# --- 完成 ---
echo ""
echo "========================================="
echo "Worktree 已就绪: $TARGET"
echo "分支: $BRANCH_NAME"
echo "========================================="
