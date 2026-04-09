#!/bin/bash
# Download Grok API documentation pages to the current directory
# Usage: bash download_docs.sh

set -e

BASE="https://docs.x.ai/docs"

pages=(
  "overview"
  "api-reference"
  "models"
  "image-generation"
  "video-generation"
  "text-generation"
)

for page in "${pages[@]}"; do
  url="${BASE}/${page}"
  out="${page}.md"
  echo "Downloading: $url -> $out"
  curl -s "$url" -o "$out"
done

echo "Done."
