#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/repo-context.sh"
enter_repo_root "LEDGER_ERROR" "$@"
shift "$REPO_CONTEXT_SHIFT"

VALID_KINDS="decision authorization fault gap shelve retrospective closed"

die() { echo "LEDGER_ERROR: $*" >&2; exit 1; }

if [[ $# -lt 2 ]]; then
  die "usage: bash ledger.sh [--repo-root <path>] (--new | <batch-id>) <kind> [--scope-spec N | --scope-issues CSV] [--issue N] [--pr M] [--detail TEXT]"
fi

NEW=""
BATCH_ID=""
if [[ "$1" == "--new" ]]; then
  NEW=1
else
  BATCH_ID="$1"
  if ! [[ "$BATCH_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
    die "batch-id must match [A-Za-z0-9._-]+, got: $BATCH_ID"
  fi
fi
shift
KIND="$1"; shift

case " $VALID_KINDS " in
  *" $KIND "*) ;;
  *) die "unknown kind: $KIND (valid: $VALID_KINDS)" ;;
esac

ISSUE="null"
PR="null"
DETAIL=""
SCOPE_SPEC=""
SCOPE_ISSUES_CSV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue)        ISSUE="${2:-}"; shift 2 || die "--issue needs a value" ;;
    --pr)           PR="${2:-}";    shift 2 || die "--pr needs a value" ;;
    --detail)       DETAIL="${2:-}"; shift 2 || die "--detail needs a value" ;;
    --scope-spec)   SCOPE_SPEC="${2:-}"; shift 2 || die "--scope-spec needs a value" ;;
    --scope-issues) SCOPE_ISSUES_CSV="${2:-}"; shift 2 || die "--scope-issues needs a value" ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ "$ISSUE" != "null" && ! "$ISSUE" =~ ^[0-9]+$ ]]; then
  die "--issue must be a number, got: $ISSUE"
fi
if [[ "$PR" != "null" && ! "$PR" =~ ^[0-9]+$ ]]; then
  die "--pr must be a number, got: $PR"
fi

if ! command -v jq >/dev/null 2>&1; then
  die "jq not found on PATH"
fi

SCOPE_JSON="null"
if [[ -n "$NEW" ]]; then
  if [[ -n "$SCOPE_SPEC" && -n "$SCOPE_ISSUES_CSV" ]] || [[ -z "$SCOPE_SPEC" && -z "$SCOPE_ISSUES_CSV" ]]; then
    die "--new needs exactly one of --scope-spec / --scope-issues (recovery rebuilds members from it)"
  fi
elif [[ -n "$SCOPE_SPEC" || -n "$SCOPE_ISSUES_CSV" ]]; then
  die "scope is set only on the first line; create the ledger with --new"
fi
if [[ -n "$SCOPE_SPEC" ]]; then
  [[ "$SCOPE_SPEC" =~ ^[0-9]+$ ]] || die "--scope-spec must be a number, got: $SCOPE_SPEC"
  SCOPE_JSON=$(jq -nc --argjson spec "$SCOPE_SPEC" '{spec: $spec}')
elif [[ -n "$SCOPE_ISSUES_CSV" ]]; then
  scope_nums=""
  seen=" "
  while IFS= read -r tok; do
    [[ -n "$tok" ]] || continue
    [[ "$tok" =~ ^[0-9]+$ ]] || die "--scope-issues has a non-numeric token: $tok"
    case "$seen" in *" $tok "*) continue ;; esac
    seen="$seen$tok "
    scope_nums="$scope_nums$tok "
  done < <(echo "$SCOPE_ISSUES_CSV" | tr ',' '\n' | tr -d ' \t')
  scope_nums="${scope_nums% }"
  [[ -n "$scope_nums" ]] || die "--scope-issues had no numbers: $SCOPE_ISSUES_CSV"
  SCOPE_JSON=$(echo "$scope_nums" | tr ' ' '\n' | jq -R 'tonumber' | jq -sc '{issues: .}')
fi

read -r TS STAMP < <(date -u '+%Y-%m-%dT%H:%M:%SZ %Y%m%d-%H%M%S')

if [[ -n "$NEW" ]]; then
  if [[ -n "$SCOPE_SPEC" ]]; then
    BATCH_ID="spec-${SCOPE_SPEC}-${STAMP}"
  else
    MEMBERS=$(echo "$SCOPE_JSON" | jq -r '.issues | sort | if length > 4 then "\(.[0])-n\(length)" else join("-") end')
    BATCH_ID="issues-${MEMBERS}-${STAMP}"
  fi
fi

mkdir -p .afk
LEDGER_FILE=".afk/${BATCH_ID}.jsonl"

if [[ -n "$NEW" && -e "$LEDGER_FILE" ]]; then
  die "batch-id already exists, retry in a second: $BATCH_ID"
fi
if [[ -z "$NEW" && ! -e "$LEDGER_FILE" ]]; then
  die "no ledger for batch-id $BATCH_ID; create it with --new"
fi

if [[ -s "$LEDGER_FILE" ]]; then
  # fail closed: an unparseable ledger must not read as "not closed"
  IS_CLOSED=$(jq -s 'any(.[]; .kind == "closed")' "$LEDGER_FILE" 2>/dev/null) \
    || die "ledger is not valid JSONL, refusing to append: $LEDGER_FILE"
  if [[ "$IS_CLOSED" == "true" && "$KIND" != "decision" ]]; then
    die "batch-id is already closed; only a decision on escalated items may follow, otherwise start a new batch with --new: $BATCH_ID"
  fi
fi
if [[ -e "$LEDGER_FILE" && ! -s "$LEDGER_FILE" ]]; then
  die "batch-id has an empty ledger reservation; start a new batch with --new: $BATCH_ID"
fi

# The handoff directory is the batch-scoped resource shared by every stage. Reserving
# it atomically before the first ledger append prevents concurrent sessions from
# mixing one batch-id's ledger and handoffs.
if [[ -n "$NEW" ]] && ! mkdir ".afk/${BATCH_ID}" 2>/dev/null; then
  die "batch-id path is already reserved, retry in a second: $BATCH_ID"
fi

jq -nc \
  --arg ts "$TS" \
  --arg kind "$KIND" \
  --argjson issue "$ISSUE" \
  --argjson pr "$PR" \
  --argjson scope "$SCOPE_JSON" \
  --arg detail "$DETAIL" \
  '{ts: $ts, kind: $kind, issue: $issue, pr: $pr, scope: $scope, detail: $detail}' >> "$LEDGER_FILE"

echo "appended ${KIND} -> ${LEDGER_FILE}" >&2
if [[ -n "$NEW" ]]; then
  echo "$BATCH_ID"
fi
