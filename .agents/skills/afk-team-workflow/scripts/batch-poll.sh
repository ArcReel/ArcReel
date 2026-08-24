#!/usr/bin/env bash
# Expand an AFK batch and project the issue dependency graph. This is a planning
# snapshot only; stage progress lives in the remote stage branch and PR.
# stdout: {generated_for, issues: [{number, title, state, state_reason, labels,
# assignees, blocked_by, blockers_completed}], initial_frontier: [number]}.
# initial_frontier is only the mechanical open/unassigned/blockers projection;
# the team-lead applies triage labels and issue semantics.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/repo-context.sh"
enter_repo_root "BATCH_POLL_ERROR" "$@"
shift "$REPO_CONTEXT_SHIFT"

usage() {
  echo "BATCH_POLL_ERROR: usage: bash batch-poll.sh [--repo-root <path>] (--spec <N> | --issues 1,2,3)" >&2
}

SPEC=""
ISSUES_CSV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --spec) SPEC="${2:-}"; shift 2 || { usage; exit 2; } ;;
    --issues) ISSUES_CSV="${2:-}"; shift 2 || { usage; exit 2; } ;;
    *) echo "BATCH_POLL_ERROR: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -n "$SPEC" && -n "$ISSUES_CSV" ]] || [[ -z "$SPEC" && -z "$ISSUES_CSV" ]]; then
  usage
  exit 2
fi
if [[ -n "$SPEC" && ! "$SPEC" =~ ^[0-9]+$ ]]; then
  echo "BATCH_POLL_ERROR: --spec must be a number, got: $SPEC" >&2
  exit 2
fi
command -v gh >/dev/null 2>&1 || { echo "BATCH_POLL_ERROR: gh CLI not found on PATH" >&2; exit 3; }
command -v jq >/dev/null 2>&1 || { echo "BATCH_POLL_ERROR: jq not found on PATH" >&2; exit 3; }

TASK_TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TASK_TMP_DIR"' EXIT

OWNER_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>"$TASK_TMP_DIR/repo.err") || {
  echo "BATCH_POLL_ERROR: gh repo view failed" >&2
  cat "$TASK_TMP_DIR/repo.err" >&2
  exit 4
}

PROJECT_ISSUE='{number, title, state, state_reason, labels: [(.labels // [])[].name], assignees: [(.assignees // [])[].login], body}'

if [[ -n "$SPEC" ]]; then
  GENERATED_FOR=$(jq -nc --argjson spec "$SPEC" '{spec: $spec}')
  gh api "repos/${OWNER_REPO}/issues/${SPEC}/sub_issues" --paginate --slurp >"$TASK_TMP_DIR/raw.json" 2>"$TASK_TMP_DIR/fetch.err" || {
    echo "BATCH_POLL_ERROR: sub-issues fetch failed for Spec #${SPEC}" >&2
    cat "$TASK_TMP_DIR/fetch.err" >&2
    exit 5
  }
  jq "[.[][] | ${PROJECT_ISSUE}]" "$TASK_TMP_DIR/raw.json" >"$TASK_TMP_DIR/issues.json"
else
  issue_numbers=""
  seen=" "
  while IFS= read -r token; do
    [[ -n "$token" ]] || continue
    [[ "$token" =~ ^[0-9]+$ ]] || {
      echo "BATCH_POLL_ERROR: --issues has a non-numeric token: $token" >&2
      exit 2
    }
    case "$seen" in *" $token "*) continue ;; esac
    seen="$seen$token "
    issue_numbers="$issue_numbers$token "
  done < <(echo "$ISSUES_CSV" | tr ',' '\n' | tr -d ' \t')
  issue_numbers="${issue_numbers% }"
  [[ -n "$issue_numbers" ]] || { echo "BATCH_POLL_ERROR: --issues had no numbers" >&2; exit 2; }

  GENERATED_FOR=$(echo "$issue_numbers" | tr ' ' '\n' | jq -R 'tonumber' | jq -sc '{issues: .}')
  : >"$TASK_TMP_DIR/issues.jsonl"
  for issue in $issue_numbers; do
    gh api "repos/${OWNER_REPO}/issues/${issue}" 2>"$TASK_TMP_DIR/issue-${issue}.err" \
      | jq "$PROJECT_ISSUE" >>"$TASK_TMP_DIR/issues.jsonl" || {
        echo "BATCH_POLL_ERROR: issue #${issue} fetch failed" >&2
        cat "$TASK_TMP_DIR/issue-${issue}.err" >&2
        exit 5
      }
  done
  jq -s '.' "$TASK_TMP_DIR/issues.jsonl" >"$TASK_TMP_DIR/issues.json"
fi

