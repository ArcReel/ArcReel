#!/usr/bin/env bash
# poll.sh — pull all AI reviewer state for a PR in one shot; emit a MINIMAL INDEX on
# stdout and stage the FULL SNAPSHOT to a temp file for query.sh (layer-2 lookups).
#
# USAGE
#   bash poll.sh <PR_NUMBER>
#
# OUTPUT
#   stdout   — minimal index JSON (schema below), semi-compact: containers expand one key
#              per line, row objects stay on one line each. When the derived index is
#              identical to the last fully-printed one, a single line replaces it:
#                {no_change: true, head, last_push_at, unchanged_since, hint}
#              unchanged_since = printed_at of the last full print, so it tells how long
#              the state has been flat. Recover the full index anytime (e.g. after context
#              compaction) via `query.sh <PR> index`. The comparison covers the DERIVED
#              INDEX only; the snapshot is still refreshed every poll, so query.sh always
#              reads current bodies. Errors to stderr prefixed `POLL_ERROR:`.
#   snapshot — full-body JSON at ${TMPDIR:-/tmp}/pr-ai-review-loop-<uid>/poll-<owner>-<repo>-<PR>.json
#              (user-private 0700 subdir), atomically overwritten on every poll. The snapshot
#              carries no cross-round state: it materializes THIS poll's fetch and is fully
#              rebuilt by re-running poll.sh. query.sh is its only intended reader.
#   index    — last fully-printed index plus its printed_at, at <snapshot>.index.json;
#              backs the no_change comparison and `query.sh index`.
#
# INDEX SCHEMA (stdout)
# {
#   "pr": <int>,
#   "pr_created_at": "<ISO8601>",                       # PR createdAt — distinct from last_push_at
#   "head": "<sha>",                                    # current PR head commit SHA
#   "last_push_at": "<ISO8601>",                        # head commit committedDate — see PITFALL 1
#   "round_estimate": <int>,                            # fix-round count: commits with committedDate > pr_created_at,
#                                                       # clustered by >5min gaps (rebase refreshes all dates =>
#                                                       # underestimates; heuristic only)
#   "snapshot_file": "<path>",                          # full snapshot staged for query.sh
#   "base_oid": "<sha>" | null,                         # last commit at/before PR creation — SINCE_SHA for the
#                                                       # first fix batch (null when every commit postdates creation)
#   "commits_since_pr_created": [{oid, committedDate}], # fix-round commits only; pre-PR dev commits stay in snapshot
#   "coderabbit": {
#     "walkthrough": {                                  # CR's first comment (auto-edited each review)
#       "id":                    <int>,                 # REST issue comment id — stable across rewrites
#       "created_at", "updated_at",
#       "reviewed_current_head": <bool>,                # updated_at > last_push_at
#       "is_ok":                 <bool>,                # CR explicit pass marker
#       "is_paused":             <bool>,                # CR paused for this PR
#       "is_in_progress":        <bool>,                # CR still processing — don't declare PASS yet
#       "actionable_count":      "<n>" | null           # parsed from "Actionable comments posted: N"
#     },
#     "reviews":          [{id, submittedAt, state, is_new}],
#     "comments_new":     [{id, createdAt, preview}],   # this-round new non-walkthrough comments
#     "comments_history": {total, last_created_at}      # older ones collapsed to counts ({total: 0} when empty)
#   },
#   "gemini": {
#     "reviews":          [{id, submittedAt, state, has_pass_marker, is_new}],
#                                                       # body NEVER inlined — query.sh gemini-latest-body
#     "comments_new":     [{id, createdAt, preview}],
#     "comments_history": {total, last_created_at}
#   },
#   "inline_new_by_user":     {"<bot[bot]>": [{id, path, created_at, severity_alt, cr_markers,
#                                              is_ack, preview}]},   # id = REST PR review comment id; severity_alt
#                                                       # omitted when null, cr_markers omitted when empty
#   "inline_history_by_user": {"<bot[bot]>": {total, acked, last_created_at}},  # collapses to {total: 0} when empty
#   "codeql_checks": {                                  # CodeQL analysis summary for current HEAD (Analyze (*) /
#     "total": <int>,                                   # codeql-required / CodeQL runs, or runs owned by the scanning
#     "all_ok": <bool>,                                 # apps). all_ok = total > 0 AND all completed AND none failing —
#     "pending": [{name, app, status}],                 # the exit-gate bit; total == 0 alone is never a pass.
#     "failing": [{name, app, conclusion}]              # failing-ish conclusion (set: see checks_failing below).
#   },                                                  # Same-name suite reruns are collapsed in-script to the latest
#                                                       # run per name (by started_at; full rows incl. started_at stay
#                                                       # in the snapshot) so a superseded failure cannot pin them red
#   "checks_failing": [{name, conclusion}],             # check runs on current HEAD with a failing-ish conclusion —
#                                                       # the failing set: failure/timed_out/cancelled/action_required/
#                                                       # startup_failure (single source of truth for "failed check");
#                                                       # red CI can block reviewers, so fix it before waiting on them
#   "security_alerts": {                                # code scanning alerts exit gate — see PITFALL 5
#     "available": <bool>,                              # false = alerts API unreachable; gate must degrade.
#     "unavailable_hint": "<str>" | null,               # first lines of the gh errors when available=false (GitHub
#                                                       # returns 404 for missing permissions too — hint, not proof);
#                                                       # omitted when available == true. pr_ref ("refs/pull/<n>/merge")
#                                                       # is snapshot-only — always omitted from the index
#     "open_introduced": [{number, rule, severity, security_severity, tool, path, url}]
#   },
#   "quota_alerts": [...],                              # PR-level issue comments matching quota-error phrases
#   "own_trigger_comments": [{author, createdAt, command}]  # human-authored trigger commands — see PITFALL 4
# }
#
# FLAG SEMANTICS (single source of truth — reviewers.md references these fields by name)
#   is_new                 new this round: created_at/submittedAt > last_push_at (see PITFALL 2)
#   reviewed_current_head  walkthrough.updated_at > last_push_at (CR rewrites its first comment each review)
#   is_ack                 reviewer acknowledgment of a fix or inline reply (never actionable): body carries a
#                          <review_comment_addressed>/<review_comment_withdrawn> marker or starts with "### Summary"
#   cr_markers             CodeRabbit tag tokens found in the first 300 chars of a body (literal match):
#                            potential_issue "_⚠️ Potential issue"     major     "_🟠 Major"
#                            refactor        "_🛠️ Refactor suggestion" minor     "_🟡 Minor"
#                            verification    "_💡 Verification agent"  trivial   "_🔵 Trivial"
#                            nitpick         "_🧹 Nitpick"             low_value "_💤 Low value"
#   severity_alt           Gemini inline severity from its ![severity](...) badge image alt text
#   has_pass_marker        Gemini review summary carries an explicit pass marker: "LGTM" / "no issues found" /
#                          "no feedback to provide" (any case) / word "approved" (any case), or body is empty
#                          aside from the "## Code Review" heading (any case)
#   preview                first 120 chars of body after stripping HTML comments and markdown images, whitespace
#                          collapsed — the eyeball safety net for flag misparses (flag vs preview conflict => fetch
#                          full body via query.sh details and trust the body)
#
# SNAPSHOT SCHEMA — same tree as the index, except: `other_comments`/`comments`/`reviews`/
#   `inline_comments_by_user` are FULL lists (no new/history split) with full `body` and `is_new`
#   on every row, walkthrough keeps `body`, `commits` is the full list, own_trigger_comments
#   keeps `body`, `codeql_checks` is the full row list ({name, app, status, conclusion,
#   started_at, is_failing}) instead of the summary, plus top-level `repo` and `generated_at`
#   for query.sh provenance.
#
# PITFALLS
#
# 1. last_push_at uses head commit committedDate, NOT pushedDate.
#    pushedDate is null on the PR's head commit — GitHub's PR API doesn't surface push event time
#    here. committedDate is the most reliable timestamp available.
#
# 2. is_new uses `created_at > last_push_at`, NOT `commit_id == head`.
#    CodeRabbit's old inline comments get their commit_id advanced when it re-reviews a new HEAD
#    (in-place edit or thread re-link — exact mechanism unconfirmed). created_at is per-comment-stable.
#
# 3. REST vs GraphQL bot login strings are NOT interchangeable.
#    GraphQL `author.login` = "coderabbitai" (no [bot] suffix).
#    REST    `user.login`   = "coderabbitai[bot]" (with [bot] suffix).
#    This script uses both endpoints; downstream consumers must use the right form for each datum.
#
# 4. Trigger-command dedup matches comments that START with the command (case-insensitive,
#    leading spaces/tabs tolerated, trailing text allowed). Prefix matching — not full-line —
#    so a human-issued "/gemini review (re: security fix)" still registers for dedup, while
#    a comment merely MENTIONING a command mid-text does not (substring matching would
#    swallow pushback comments that quote a command, silently suppressing real triggers).
#    Leading whitespace is [ \t] only, NOT \s: \s matches \n, which would also register a
#    command sitting on the second line after a blank first line — keep the matcher aligned
#    with the documented contract (command at the very start of the comment).
#
# 5. security_alerts.open_introduced subtracts default-branch open alerts by alert number.
#    The merge-ref analysis covers the whole codebase, so pre-existing alerts (e.g. scheduled
#    Trivy scans on main) would otherwise block the exit gate forever. Alert numbers are
#    repo-global and identical across refs, so a set difference on number is exact.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "POLL_ERROR: missing PR_NUMBER. Usage: bash poll.sh <PR_NUMBER>" >&2
  exit 2
