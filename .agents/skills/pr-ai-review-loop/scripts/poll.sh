#!/usr/bin/env bash
# poll.sh — pull all AI reviewer state for a PR in one shot.
#
# USAGE
#   bash poll.sh <PR_NUMBER>
#
# OUTPUT: single JSON object to stdout (errors to stderr prefixed `POLL_ERROR:`).
#
# JSON SCHEMA
# {
#   "pr": <int>,                                        # PR number
#   "pr_created_at": "<ISO8601>",                       # PR createdAt (issue creation time) — distinct from last_push_at
#   "head": "<sha>",                                    # current PR head commit SHA
#   "last_push_at": "<ISO8601>",                        # head commit committedDate — see PITFALL 1
#   "commits": [{oid, committedDate}],                  # full commit list (includes pre-PR development commits)
#   "round_estimate": <int>,                            # fix-round count: commits with committedDate > pr_created_at, clustered
#                                                       # by >5min gaps (rebase refreshes all dates => underestimates; heuristic only)
#   "coderabbit": {
#     "walkthrough": {                                  # CR's first comment (auto-edited each review)
#       "id":                 <int>,                    # REST issue comment id — stable across walkthrough rewrites
#       "created_at", "updated_at",                     # updated_at > last_push_at => CR has reviewed current HEAD
#       "is_ok":              <bool>,                   # CR explicit pass marker
#       "is_paused":          <bool>,                   # CR paused for this PR
#       "is_in_progress":     <bool>,                   # CR still processing — don't declare PASS yet
#       "actionable_count":   "<n>" | null              # parsed from "Actionable comments posted: N"
#     },
#     "other_comments":       [...],                    # CR's other PR comments (not walkthrough)
#     "reviews":              [...]                     # CR's review-level submissions
#   },
#   "gemini": {
#     "reviews":  [{id, submittedAt, state, body}],     # body = review SUMMARY (## Code Review ...) — can contain actionable text not in inline; id = GraphQL node id
#     "comments": [...]
#   },
#   "codex": {
#     "reviews":   [{id, submittedAt, state, body}],    # body contains "Reviewed commit: <SHA>" when present; id = GraphQL node id
#     "comments":  [...],
#     "reactions": [{content, created_at}]              # +1 reaction on PR = silent ack — see PITFALL 4
#   },
#   "inline_comments_by_user": {                        # PR-level inline review comments grouped by bot — includes
#     "<bot[bot]>": [{id, path, commit_id, created_at, severity_alt, is_ack, body_head}]  # github-code-quality[bot] and
#   },                                                  # github-advanced-security[bot]; id = REST PR review comment id
#   "codeql_checks": [{name, app, status, conclusion}], # CodeQL-related check runs on current HEAD (Analyze (*) /
#                                                       # codeql-required / CodeQL, or runs owned by the code scanning apps)
#   "checks_failing": [{name, conclusion}],             # ALL check runs on current HEAD with a failing-ish conclusion
#                                                       # (failure/timed_out/cancelled/action_required/startup_failure);
#                                                       # red CI can block reviewers, so fix it before waiting on them
#   "security_alerts": {                                # code scanning alerts exit gate — see PITFALL 7
#     "available": <bool>,                              # false = alerts API unreachable (missing scope / no merge-ref analysis); gate must degrade
#     "unavailable_hint": "<str>" | null,               # first lines of the gh errors when available=false — helps distinguish
#                                                       # 404 not-enabled vs 403 missing-scope vs no-analysis (note: GitHub returns
#                                                       # 404 for missing permissions too, so treat as a hint, not proof)
#     "pr_ref": "refs/pull/<n>/merge",
#     "open_introduced": [{number, rule, severity, security_severity, tool, path, url}]  # open alerts introduced by this PR
#   },
#   "quota_alerts": [...],                              # PR-level issue comments matching quota keywords (bots emit quota errors as plain comments, not reviews)
#   "own_trigger_comments": [...]                       # human-authored /gemini review / @codex review / @coderabbitai resume
# }
#
# PITFALLS
#
# 1. last_push_at uses head commit committedDate, NOT pushedDate.
#    pushedDate is null on the PR's head commit — GitHub's PR API doesn't surface push event time here.
#    committedDate is the most reliable timestamp available.
#
# 2. Determining "this round's new inline" must use `created_at > last_push_at`, NOT `commit_id == head`.
#    CodeRabbit's old inline comments get their commit_id advanced when it re-reviews a new HEAD
#    (in-place edit or thread re-link — exact mechanism unconfirmed). created_at is per-comment-stable.
#
# 3. REST vs GraphQL bot login strings are NOT interchangeable.
#    GraphQL `author.login` = "coderabbitai" (no [bot] suffix).
#    REST    `user.login`   = "coderabbitai[bot]" (with [bot] suffix).
#    This script uses both endpoints; downstream consumers must use the right form for each datum.
#
# 4. Codex acks PR in 3 modes — all must be checked (see references/reviewers.md for full table):
#    (a) inline review with body "### 💡 Codex Review" + "Reviewed commit: <SHA>"
#    (b) PR-level +1 reaction with NO comment (silent pass)
#    (c) empty-body review (state=COMMENTED, body="") with no new inline
#
# 5. Trigger-command dedup matches comments that START with the command (case-insensitive,
#    leading spaces/tabs tolerated, trailing text allowed). Prefix matching — not full-line —
#    so a human-issued "/gemini review (re: security fix)" still registers for dedup, while
#    a comment merely MENTIONING a command mid-text does not (substring matching would
#    swallow pushback comments that quote a command, silently suppressing real triggers).
#    Leading whitespace is [ \t] only, NOT \s: \s matches \n, which would also register a
#    command sitting on the second line after a blank first line — keep the matcher aligned
#    with the documented contract (command at the very start of the comment).
#
# 6. Quota / rate-limit errors from Codex are PR-level ISSUE comments, NOT reviews/inline/reactions.
#    Codex emits e.g. "You have reached your Codex usage limits..." as a plain PR comment — easy to miss.
#    Captured into quota_alerts so the skill catches it on the first poll.
#
# 7. security_alerts.open_introduced subtracts default-branch open alerts by alert number.
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

