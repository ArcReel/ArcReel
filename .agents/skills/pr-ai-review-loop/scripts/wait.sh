#!/usr/bin/env bash
# wait.sh — wait for one decision-relevant PR-state change or the fixed safety timeout.
#
# USAGE
#   bash wait.sh --repo-root <path> <PR_NUMBER> [--max <seconds>]
#
# The default wait uses 1770 seconds of a 30-minute command budget, leaving 30 seconds
# for setup and result delivery. --max is an override for tests and manual diagnostics.
# Every probe is an atomic fingerprint of reviews, comments, reactions, inline comments,
# check runs, and the PR/base code-scanning alert sets. Truncated output, network errors,
# rate limits, and HTTP 5xx responses retry the whole probe up to three times. Authentication,
# permission, and other request errors fail loudly with WAIT_ERROR.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/repo-context.sh"
enter_repo_root "WAIT_ERROR" "$@"
shift "$REPO_CONTEXT_SHIFT"

DEFAULT_MAX_WAIT=1770
POLL_INTERVAL=60
RETRY_DELAYS=(2 5 10)

usage() {
  echo "WAIT_ERROR: Usage: bash wait.sh [--repo-root <path>] <PR_NUMBER> [--max <seconds>]" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
PR="$1"
shift
MAX_WAIT="$DEFAULT_MAX_WAIT"

if [[ $# -gt 0 ]]; then
  [[ $# -eq 2 && "$1" == "--max" ]] || usage
  MAX_WAIT="$2"
fi

[[ "$PR" =~ ^[0-9]+$ ]] || usage
[[ "$MAX_WAIT" =~ ^[0-9]+$ && "$MAX_WAIT" -gt 0 ]] || usage

command -v gh >/dev/null 2>&1 || {
  echo "WAIT_ERROR: gh CLI not found on PATH" >&2
  exit 3
}
command -v jq >/dev/null 2>&1 || {
  echo "WAIT_ERROR: jq not found on PATH" >&2
  exit 3
}

WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/pr-ai-review-wait.XXXXXX")
trap 'rm -rf "$WORKDIR"' EXIT
WATCHDOG_SEQUENCE=0

STARTED_AT=$(date +%s)
DEADLINE=$((STARTED_AT + MAX_WAIT))
FAILURE_KIND=""
LAST_ERROR_FILE="$WORKDIR/probe.err"
RETRY_AFTER=0

report_timeout() {
  printf 'WAIT_TIMEOUT: no relevant PR state change within %s seconds\n' "$MAX_WAIT"
}

run_until_deadline() {
  local output_file="$1"
  local error_file="$2"
  local now remaining command_pid watchdog_pid status watchdog_fifo
  shift 2

  now=$(date +%s)
  remaining=$((DEADLINE - now))
  (( remaining > 0 )) || {
    printf 'GitHub request skipped because the wait deadline elapsed\n' > "$error_file"
    return 124
  }

  rm -f "$WORKDIR/request-timed-out"
  WATCHDOG_SEQUENCE=$((WATCHDOG_SEQUENCE + 1))
  watchdog_fifo="$WORKDIR/watchdog-${WATCHDOG_SEQUENCE}.fifo"
  mkfifo "$watchdog_fifo"
  exec 9<> "$watchdog_fifo"
  "$@" > "$output_file" 2> "$error_file" &
  command_pid=$!
  (
    IFS= read -r -t "$remaining" -u 9 && exit 0
    if kill -0 "$command_pid" 2>/dev/null; then
      printf 'GitHub request exceeded the wait deadline\n' > "$WORKDIR/request-timed-out"
      kill -TERM "$command_pid" 2>/dev/null || true
    fi
  ) &
  watchdog_pid=$!

  if wait "$command_pid" 2>/dev/null; then
    status=0
  else
    status=$?
  fi
  printf '\n' >&9
  wait "$watchdog_pid" 2>/dev/null || true
  exec 9>&-
  rm -f "$watchdog_fifo"

  if [[ -f "$WORKDIR/request-timed-out" ]]; then
    cat "$WORKDIR/request-timed-out" >> "$error_file"
    return 124
  fi
  return "$status"
}

classify_failure() {
  local error_file="$1"
  local retry_hint

  FAILURE_KIND="fatal"
  RETRY_AFTER=0

  if grep -Eiq 'rate[ -]?limit|secondary rate|abuse detection' "$error_file" \
    || grep -Eiq '(^|[^0-9])429([^0-9]|$)' "$error_file"; then
    FAILURE_KIND="transient"
    retry_hint=$(sed -nE 's/.*[Rr]etry-[Aa]fter[:= ]+([0-9]+).*/\1/p' "$error_file" | head -n 1)
    if [[ "$retry_hint" =~ ^[0-9]+$ ]]; then
      RETRY_AFTER="$retry_hint"
    fi
  elif grep -Eiq '(^|[^0-9])5[0-9]{2}([^0-9]|$)' "$error_file" \
    || grep -Eiq 'unexpected.*(EOF|end of)|(^|[^a-z])EOF([^a-z]|$)|timed? out|timeout|connection.*(reset|closed|refused)|connect:.*refused|TLS|temporary|temporarily unavailable|network is unreachable|could not resolve|no such host|dial tcp' "$error_file"; then
    FAILURE_KIND="transient"
  fi
}

strip_http_headers() {
  awk '
    /^HTTP\/[0-9.]+ [0-9][0-9][0-9]/ { in_headers = 1; next }
    in_headers && ($0 == "" || $0 == "\r") { in_headers = 0; next }
    in_headers { next }
    { print }
  ' "$1"
}

fetch_json() {
  local output_file="$1"
  local error_file="$2"
  local availability="$3"
  local raw_output="${output_file}.raw"
  local command_status
  shift 3

  LAST_ERROR_FILE="$error_file"
  : > "$error_file"
  if run_until_deadline "$raw_output" "$error_file" "$@"; then
    strip_http_headers "$raw_output" > "$output_file"
    if jq -e -s 'length > 0' "$output_file" >/dev/null 2> "$error_file"; then
      return 0
    fi
    printf 'GitHub returned truncated or invalid JSON\n' >> "$error_file"
    FAILURE_KIND="transient"
    RETRY_AFTER=0
    return 1
  else
    command_status=$?
  fi

  if [[ "$command_status" -eq 124 ]]; then
    FAILURE_KIND="transient"
    RETRY_AFTER=0
    return 1
  fi

  grep -Ei '^(HTTP/|Retry-After:)' "$raw_output" >> "$error_file" || true
  if [[ "$availability" == "optional" ]] \
    && grep -Eq '(^|[^0-9])(404|422)([^0-9]|$)' "$error_file"; then
    FAILURE_KIND="unavailable"
    RETRY_AFTER=0
    return 1
  fi

  classify_failure "$error_file"
  return 1
}

sleep_for_retry() {
  local requested="$1"
  local now remaining

  now=$(date +%s)
  remaining=$((DEADLINE - now))
  (( remaining > 0 )) || return 1
  if (( requested >= remaining )); then
    sleep "$remaining"
    return 1
  fi
  sleep "$requested"
}

retry_operation() {
  local operation="$1"
  local output_file="$2"
  local attempt delay

  for attempt in 0 1 2 3; do
    if (( attempt > 0 )); then
      delay="${RETRY_DELAYS[attempt - 1]}"
      if (( RETRY_AFTER > delay )); then
        delay="$RETRY_AFTER"
      fi
      sleep_for_retry "$delay" || return 124
    fi

    FAILURE_KIND=""
    RETRY_AFTER=0
    if "$operation" "$output_file"; then
      return 0
    fi
    [[ "$FAILURE_KIND" == "transient" ]] || return 1
  done
  return 1
}

repo_view_once() {
  local output_file="$1"
  local error_file="$WORKDIR/repo-view.err"
  local repo_slug
  local command_status

  LAST_ERROR_FILE="$error_file"
  : > "$error_file"
  if run_until_deadline "$output_file" "$error_file" \
    gh repo view --json nameWithOwner --jq .nameWithOwner; then
    repo_slug=$(<"$output_file")
    if [[ "$repo_slug" =~ ^[^/[:space:]]+/[^/[:space:]]+$ ]]; then
      return 0
    fi
    printf 'GitHub returned a truncated or invalid repository slug\n' > "$error_file"
    FAILURE_KIND="transient"
    RETRY_AFTER=0
    return 1
  else
    command_status=$?
  fi

  if [[ "$command_status" -eq 124 ]]; then
    FAILURE_KIND="transient"
    RETRY_AFTER=0
    return 1
  fi

  classify_failure "$error_file"
  return 1
}

if retry_operation repo_view_once "$WORKDIR/repo-slug"; then
  :
else
  status=$?
  if [[ "$status" -eq 124 ]]; then
    report_timeout
    exit 0
  fi
  echo "WAIT_ERROR: gh repo view failed" >&2
  cat "$LAST_ERROR_FILE" >&2
  exit 4
fi

REPO_SLUG=$(<"$WORKDIR/repo-slug")
OWNER=${REPO_SLUG%%/*}
REPO=${REPO_SLUG#*/}

# GraphQL variable names are literals consumed by GitHub, not shell expansions.
# shellcheck disable=SC2016
REVIEWS_QUERY='query($owner:String!,$repo:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviews(first:100,after:$endCursor){nodes{id submittedAt updatedAt state commit{oid}}pageInfo{hasNextPage endCursor}}}}}'
# shellcheck disable=SC2016
COMMENTS_QUERY='query($owner:String!,$repo:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){headRefOid comments(first:100,after:$endCursor){nodes{id updatedAt reactionGroups{content reactors(first:1){totalCount}}}pageInfo{hasNextPage endCursor}}}}}'

probe_once() {
  local output_file="$1"
  local security_available=true

  fetch_json "$WORKDIR/reviews.json" "$WORKDIR/reviews.err" required \
    gh api --include graphql --paginate \
    -F owner="$OWNER" -F repo="$REPO" -F number="$PR" \
    -f query="$REVIEWS_QUERY" || return 1

  fetch_json "$WORKDIR/comments.json" "$WORKDIR/comments.err" required \
    gh api --include graphql --paginate \
    -F owner="$OWNER" -F repo="$REPO" -F number="$PR" \
    -f query="$COMMENTS_QUERY" || return 1

  fetch_json "$WORKDIR/reactions.json" "$WORKDIR/reactions.err" required \
    gh api --include "repos/${REPO_SLUG}/issues/${PR}/reactions" --paginate || return 1

  fetch_json "$WORKDIR/inline-comments.json" "$WORKDIR/inline-comments.err" required \
    gh api --include "repos/${REPO_SLUG}/pulls/${PR}/comments" --paginate || return 1

  local head_sha
  head_sha=$(jq -er -s '.[0].data.repository.pullRequest.headRefOid' "$WORKDIR/comments.json" 2> "$WORKDIR/head.err") || {
    LAST_ERROR_FILE="$WORKDIR/head.err"
    printf 'GitHub probe returned no head SHA\n' >> "$LAST_ERROR_FILE"
    FAILURE_KIND="transient"
    RETRY_AFTER=0
    return 1
  }

  fetch_json "$WORKDIR/check-runs.json" "$WORKDIR/check-runs.err" required \
    gh api --include "repos/${REPO_SLUG}/commits/${head_sha}/check-runs?per_page=100" --paginate || return 1

  if fetch_json "$WORKDIR/security-alerts-pr.json" "$WORKDIR/security-alerts-pr.err" optional \
    gh api --include "repos/${REPO_SLUG}/code-scanning/alerts?ref=refs/pull/${PR}/merge&state=open&per_page=100" --paginate; then
    :
  elif [[ "$FAILURE_KIND" == "unavailable" ]]; then
    security_available=false
    printf '[]\n' > "$WORKDIR/security-alerts-pr.json"
  else
    return 1
  fi

  if fetch_json "$WORKDIR/security-alerts-base.json" "$WORKDIR/security-alerts-base.err" optional \
    gh api --include "repos/${REPO_SLUG}/code-scanning/alerts?state=open&per_page=100" --paginate; then
    :
  elif [[ "$FAILURE_KIND" == "unavailable" ]]; then
    security_available=false
    printf '[]\n' > "$WORKDIR/security-alerts-base.json"
  else
    return 1
  fi

  LAST_ERROR_FILE="$WORKDIR/probe.err"
  : > "$LAST_ERROR_FILE"
  if ! jq -n \
    --slurpfile reviews "$WORKDIR/reviews.json" \
    --slurpfile comments "$WORKDIR/comments.json" \
    --slurpfile reactions "$WORKDIR/reactions.json" \
    --slurpfile inline_comments "$WORKDIR/inline-comments.json" \
    --slurpfile check_runs "$WORKDIR/check-runs.json" \
    --slurpfile security_alerts_pr "$WORKDIR/security-alerts-pr.json" \
    --slurpfile security_alerts_base "$WORKDIR/security-alerts-base.json" \
    --argjson security_available "$security_available" \
    '
    if any($reviews[]; .errors? != null) or any($comments[]; .errors? != null) then
      error("GraphQL response contains errors")
    else
      {
        head: $comments[0].data.repository.pullRequest.headRefOid,
        reviews:
          ([$reviews[].data.repository.pullRequest.reviews.nodes[]?
            | {id, submitted_at: .submittedAt, updated_at: .updatedAt,
               state, commit_oid: .commit.oid}]
           | sort_by(.id)),
        issue_comments:
          ([$comments[].data.repository.pullRequest.comments.nodes[]?
            | {id, updated_at: .updatedAt,
               reactions:
                 ([.reactionGroups[]?
                   | {content, total_count: .reactors.totalCount}]
                  | sort_by(.content))}]
           | sort_by(.id)),
        codex_reactions:
          ([$reactions[][]?
            | select(.user.login == "chatgpt-codex-connector[bot]")
            | {id, content}]
           | sort_by(.id)),
        inline_comments:
          ([$inline_comments[][]?
            | select((.user.login // "")
                | test("(coderabbitai|gemini-code-assist|chatgpt-codex-connector|github-advanced-security)\\[bot\\]$"))
            | {id, updated_at, commit_id, in_reply_to_id, user: .user.login}]
           | sort_by(.id)),
        check_runs:
          ([$check_runs[].check_runs[]?
            | {id, name, app: .app.slug, status, conclusion, started_at, completed_at}]
           | sort_by(.id)),
        security_alerts: {
          available: $security_available,
          pr_open:
            ([$security_alerts_pr[][]?
              | {number, state, rule_id: .rule.id, tool: .tool.name,
                 ref: .most_recent_instance.ref,
                 analysis_key: .most_recent_instance.analysis_key,
                 path: .most_recent_instance.location.path,
                 start_line: .most_recent_instance.location.start_line}]
             | sort_by(.number)),
          base_open:
            ([$security_alerts_base[][]?
              | {number, state, rule_id: .rule.id, tool: .tool.name,
                 ref: .most_recent_instance.ref,
                 analysis_key: .most_recent_instance.analysis_key,
                 path: .most_recent_instance.location.path,
                 start_line: .most_recent_instance.location.start_line}]
             | sort_by(.number))
        }
      }
    end
    ' > "$output_file.next" 2> "$LAST_ERROR_FILE"; then
    printf 'GitHub probe could not assemble a complete state fingerprint\n' >> "$LAST_ERROR_FILE"
    FAILURE_KIND="transient"
    RETRY_AFTER=0
    rm -f "$output_file.next"
    return 1
  fi

  mv "$output_file.next" "$output_file"
}

BASELINE_FILE="$WORKDIR/baseline.json"
if retry_operation probe_once "$BASELINE_FILE"; then
  :
else
  status=$?
  if [[ "$status" -eq 124 ]]; then
    report_timeout
    exit 0
  fi
  echo "WAIT_ERROR: GitHub probe failed" >&2
  cat "$LAST_ERROR_FILE" >&2
  exit 5
fi

while true; do
  now=$(date +%s)
  remaining=$((DEADLINE - now))
  (( remaining > 0 )) || break
  interval="$POLL_INTERVAL"
  (( remaining >= interval )) || interval="$remaining"
  sleep "$interval"

  now=$(date +%s)
  (( now < DEADLINE )) || break

  CURRENT_FILE="$WORKDIR/current.json"
  if retry_operation probe_once "$CURRENT_FILE"; then
    :
  else
    status=$?
    if [[ "$status" -eq 124 ]]; then
      report_timeout
      exit 0
    fi
    echo "WAIT_ERROR: GitHub probe failed after transient retries" >&2
    cat "$LAST_ERROR_FILE" >&2
    exit 5
  fi

  if ! cmp -s "$BASELINE_FILE" "$CURRENT_FILE"; then
    echo "WAIT_CHANGE: relevant PR state changed"
    exit 0
  fi
done

report_timeout