fi

PR="$1"

if ! [[ "$PR" =~ ^[0-9]+$ ]]; then
  echo "POLL_ERROR: PR_NUMBER must be a number, got: $PR" >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "POLL_ERROR: gh CLI not found on PATH" >&2
  exit 3
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "POLL_ERROR: jq not found on PATH" >&2
  exit 3
fi

# Snapshot dir: a user-private subdir (0700) keeps the predictable filename out of the
# shared /tmp namespace on multi-user hosts; a pre-planted symlink (mkdir -p follows it)
# or a foreign-owned dir at the path aborts loudly before anything is written.
SNAP_BASE="${TMPDIR:-/tmp}"
SNAP_DIR="${SNAP_BASE%/}/pr-ai-review-loop-$(id -u)"
if [[ -L "$SNAP_DIR" ]]; then
  echo "POLL_ERROR: snapshot dir is a symlink: $SNAP_DIR" >&2
  exit 4
fi
# Plain mkdir (no -p): never follows a symlink to create elsewhere; parent tmpdir always
# exists. On EEXIST re-validate — including -L for a symlink raced in after the check.
if ! mkdir "$SNAP_DIR" 2>/dev/null; then
  if [[ -L "$SNAP_DIR" || ! -d "$SNAP_DIR" || ! -O "$SNAP_DIR" ]]; then
    echo "POLL_ERROR: snapshot dir is a symlink, missing, or not owned by the current user: $SNAP_DIR" >&2
    exit 4
  fi
