#!/usr/bin/env bash
# batch-poll.sh — pull the remote state of a whole AFK batch in one shot.
#
# Sibling in spirit to pr-ai-review-loop/scripts/poll.sh: collect gh/git facts,
# project them mechanically, and leave every semantic call to Claude. This script
# answers "where does each issue physically sit on the remote, right now" so the
# lead doesn't hand-expand sub-issues / rebuild the dependency graph each time it
# plans, runs a health check, or recovers a crashed batch.
#
# USAGE
#   bash batch-poll.sh --spec <N>           # expand a Spec's GitHub sub-issues
#   bash batch-poll.sh --issues 1,2,3       # an explicit cross-Spec issue set
#
# OUTPUT: single JSON object to stdout (fatal errors to stderr prefixed
# `BATCH_POLL_ERROR:`; per-issue degradations prefixed `BATCH_POLL_WARN:`).
#
# JSON SCHEMA
# {
#   "batch_id_hint": "spec-<N>" | null,       # spec-<N> for --spec; null for --issues (lead names that batch's slug)
#   "generated_for": {"spec": <N>} | {"issues": [<int>,...]},
#   "issues": [
#     {
#       "number":          <int>,
#       "title":           "<str>",
#       "state":           "open" | "closed",
#       "state_reason":    "completed" | "not_planned" | "reopened" | null,
#       "labels":          ["<name>", ...],   # raw triage labels — lead applies ready-for-agent/-human policy, script does NOT
#       "blocked_by":      [<int>, ...],       # see "## Blocked by" parsing below; [] when the section starts with "None"
#       "blockers_merged": <bool> | null,      # all blocked_by issues closed-as-completed; null when blocked_by is empty;
#                                              # an unknown/unfetchable blocker counts as NOT merged (conservative)
#       "remote_branch":   <bool>,             # a remote branch named issue/<N> exists right now (merged PRs delete it)
#       "pr": {                                # the PR whose head ref is issue/<N>; null when none exists yet
#         "number", "state", "isDraft", "mergeable", "mergeStateStatus", "headRefOid", "updatedAt",
#         "checks_failing": [{name, conclusion}],  # CheckRun failing conclusion or StatusContext FAILURE/ERROR
#         "checks_pending": [{name, status}]       # CheckRun not COMPLETED or StatusContext PENDING/EXPECTED
#       } | null,
#       "stage_hint": "done" | "shelved" | "review-loop" | "local-review" | "no-branch"
#     }
#   ],
#   "ready_to_start":  [<int>,...],  # state==open AND stage_hint==no-branch AND (no blockers OR blockers_merged)
#   "merge_candidate": [<int>,...],  # PR open, non-draft, mergeable==MERGEABLE, no failing AND no pending checks
#   "conflicting":     [<int>,...]   # PR mergeable==CONFLICTING
# }
#
# stage_hint LADDER (first match wins; purely remote-mechanical, no liveness guess)
#   1. PR MERGED                              -> done
#   2. PR OPEN and not draft                  -> review-loop
#   3. PR is draft, or PR CLOSED unmerged     -> shelved   (this workflow shelves by drafting the PR)
#   4. no PR but remote branch issue/<N>      -> local-review (branch pushed, PR not opened yet)
#   5. no PR, no branch, issue closed/done    -> done       (closed-completed without a PR of its own)
#   6. no PR, no branch, issue closed/other   -> shelved
#   7. otherwise                              -> no-branch  (means ONLY "no remote branch" — NOT "teammate dead")
#
# "## Blocked by" PARSING (mechanical, follows the to-tickets template convention)
#   The dependency graph lives in each issue body's "## Blocked by" section, not in
#   GitHub's native issue-dependency links (this repo leaves those empty). The section
#   is either "None ..." or a list of "- #<N>" items. Rule: take the section's lines up
#   to the next "## " heading; if the first non-empty line starts with "None" (any case)
#   -> blocked_by is []; otherwise collect every #<N> reference in the section. The
#   "None" prefix check is what defends against bodies like
#   "None - can start immediately (prereqs #41/#42 already merged)" — those trailing #refs
#   are merged context, not blockers, so a naive #N grep would wrongly capture them as deps.
#   This is a documented-format projection, not open-ended NLP; the lead still reads each
#   body (SKILL.md step 1) and overrides if a human wrote the section unconventionally.
#
# BOUNDARY (hold this line — crossing it turns a fact collector into a semantic judge)
#   - Reports gh/git facts and mechanical roll-ups ONLY.
#   - Does NOT judge whether a teammate is alive/stalled (no-branch is a remote fact, not a verdict).
#   - Does NOT decide whether a PR should merge (merge_candidate is the mechanical "green & mergeable"
#     signal; the merge decision stays with the lead, who also weighs the review-looper's report).
#   - Does NOT drill into per-reviewer detail (that is poll.sh's job, one PR at a time).
#   - Does NOT read the .afk ledger (the ledger is replayed by the recovery flow, not by this script).
#
# PITFALLS
#   1. gh pr list --head finds a MERGED PR by head ref even after the branch is deleted, so a
#      finished issue still resolves to stage_hint=done. remote_branch goes false at the same time;
#      that pair (no branch + merged PR) is normal for done work, not a contradiction.
#   2. mergeable/mergeStateStatus read UNKNOWN transiently (GitHub computes them lazily after a push)
#      and on MERGED PRs. A freshly pushed PR can miss merge_candidate for a poll or two — re-run.
#   3. statusCheckRollup mixes CheckRun (status/conclusion) and StatusContext (state, e.g. CodeRabbit,
#      license/cla). Both shapes are checked; metric-only contexts (codecov) count as real checks here,
#      so a red codecov keeps a PR out of merge_candidate — the lead overrides knowing it is non-blocking.
#   4. ready_to_start is dependency+stage only; it does NOT subtract ready-for-human. The lead intersects
#      it with triage (SKILL.md step 2) — a needs-human issue can be "startable" by deps yet must be skipped.

