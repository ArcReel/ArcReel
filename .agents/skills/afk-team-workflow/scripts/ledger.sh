#!/usr/bin/env bash
# ledger.sh — append one line to a batch's thin ledger.
#
# The ledger records ONLY facts that gh/git cannot re-derive: a decision the lead
# made, an authorization the user gave verbally, a fault the lead absorbed, a gap it
# spotted, why an issue was shelved, a merge it performed, a retrospective handed in.
# Everything reconstructable from the remote (issue/PR/branch state, dependency graph)
# stays out — batch-poll.sh recomputes that on demand. Recovery replays this file to
# rebuild what crashed-and-lost context can't, then reconciles against a fresh poll.
#
# Why a script and not `echo '{...}' >> file`: a hand-built line breaks the moment a
# detail string contains a quote or newline, and a malformed line breaks recovery's
# replay. jq builds valid JSON every time, the timestamp is stamped deterministically,
# and the kind is validated so a typo can't silently drop an event from a recovery scan.
#
# USAGE
#   bash ledger.sh <batch-id> <kind> [--issue N] [--pr M] \
#                  [--scope-spec N | --scope-issues "1,2,3"] [--detail "free text"]
#
#   <batch-id>  spec-<N> for a Spec batch, or a slug for an explicit-issue batch (e.g.
#               batch-2026-06-20). Restricted to [A-Za-z0-9._-]; becomes the filename.
#   <kind>      one of: decision | authorization | fault | gap | shelve | merge |
#               retrospective | closed
#
# LINE SCHEMA (one JSON object per line, appended to .afk/<batch-id>.jsonl)
#   {
#     "ts":     "<ISO8601 UTC>",   # stamped here, not by the caller
#     "kind":   "<kind>",
#     "issue":  <int> | null,      # the issue this event concerns, when applicable
#     "pr":     <int> | null,      # the PR this event concerns, when applicable
#     "scope":  {"spec": <int>} | {"issues": [<int>,...]} | null,
#                                  # the batch's membership in machine-readable form, so recovery
#                                  # rebuilds the batch-poll input deterministically instead of parsing
#                                  # free text. Set it on the FIRST line (the plan decision/authorization):
#                                  # --scope-spec <N> for a Spec batch, --scope-issues <csv> for a slug batch
#                                  # (where batch-id alone cannot recover the member set). null otherwise.
#     "detail": "<str>"            # human-readable specifics (the argument/decision/cause)
#   }
#
# LIFECYCLE (the skill drives this; the script only appends)
#   - First append happens when the user confirms the plan (the pre-authorization /
#     plan decision), which also creates .afk/ and the file. This first line MUST carry
#     the batch scope (--scope-spec / --scope-issues, enforced below) so recovery can
#     rebuild the members.
#   - Events append throughout the run.
#   - The batch ends with a `closed` line. The file is NOT deleted — it is the
#     retrospective/audit source, and recovery treats a `closed` line as the terminal
#     marker (a ledger without one is a candidate for resumption).
#
# NOTE: .afk/ is gitignored. This ledger is local operational state, never committed.

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

# resolve the optional batch scope (recorded on the first line so recovery can rebuild members)
SCOPE_JSON="null"
if [[ -n "$SCOPE_SPEC" && -n "$SCOPE_ISSUES_CSV" ]]; then
  die "pass at most one of --scope-spec / --scope-issues"
fi
if [[ -n "$SCOPE_SPEC" ]]; then
  [[ "$SCOPE_SPEC" =~ ^[0-9]+$ ]] || die "--scope-spec must be a number, got: $SCOPE_SPEC"
  SCOPE_JSON=$(jq -nc --argjson spec "$SCOPE_SPEC" '{spec: $spec}')
elif [[ -n "$SCOPE_ISSUES_CSV" ]]; then
  # same fail-loud + de-dup discipline as batch-poll.sh --issues: a silent drop here would
  # let recovery rebuild the wrong member set
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

# the first line of a batch must carry scope, else recovery cannot rebuild the member set
if [[ ! -s "$LEDGER_FILE" && "$SCOPE_JSON" == "null" ]]; then
  die "first ledger line needs --scope-spec or --scope-issues (recovery rebuilds members from it)"
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