fi
chmod 700 "$SNAP_DIR"

# Stage gh output into temp files. Large PRs (dozens of comments) make --argjson
# overflow ARG_MAX; --slurpfile reads from disk and is unbounded. Each gh call paginates,
# so PRs with hundreds of comments work too. WORKDIR is created up-front so every gh
# invocation can route its stderr here — the skill's troubleshooting section promises
# stderr on failure, so silently dropping it via `2>/dev/null` defeats that contract.
# It lives inside SNAP_DIR so staged PR data shares the 0700 protection and the final
# snapshot rename never crosses a filesystem boundary (mv stays atomic).
WORKDIR=$(mktemp -d "$SNAP_DIR/tmp.XXXXXX")
trap 'rm -rf "$WORKDIR"' EXIT

OWNER_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>"$WORKDIR/gh_repo_view.err") || {
  echo "POLL_ERROR: gh repo view failed (auth? wrong cwd?)" >&2
  cat "$WORKDIR/gh_repo_view.err" >&2
  exit 4
}

# The repo slug keeps same-numbered PRs from different repos apart.
SNAPSHOT_FILE="$SNAP_DIR/poll-${OWNER_REPO//\//-}-${PR}.json"

# Main query — GraphQL via gh pr view. author.login here is WITHOUT [bot] suffix.
gh pr view "$PR" --json number,createdAt,headRefOid,reviews,comments,commits > "$WORKDIR/main.json" 2>"$WORKDIR/gh_pr_view.err" || {
  echo "POLL_ERROR: gh pr view $PR failed" >&2
  cat "$WORKDIR/gh_pr_view.err" >&2
  exit 5
}

