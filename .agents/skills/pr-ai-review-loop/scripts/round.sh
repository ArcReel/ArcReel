#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/repo-context.sh"
enter_repo_root "ROUND_ERROR" "$@"
shift "$REPO_CONTEXT_SHIFT"

usage() {
  echo "ROUND_ERROR: usage: bash round.sh [--repo-root <path>] <PR_NUMBER> {mark --implemented <n> --pushback <n> [--note <text>]|show}" >&2
  exit 2
}

[[ $# -ge 2 ]] || usage

PR="$1"
shift
CMD="$1"
shift

if ! [[ "$PR" =~ ^[0-9]+$ ]]; then
  echo "ROUND_ERROR: PR_NUMBER must be a number, got: $PR" >&2
  exit 2
fi

for tool in gh jq; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ROUND_ERROR: $tool not found on PATH" >&2
    exit 3
  fi
done

OWNER_REPO=$(gh api 'repos/{owner}/{repo}' --jq .full_name) || {
  echo "ROUND_ERROR: REST repository lookup failed (auth? wrong cwd?)" >&2
  exit 4
}

enter_snapshot_dir "ROUND_ERROR" || exit $?
SNAPSHOT_FILE=$(snapshot_file_for "$OWNER_REPO" "$PR")
LEDGER_FILE="${SNAPSHOT_FILE%.json}.rounds.json"

case "$CMD" in

  show)
    if [[ -f "$LEDGER_FILE" ]]; then
      jq . "$LEDGER_FILE"
    else
      jq -n --argjson pr "$PR" '{pr: $pr, rounds: []}'
    fi
    ;;

  mark)
    IMPLEMENTED=""
    PUSHBACK=""
    NOTE=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --implemented) [[ $# -ge 2 ]] || usage; IMPLEMENTED="$2"; shift 2 ;;
        --pushback)    [[ $# -ge 2 ]] || usage; PUSHBACK="$2"; shift 2 ;;
        --note)        [[ $# -ge 2 ]] || usage; NOTE="$2"; shift 2 ;;
        *) usage ;;
      esac
    done
    if ! [[ "$IMPLEMENTED" =~ ^[0-9]+$ ]]; then
      echo "ROUND_ERROR: --implemented must be a non-negative integer, got: '$IMPLEMENTED'" >&2
      exit 2
    fi
    if ! [[ "$PUSHBACK" =~ ^[0-9]+$ ]]; then
      echo "ROUND_ERROR: --pushback must be a non-negative integer, got: '$PUSHBACK'" >&2
      exit 2
    fi

    HEAD_SHA=$(gh api "repos/${OWNER_REPO}/pulls/${PR}" --jq .head.sha) || {
      echo "ROUND_ERROR: REST PR lookup $PR failed" >&2
      exit 5
    }
    MARKED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    TMP_FILE=$(mktemp "$SNAP_DIR/rounds.XXXXXX")
    trap 'rm -f "$TMP_FILE"' EXIT
    if [[ -f "$LEDGER_FILE" ]]; then
      cat "$LEDGER_FILE"
    else
      jq -n --argjson pr "$PR" '{pr: $pr, rounds: []}'
    fi | jq --arg head "$HEAD_SHA" --arg marked_at "$MARKED_AT" --arg note "$NOTE" \
       --argjson implemented "$IMPLEMENTED" --argjson pushback "$PUSHBACK" '
      .rounds += [{
        round: ((.rounds | length) + 1),
        head: $head, marked_at: $marked_at,
        implemented: $implemented, pushback: $pushback, note: $note
      }]
    ' > "$TMP_FILE"
    mv "$TMP_FILE" "$LEDGER_FILE"
    trap - EXIT
    jq -c '.rounds | (last + {rounds: length})' "$LEDGER_FILE"
    ;;

  *)
    usage
    ;;
esac
