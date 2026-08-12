#!/usr/bin/env bash
# wait.sh — wait for one lightweight PR-state change, or until the polling delay expires.
#
# USAGE
#   bash wait.sh --repo-root <path> <PR_NUMBER> [--max <seconds>]
#
# Without --max, the first wait observed for a HEAD is 360 seconds and later waits on the
# same HEAD are 180 seconds.
#
# The probe reads only the PR head SHA, review submittedAt values, issue-comment
# updatedAt values, and Codex PR reactions. Any change returns immediately. GitHub
# 403/429 responses switch the remainder of this wait to sleep-only mode; other errors
# fail loudly with WAIT_ERROR.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/repo-context.sh"
enter_repo_root "WAIT_ERROR" "$@"
shift "$REPO_CONTEXT_SHIFT"

usage() {
  echo "WAIT_ERROR: Usage: bash wait.sh [--repo-root <path>] <PR_NUMBER> [--max <seconds>]" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
PR="$1"
shift
MAX_WAIT=""

if [[ $# -gt 0 ]]; then
  [[ $# -eq 2 && "$1" == "--max" ]] || usage
  MAX_WAIT="$2"
fi

[[ "$PR" =~ ^[0-9]+$ ]] || usage
[[ -z "$MAX_WAIT" || ("$MAX_WAIT" =~ ^[0-9]+$ && "$MAX_WAIT" -gt 0) ]] || usage

command -v gh >/dev/null 2>&1 || {
  echo "WAIT_ERROR: gh CLI not found on PATH" >&2
  exit 3
}
command -v jq >/dev/null 2>&1 || {
  echo "WAIT_ERROR: jq not found on PATH" >&2
  exit 3
}

REPO_SLUG=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>&1) || {
  if grep -Eq '(^|[^0-9])(403|429)([^0-9]|$)' <<<"$REPO_SLUG"; then
    sleep "${MAX_WAIT:-180}"
    exit 0
  fi
  echo "WAIT_ERROR: gh repo view failed" >&2
  echo "$REPO_SLUG" >&2
  exit 4
}
OWNER=${REPO_SLUG%%/*}
REPO=${REPO_SLUG#*/}
[[ "$REPO_SLUG" == */* && -n "$OWNER" && -n "$REPO" ]] || {
  echo "WAIT_ERROR: gh repo view returned an invalid repository slug: $REPO_SLUG" >&2
  exit 4
}

WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/pr-ai-review-wait.XXXXXX")
trap 'rm -rf "$WORKDIR"' EXIT
RATE_LIMITED=75

# GraphQL variable names are literals consumed by GitHub, not shell expansions.
# shellcheck disable=SC2016
REVIEWS_QUERY='query($owner:String!,$repo:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviews(first:100,after:$endCursor){nodes{submittedAt}pageInfo{hasNextPage endCursor}}}}}'
# shellcheck disable=SC2016
COMMENTS_QUERY='query($owner:String!,$repo:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){headRefOid comments(first:100,after:$endCursor){nodes{updatedAt}pageInfo{hasNextPage endCursor}}}}}'

is_rate_limited() {
  grep -Eq '(^|[^0-9])(403|429)([^0-9]|$)' "$1"
}

probe() {
  local reviews_json comments_json reactions_json

  if ! reviews_json=$(gh api graphql --paginate \
    -F owner="$OWNER" -F repo="$REPO" -F number="$PR" \
    -f query="$REVIEWS_QUERY" 2>"$WORKDIR/probe.err"); then
    is_rate_limited "$WORKDIR/probe.err" && return "$RATE_LIMITED"
    return 1
  fi
  if ! comments_json=$(gh api graphql --paginate \
    -F owner="$OWNER" -F repo="$REPO" -F number="$PR" \
    -f query="$COMMENTS_QUERY" 2>"$WORKDIR/probe.err"); then
    is_rate_limited "$WORKDIR/probe.err" && return "$RATE_LIMITED"
    return 1
  fi
  if ! reactions_json=$(gh api "repos/${REPO_SLUG}/issues/${PR}/reactions" --paginate \
    2>"$WORKDIR/probe.err"); then
    is_rate_limited "$WORKDIR/probe.err" && return "$RATE_LIMITED"
    return 1
  fi

  jq -n \
    --slurpfile reviews <(printf '%s\n' "$reviews_json") \
    --slurpfile comments <(printf '%s\n' "$comments_json") \
    --slurpfile reactions <(printf '%s\n' "$reactions_json") \
    '{
      head: $comments[0].data.repository.pullRequest.headRefOid,
      review_submitted_at:
        ([$reviews[].data.repository.pullRequest.reviews.nodes[]?.submittedAt] | max // null),
      comment_updated_at:
        ([$comments[].data.repository.pullRequest.comments.nodes[]?.updatedAt] | max // null),
      codex_reactions:
        ([$reactions[][]
          | select(.user.login == "chatgpt-codex-connector[bot]")
          | .content]
         | sort)
    }'
}

BASELINE_FILE="$WORKDIR/baseline.json"
if probe > "$BASELINE_FILE"; then
  :
else
  status=$?
  if [[ "$status" -eq "$RATE_LIMITED" ]]; then
    sleep "${MAX_WAIT:-180}"
    exit 0
  fi
  echo "WAIT_ERROR: GitHub probe failed" >&2
  cat "$WORKDIR/probe.err" >&2
  exit 5
fi

CURRENT_HEAD=$(jq -er '.head' "$BASELINE_FILE") || {
  echo "WAIT_ERROR: GitHub probe returned no head SHA" >&2
  exit 5
}
STATE_DIR="${TMPDIR:-/tmp}/pr-ai-review-loop-$(id -u)"
if [[ -L "$STATE_DIR" ]]; then
  echo "WAIT_ERROR: state dir is a symlink: $STATE_DIR" >&2
  exit 4
fi
if ! mkdir "$STATE_DIR" 2>/dev/null; then
  if [[ -L "$STATE_DIR" || ! -d "$STATE_DIR" || ! -O "$STATE_DIR" ]]; then
    echo "WAIT_ERROR: state dir is missing or not owned by the current user: $STATE_DIR" >&2
    exit 4
  fi
fi
chmod 700 "$STATE_DIR"
HEAD_STATE="$STATE_DIR/wait-${OWNER}-${REPO}-${PR}.head"
PREVIOUS_HEAD=""
[[ ! -f "$HEAD_STATE" ]] || PREVIOUS_HEAD=$(<"$HEAD_STATE")
if [[ -z "$MAX_WAIT" ]]; then
  if [[ "$PREVIOUS_HEAD" == "$CURRENT_HEAD" ]]; then
    MAX_WAIT=180
  else
    MAX_WAIT=360
  fi
fi
printf '%s\n' "$CURRENT_HEAD" > "$WORKDIR/head-state"
mv "$WORKDIR/head-state" "$HEAD_STATE"

elapsed=0
while (( elapsed < MAX_WAIT )); do
  interval=60
  remaining=$((MAX_WAIT - elapsed))
  (( remaining >= interval )) || interval=$remaining
  sleep "$interval"
  elapsed=$((elapsed + interval))

  CURRENT_FILE="$WORKDIR/current.json"
  if probe > "$CURRENT_FILE"; then
    :
  else
    status=$?
    if [[ "$status" -eq "$RATE_LIMITED" ]]; then
      remaining=$((MAX_WAIT - elapsed))
      (( remaining == 0 )) || sleep "$remaining"
      exit 0
    fi
    echo "WAIT_ERROR: GitHub probe failed" >&2
    cat "$WORKDIR/probe.err" >&2
    exit 5
  fi

  if ! cmp -s "$BASELINE_FILE" "$CURRENT_FILE"; then
    exit 0
  fi
done
