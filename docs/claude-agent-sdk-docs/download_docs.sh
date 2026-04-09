#!/bin/bash
# Download Claude Agent SDK documentation pages to the current directory
# Usage: bash download_docs.sh

set -e

BASE="https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/sdk"

pages=(
  "overview"
  "authentication"
  "basic-usage"
  "streaming"
  "tool-use"
  "memory-and-storage"
  "agents"
)

for page in "${pages[@]}"; do
  url="${BASE}/${page}"
  out="${page}.md"
  echo "Downloading: $url -> $out"
  curl -s "$url" -o "$out"
done

echo "Done."
