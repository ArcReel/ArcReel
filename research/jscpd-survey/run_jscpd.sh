#!/bin/sh
# 按 min-tokens 分层扫描后端 tests/，产出 json 报告到 raw/k<N>/
# 用法：sh research/jscpd-survey/run_jscpd.sh 10 25 50 75 100 150 200
set -e
cd "$(dirname "$0")/../.."
for k in "$@"; do
  echo "=== min-tokens $k ==="
  pnpm dlx jscpd tests --format python --min-tokens "$k" \
    --reporters json,console --output "research/jscpd-survey/raw/k$k" \
    --no-tips --no-colors 2>&1 | tail -8
done
