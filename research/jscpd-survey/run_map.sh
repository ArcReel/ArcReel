#!/bin/sh
# 对各档报告跑函数级映射，只打印统计头。
# 用法：sh research/jscpd-survey/run_map.sh <cover> <k...>
set -e
cd "$(dirname "$0")/../.."
cover=$1
shift
for k in "$@"; do
  echo "=== k=$k cover=$cover ==="
  python3 research/jscpd-survey/map_clones.py \
    "research/jscpd-survey/raw/k$k/jscpd-report.json" --cover "$cover" | head -6
done