set -euo pipefail

usage() {
  echo "BATCH_POLL_ERROR: usage: bash batch-poll.sh --spec <N> | --issues 1,2,3" >&2
}

SPEC=""
ISSUES_CSV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --spec)
      SPEC="${2:-}"; shift 2 || { usage; exit 2; } ;;
    --issues)
      ISSUES_CSV="${2:-}"; shift 2 || { usage; exit 2; } ;;
    *)
      echo "BATCH_POLL_ERROR: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -n "$SPEC" && -n "$ISSUES_CSV" ]]; then
  echo "BATCH_POLL_ERROR: pass exactly one of --spec / --issues, not both" >&2; exit 2
fi
if [[ -z "$SPEC" && -z "$ISSUES_CSV" ]]; then
  usage; exit 2
fi
if [[ -n "$SPEC" && ! "$SPEC" =~ ^[0-9]+$ ]]; then
  echo "BATCH_POLL_ERROR: --spec must be a number, got: $SPEC" >&2; exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "BATCH_POLL_ERROR: gh CLI not found on PATH" >&2; exit 3
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "BATCH_POLL_ERROR: jq not found on PATH" >&2; exit 3
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

OWNER_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>"$TMPDIR/repo.err") || {
  echo "BATCH_POLL_ERROR: gh repo view failed (auth? wrong cwd?)" >&2
  cat "$TMPDIR/repo.err" >&2
  exit 4
}

# ---- assemble the batch's raw issue objects (uniform shape regardless of input mode) ----
# Project to {number, title, state, state_reason, labels, body} — the same fields whether they
# arrive via the sub_issues array (--spec) or per-issue REST reads (--issues).
PROJECT_ISSUE='{number, title, state, state_reason, labels: [.labels[].name], body}'

