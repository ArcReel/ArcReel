#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob
for f in "$DUMP_DIR"/*.progress; do
  echo "::group::$(basename "$f") 最后的进展"
  tail -n 5 "$f"
  echo "::endgroup::"
done
for f in "$DUMP_DIR"/*.stacks "$DUMP_DIR"/*.SIG*; do
  [ -s "$f" ] || continue
  echo "===== $(basename "$f")"
  cat "$f"
done
sudo dmesg --ctime | grep -iE 'out of memory|oom|killed process|segfault' || echo "内核日志里没有 OOM 或段错误记录"