# Project the canonical native graph. Body links are the repository fallback;
# the historical section remains readable for existing issues.
: >"$TASK_TMP_DIR/native.jsonl"
for issue in $(jq -r '.[].number' "$TASK_TMP_DIR/issues.json"); do
  if gh api "repos/${OWNER_REPO}/issues/${issue}/dependencies/blocked_by" --paginate --slurp \
    >"$TASK_TMP_DIR/native-${issue}.json" 2>"$TASK_TMP_DIR/native-${issue}.err"; then
    jq -c --argjson number "$issue" \
      '{number: $number, available: true, blocked_by: [.[][] | .number] | unique}' \
      "$TASK_TMP_DIR/native-${issue}.json" >>"$TASK_TMP_DIR/native.jsonl"
  elif grep -q 'HTTP 404' "$TASK_TMP_DIR/native-${issue}.err"; then
    echo "BATCH_POLL_WARN: native dependencies unavailable for #${issue}; using body fallback" >&2
    jq -nc --argjson number "$issue" \
      '{number: $number, available: false, blocked_by: []}' \
      >>"$TASK_TMP_DIR/native.jsonl"
  else
    echo "BATCH_POLL_ERROR: native dependencies fetch failed for #${issue}" >&2
    cat "$TASK_TMP_DIR/native-${issue}.err" >&2
    exit 5
  fi
done
jq -s '.' "$TASK_TMP_DIR/native.jsonl" >"$TASK_TMP_DIR/native.json"

jq --slurpfile native "$TASK_TMP_DIR/native.json" '
  def refs:
    [scan("#([0-9]+)") | .[0] | tonumber] | unique;
  def top_blocked_by:
    (. // "") | split("\n") | map(select(test("\\S"))) | (.[0] // "")
    | if test("^\\s*Blocked by\\s*:"; "i") then refs else [] end;
  def section_lines($name):
    (. // "") | split("\n")
    | reduce .[] as $line ({inside: false, lines: []};
        if ($line | test("^##\\s+" + $name; "i")) then .inside = true
        elif ($line | test("^##\\s")) then .inside = false
        elif .inside then .lines += [$line]
        else . end)
    | .lines;
  def legacy_blocked_by:
    section_lines("Blocked by") | map(select(test("\\S")))
    | if length == 0 or (.[0] | gsub("^\\s+"; "") | test("^none"; "i")) then []
      else join("\n") | refs
      end;
  def body_blocked_by:
    . as $body | ($body | top_blocked_by) as $top
    | if ($top | length) > 0 then $top else ($body | legacy_blocked_by) end;
  ($native[0] | INDEX(.number | tostring)) as $native_by_issue
  | [.[]
     | . as $issue
     | ($native_by_issue[.number | tostring]) as $native_issue
     | {
         number,
         blocked_by: (if $native_issue.available
           then $native_issue.blocked_by
           else ($issue.body | body_blocked_by)
           end)
       }]
' "$TASK_TMP_DIR/issues.json" >"$TASK_TMP_DIR/dependencies.json"

BATCH_NUMBERS=" $(jq -r '[.[].number] | join(" ")' "$TASK_TMP_DIR/issues.json") "
: >"$TASK_TMP_DIR/external.jsonl"
for blocker in $(jq -r '[.[].blocked_by[]] | unique | .[]' "$TASK_TMP_DIR/dependencies.json"); do
  [[ "$BATCH_NUMBERS" == *" $blocker "* ]] && continue
  if ! gh api "repos/${OWNER_REPO}/issues/${blocker}" --jq '{number, state, state_reason}' \
    >>"$TASK_TMP_DIR/external.jsonl" 2>"$TASK_TMP_DIR/blocker-${blocker}.err"; then
    echo "BATCH_POLL_WARN: blocker #${blocker} state fetch failed; treating it as incomplete" >&2
  fi
done

jq -n \
  --slurpfile issue_sets "$TASK_TMP_DIR/issues.json" \
  --slurpfile dependencies "$TASK_TMP_DIR/dependencies.json" \
  --slurpfile external "$TASK_TMP_DIR/external.jsonl" \
  --argjson generated_for "$GENERATED_FOR" '
    ($issue_sets[0]) as $issues
    | ($dependencies[0] | INDEX(.number | tostring)) as $deps
    | (($issues | map({number, state, state_reason})) + $external | INDEX(.number | tostring)) as $states
    | [$issues[]
       | . as $issue
       | ($deps[.number | tostring].blocked_by // []) as $blocked
       | . + {
           blocked_by: $blocked,
           blockers_completed: ($blocked | all(. as $number
             | ($states[$number | tostring]) as $state
             | $state != null and $state.state == "closed" and $state.state_reason == "completed"))
         }
       | del(.body)] as $rows
    | {
        generated_for: $generated_for,
        issues: $rows,
        initial_frontier: [$rows[]
          | select(.state == "open" and .blockers_completed and (.assignees | length) == 0)
          | .number]
      }
  '
