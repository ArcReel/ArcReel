#!/usr/bin/env bash
# wait.sh — block until the PR has something to act on, or the fixed safety timeout.
#
# USAGE
#   bash wait.sh --repo-root <path> <PR_NUMBER> [--max <seconds>]
#
# The one entry point for waiting on CI and on reviewers. It is EDGE-TRIGGERED: the first
# probe is the baseline, and the script returns only when something actionable appears
# that the baseline did not have. Whatever already holds on entry (finished checks, an
# overdue Codex trigger, findings already posted) is the caller's to handle from poll.sh
# before waiting; it never makes the wait return on its own.
#
# OUTPUT (stdout, one line)
#   WAIT_READY: <reason>[, <reason>...]
#     head_changed     the PR head is no longer the baseline head
#     new_finding      a CodeRabbit or Codex review with a body, a top-level inline comment
#                      from CodeRabbit, Codex or CodeQL, or a code-scanning alert the PR
#                      introduces (open on the PR ref, absent from the base). Deliberately
#                      at least as wide as poll.sh's has_new_findings: waking for a body
#                      that turns out to be an ack is cheap, sleeping through a finding is not
#     reviewer_notice  a reviewer bot posted a new top-level comment other than CodeRabbit's
#                      walkthrough and Codex's clean-pass receipt or summary, or one of its
#                      comments newly shows a pause, rate limit, skipped review or quota error,
#                      or Codex's summary newly reports the review of a commit as failed
#     codex_silent     the Codex trigger window closed during this wait: the head commit is
#                      CODEX_WINDOW seconds old, Codex shows no start signal for it (a review
#                      on the head, a reaction or comment after the commit, its summary naming
#                      the head) and nobody has posted `@codex review` since. Fires on the
#                      transition only, so a PR where that reviewer is retired does not
#                      return on every call
#     checks_passed    the required checks of the head went from unfinished to all finished,
#     checks_failed(<names>)  none failing / the named ones failing
#     state_changed    the required checks were already finished on entry — the caller is
#                      waiting on reviewers — and any decision-relevant state changed
#                      (reviews, comments, reactions, inline comments, check runs, alerts)
#   WAIT_TIMEOUT: nothing actionable within <seconds> seconds
#   Errors go to stderr prefixed `WAIT_ERROR:` with a non-zero exit.
#
# While required checks are still running, nothing else returns: check-run status flips,
# reactions, walkthrough rewrites, in-thread bot replies and acks all wait for the checks.
#
# REQUIRED CHECKS
#   Names come from the base branch rulesets (repos/<slug>/rules/branches/<base>, rule type
#   required_status_checks), read once per run. Finished = every name has a check run on
#   the head and the latest run per name (by started_at, as in poll.sh) is completed; a name
#   with no run yet — the minutes right after a push — is unfinished. When the rules are
#   unreadable or name nothing, every check run on the head stands in, and an empty list is
#   unfinished.
#
# The default wait uses 1770 seconds of a 30-minute command budget, leaving 30 seconds
# for setup and result delivery. --max is an override for tests and manual diagnostics.
# Every probe is an atomic read of reviews, comments, reactions, inline comments, check
# runs, and the PR/base code-scanning alert sets. Truncated output, network errors,
# rate limits, and HTTP 5xx responses retry the whole probe up to three times. Authentication,
# permission, and other request errors fail loudly with WAIT_ERROR. Body judgments are
# shared with poll.sh through signals.jq. The push time is the head commit committer date
# (poll.sh PITFALL 1).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/repo-context.sh"
enter_repo_root "WAIT_ERROR" "$@"
shift "$REPO_CONTEXT_SHIFT"

DEFAULT_MAX_WAIT=1770
POLL_INTERVAL=60
CODEX_WINDOW=300
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
  printf 'WAIT_TIMEOUT: nothing actionable within %s seconds\n' "$MAX_WAIT"
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

# GitHub also answers primary and secondary rate limits with HTTP 403.
is_rate_limited() {
  grep -Eiq 'rate[ -]?limit|secondary rate|abuse detection' "$1" \
    || grep -Eiq '(^|[^0-9])429([^0-9]|$)' "$1"
}