# REST endpoints. --paginate output shape differs by mode (verified empirically):
#   - WITHOUT --jq/-q: gh merges all pages of an array endpoint into ONE JSON array,
#     so --slurpfile sees a single value — unwrap with [0].
#   - WITH -q: the projection runs per page, emitting one JSON value PER PAGE
#     (concatenated stream) — unwrap with `add` (see sub-query D).
# user.login here is WITH [bot] suffix.

# Sub-query A — REST issue comments. Used to get CodeRabbit walkthrough's updated_at
# (GraphQL doesn't expose updated_at on PR comments).
gh api "repos/${OWNER_REPO}/issues/${PR}/comments" --paginate > "$WORKDIR/sub_a.json" 2>"$WORKDIR/gh_issue_comments.err" || {
  echo "POLL_ERROR: REST issue comments fetch failed" >&2
  cat "$WORKDIR/gh_issue_comments.err" >&2
  exit 5
}

# Sub-query C — REST inline review comments on the PR diff (severity tags live here).
# (Sub-query B removed — was Codex reactions.)
gh api "repos/${OWNER_REPO}/pulls/${PR}/comments" --paginate > "$WORKDIR/sub_c.json" 2>"$WORKDIR/gh_pr_comments.err" || {
  echo "POLL_ERROR: REST PR review comments fetch failed" >&2
  cat "$WORKDIR/gh_pr_comments.err" >&2
  exit 5
}

# Sub-query D — check runs on the PR head. Feeds two projections: codeql_checks (exit
# gate: "analysis finished before declaring PASS") and checks_failing (red CI can block
# reviewers). --paginate with -q runs the projection per page, emitting one array per
# page; downstream slurpfile flattens with `add` (same as sub-query E).
HEAD_SHA=$(jq -r '.headRefOid' "$WORKDIR/main.json")
gh api "repos/${OWNER_REPO}/commits/${HEAD_SHA}/check-runs?per_page=100" --paginate -q '[.check_runs[] | {name, app: .app.slug, status, conclusion, started_at}]' > "$WORKDIR/sub_d.json" 2>"$WORKDIR/gh_check_runs.err" || {
  echo "POLL_ERROR: REST check-runs fetch failed" >&2
  cat "$WORKDIR/gh_check_runs.err" >&2
  exit 5
}

# Sub-query E — code scanning alerts (security exit gate). This API can fail for benign
# reasons (token missing security-events scope, merge ref not analyzed yet, merge
# conflict), so degrade to available=false instead of failing the whole poll.
SECURITY_ALERTS_AVAILABLE=true
SECURITY_ALERTS_HINT=""
if ! gh api "repos/${OWNER_REPO}/code-scanning/alerts?ref=refs/pull/${PR}/merge&state=open&per_page=100" --paginate > "$WORKDIR/sub_e_pr.json" 2>"$WORKDIR/gh_alerts_pr.err"; then
  SECURITY_ALERTS_AVAILABLE=false
  SECURITY_ALERTS_HINT="pr-ref: $(head -n 2 "$WORKDIR/gh_alerts_pr.err" | tr '\n' ' ' | cut -c1-300)"
  echo '[]' > "$WORKDIR/sub_e_pr.json"
fi
if ! gh api "repos/${OWNER_REPO}/code-scanning/alerts?state=open&per_page=100" --paginate > "$WORKDIR/sub_e_base.json" 2>"$WORKDIR/gh_alerts_base.err"; then
  SECURITY_ALERTS_AVAILABLE=false
  SECURITY_ALERTS_HINT="${SECURITY_ALERTS_HINT} base: $(head -n 2 "$WORKDIR/gh_alerts_base.err" | tr '\n' ' ' | cut -c1-300)"
  echo '[]' > "$WORKDIR/sub_e_base.json"
