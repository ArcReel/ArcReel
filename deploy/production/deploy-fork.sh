#!/usr/bin/env bash
# StoryFlow fork 一键部署脚本
#
# 用法（在 VPS 上执行）：
#   bash /home/administrator/arcreel/deploy/production/deploy-fork.sh
#
# 设计前提：
#   - VPS 仓库只作为 storyflow/integration 的 checkout，不应有本地提交或修改
#   - fork-only 分支可能被 force-push，所以用 fetch + checkout -B 而非 pull --ff-only
#   - .env / pgdata / projects / vertex_keys / claude_data 均在 .gitignore，不会被覆盖

set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/administrator/arcreel}"
REMOTE="${REMOTE:-fork}"
BRANCH="${BRANCH:-storyflow/integration}"
COMPOSE_DIR="${COMPOSE_DIR:-$REPO_DIR/deploy/production}"

echo "==> 仓库: $REPO_DIR"
echo "==> 目标: $REMOTE/$BRANCH"

cd "$REPO_DIR"

echo "==> 拉取远端引用并清理已删分支"
git fetch "$REMOTE" --prune

echo "==> 强制对齐本地分支到 $REMOTE/$BRANCH"
git checkout -B "$BRANCH" "$REMOTE/$BRANCH"

echo "==> 当前 HEAD:"
git log --oneline -n 1

echo "==> 启动 docker compose"
cd "$COMPOSE_DIR"
# 先 build + 拉起所有 service（postgres 已运行则不动）
docker compose -f docker-compose.yml -f docker-compose.fork.yml up -d --build
# 再单独强制重建 arcreel，确保 security_opt / cap_add 等创建期参数改动落地。
# 限定 service 名避免无谓重启 postgres（数据无损但会断连 ~10s）。
docker compose -f docker-compose.yml -f docker-compose.fork.yml up -d --force-recreate --no-deps arcreel

echo "==> 部署完成"
docker compose -f docker-compose.yml -f docker-compose.fork.yml ps
