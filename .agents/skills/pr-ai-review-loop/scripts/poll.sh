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
#   "inline_comments_by_user": {                        # PR-level inline review comments grouped by bot
#     "<bot[bot]>": [{id, path, commit_id, created_at, severity_alt, is_ack, body_head}]  # id = REST PR review comment id
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
# 5. Trigger-command dedup MUST normalize whitespace + case.
#    "@CodeRabbitAI Resume" / " @coderabbitai resume " / "@coderabbitai resume\n" all count as the same command.
#    Use `test("...";"i")` with leading/trailing \s* — keep this regex consistent everywhere.
#
# 6. Quota / rate-limit errors from Codex are PR-level ISSUE comments, NOT reviews/inline/reactions.
#    Codex emits e.g. "You have reached your Codex usage limits..." as a plain PR comment — easy to miss.
#    Captured into quota_alerts so the skill catches it on the first poll.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "POLL_ERROR: missing PR_NUMBER. Usage: bash poll.sh <PR_NUMBER>" >&2
  exit 2
fi

PR="$1"

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

# REST endpoints — gh api wraps each page in []; --paginate yields concatenated arrays
# (one big array, not a stream), which --slurpfile correctly reads as a single value.
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

# Combine everything in jq. Bot login normalization happens here so consumers see consistent keys.
# --slurpfile wraps each file in [...]; unwrap with [0] at the top.
jq -n \
  --slurpfile main_w "$TMPDIR/main.json" \
  --slurpfile sub_a_w "$TMPDIR/sub_a.json" \
  --slurpfile sub_b_w "$TMPDIR/sub_b.json" \
  --slurpfile sub_c_w "$TMPDIR/sub_c.json" \
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
    [$sub_c[] | select(.user.login | test("(coderabbitai|gemini-code-assist|chatgpt-codex-connector)\\[bot\\]$"))]
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
          body_head:    (.body | .[0:200])
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

    quota_alerts: quota_alerts,

    own_trigger_comments:
      [$main.comments[]
       | select(
           (.author.login != "coderabbitai"
            and .author.login != "gemini-code-assist"
            and .author.login != "chatgpt-codex-connector")
           and (.body | test("^\\s*(/gemini review|@codex review|@coderabbitai resume)\\s*$"; "i"))
         )
       | {author: .author.login, createdAt, body: (.body | gsub("^\\s+|\\s+$"; ""))}]
  }
  '