fi

GENERATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ---- Pass 1: build the FULL SNAPSHOT (full bodies + every flag) ----
# Flags are computed once, here, so the index and every query.sh consumer see identical
# judgments. Bot login normalization happens here so consumers see consistent keys.
# --slurpfile wraps each file in [...]. Unwrap rule: files written WITHOUT -q hold one
# merged array (gh merges pages) — [0] suffices; sub-query D is written WITH -q, so it
# holds one array PER PAGE — only `add` flattens that correctly ([0] would drop pages 2+).
# `add` also equals [0] on single-value files, so D/E both use it.
jq -n \
  --slurpfile main_w "$WORKDIR/main.json" \
  --slurpfile sub_a_w "$WORKDIR/sub_a.json" \
  --slurpfile sub_c_w "$WORKDIR/sub_c.json" \
  --slurpfile sub_d_w "$WORKDIR/sub_d.json" \
  --slurpfile sub_e_pr_w "$WORKDIR/sub_e_pr.json" \
  --slurpfile sub_e_base_w "$WORKDIR/sub_e_base.json" \
  --argjson security_available "$SECURITY_ALERTS_AVAILABLE" \
  --arg security_hint "$SECURITY_ALERTS_HINT" \
  --arg repo "$OWNER_REPO" \
  --arg generated_at "$GENERATED_AT" \
  '
  ($main_w[0]) as $main
  | ($sub_a_w[0]) as $sub_a
  | ($sub_c_w[0]) as $sub_c
  | ($main.commits | last | .committedDate) as $last_push
  # Check-suite reruns leave same-name duplicates; keep only the latest run per name so a
  # superseded failure cannot pin codeql_checks / checks_failing red forever.
  | ((($sub_d_w | add) // []) | group_by(.name) | map(max_by(.started_at))) as $check_runs |
  # ---- shared helpers ----
  # Every body-consuming helper opens with (. // "") — jq string functions (gsub/test/
  # contains/capture) raise fatal errors on null input, and a null body must not kill a poll.
  def mk_preview:
    (. // "")
    | gsub("<!--[\\s\\S]*?-->"; "")
    | gsub("!\\[[^\\]]*\\]\\([^\\)]*\\)"; "")
    | gsub("\\s+"; " ")
    | sub("^ +"; "")
    | .[0:120];

  def is_ack_body:
    (. // "")
    | ((test("<!--\\s*<review_comment_addressed>"))
       or (test("<!--\\s*<review_comment_withdrawn>"))
       or (test("^### Summary")));

  def is_failing_conclusion:
    IN("failure", "timed_out", "cancelled", "action_required", "startup_failure");

  def cr_markers_of:
    (. // "")[0:300] as $h
    | [
        ["potential_issue", "_⚠️ Potential issue"],
        ["major",           "_🟠 Major"],
        ["minor",           "_🟡 Minor"],
        ["refactor",        "_🛠️ Refactor suggestion"],
        ["verification",    "_💡 Verification agent"],
        ["nitpick",         "_🧹 Nitpick"],
        ["trivial",         "_🔵 Trivial"],
        ["low_value",       "_💤 Low value"]
      ]
    | map(select(.[1] as $pat | $h | contains($pat)) | .[0]);

  def has_pass_marker_body:
    (. // "")
    | ((test("\\bLGTM\\b"))
       or (test("no issues found"; "i"))
       or (test("no feedback to provide"; "i"))
       or (test("\\bapproved\\b"; "i"))
       or ((gsub("\\s+"; "") | ascii_downcase) as $bare | ($bare == "" or $bare == "##codereview")));

  def cr_walkthrough_rest:
    [$sub_a[] | select(.user.login == "coderabbitai[bot]")]
    | sort_by(.created_at)
    | first
    | if . == null then null else
        (.body // "") as $wb
        | {
          id,
          created_at,
          updated_at,
          reviewed_current_head: (.updated_at > $last_push),
          is_ok:          ($wb | test("No actionable comments were generated in the recent review")),
          is_paused:      ($wb | test("(review[s]?\\s+paused|paused\\s+by\\s+coderabbit|automatic reviews are paused|paused\\s+for\\s+this\\s+PR)"; "i")),
          is_in_progress: ($wb | test("(review in progress by coderabbit|currently processing new changes)"; "i")),
          actionable_count:
            (if ($wb | test("Actionable comments posted:"))
             then ($wb | capture("Actionable comments posted:\\s*(?<n>[0-9]+)") | .n)
             else null end),
          body
        }
      end;

  def inline_by_bot:
    [$sub_c[] | select(.user.login | test("(coderabbitai|gemini-code-assist|github-code-quality|github-advanced-security)\\[bot\\]$"))]
    | group_by(.user.login)
    | map({
        key:   .[0].user.login,
        value: map({
          id,
          path,
          commit_id,
          created_at,
          is_new:       (.created_at > $last_push),
          severity_alt: ([(.body // "") | capture("!\\[(?<s>[^\\]]+)\\]")] | .[0].s // null),
          cr_markers:   (.body | cr_markers_of),
          is_ack:       (.body | is_ack_body),
          preview:      (.body | mk_preview),
          body
        })
      })
    | from_entries;

  def quota_alerts:
    # Match ONLY explicit quota/rate-limit ERROR phrases, restricted to body head.
    # Bare keywords like "quota" / "rate limit" alone produce false positives when a
    # bot reply happens to discuss quota as a topic (e.g. a PR description that mentions quota).
    # Real alerts always pair a keyword with a verb like "exceeded" / "reached" / "exhausted",
    # or use a fixed phrase like "You have / You\x27ve reached your ... limit".
    # CodeRabbit additionally always wraps its rate-limit banner in a dedicated
    # HTML marker ("rate limited by coderabbit.ai"); matching that marker directly
    # is CodeRabbit-authored and unambiguous, so this check is not restricted to
    # the body head — a preceding change-stack link banner can otherwise push
    # the phrase itself past the 500-char window and cause a silent miss.
    [$sub_a[]
     | select(.user.login | test("(gemini-code-assist|coderabbitai)\\[bot\\]$"))
     | (.body // "") as $qb
     | select(
         ($qb | test("<!--\\s*This is an auto-generated comment:\\s*rate limited by coderabbit\\.ai\\s*-->"))
         or ($qb[0:500] | test("you(\\x27ve|\\x{2019}ve|\\s+have)\\s+reached your[^\\n]*?limit"; "i"))
         or ($qb[0:500] | test("(usage|rate|api|daily|monthly)\\s+limit[^\\n]*?(exceeded|reached|hit|reset)"; "i"))
         or ($qb[0:500] | test("quota[^\\n]*?(exceeded|exhausted|reached|reset|limit hit)"; "i"))
         or ($qb[0:500] | test("(http\\s*)?429\\b|too many requests"; "i"))
       )
     | {user: .user.login, created_at, body_head: ($qb[0:300])}];

  # ---- snapshot projection ----
  {
    pr:            $main.number,
    pr_created_at: $main.createdAt,
    head:          $main.headRefOid,
    last_push_at:  $last_push,
    repo:          $repo,
    generated_at:  $generated_at,
    commits:       [$main.commits[] | {oid, committedDate}],
    round_estimate:
      ([$main.commits[] | select(.committedDate > $main.createdAt) | .committedDate]
       | sort | map(fromdateiso8601)
       | reduce .[] as $t ({prev: 0, n: 0};
           if ($t - .prev) > 300 then {prev: $t, n: (.n + 1)} else {prev: $t, n: .n} end)
       | .n),

    coderabbit: {
      walkthrough: cr_walkthrough_rest,
      other_comments:
        ([$main.comments[] | select(.author.login == "coderabbitai")]
         | sort_by(.createdAt) | .[1:]
         | map({id, createdAt, is_new: (.createdAt > $last_push), preview: (.body | mk_preview), body})),
      reviews:
        [$main.reviews[] | select(.author.login == "coderabbitai")
         | {id, submittedAt, state, is_new: (.submittedAt > $last_push), body}]
    },

    gemini: {
      reviews:
        [$main.reviews[] | select(.author.login == "gemini-code-assist")
         | {id, submittedAt, state, is_new: (.submittedAt > $last_push),
            has_pass_marker: (.body | has_pass_marker_body), body}],
      comments:
        [$main.comments[] | select(.author.login == "gemini-code-assist")
         | {id, createdAt, is_new: (.createdAt > $last_push), preview: (.body | mk_preview), body}]
    },

    inline_comments_by_user: inline_by_bot,

    codeql_checks:
      [$check_runs[]
       | select((.app == "github-advanced-security" or .app == "github-code-quality")
                or (.name | test("^Analyze \\(|^codeql-required$|^CodeQL$")))
       | . + {is_failing: (.conclusion | is_failing_conclusion)}],

    checks_failing:
      [$check_runs[]
       | select(.conclusion | is_failing_conclusion)
       | {name, conclusion}],

    security_alerts: {
      available: $security_available,
      unavailable_hint: (if $security_available then null else $security_hint end),
      pr_ref: ("refs/pull/" + ($main.number | tostring) + "/merge"),
      open_introduced:
        (($sub_e_base_w | add | map(.number)) as $base_numbers
         | [($sub_e_pr_w | add)[]
            | select(.number as $n | $base_numbers | index($n) | not)
            | {number,
               rule:              .rule.id,
               severity:          .rule.severity,
               security_severity: .rule.security_severity_level,
               tool:              .tool.name,
               path:              .most_recent_instance.location.path,
               url:               .html_url}])
    },

    quota_alerts: quota_alerts,

    own_trigger_comments:
      [$main.comments[]
       | (.body // "") as $tb
       | select(
           (.author.login != "coderabbitai"
            and .author.login != "gemini-code-assist")
           and ($tb | test("^[ \\t]*(/gemini review|@coderabbitai (resume|full review|review))(\\s|$)"; "i"))
         )
       | {author: .author.login, createdAt,
          command: ($tb | capture("^[ \\t]*(?<c>/gemini review|@coderabbitai (resume|full review|review))"; "i") | .c | ascii_downcase),
          body: ($tb | gsub("^\\s+|\\s+$"; ""))}]
  }
  ' > "$WORKDIR/snapshot.json"

# Atomic overwrite: same-filesystem rename keeps concurrent query.sh reads consistent.
mv "$WORKDIR/snapshot.json" "$SNAPSHOT_FILE"

# ---- Pass 2: project the MINIMAL INDEX from the snapshot ----
# New rows carry flags + preview; older rows collapse to per-bot counts. Bodies never
# reach stdout — query.sh reads them from the snapshot on demand.
jq --arg snapshot_file "$SNAPSHOT_FILE" '
  def prune_hist: if .total == 0 then {total} else . end;
  . as $s
  | ($s.pr_created_at) as $created
  | {
      pr:             $s.pr,
      pr_created_at:  $s.pr_created_at,
      head:           $s.head,
      last_push_at:   $s.last_push_at,
      round_estimate: $s.round_estimate,
      snapshot_file:  $snapshot_file,
      base_oid:       ([$s.commits[] | select(.committedDate <= $created)] | (last | .oid) // null),
      commits_since_pr_created: [$s.commits[] | select(.committedDate > $created)],

      coderabbit: {
        walkthrough: ($s.coderabbit.walkthrough | if . == null then null else del(.body) end),
        reviews:     [$s.coderabbit.reviews[] | {id, submittedAt, state, is_new}],
        comments_new:
          [$s.coderabbit.other_comments[] | select(.is_new) | {id, createdAt, preview}],
        comments_history:
          ([$s.coderabbit.other_comments[] | select(.is_new | not)]
           | {total: length, last_created_at: (map(.createdAt) | max // null)}
           | prune_hist)
      },

      gemini: {
        reviews: [$s.gemini.reviews[] | {id, submittedAt, state, has_pass_marker, is_new}],
        comments_new:
          [$s.gemini.comments[] | select(.is_new) | {id, createdAt, preview}],
        comments_history:
          ([$s.gemini.comments[] | select(.is_new | not)]
           | {total: length, last_created_at: (map(.createdAt) | max // null)}
           | prune_hist)
      },

      inline_new_by_user:
        ($s.inline_comments_by_user
         | map_values([.[] | select(.is_new)
                       | {id, path, created_at, severity_alt, cr_markers, is_ack, preview}
                       | (if .severity_alt == null then del(.severity_alt) else . end)
                       | (if .cr_markers == [] then del(.cr_markers) else . end)])),
      inline_history_by_user:
        ($s.inline_comments_by_user
         | map_values([.[] | select(.is_new | not)]
                      | {total: length,
                         acked: (map(select(.is_ack)) | length),
                         last_created_at: (map(.created_at) | max // null)}
                      | prune_hist)),

      codeql_checks:
        ($s.codeql_checks
         | {total: length,
            all_ok: ((length > 0) and all(.[]; .status == "completed" and (.is_failing | not))),
            pending: [.[] | select(.status != "completed") | {name, app, status}],
            failing: [.[] | select(.is_failing) | {name, app, conclusion}]}),

      checks_failing:  $s.checks_failing,
      security_alerts:
        ($s.security_alerts
         | if .available then {available, open_introduced} else del(.pr_ref) end),
      quota_alerts:    $s.quota_alerts,
      own_trigger_comments: [$s.own_trigger_comments[] | {author, createdAt, command}]
    }
  ' "$SNAPSHOT_FILE" > "$WORKDIR/index.json"

# ---- Pass 3: no-change collapse + semi-compact print ----
# Waiting rounds dominate the loop; re-printing an identical index every poll is pure
# context waste. Compare the derived index (key-order normalized) against the last fully
# printed one and collapse to a single no_change line on match. printed_at sticks to the
# last full print, so unchanged_since reports how long the state has been flat.
INDEX_FILE="${SNAPSHOT_FILE%.json}.index.json"
NEW_NORM=$(jq -cS . "$WORKDIR/index.json")
PREV_NORM=""
if [[ -f "$INDEX_FILE" ]]; then
  PREV_NORM=$(jq -cS '.index' "$INDEX_FILE" 2>/dev/null) || PREV_NORM=""
fi

if [[ -n "$PREV_NORM" && "$PREV_NORM" == "$NEW_NORM" ]]; then
  jq -c --arg pr "$PR" '
    {no_change: true,
     head: .index.head,
     last_push_at: .index.last_push_at,
     unchanged_since: .printed_at,
     hint: ("index identical to every poll since unchanged_since; full index: bash query.sh " + $pr + " index")}
  ' "$INDEX_FILE"
else
  jq -n --arg printed_at "$GENERATED_AT" --slurpfile idx "$WORKDIR/index.json" \
    '{printed_at: $printed_at, index: $idx[0]}' > "$WORKDIR/index_store.json"
  mv "$WORKDIR/index_store.json" "$INDEX_FILE"
  # Semi-compact render: same JSON, but row objects (and flat leaf objects like the
  # walkthrough) print on one line each instead of one line per field.
  jq -r '
    def scalarish: (type != "object") and (type != "array");
    def flatarr: type == "array" and all(.[]?; scalarish);
    def leafobj: type == "object" and all(.[]?; scalarish or flatarr);
    def render($ind):
      if scalarish or flatarr then tojson
      elif leafobj then tojson
      elif type == "array" and all(.[]?; leafobj) then
        "[\n" + ([.[] | $ind + "  " + tojson] | join(",\n")) + "\n" + $ind + "]"
      elif type == "array" then
        "[\n" + ([.[] | $ind + "  " + render($ind + "  ")] | join(",\n")) + "\n" + $ind + "]"
      else
        "{\n" + ([to_entries[] | $ind + "  " + (.key | tojson) + ": "
                  + (.value | render($ind + "  "))] | join(",\n")) + "\n" + $ind + "}"
      end;
    render("")
  ' "$WORKDIR/index.json"
fi