if [[ -n "$SPEC" ]]; then
  BATCH_ID_HINT="spec-$SPEC"
  GEN_JSON=$(jq -n --argjson n "$SPEC" '{spec: $n}')
  # --paginate without -q: gh merges all pages into ONE array (poll.sh unwrap rule).
  gh api "repos/${OWNER_REPO}/issues/${SPEC}/sub_issues" --paginate > "$TMPDIR/sub_raw.json" 2>"$TMPDIR/sub.err" || {
    echo "BATCH_POLL_ERROR: sub_issues fetch failed for Spec #${SPEC}" >&2
    cat "$TMPDIR/sub.err" >&2
    exit 5
  }
  jq "[ .[] | ${PROJECT_ISSUE} ]" "$TMPDIR/sub_raw.json" > "$TMPDIR/batch_raw.json"
else
  BATCH_ID_HINT=""
  # split the CSV; fail loud on any non-numeric token (silently dropping it would shrink
  # the batch with no signal) and de-dup so the same issue is not processed twice
  ISSUE_NUMS=""
  seen=" "
  while IFS= read -r tok; do
    [[ -n "$tok" ]] || continue
    [[ "$tok" =~ ^[0-9]+$ ]] || { echo "BATCH_POLL_ERROR: --issues has a non-numeric token: $tok" >&2; exit 2; }
    case "$seen" in *" $tok "*) continue ;; esac
    seen="$seen$tok "
    ISSUE_NUMS="$ISSUE_NUMS$tok "
  done < <(echo "$ISSUES_CSV" | tr ',' '\n' | tr -d ' \t')
  ISSUE_NUMS="${ISSUE_NUMS% }"
  if [[ -z "$ISSUE_NUMS" ]]; then
    echo "BATCH_POLL_ERROR: --issues had no numbers: $ISSUES_CSV" >&2; exit 2
  fi
  GEN_JSON=$(echo "$ISSUE_NUMS" | tr ' ' '\n' | jq -R 'tonumber' | jq -s '{issues: .}')
  : > "$TMPDIR/batch_lines.jsonl"
  for N in $ISSUE_NUMS; do
    gh api "repos/${OWNER_REPO}/issues/${N}" 2>"$TMPDIR/issue_${N}.err" \
      | jq "${PROJECT_ISSUE}" >> "$TMPDIR/batch_lines.jsonl" || {
        echo "BATCH_POLL_ERROR: issue #${N} fetch failed" >&2
        cat "$TMPDIR/issue_${N}.err" >&2
        exit 5
      }
  done
  jq -s '.' "$TMPDIR/batch_lines.jsonl" > "$TMPDIR/batch_raw.json"
fi