# Stage gh output into temp files. Large PRs (dozens of comments) make --argjson
# overflow ARG_MAX; --slurpfile reads from disk and is unbounded. Each gh call paginates,
# so PRs with hundreds of comments work too. TMPDIR is created up-front so every gh
# invocation can route its stderr here — the skill's troubleshooting section promises
# stderr on failure, so silently dropping it via `2>/dev/null` defeats that contract.
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

OWNER_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>"$TMPDIR/gh_repo_view.err") || {
  echo "POLL_ERROR: gh repo view failed (auth? wrong cwd?)" >&2
  cat "$TMPDIR/gh_repo_view.err" >&2
  exit 4
}

# Main query — GraphQL via gh pr view. author.login here is WITHOUT [bot] suffix.
gh pr view "$PR" --json number,createdAt,headRefOid,reviews,comments,commits > "$TMPDIR/main.json" 2>"$TMPDIR/gh_pr_view.err" || {
  echo "POLL_ERROR: gh pr view $PR failed" >&2
  cat "$TMPDIR/gh_pr_view.err" >&2
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
gh api "repos/${OWNER_REPO}/issues/${PR}/comments" --paginate > "$TMPDIR/sub_a.json" 2>"$TMPDIR/gh_issue_comments.err" || {
  echo "POLL_ERROR: REST issue comments fetch failed" >&2
  cat "$TMPDIR/gh_issue_comments.err" >&2
  exit 5
}

# Sub-query B — PR-level reactions (Codex silent +1 ack path).
gh api "repos/${OWNER_REPO}/issues/${PR}/reactions" --paginate > "$TMPDIR/sub_b.json" 2>"$TMPDIR/gh_reactions.err" || {
  echo "POLL_ERROR: REST reactions fetch failed" >&2
  cat "$TMPDIR/gh_reactions.err" >&2
  exit 5
}

# Sub-query C — REST inline review comments on the PR diff (severity tags live here).
gh api "repos/${OWNER_REPO}/pulls/${PR}/comments" --paginate > "$TMPDIR/sub_c.json" 2>"$TMPDIR/gh_pr_comments.err" || {
  echo "POLL_ERROR: REST PR review comments fetch failed" >&2
  cat "$TMPDIR/gh_pr_comments.err" >&2
  exit 5
}

# Sub-query D — check runs on the PR head. Feeds two projections: codeql_checks (exit
# gate: "analysis finished before declaring PASS") and checks_failing (red CI can block
# reviewers). --paginate with -q runs the projection per page, emitting one array per
# page; downstream slurpfile flattens with `add` (same as sub-query E).
HEAD_SHA=$(jq -r '.headRefOid' "$TMPDIR/main.json")
gh api "repos/${OWNER_REPO}/commits/${HEAD_SHA}/check-runs?per_page=100" --paginate -q '[.check_runs[] | {name, app: .app.slug, status, conclusion}]' > "$TMPDIR/sub_d.json" 2>"$TMPDIR/gh_check_runs.err" || {
  echo "POLL_ERROR: REST check-runs fetch failed" >&2
  cat "$TMPDIR/gh_check_runs.err" >&2
  exit 5
}

# Sub-query E — code scanning alerts (security exit gate). This API can fail for benign
# reasons (token missing security-events scope, merge ref not analyzed yet, merge
# conflict), so degrade to available=false instead of failing the whole poll.
SECURITY_ALERTS_AVAILABLE=true
SECURITY_ALERTS_HINT=""
if ! gh api "repos/${OWNER_REPO}/code-scanning/alerts?ref=refs/pull/${PR}/merge&state=open&per_page=100" --paginate > "$TMPDIR/sub_e_pr.json" 2>"$TMPDIR/gh_alerts_pr.err"; then
  SECURITY_ALERTS_AVAILABLE=false
  SECURITY_ALERTS_HINT="pr-ref: $(head -n 2 "$TMPDIR/gh_alerts_pr.err" | tr '\n' ' ' | cut -c1-300)"
  echo '[]' > "$TMPDIR/sub_e_pr.json"
fi
if ! gh api "repos/${OWNER_REPO}/code-scanning/alerts?state=open&per_page=100" --paginate > "$TMPDIR/sub_e_base.json" 2>"$TMPDIR/gh_alerts_base.err"; then
  SECURITY_ALERTS_AVAILABLE=false
  SECURITY_ALERTS_HINT="${SECURITY_ALERTS_HINT} base: $(head -n 2 "$TMPDIR/gh_alerts_base.err" | tr '\n' ' ' | cut -c1-300)"
  echo '[]' > "$TMPDIR/sub_e_base.json"
fi

# Combine everything in jq. Bot login normalization happens here so consumers see consistent keys.
# --slurpfile wraps each file in [...]. Unwrap rule: files written WITHOUT -q hold one
# merged array (gh merges pages) — [0] suffices; sub-query D is written WITH -q, so it
# holds one array PER PAGE — only `add` flattens that correctly ([0] would drop pages 2+).
# `add` also equals [0] on single-value files, so D/E both use it.
jq -n \
  --slurpfile main_w "$TMPDIR/main.json" \
  --slurpfile sub_a_w "$TMPDIR/sub_a.json" \
  --slurpfile sub_b_w "$TMPDIR/sub_b.json" \
  --slurpfile sub_c_w "$TMPDIR/sub_c.json" \
  --slurpfile sub_d_w "$TMPDIR/sub_d.json" \
  --slurpfile sub_e_pr_w "$TMPDIR/sub_e_pr.json" \
  --slurpfile sub_e_base_w "$TMPDIR/sub_e_base.json" \
  --argjson security_available "$SECURITY_ALERTS_AVAILABLE" \
  --arg security_hint "$SECURITY_ALERTS_HINT" \
  '
  ($main_w[0]) as $main
  | ($sub_a_w[0]) as $sub_a
  | ($sub_b_w[0]) as $sub_b
  | ($sub_c_w[0]) as $sub_c |
  # ---- shared helpers ----
  def cr_walkthrough_rest:
    [$sub_a[] | select(.user.login == "coderabbitai[bot]")]
    | sort_by(.created_at)
    | first
    | if . == null then null else
        {
          id,
          created_at,
          updated_at,
          is_ok:          (.body | test("No actionable comments were generated in the recent review")),
          is_paused:      (.body | test("(review[s]?\\s+paused|paused\\s+by\\s+coderabbit|automatic reviews are paused|paused\\s+for\\s+this\\s+PR)"; "i")),
          is_in_progress: (.body | test("(review in progress by coderabbit|currently processing new changes)"; "i")),
          actionable_count:
            (if (.body | test("Actionable comments posted:"))
             then (.body | capture("Actionable comments posted:\\s*(?<n>[0-9]+)") | .n)
             else null end)
        }
      end;

  def is_ack_body:
    (test("<!--\\s*<review_comment_addressed>")) or (test("^### Summary"));

  def inline_by_bot:
    [$sub_c[] | select(.user.login | test("(coderabbitai|gemini-code-assist|chatgpt-codex-connector|github-code-quality|github-advanced-security)\\[bot\\]$"))]
    | group_by(.user.login)
    | map({
        key:   .[0].user.login,
        value: map({
          id,
          path,
          commit_id,
          created_at,
          severity_alt: ([.body | capture("!\\[(?<s>[^\\]]+)\\]")] | .[0].s // null),
          is_ack:       (.body | is_ack_body),
          body_head:    (.body | .[0:400])
        })
      })
    | from_entries;

  def quota_alerts:
    # Match ONLY explicit quota/rate-limit ERROR phrases, restricted to body head.
    # Bare keywords like "quota" / "rate limit" alone produce false positives when a
    # bot reply happens to discuss quota as a topic (e.g. a PR description that mentions quota).
    # Real alerts always pair a keyword with a verb like "exceeded" / "reached" / "exhausted",
    # or use a fixed phrase like the Codex error "You have reached your ... limit".
    [$sub_a[]
     | select(.user.login | test("(chatgpt-codex-connector|gemini-code-assist|coderabbitai)\\[bot\\]$"))
     | select(
         (.body[0:500] | test("you have reached your[^\\n]*?limit"; "i"))
         or (.body[0:500] | test("(usage|rate|api|daily|monthly)\\s+limit[^\\n]*?(exceeded|reached|hit|reset)"; "i"))
         or (.body[0:500] | test("quota[^\\n]*?(exceeded|exhausted|reached|reset|limit hit)"; "i"))
         or (.body[0:500] | test("(http\\s*)?429\\b|too many requests"; "i"))
       )
     | {user: .user.login, created_at, body_head: (.body | .[0:300])}];

  # ---- main projection ----
  {
    pr:            $main.number,
    pr_created_at: $main.createdAt,
    head:          $main.headRefOid,
    last_push_at:  ($main.commits | last.committedDate),
    commits:       [$main.commits[] | {oid, committedDate}],
    round_estimate:
      ([$main.commits[] | select(.committedDate > $main.createdAt) | .committedDate]
       | sort | map(fromdateiso8601)
       | reduce .[] as $t ({prev: 0, n: 0};
           if ($t - .prev) > 300 then {prev: $t, n: (.n + 1)} else {prev: $t, n: .n} end)
       | .n),

    coderabbit: {
      walkthrough:    cr_walkthrough_rest,
      other_comments: ([$main.comments[] | select(.author.login == "coderabbitai")] | sort_by(.createdAt) | .[1:]),
      reviews:        [$main.reviews[] | select(.author.login == "coderabbitai")]
    },

    gemini: {
      reviews:  [$main.reviews[]  | select(.author.login == "gemini-code-assist") | {id, submittedAt, state, body}],
      comments: [$main.comments[] | select(.author.login == "gemini-code-assist")]
    },

    codex: {
      reviews:   [$main.reviews[]  | select(.author.login == "chatgpt-codex-connector") | {id, submittedAt, state, body}],
      comments:  [$main.comments[] | select(.author.login == "chatgpt-codex-connector")],
      reactions: [$sub_b[] | select(.user.login == "chatgpt-codex-connector[bot]") | {content, created_at}]
    },

    inline_comments_by_user: inline_by_bot,

    codeql_checks:
      [($sub_d_w | add)[]
       | select((.app == "github-advanced-security" or .app == "github-code-quality")
                or (.name | test("^Analyze \\(|^codeql-required$|^CodeQL$")))],

    checks_failing:
      [($sub_d_w | add)[]
       | select(.conclusion | IN("failure", "timed_out", "cancelled", "action_required", "startup_failure"))
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
       | select(
           (.author.login != "coderabbitai"
            and .author.login != "gemini-code-assist"
            and .author.login != "chatgpt-codex-connector")
           and (.body | test("^[ \\t]*(/gemini review|@codex review|@coderabbitai resume)(\\s|$)"; "i"))
         )
       | {author: .author.login, createdAt, body: (.body | gsub("^\\s+|\\s+$"; ""))}]
  }
  '