classify_failure() {
  local error_file="$1"
  local retry_hint

  FAILURE_KIND="fatal"
  RETRY_AFTER=0

  if is_rate_limited "$error_file"; then
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
    && ! is_rate_limited "$error_file" \
    && grep -Eq '(^|[^0-9])(403|404|422)([^0-9]|$)' "$error_file"; then
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
    gh api 'repos/{owner}/{repo}' --jq .full_name; then
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
  echo "WAIT_ERROR: REST repository lookup failed" >&2
  cat "$LAST_ERROR_FILE" >&2
  exit 4
fi

REPO_SLUG=$(<"$WORKDIR/repo-slug")
# Each page goes through the deadline, response validation, and failure classifier.
fetch_pages() {
  local output_file="$1" error_file="$2" availability="$3" endpoint="$4" projection="${5:-.}"
  local page=1 separator='?' count
  [[ "$endpoint" != *'?'* ]] || separator='&'
  : > "$output_file"
  while true; do
    fetch_json "$WORKDIR/page.json" "$error_file" "$availability" \
      gh api --include "${endpoint}${separator}per_page=100&page=${page}" || return 1
    count=$(jq -er "${projection} | if type == \"array\" then length else error(\"expected array\") end" "$WORKDIR/page.json" 2> "$error_file") || {
      LAST_ERROR_FILE="$error_file"
      FAILURE_KIND="transient"
      RETRY_AFTER=0
      return 1
    }
    cat "$WORKDIR/page.json" >> "$output_file"
    printf '\n' >> "$output_file"
    (( count == 100 )) || break
    page=$((page + 1))
  done
}

probe_once() {
  local output_file="$1"
  local security_available=true

  fetch_pages "$WORKDIR/reviews.json" "$WORKDIR/reviews.err" required \
    "repos/${REPO_SLUG}/pulls/${PR}/reviews" || return 1

  fetch_pages "$WORKDIR/comments.json" "$WORKDIR/comments.err" required \
    "repos/${REPO_SLUG}/issues/${PR}/comments" || return 1

  fetch_json "$WORKDIR/pr.json" "$WORKDIR/pr.err" required \
    gh api --include "repos/${REPO_SLUG}/pulls/${PR}" || return 1

  fetch_pages "$WORKDIR/reactions.json" "$WORKDIR/reactions.err" required \
    "repos/${REPO_SLUG}/issues/${PR}/reactions" || return 1

  fetch_pages "$WORKDIR/inline-comments.json" "$WORKDIR/inline-comments.err" required \
    "repos/${REPO_SLUG}/pulls/${PR}/comments" || return 1

  local head_sha
  head_sha=$(jq -er -s '.[0].head.sha' "$WORKDIR/pr.json" 2> "$WORKDIR/head.err") || {
    LAST_ERROR_FILE="$WORKDIR/head.err"
    printf 'GitHub probe returned no head SHA\n' >> "$LAST_ERROR_FILE"
    FAILURE_KIND="transient"
    RETRY_AFTER=0
    return 1
  }

  fetch_pages "$WORKDIR/check-runs.json" "$WORKDIR/check-runs.err" required \
    "repos/${REPO_SLUG}/commits/${head_sha}/check-runs" '.check_runs' || return 1

  # Committer date of the head commit; immutable per sha, so one fetch per head.
  if [[ ! -s "$WORKDIR/commit-${head_sha}.json" ]]; then
    fetch_json "$WORKDIR/commit.json" "$WORKDIR/commit.err" required \
      gh api --include "repos/${REPO_SLUG}/commits/${head_sha}" || return 1
    jq -e -s '.[0].commit.committer.date | type == "string"' "$WORKDIR/commit.json" \
      >/dev/null 2> "$WORKDIR/commit.err" || {
      LAST_ERROR_FILE="$WORKDIR/commit.err"
      printf 'GitHub probe returned no committer date for the head commit\n' >> "$LAST_ERROR_FILE"
      FAILURE_KIND="transient"
      RETRY_AFTER=0
      return 1
    }
    mv "$WORKDIR/commit.json" "$WORKDIR/commit-${head_sha}.json"
  fi

  # Required check names from the base branch rulesets, read once per run.
  if [[ ! -s "$WORKDIR/required.json" ]]; then
    local base_ref
    base_ref=$(jq -r -s '.[0].base.ref // ""' "$WORKDIR/pr.json")
    if [[ -n "$base_ref" ]] && fetch_pages "$WORKDIR/rules.json" "$WORKDIR/rules.err" optional \
      "repos/${REPO_SLUG}/rules/branches/${base_ref}"; then
      jq -c -s '
        [.[][]? | select(.type == "required_status_checks")
         | .parameters.required_status_checks[]?.context | select(type == "string")]
        | unique
        | if length > 0 then {mode: "rules", names: .} else {mode: "all", names: []} end
      ' "$WORKDIR/rules.json" > "$WORKDIR/required.json"
    elif [[ -z "$base_ref" || "$FAILURE_KIND" == "unavailable" ]]; then
      printf '{"mode":"all","names":[]}\n' > "$WORKDIR/required.json"
    else
      return 1
    fi
  fi

  if fetch_pages "$WORKDIR/security-alerts-pr.json" "$WORKDIR/security-alerts-pr.err" optional \
    "repos/${REPO_SLUG}/code-scanning/alerts?ref=refs/pull/${PR}/merge&state=open"; then
    :
  elif [[ "$FAILURE_KIND" == "unavailable" ]]; then
    security_available=false
    printf '[]\n' > "$WORKDIR/security-alerts-pr.json"
  else
    return 1
  fi

  if fetch_pages "$WORKDIR/security-alerts-base.json" "$WORKDIR/security-alerts-base.err" optional \
    "repos/${REPO_SLUG}/code-scanning/alerts?state=open"; then
    :
  elif [[ "$FAILURE_KIND" == "unavailable" ]]; then
    security_available=false
    printf '[]\n' > "$WORKDIR/security-alerts-base.json"
  else
    return 1
  fi

  LAST_ERROR_FILE="$WORKDIR/probe.err"
  : > "$LAST_ERROR_FILE"
  if ! jq -n -L "$SCRIPT_DIR" \
    --slurpfile pr "$WORKDIR/pr.json" \
    --slurpfile commit "$WORKDIR/commit-${head_sha}.json" \
    --slurpfile required "$WORKDIR/required.json" \
    --slurpfile reviews "$WORKDIR/reviews.json" \
    --slurpfile comments "$WORKDIR/comments.json" \
    --slurpfile reactions "$WORKDIR/reactions.json" \
    --slurpfile inline_comments "$WORKDIR/inline-comments.json" \
    --slurpfile check_runs "$WORKDIR/check-runs.json" \
    --slurpfile security_alerts_pr "$WORKDIR/security-alerts-pr.json" \
    --slurpfile security_alerts_base "$WORKDIR/security-alerts-base.json" \
    --argjson security_available "$security_available" \
    '
      include "signals";
      def login: (.user.login // "");
      def is_cr: login == "coderabbitai[bot]";
      def is_codex: login == "chatgpt-codex-connector[bot]";
      ($pr[0].head.sha) as $head
      | ($commit[0].commit.committer.date) as $push_at
      | ($required[0]) as $required
      | [$reviews[][]?] as $all_reviews
      | [$comments[][]?] as $all_comments
      | [$reactions[][]? | select(is_codex)] as $codex_reactions
      | [$inline_comments[][]?
         | select(login | test("(coderabbitai|chatgpt-codex-connector|github-advanced-security)\\[bot\\]$"))]
        as $bot_inline
      # Same-name reruns collapse to the latest run, as in poll.sh.
      | ([$check_runs[].check_runs[]?] | group_by(.name) | map(max_by(.started_at))) as $latest_runs
      # One slot per required name (null = no run on the head yet); every run when degraded.
      | (if $required.mode == "rules"
         then [$required.names[] as $name | ($latest_runs | map(select(.name == $name)) | first)]
         else $latest_runs end) as $gate
      | ([$security_alerts_base[][]? | .number]) as $base_alerts
      # CodeRabbit rewrites its first comment (the walkthrough) on every review.
      | ([$all_comments[] | select(is_cr)] | sort_by(.created_at) | first | .id) as $walkthrough_id
      | {
        head: $head,
        push_at: $push_at,
        required: $required,
        checks: {
          done: (($gate | length) > 0 and all($gate[]; . != null and .status == "completed")),
          failing: [$gate[] | select(. != null and (.conclusion | is_failing_conclusion)) | .name]
        },
        findings:
          ([$all_reviews[] | select(is_cr or is_codex) | select((.body // "") | test("\\S"))
            | "review:\(.id)"]
           + [$bot_inline[] | select(.in_reply_to_id == null) | "inline:\(.id)"]
           + [$security_alerts_pr[][]? | select(.number as $n | $base_alerts | index($n) | not)
              | "alert:\(.number)"]
           | unique),
        notices:
          ([$all_comments[] | select(is_cr or is_codex)
            | (.body // "") as $body
            | (if .id == $walkthrough_id
                  or (is_codex and ($body | codex_comment_has_pass_marker or codex_summary_body))
               then empty else "comment:\(.id)" end),
              (if is_codex and ($body | codex_summary_failed)
               then "codex_failed:\($body | codex_summary_commit)" else empty end),
              (if $body | cr_paused_body then "paused:\(.id)" else empty end),
              (if $body | cr_review_skipped_body then "skipped:\(.id)" else empty end),
              (if $body | quota_alert_body then "quota:\(.id)" else empty end)]
           | unique),
        codex: {
          started:
            ((any($all_comments[];
                  is_codex and (.body | codex_summary_failed)
                  and ((.body | codex_summary_commit) as $sha | $sha != null and ($head | startswith($sha))))
              | not)
             and (any($all_reviews[]; is_codex and .commit_id == $head)
             or any($codex_reactions[]; .created_at > $push_at)
             # A comment that names its commit (summary, receipt) counts by that anchor alone:
             # a slow review of an orphaned head lands after the push too. Same rule as poll.sh.
             or any($all_comments[];
                    is_codex
                    and ((.body | codex_summary_commit // codex_reviewed_commit_from_body) as $sha
                         | if $sha == null then .created_at > $push_at
                           else ($head | startswith($sha)) end)))),
          triggered:
            any($all_comments[];
                (is_cr or is_codex | not)
                and .created_at > $push_at
                and (.body | is_trigger_command("@codex review")))
        },
        raw: {
          reviews:
            ([$all_reviews[]
              # REST reviews omit updated_at; body changes still need to wake the loop.
              | {id, submitted_at, body, state, commit_oid: .commit_id}]
             | sort_by(.id)),
          issue_comments:
            ([$all_comments[] | {id, updated_at, reactions}] | sort_by(.id)),
          codex_reactions:
            ([$codex_reactions[] | {id, content}] | sort_by(.id)),
          inline_comments:
            ([$bot_inline[] | {id, updated_at, commit_id, in_reply_to_id, user: .user.login}]
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
      }
    ' > "$output_file.next" 2> "$LAST_ERROR_FILE"; then
    printf 'GitHub probe could not assemble a complete state fingerprint\n' >> "$LAST_ERROR_FILE"
    FAILURE_KIND="transient"
    RETRY_AFTER=0
    rm -f "$output_file.next"
    return 1
  fi

  mv "$output_file.next" "$output_file"
}

# Reasons the current state is actionable relative to the baseline; empty = keep waiting.
ready_reasons() {
  local current_file="$1" now="$2"
  jq -r -n \
    --slurpfile baseline "$BASELINE_FILE" \
    --slurpfile current "$current_file" \
    --argjson entry "$ENTERED_AT" \
    --argjson now "$now" \
    --argjson window "$CODEX_WINDOW" \
    '
      def codex_due($at):
        (.codex.started | not) and (.codex.triggered | not)
        and (($at - (.push_at | fromdateiso8601)) >= $window);
      $baseline[0] as $b
      | $current[0] as $c
      | [ (if $c.head != $b.head then "head_changed" else empty end),
          (if ($c.findings - $b.findings) | length > 0 then "new_finding" else empty end),
          (if ($c.notices - $b.notices) | length > 0 then "reviewer_notice" else empty end),
          (if $c.head == $b.head and ($b | codex_due($entry) | not) and ($c | codex_due($now))
           then "codex_silent" else empty end),
          (if ($b.checks.done | not) and $c.checks.done
           then (if $c.checks.failing | length > 0
                 then "checks_failed(" + ($c.checks.failing | join(" ")) + ")"
                 else "checks_passed" end)
           else empty end)
        ] as $reasons
      | if ($reasons | length) == 0 and $b.checks.done and $b.raw != $c.raw
        then "state_changed"
        else $reasons | join(", ") end
    '
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
ENTERED_AT=$(date +%s)

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

  REASONS=$(ready_reasons "$CURRENT_FILE" "$(date +%s)")
  if [[ -n "$REASONS" ]]; then
    echo "WAIT_READY: $REASONS"
    exit 0
  fi
done

report_timeout