# ---- extract blocked_by per issue (parser lives here once; final jq joins by number) ----
jq '
  def section_lines($name):
    (. // "")
    | split("\n")
    | reduce .[] as $ln ({inseg: false, lines: []};
        if   ($ln | test("^##\\s+" + $name; "i")) then (.inseg = true)
        elif ($ln | test("^##\\s"))               then (.inseg = false)
        elif .inseg                               then (.lines += [$ln])
        else . end)
    | .lines;
  def blocked_by_of:
    (section_lines("Blocked by")) as $sec
    | ($sec | map(select(test("\\S")))) as $content
    | if   ($content | length) == 0                              then []
      elif ($content[0] | gsub("^\\s+"; "") | test("^none"; "i")) then []
      else  [ $content[] | scan("#([0-9]+)") | .[0] | tonumber ] | unique
      end;
  [ .[] | {number, blocked_by: (.body | blocked_by_of)} ]
' "$TMPDIR/batch_raw.json" > "$TMPDIR/blocked.json"

# ---- resolve blocker states (batch members are known; fetch only the externals) ----
BATCH_SET=" $(jq -r '[.[].number] | join(" ")' "$TMPDIR/batch_raw.json") "
ALL_BLOCKERS=$(jq -r '[.[].blocked_by[]] | unique | .[]' "$TMPDIR/blocked.json")
: > "$TMPDIR/blocker_states.jsonl"
for b in $ALL_BLOCKERS; do
  if [[ "$BATCH_SET" == *" $b "* ]]; then
    continue   # in-batch: state already carried by batch_raw.json
  fi
  if ! gh api "repos/${OWNER_REPO}/issues/${b}" --jq '{number, state, state_reason}' \
        >> "$TMPDIR/blocker_states.jsonl" 2>"$TMPDIR/blocker_${b}.err"; then
    echo "BATCH_POLL_WARN: blocker #${b} state fetch failed (counts as not-merged)" >&2
    cat "$TMPDIR/blocker_${b}.err" >&2
  fi
done

# ---- find the PR (by head=issue/<N>) for each batch issue ----
PR_FIELDS="number,state,isDraft,mergeable,mergeStateStatus,headRefOid,updatedAt,statusCheckRollup"
: > "$TMPDIR/prs.jsonl"
for N in $(jq -r '.[].number' "$TMPDIR/batch_raw.json"); do
  if PRJSON=$(gh pr list --repo "$OWNER_REPO" --head "issue/${N}" --state all --limit 10 \
               --json "$PR_FIELDS" 2>"$TMPDIR/prlist_${N}.err"); then
    jq -n --argjson issue "$N" --argjson prs "$PRJSON" '{issue: $issue, prs: $prs}' >> "$TMPDIR/prs.jsonl"
  else
    echo "BATCH_POLL_WARN: gh pr list failed for issue/${N} (treating as no PR)" >&2
    cat "$TMPDIR/prlist_${N}.err" >&2
    jq -n --argjson issue "$N" '{issue: $issue, prs: []}' >> "$TMPDIR/prs.jsonl"
  fi
done

# ---- list remote issue/* branches in one call (no local git remote dependency) ----
if ! gh api "repos/${OWNER_REPO}/git/matching-refs/heads/issue/" --paginate --jq '.[].ref' \
      > "$TMPDIR/branches.txt" 2>"$TMPDIR/refs.err"; then
  echo "BATCH_POLL_WARN: matching-refs fetch failed (remote_branch will read false)" >&2
  cat "$TMPDIR/refs.err" >&2
  : > "$TMPDIR/branches.txt"
fi

# ---- combine into the final schema ----
jq -n \
  --slurpfile batch_w "$TMPDIR/batch_raw.json" \
  --slurpfile blocked_w "$TMPDIR/blocked.json" \
  --slurpfile prs "$TMPDIR/prs.jsonl" \
  --slurpfile blockerstates "$TMPDIR/blocker_states.jsonl" \
  --rawfile branches "$TMPDIR/branches.txt" \
  --arg batch_id_hint "$BATCH_ID_HINT" \
  --argjson gen "$GEN_JSON" \
  '
  def pick_pr($arr):
    if ($arr | length) == 0 then null
    # an OPEN PR is the active one; fall back to MERGED/CLOSED history only when none is
    # open, so a stale MERGED PR on a reused issue/<N> ref cannot shadow a reopened PR
    else ([$arr[] | select(.state == "OPEN")]) as $open
      | if ($open | length) > 0 then ($open | sort_by(.number) | last)
        else ([$arr[] | select(.state == "MERGED")]) as $m
          | (if ($m | length) > 0 then $m else $arr end | sort_by(.number) | last)
        end
    end;

  def failing($pr):
    [ ($pr.statusCheckRollup // [])[]
      | . as $e
      | select(
          ($e.__typename == "CheckRun"
             and (["FAILURE","TIMED_OUT","CANCELLED","ACTION_REQUIRED","STARTUP_FAILURE"]
                  | index(($e.conclusion // "" | ascii_upcase))))
          or ($e.__typename == "StatusContext"
             and (["FAILURE","ERROR"] | index(($e.state // "" | ascii_upcase))))
        )
      | {name: ($e.name // $e.context), conclusion: ($e.conclusion // $e.state)} ];

  def pending($pr):
    [ ($pr.statusCheckRollup // [])[]
      | . as $e
      | select(
          ($e.__typename == "CheckRun" and (($e.status // "" | ascii_upcase) != "COMPLETED"))
          or ($e.__typename == "StatusContext"
             and (["PENDING","EXPECTED"] | index(($e.state // "" | ascii_upcase))))
        )
      | {name: ($e.name // $e.context), status: ($e.status // $e.state)} ];

  def stage_hint($pr; $hasBranch; $istate; $ireason):
    if   ($pr != null and $pr.state == "MERGED")                          then "done"
    elif ($pr != null and $pr.state == "OPEN" and ($pr.isDraft | not))    then "review-loop"
    elif ($pr != null and ($pr.isDraft == true or $pr.state == "CLOSED")) then "shelved"
    elif $hasBranch                                                       then "local-review"
    elif ($istate == "closed" and $ireason == "completed")               then "done"
    elif ($istate == "closed")                                           then "shelved"
    else "no-branch" end;

  def blockers_merged($bb; $lookup):
    if ($bb | length) == 0 then null
    else ($bb | all(. as $b
            | ($lookup[$b | tostring]) as $s
            | ($s != null and $s.state == "closed" and $s.state_reason == "completed")))
    end;

  ($batch_w[0]) as $issues
  | ($blocked_w[0]) as $blocked
  | ($branches | split("\n") | map(select(length > 0))) as $branchrefs
  | ($prs | INDEX(.issue | tostring)) as $prByIssue
  | ($blocked | INDEX(.number | tostring)) as $blkByIssue
  | (($issues | map({number, state, state_reason})) + $blockerstates | INDEX(.number | tostring)) as $stateByNum
  | ( [ $issues[]
        | . as $iss
        | ($iss.number) as $n
        | ($prByIssue[$n | tostring].prs // []) as $prarr
        | pick_pr($prarr) as $prraw
        | ($blkByIssue[$n | tostring].blocked_by // []) as $bb
        | (($branchrefs | index("refs/heads/issue/\($n)")) != null) as $hasBranch
        | {
            number:          $n,
            title:           $iss.title,
            state:           $iss.state,
            state_reason:    $iss.state_reason,
            labels:          $iss.labels,
            blocked_by:      $bb,
            blockers_merged: blockers_merged($bb; $stateByNum),
            remote_branch:   $hasBranch,
            pr: (if $prraw == null then null else {
                   number:           $prraw.number,
                   state:            $prraw.state,
                   isDraft:          $prraw.isDraft,
                   mergeable:        $prraw.mergeable,
                   mergeStateStatus: $prraw.mergeStateStatus,
                   headRefOid:       $prraw.headRefOid,
                   updatedAt:        $prraw.updatedAt,
                   checks_failing:   failing($prraw),
                   checks_pending:   pending($prraw)
                 } end),
            stage_hint: stage_hint($prraw; $hasBranch; $iss.state; $iss.state_reason)
          }
      ] ) as $rows
  | {
      batch_id_hint: (if $batch_id_hint == "" then null else $batch_id_hint end),
      generated_for: $gen,
      issues: $rows,
      ready_to_start:
        [ $rows[]
          | select(.state == "open" and .stage_hint == "no-branch"
                   and ((.blocked_by | length) == 0 or .blockers_merged == true))
          | .number ],
      merge_candidate:
        [ $rows[]
          | select(.pr != null and .pr.state == "OPEN" and (.pr.isDraft | not)
                   and .pr.mergeable == "MERGEABLE"
                   and ((.pr.checks_failing | length) == 0)
                   and ((.pr.checks_pending | length) == 0))
          | .number ],
      conflicting:
        [ $rows[] | select(.pr != null and .pr.mergeable == "CONFLICTING") | .number ]
    }
  '
