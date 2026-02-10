#!/bin/bash
# 下载 Claude Agent SDK 文档到当前目录
# 用法: cd docs/claude-agent-sdk-docs && bash download_docs.sh

set -euo pipefail

BASE_URL="https://platform.claude.com/docs/en/agent-sdk"
OUTPUT_DIR="$(cd "$(dirname "$0")" && pwd)"

DOCS=(
  "overview"
  "streaming-vs-single-mode"
  "streaming-output"
  "stop-reasons"
  "permissions"
  "user-input"
  "hooks"
  "sessions"
  "file-checkpointing"
  "hosting"
  "secure-deployment"
  "subagents"
  "slash-commands"
  "skills"
  "todo-tracking"
)

echo "下载目录: $OUTPUT_DIR"
echo "共 ${#DOCS[@]} 个文档待下载"
echo "---"

success=0
fail=0

for doc in "${DOCS[@]}"; do
  url="${BASE_URL}/${doc}.md"
  output="${OUTPUT_DIR}/${doc}.md"

  echo -n "下载 ${doc}.md ... "

  if curl -fsSL "$url" -o "$output" 2>/dev/null; then
    size=$(wc -c < "$output" | tr -d ' ')
    echo "成功 (${size} bytes)"
    ((success++))
  else
    echo "失败，尝试从页面提取..."
    # 如果 .md 直接下载失败，尝试抓取 HTML 页面
    page_url="${BASE_URL}/${doc}"
    if curl -fsSL "$page_url" -o "${output}.html" 2>/dev/null; then
      # 保留 HTML 备用，标记需要手动处理
      mv "${output}.html" "$output"
      size=$(wc -c < "$output" | tr -d ' ')
      echo "已保存 HTML (${size} bytes)"
      ((success++))
    else
      echo "失败"
      ((fail++))
    fi
  fi
done

echo "---"
echo "完成: ${success} 成功, ${fail} 失败"
