#!/bin/bash
# 下载 Grok API 文档到当前目录
# 用法: cd docs/grok-docs && bash download_docs.sh

set -euo pipefail

BASE_URL="https://docs.x.ai/developers"
OUTPUT_DIR="$(cd "$(dirname "$0")" && pwd)"

# URL 路径 → 本地文件名（用平行数组兼容 bash 3.x）
PATHS=(
  "models.md"
  "model-capabilities/images/generation.md"
  "model-capabilities/video/generation.md"
)
FILENAMES=(
  "models.md"
  "images-generation.md"
  "video-generation.md"
)

echo "下载目录: $OUTPUT_DIR"
echo "共 ${#PATHS[@]} 个文档待下载"
echo "---"

success=0
fail=0

# 防止 ((x++)) 在 x=0 时因返回值 1 触发 set -e
incr_success() { success=$((success + 1)); }
incr_fail() { fail=$((fail + 1)); }

for i in "${!PATHS[@]}"; do
  path="${PATHS[$i]}"
  filename="${FILENAMES[$i]}"
  url="${BASE_URL}/${path}"
  output="${OUTPUT_DIR}/${filename}"

  echo -n "下载 ${filename} ... "

  if curl -fsSL "$url" -o "$output" 2>/dev/null; then
    size=$(wc -c < "$output" | tr -d ' ')
    if [ "$size" -gt 0 ]; then
      echo "成功 (${size} bytes)"
      incr_success
    else
      echo "失败 (空文件)"
      rm -f "$output"
      incr_fail
    fi
  else
    echo "失败，尝试不带 .md 后缀..."
    # 尝试不带 .md 的 URL
    alt_url="${BASE_URL}/${path%.md}"
    if curl -fsSL "$alt_url" -o "$output" 2>/dev/null; then
      size=$(wc -c < "$output" | tr -d ' ')
      if [ "$size" -gt 0 ]; then
        echo "已保存 (${size} bytes)"
        incr_success
      else
        echo "失败 (空文件)"
        rm -f "$output"
        incr_fail
      fi
    else
      echo "失败"
      incr_fail
    fi
  fi
done

echo "---"
echo "完成: ${success} 成功, ${fail} 失败"
