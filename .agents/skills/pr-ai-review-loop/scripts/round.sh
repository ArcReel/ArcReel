#!/usr/bin/env bash
# round.sh — per-PR round ledger for the review loop. One entry per disposed batch of
# reviewer feedback: a fix push, or a batch answered entirely by pushback (no push).
#
# USAGE
#   bash round.sh --repo-root <path> <PR_NUMBER> mark --implemented <n> --pushback <n> [--note "<one sentence>"]
#   bash round.sh --repo-root <path> <PR_NUMBER> show
#
# LEDGER
#   <snapshot>.rounds.json beside poll.sh's snapshot (same user-private dir and repo/PR
#   naming), shape {pr, rounds: [{round, head, marked_at, implemented, pushback, note}]}.
#   Append-only within a PR: round = entries so far + 1, head = PR head at mark time (a
#   pushback-only round repeats the previous head). poll.sh surfaces the entry count as
#   `rounds` in its index, so the ledger survives context compaction and looper hand-offs.
#   What counts as a round is defined once, in SKILL.md「轮次与收敛」.
#
# OUTPUT
#   mark: {round, head, marked_at, implemented, pushback, note, rounds} for the new entry.
#   show: the whole ledger ({pr, rounds: []} when nothing is marked yet).
#   Errors are loud: non-zero exit + `ROUND_ERROR:` on stderr.

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

OWNER_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner) || {
  echo "ROUND_ERROR: gh repo view failed (auth? wrong cwd?)" >&2
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

    HEAD_SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid) || {
      echo "ROUND_ERROR: gh pr view $PR failed" >&2
      exit 5
    }
    MARKED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    # Every write, the first included, lands via mktemp + mv so the ledger is never
    # observed half-written; a missing ledger is synthesised in-stream instead.
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
