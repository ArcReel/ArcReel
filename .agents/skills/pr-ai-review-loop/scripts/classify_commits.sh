#!/usr/bin/env bash
# classify_commits.sh — emit metadata for each PR commit so Claude can judge "nit-only vs feature".
#
# USAGE
#   bash classify_commits.sh <PR_NUMBER> [SINCE_SHA]
#
# If SINCE_SHA omitted, returns all commits on the PR. With SINCE_SHA, returns commits AFTER that SHA
# (use to inspect just the latest push: pass the previous round's head).
#
# OUTPUT: JSON array, one object per commit:
# [
#   {
#     "sha":           "<full sha>",
#     "short":         "<short sha>",
#     "message_head":  "<first line of commit message>",
#     "message_full":  "<full commit message>",
#     "files_changed": <int>,
#     "lines_added":   <int>,
#     "lines_deleted": <int>,
#     "files":         ["path1", "path2", ...]
#   },
#   ...
# ]
#
# WHY
# Skill needs to judge whether the latest push is "fix-up only" (nit/format/typo/small bug) so it can:
#   (a) skip burning Gemini quota on a manual re-trigger (the conservative-trigger gate), and
#   (b) count consecutive nit-only rounds toward the convergence exit.
# Output is raw metadata (file count, line stats, message text); Claude makes the final call —
# scripting "is this nit?" would miss semantic cues like "fix typo in error message
# (1 line, 1 file)" being clearly nit vs "fix race in lock release (1 line, 1 file)" being NOT nit.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "POLL_ERROR: missing PR_NUMBER. Usage: bash classify_commits.sh <PR_NUMBER> [SINCE_SHA]" >&2
  exit 2
fi

PR="$1"
SINCE_SHA="${2:-}"

if ! command -v gh >/dev/null 2>&1; then
  echo "POLL_ERROR: gh CLI not found on PATH" >&2
  exit 3
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "POLL_ERROR: jq not found on PATH" >&2
  exit 3
fi

COMMITS_JSON=$(gh pr view "$PR" --json commits 2>/dev/null) || {
  echo "POLL_ERROR: gh pr view $PR failed" >&2
  exit 5
}

# Filter to commits after SINCE_SHA if provided.
if [[ -n "$SINCE_SHA" ]]; then
  FILTERED=$(echo "$COMMITS_JSON" | jq --arg since "$SINCE_SHA" '
    .commits as $all
    | ($all | map(.oid) | index($since)) as $idx
    | if $idx == null then $all else $all[$idx+1:] end
  ')
else
  FILTERED=$(echo "$COMMITS_JSON" | jq '.commits')
fi

# For each commit, pull file-level stats via git locally (gh doesn't expose per-commit stats cheaply).
echo "$FILTERED" | jq -c '.[]' | while IFS= read -r commit; do
  SHA=$(echo "$commit" | jq -r '.oid')
  MSG_FULL=$(echo "$commit" | jq -r '.messageHeadline + (if (.messageBody // "") == "" then "" else "\n\n" + .messageBody end)')
  MSG_HEAD=$(echo "$commit" | jq -r '.messageHeadline')

  # git show --numstat: one line per file: <added>\t<deleted>\t<path>
  if ! STATS=$(git show --numstat --format= "$SHA" 2>/dev/null); then
    echo "WARNING: git show --numstat failed for commit $SHA (shallow clone or missing object)" >&2
    STATS=""
  fi

  # git show --numstat uses TAB as separator; default awk splits on any whitespace
  # and breaks on filenames containing spaces. Pin -F'\t' explicitly.
  FILES_JSON=$(echo "$STATS" | awk -F'\t' 'NF==3 {print $3}' | jq -R . | jq -s .)
  FILES_COUNT=$(echo "$FILES_JSON" | jq 'length')
  LINES_ADDED=$(echo "$STATS" | awk -F'\t' 'NF==3 && $1 ~ /^[0-9]+$/ {s+=$1} END {print s+0}')
  LINES_DELETED=$(echo "$STATS" | awk -F'\t' 'NF==3 && $2 ~ /^[0-9]+$/ {s+=$2} END {print s+0}')

  jq -n \
    --arg sha "$SHA" \
    --arg short "${SHA:0:7}" \
    --arg head "$MSG_HEAD" \
    --arg full "$MSG_FULL" \
    --argjson files "$FILES_JSON" \
    --argjson fc "$FILES_COUNT" \
    --argjson la "$LINES_ADDED" \
    --argjson ld "$LINES_DELETED" \
    '{
      sha: $sha,
      short: $short,
      message_head: $head,
      message_full: $full,
      files_changed: $fc,
      lines_added: $la,
      lines_deleted: $ld,
      files: $files
    }'
done | jq -s .
