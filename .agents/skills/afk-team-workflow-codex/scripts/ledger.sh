#!/usr/bin/env bash
# ledger.sh — append one line to a batch's thin ledger.
#
# This is behavior-equivalent to afk-team-workflow/scripts/ledger.sh. The only
# functional schema difference is the fixed audit field "runtime": "codex".
#
# USAGE
#   bash ledger.sh <batch-id> <kind> [--issue N] [--pr M] \
#                  [--scope-spec N | --scope-issues "1,2,3"] [--detail "free text"]

set -euo pipefail

VALID_KINDS="decision authorization fault gap shelve merge retrospective closed"

die() { echo "LEDGER_ERROR: $*" >&2; exit 1; }

if [[ $# -lt 2 ]]; then
  die "usage: bash ledger.sh <batch-id> <kind> [--issue N] [--pr M] [--detail TEXT]"
fi

BATCH_ID="$1"; shift
KIND="$1"; shift

if ! [[ "$BATCH_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  die "batch-id must match [A-Za-z0-9._-]+, got: $BATCH_ID"
fi
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
if [[ -n "$SCOPE_SPEC" && -n "$SCOPE_ISSUES_CSV" ]]; then
  die "pass at most one of --scope-spec / --scope-issues"
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

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

mkdir -p .afk
LEDGER_FILE=".afk/${BATCH_ID}.jsonl"

if [[ ! -s "$LEDGER_FILE" && "$SCOPE_JSON" == "null" ]]; then
  die "first ledger line needs --scope-spec or --scope-issues (recovery rebuilds members from it)"
fi

jq -nc \
  --arg ts "$TS" \
  --arg kind "$KIND" \
  --arg runtime "codex" \
  --argjson issue "$ISSUE" \
  --argjson pr "$PR" \
  --argjson scope "$SCOPE_JSON" \
  --arg detail "$DETAIL" \
  '{ts: $ts, kind: $kind, runtime: $runtime, issue: $issue, pr: $pr, scope: $scope, detail: $detail}' >> "$LEDGER_FILE"

echo "appended ${KIND} -> ${LEDGER_FILE}" >&2
