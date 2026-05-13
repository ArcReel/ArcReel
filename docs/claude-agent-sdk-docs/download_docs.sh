#!/bin/bash
# 下载 Claude Agent SDK 文档到当前目录
# 用法: cd docs/claude-agent-sdk-docs && bash download_docs.sh

set -euo pipefail

BASE_URL="https://code.claude.com/docs/en/agent-sdk"
ROOT_BASE_URL="https://code.claude.com/docs/en"
OUTPUT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 根路径下（非 agent-sdk 子目录）的文档
ROOT_DOCS=(
  "sandboxing"
)

DOCS=(
  # 顶层
  "overview"
  "quickstart"
  "agent-loop"
  # "migration-guide"  # TS/旧 SDK 迁移指南，不需要

  # Guides
  "claude-code-features"
  "streaming-vs-single-mode"
  "streaming-output"
  "permissions"
  "user-input"
  "hooks"
  "file-checkpointing"
  "structured-outputs"
  "hosting"
  "secure-deployment"
  "modifying-system-prompts"
  "mcp"
  "custom-tools"
  "tool-search"
  "subagents"
  "slash-commands"
  "skills"
  "cost-tracking"
  "observability"
  "todo-tracking"
  "plugins"

  "sessions"

  # SDK References
  "python"
  # "typescript"             # TS 参考，不需要
  # "typescript-v2-preview"  # TS V2 预览，不需要
)

total=$(( ${#DOCS[@]} + ${#ROOT_DOCS[@]} ))
echo "下载目录: $OUTPUT_DIR"
echo "共 ${total} 个文档待下载（agent-sdk: ${#DOCS[@]}，根路径: ${#ROOT_DOCS[@]}）"
echo "---"

success=0
fail=0

download_one() {
  local base="$1"
  local doc="$2"
  local url="${base}/${doc}.md"
  local output="${OUTPUT_DIR}/${doc}.md"

  echo -n "下载 ${doc}.md (from ${base}) ... "

  if curl -fsSL "$url" -o "$output" 2>/dev/null; then
    local size
    size=$(wc -c < "$output" | tr -d ' ')
    echo "成功 (${size} bytes)"
    success=$((success + 1))
    return
  fi

  echo -n "直链失败，回退抓 HTML... "
  local page_url="${base}/${doc}"
  if curl -fsSL "$page_url" -o "${output}.html" 2>/dev/null; then
    mv "${output}.html" "$output"
    local size
    size=$(wc -c < "$output" | tr -d ' ')
    echo "已保存 HTML (${size} bytes)"
    success=$((success + 1))
  else
    echo "失败"
    fail=$((fail + 1))
  fi
}

for doc in "${DOCS[@]}"; do
  download_one "$BASE_URL" "$doc"
done

for doc in "${ROOT_DOCS[@]}"; do
  download_one "$ROOT_BASE_URL" "$doc"
done

echo "---"
echo "完成: ${success} 成功, ${fail} 失败"
