#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/repo-context.sh"
enter_repo_root "POLL_ERROR" "$@"
shift "$REPO_CONTEXT_SHIFT"

if [[ $# -lt 1 ]]; then
  echo "POLL_ERROR: missing PR_NUMBER. Usage: bash poll.sh [--repo-root <path>] <PR_NUMBER>" >&2
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

enter_snapshot_dir "POLL_ERROR" || exit $?

# Stage gh output into temp files. Large PRs (dozens of comments) make --argjson
# overflow ARG_MAX; --slurpfile reads from disk and is unbounded. Each gh call paginates,
# so PRs with hundreds of comments work too. WORKDIR is created up-front so every gh
# invocation can route its stderr here — the skill's troubleshooting section promises
# stderr on failure, so silently dropping it via `2>/dev/null` defeats that contract.
# It lives inside SNAP_DIR so staged PR data shares the 0700 protection and the final
# snapshot rename never crosses a filesystem boundary (mv stays atomic).
WORKDIR=$(mktemp -d "$SNAP_DIR/tmp.XXXXXX")
trap 'rm -rf "$WORKDIR"' EXIT

OWNER_REPO=$(gh api 'repos/{owner}/{repo}' --jq .full_name 2>"$WORKDIR/gh_repo_view.err") || {
  echo "POLL_ERROR: REST repository lookup failed (auth? wrong cwd?)" >&2
  cat "$WORKDIR/gh_repo_view.err" >&2
  exit 4
}

SNAPSHOT_FILE=$(snapshot_file_for "$OWNER_REPO" "$PR")

gh api "repos/${OWNER_REPO}/pulls/${PR}" > "$WORKDIR/pr.json" 2>"$WORKDIR/gh_pr.err" || {
  echo "POLL_ERROR: REST PR $PR fetch failed" >&2
  cat "$WORKDIR/gh_pr.err" >&2
  exit 5
}
rest_pages "repos/${OWNER_REPO}/pulls/${PR}/commits" > "$WORKDIR/commits.json" 2>"$WORKDIR/gh_commits.err" || {
  echo "POLL_ERROR: REST commits fetch failed" >&2
  cat "$WORKDIR/gh_commits.err" >&2
  exit 5
}

# user.login here is WITH [bot] suffix.
rest_pages "repos/${OWNER_REPO}/issues/${PR}/comments" > "$WORKDIR/sub_a.json" 2>"$WORKDIR/gh_issue_comments.err" || {
  echo "POLL_ERROR: REST issue comments fetch failed" >&2
  cat "$WORKDIR/gh_issue_comments.err" >&2
  exit 5
}

rest_pages "repos/${OWNER_REPO}/issues/${PR}/reactions" > "$WORKDIR/sub_b.json" 2>"$WORKDIR/gh_reactions.err" || {
  echo "POLL_ERROR: REST reactions fetch failed" >&2
  cat "$WORKDIR/gh_reactions.err" >&2
  exit 5
}

# Sub-query B2 — exact reactors on human-authored @codex review comments. The
# comment reaction count cannot identify who added eyes, so
# using it would let an unrelated human reaction suppress the cold-start fallback.
printf '[]\n' > "$WORKDIR/sub_b2.json"
while IFS= read -r comment_id; do
  rest_pages "repos/${OWNER_REPO}/issues/comments/${comment_id}/reactions" > "$WORKDIR/comment_reactions.json" 2>"$WORKDIR/gh_comment_reactions.err" || {
    echo "POLL_ERROR: REST comment reactions fetch failed for comment ${comment_id}" >&2
    cat "$WORKDIR/gh_comment_reactions.err" >&2
    exit 5
  }
  jq --argjson comment_id "$comment_id" \
    '[.[] | select(.user.login == "chatgpt-codex-connector[bot]") | {comment_id: $comment_id, content}]' \
    "$WORKDIR/comment_reactions.json" > "$WORKDIR/comment_codex_reactions.json"
  jq -s 'add' "$WORKDIR/sub_b2.json" "$WORKDIR/comment_codex_reactions.json" > "$WORKDIR/sub_b2.next.json"
  mv "$WORKDIR/sub_b2.next.json" "$WORKDIR/sub_b2.json"
done < <(
  jq -r '.[]
    | select(.user.login != "chatgpt-codex-connector[bot]")
    | select((.body // "") | test("^[ \\t]*@codex review(\\s|$)"; "i"))
    | select((.reactions == null) or ((.reactions.eyes // 0) > 0))
    | .id' "$WORKDIR/sub_a.json"
)

rest_pages "repos/${OWNER_REPO}/pulls/${PR}/comments" > "$WORKDIR/sub_c.json" 2>"$WORKDIR/gh_pr_comments.err" || {
  echo "POLL_ERROR: REST PR review comments fetch failed" >&2
  cat "$WORKDIR/gh_pr_comments.err" >&2
  exit 5
}

rest_pages "repos/${OWNER_REPO}/pulls/${PR}/reviews" > "$WORKDIR/sub_c2.json" 2>"$WORKDIR/gh_pr_reviews.err" || {
  echo "POLL_ERROR: REST PR reviews fetch failed" >&2
  cat "$WORKDIR/gh_pr_reviews.err" >&2
  exit 5
}

HEAD_SHA=$(jq -r '.head.sha' "$WORKDIR/pr.json")
rest_pages "repos/${OWNER_REPO}/commits/${HEAD_SHA}/check-runs" '[.check_runs[] | {name, app: .app.slug, status, conclusion, started_at}]' > "$WORKDIR/sub_d.json" 2>"$WORKDIR/gh_check_runs.err" || {
  echo "POLL_ERROR: REST check-runs fetch failed" >&2
  cat "$WORKDIR/gh_check_runs.err" >&2
  exit 5
}

SECURITY_ALERTS_AVAILABLE=true
SECURITY_ALERTS_HINT=""
if ! rest_pages "repos/${OWNER_REPO}/code-scanning/alerts?ref=refs/pull/${PR}/merge&state=open" > "$WORKDIR/sub_e_pr.json" 2>"$WORKDIR/gh_alerts_pr.err"; then
  SECURITY_ALERTS_AVAILABLE=false
  SECURITY_ALERTS_HINT="pr-ref: $(head -n 2 "$WORKDIR/gh_alerts_pr.err" | tr '\n' ' ' | cut -c1-300)"
  echo '[]' > "$WORKDIR/sub_e_pr.json"
fi
if ! rest_pages "repos/${OWNER_REPO}/code-scanning/alerts?state=open" > "$WORKDIR/sub_e_base.json" 2>"$WORKDIR/gh_alerts_base.err"; then
  SECURITY_ALERTS_AVAILABLE=false
  SECURITY_ALERTS_HINT="${SECURITY_ALERTS_HINT} base: $(head -n 2 "$WORKDIR/gh_alerts_base.err" | tr '\n' ' ' | cut -c1-300)"
  echo '[]' > "$WORKDIR/sub_e_base.json"
fi

jq -n --slurpfile pr "$WORKDIR/pr.json" --slurpfile reviews "$WORKDIR/sub_c2.json" \
  --slurpfile comments "$WORKDIR/sub_a.json" --slurpfile commits "$WORKDIR/commits.json" '
  {number: $pr[0].number, createdAt: $pr[0].created_at, headRefOid: $pr[0].head.sha,
   reviews: [$reviews[][] | {id: .node_id, author: {login: ((.user.login // "") | sub("\\[bot\\]$"; ""))},
     submittedAt: .submitted_at, state, body}],
   comments: [$comments[][] | {id: .node_id, author: {login: ((.user.login // "") | sub("\\[bot\\]$"; ""))},
     createdAt: .created_at, body}],
   commits: [$commits[][] | {oid: .sha, committedDate: .commit.committer.date}]}
  ' > "$WORKDIR/main.json"

GENERATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

SEEN_FILE="${SNAPSHOT_FILE%.json}.seen.json"
if [[ -f "$SEEN_FILE" ]] && jq -e -s 'length == 1 and (.[0] | type == "array")' "$SEEN_FILE" >/dev/null 2>&1; then
  cp "$SEEN_FILE" "$WORKDIR/seen.json"
else
  printf 'null' > "$WORKDIR/seen.json"
fi

ROUNDS_FILE="${SNAPSHOT_FILE%.json}.rounds.json"
ROUNDS=0
LAST_MARKED_AT=""
if [[ -f "$ROUNDS_FILE" ]]; then
  ROUNDS=$(jq -e '.rounds | length' "$ROUNDS_FILE") || {
    echo "POLL_ERROR: round ledger unreadable: $ROUNDS_FILE" >&2
    exit 6
  }
  LAST_MARKED_AT=$(jq -r '.rounds | last | .marked_at // ""' "$ROUNDS_FILE")
fi

jq -n -L "$SCRIPT_DIR" \
  --slurpfile main_w "$WORKDIR/main.json" \
  --slurpfile sub_a_w "$WORKDIR/sub_a.json" \
  --slurpfile sub_b_w "$WORKDIR/sub_b.json" \
  --slurpfile sub_b2_w "$WORKDIR/sub_b2.json" \
  --slurpfile sub_c_w "$WORKDIR/sub_c.json" \
  --slurpfile sub_c2_w "$WORKDIR/sub_c2.json" \
  --slurpfile sub_d_w "$WORKDIR/sub_d.json" \
  --slurpfile sub_e_pr_w "$WORKDIR/sub_e_pr.json" \
  --slurpfile sub_e_base_w "$WORKDIR/sub_e_base.json" \
  --slurpfile seen_w "$WORKDIR/seen.json" \
  --argjson security_available "$SECURITY_ALERTS_AVAILABLE" \
  --arg security_hint "$SECURITY_ALERTS_HINT" \
  --arg repo "$OWNER_REPO" \
  --arg generated_at "$GENERATED_AT" \
  --arg last_marked "$LAST_MARKED_AT" \
  '
  include "signals";
  ($main_w[0]) as $main
  | (($sub_a_w | add) // []) as $sub_a
  | (($sub_b_w | add) // []) as $sub_b
  | ($sub_b2_w[0]) as $sub_b2
  | (($sub_c_w | add) // []) as $sub_c
  | (($sub_c2_w | add) // []) as $sub_c2
  | ($sub_c2 | map(select(.node_id != null))
     | map({key: .node_id, value: .commit_id}) | from_entries) as $review_commit_by_id
  | (($main.commits | map(select(.oid == $main.headRefOid)) | first)
     // ($main.commits | last) | .committedDate) as $last_push
  | (if $last_marked > $last_push then $last_marked else $last_push end) as $fresh_since
  | ($seen_w[0]) as $seen_ids
  # Check-suite reruns leave same-name duplicates; keep only the latest run per name so a
  # superseded failure cannot pin codeql_checks / checks_failing red forever.
  | ((($sub_d_w | add) // []) | group_by(.name) | map(max_by(.started_at))) as $check_runs |
  # ---- shared helpers ----
  # Every body-consuming helper opens with (. // "") — jq string functions (gsub/test/
  # contains/capture) raise fatal errors on null input, and a null body must not kill a poll.
  # is_new with the straggler catch (PITFALL 6): newer than the freshness watermark (last
  # push or last marked batch, PITFALL 8), or an id no earlier poll has observed.
  # $seen_ids == null (first poll) keeps the timestamp rule alone.
  def fresh($ts; $id):
    ($ts > $fresh_since)
    or ($seen_ids != null and $id != null
        and (any($seen_ids[]; . == ($id | tostring)) | not));

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

  def codex_commit_is_current_head:
    . as $sha
    | ($sha != null
       and (($main.headRefOid | startswith($sha))
            or ($sha | startswith($main.headRefOid))));

  def codex_body_has_finding:
    (. // "") | test("!\\[P[0-3] Badge\\]"; "i");

  def has_outside_diff_body:
    (. // "") | test("Outside diff range comments \\([1-9][0-9]*\\)"; "i");

  def cr_walkthrough_head:
    # The reviewed-head anchor CR writes into the walkthrough body (PITFALL 9), in preference
    # order: the change_assessment_commit marker, the end sha of the "📥 Commits" range line,
    # the final_review_risk_coverage coveredCommitId. null when the body carries none.
    (. // "") as $b
    | (([$b | capture("<!--\\s*change_assessment_commit:\\s*\"(?<sha>[0-9a-fA-F]{7,40})\"")] | .[0].sha // null)
       // ([$b | capture("between [0-9a-fA-F]{7,40} and (?<sha>[0-9a-fA-F]{7,40})")] | .[0].sha // null)
       // ([$b | capture("\"coveredCommitId\":\\s*\"(?<sha>[0-9a-fA-F]{7,40})\"")] | .[0].sha // null));

  def cr_walkthrough_rest:
    [$sub_a[] | select(.user.login == "coderabbitai[bot]")]
    | sort_by(.created_at)
    | first
    | if . == null then null else
        (.body // "") as $wb
        | ($wb | cr_rate_limited_body) as $rate_limited
        | ($wb | cr_review_skipped_body) as $skipped
        # Freshness is timestamp-based, but a slow review of an older HEAD rewrites the walkthrough
        # after the next push, faking it. The contradiction check is a commit anchor: walkthrough_head
        # parsed from the body when present (the anchor that follows incremental reviews, PITFALL 9),
        # else the REST commit_id of the latest CR review (null = no reviews, no evidence either way).
        # A present walkthrough_head is authoritative: a review anchored on the head does not
        # override a walkthrough anchored elsewhere.
        # No apostrophes in these comments: the whole jq program is one single-quoted bash string.
        | ([$main.reviews[] | select(.author.login == "coderabbitai")] | sort_by(.submittedAt)
           | last | if . == null then null else ($review_commit_by_id[.id] // null) end)
          as $latest_review_commit
        # A no-reviewable-changes skip keeps the change_assessment_commit marker of the last real
        # review; only its newest range line names the skipped head, so it is the anchor there.
        | (if ($wb | cr_no_reviewable_changes_body)
           then ([$wb | capture("between [0-9a-fA-F]{7,40} and (?<sha>[0-9a-fA-F]{7,40})")] | .[0].sha // null)
           else ($wb | cr_walkthrough_head) end) as $walkthrough_head
        | {
          id,
          created_at,
          updated_at,
          reviewed_current_head:
            ((.updated_at > $last_push) and ($rate_limited | not) and ($skipped | not)
             and (if $walkthrough_head != null
                  then ($walkthrough_head | codex_commit_is_current_head)
                  elif $latest_review_commit == null then true
                  else ($latest_review_commit | codex_commit_is_current_head) end)),
          walkthrough_head: $walkthrough_head,
          latest_review_commit: $latest_review_commit,
          is_rate_limited: $rate_limited,
          is_review_skipped: $skipped,
          is_ok:          ($wb | test("No actionable comments were generated in the recent review")),
          is_paused:      ($wb | cr_paused_body),
          is_in_progress: ($wb | test("(review in progress by coderabbit|currently processing new changes)"; "i")),
          actionable_count:
            (if ($wb | test("Actionable comments posted:"))
             then ($wb | capture("Actionable comments posted:\\s*(?<n>[0-9]+)") | .n)
             else null end),
          body
        }
      end;

  def codex_review_row:
    (.body // "") as $rb
    | .id as $review_id
    | (($review_commit_by_id[$review_id] // null)
       // ($rb | codex_reviewed_commit_from_body)) as $reviewed_commit
    | (if $reviewed_commit == null
       then (.submittedAt > $last_push)
       else ($reviewed_commit | codex_commit_is_current_head)
       end) as $reviewed_current_head
    | {id, submittedAt, state, reviewed_commit: $reviewed_commit,
       reviewed_current_head: $reviewed_current_head,
       has_body_finding: ($rb | codex_body_has_finding),
       is_new: fresh(.submittedAt; .id), preview: ($rb | mk_preview), body};

  def codex_comment_row:
    (.body // "") as $cb
    | ($cb | codex_reviewed_commit_from_body) as $reviewed_commit
    | ($cb | codex_summary_commit) as $summary_commit
    | {id, createdAt, is_new: fresh(.createdAt; .id),
       reviewed_commit: $reviewed_commit,
       reviewed_current_head:
         (if $reviewed_commit != null then ($reviewed_commit | codex_commit_is_current_head)
          # The summary names its commit itself (summary_on_current_head); its creation time says nothing.
          elif $summary_commit != null then false
          else (.createdAt > $last_push)
          end),
       has_pass_marker: ($cb | codex_comment_has_pass_marker),
       summary_on_current_head: (($summary_commit | codex_commit_is_current_head) and ($cb | codex_summary_failed | not)),
       summary_failed_current_head: (($summary_commit | codex_commit_is_current_head) and ($cb | codex_summary_failed)),
       preview: ($cb | mk_preview), body};

  def inline_by_bot:
    [$sub_c[] | select(.user.login | test("(coderabbitai|chatgpt-codex-connector|github-advanced-security)\\[bot\\]$"))]
    | group_by(.user.login)
    | map({
        key:   .[0].user.login,
        value: map({
          id,
          path,
          commit_id,
          created_at,
          is_new:       fresh(.created_at; .id),
          severity_alt: ([(.body // "") | capture("!\\[(?<s>[^\\]]+)\\]")] | .[0].s // null),
          cr_markers:   (.body | cr_markers_of),
          is_ack:       (.body | is_ack_body),
          preview:      (.body | mk_preview),
          body
        })
      })
    | from_entries;

  def quota_alerts:
    # PR-level reviewer comments that carry a quota or rate-limit error (quota_alert_body).
    [$sub_a[]
     | select(.user.login | test("(chatgpt-codex-connector|coderabbitai)\\[bot\\]$"))
     | (.body // "") as $qb
     | select($qb | quota_alert_body)
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

    coderabbit: {
      walkthrough: cr_walkthrough_rest,
      other_comments:
        ([$main.comments[] | select(.author.login == "coderabbitai")]
         | sort_by(.createdAt) | .[1:]
         | map({id, createdAt, is_new: fresh(.createdAt; .id), preview: (.body | mk_preview), body})),
      reviews:
        [$main.reviews[] | select(.author.login == "coderabbitai")
         | {id, submittedAt, state, has_outside_diff: (.body | has_outside_diff_body),
            is_new: fresh(.submittedAt; .id), preview: (.body | mk_preview), body}]
    },

    codex: {
      reviews:
        [$main.reviews[] | select(.author.login == "chatgpt-codex-connector")
         | codex_review_row],
      comments:
        [$main.comments[] | select(.author.login == "chatgpt-codex-connector")
         | codex_comment_row],
      reactions:
        [$sub_b[] | select(.user.login == "chatgpt-codex-connector[bot]")
         | {content, created_at, is_new: (.created_at > $last_push)}]
    },

    inline_comments_by_user: inline_by_bot,

    codeql_checks:
      [$check_runs[]
       | select(.app == "github-advanced-security"
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
      [$sub_a[]
       | (.body // "") as $tb
       | select(
           (.user.login != "coderabbitai[bot]"
            and .user.login != "chatgpt-codex-connector[bot]")
           and ($tb | test("^[ \\t]*(@codex review|@coderabbitai (resume|full review|review))(\\s|$)"; "i"))
         )
       | .id as $comment_id
       | {author: .user.login, createdAt: .created_at,
          command: ($tb | capture("^[ \\t]*(?<c>@codex review|@coderabbitai (resume|full review|review))"; "i") | .c | ascii_downcase),
          has_codex_eyes: (any($sub_b2[]; .comment_id == $comment_id and .content == "eyes")),
          body: ($tb | gsub("^\\s+|\\s+$"; ""))}]
  }
  ' > "$WORKDIR/snapshot.json"

mv "$WORKDIR/snapshot.json" "$SNAPSHOT_FILE"

jq --arg snapshot_file "$SNAPSHOT_FILE" --argjson rounds "$ROUNDS" '
  def prune_hist: if .total == 0 then {total} else . end;
  # Rows carrying reviewed_current_head stay listed after retirement (PITFALL 8) — head
  # status must survive a pushback-only mark — so history excludes them too.
  def shown: (.is_new or (.reviewed_current_head // false));
  def review_history:
    [.[] | select(shown | not)]
    | {total: length, last_submitted_at: (map(.submittedAt) | max // null)}
    | prune_hist;
  # The decision point `triage_hint` targets: a non-ack inline, any new CodeRabbit review (its
  # nitpick and outside-diff findings live in the body), or a Codex body finding.
  def has_new_findings:
    any((.inline_comments_by_user // {})[]?[]?; .is_new and (.is_ack | not))
    or any(.coderabbit.reviews[]?; .is_new)
    or any(.codex.reviews[]?; .is_new and .has_body_finding);
  def triage_hint($rounds):
    if has_new_findings then
      "先定性再动代码：缺陷才修，YAGNI / nitpick / 存量 / 误报以回复结案；修根因而非症状（SKILL「工程判断」）。"
      + (if $rounds >= 3 then "已处置 \($rounds) 批：同一处又出意见时，回到根因重整实现（SKILL「收敛」）。" else "" end)
    else null end;
  . as $s
  | {
      pr:             $s.pr,
      pr_created_at:  $s.pr_created_at,
      head:           $s.head,
      last_push_at:   $s.last_push_at,
      rounds:         $rounds,
      triage_hint:    ($s | triage_hint($rounds)),
      snapshot_file:  $snapshot_file,

      coderabbit: {
        walkthrough: ($s.coderabbit.walkthrough | if . == null then null else del(.body) end),
        reviews:
          [$s.coderabbit.reviews[] | select(.is_new)
           | {id, submittedAt, state, has_outside_diff, is_new, preview}],
        reviews_history: ($s.coderabbit.reviews | review_history),
        comments_new:
          [$s.coderabbit.other_comments[] | select(.is_new) | {id, createdAt, preview}],
        comments_history:
          ([$s.coderabbit.other_comments[] | select(.is_new | not)]
           | {total: length, last_created_at: (map(.createdAt) | max // null)}
           | prune_hist)
      },

      codex: {
        started_current_head:
          ((any($s.codex.comments[]; .summary_failed_current_head) | not)
           and ((any($s.codex.reactions[]; .is_new))
                or (any($s.codex.reviews[]; .reviewed_current_head))
                or (any($s.codex.comments[]; .reviewed_current_head or .summary_on_current_head))
                or (any($s.own_trigger_comments[];
                        .command == "@codex review" and .has_codex_eyes
                        and .createdAt > $s.last_push_at)))),
        failed_current_head: any($s.codex.comments[]; .summary_failed_current_head),
        reviews:
          [$s.codex.reviews[] | select(shown)
           | {id, submittedAt, state, reviewed_commit, reviewed_current_head,
              has_body_finding, is_new, preview}],
        reviews_history: ($s.codex.reviews | review_history),
        comments_new:
          [$s.codex.comments[] | select(shown)
           | {id, createdAt, reviewed_commit, reviewed_current_head, has_pass_marker, is_new, preview}],
        comments_history:
          ([$s.codex.comments[] | select(shown | not)]
           | {total: length, last_created_at: (map(.createdAt) | max // null)}
           | prune_hist),
        reactions: [$s.codex.reactions[] | {content, created_at, is_new}]
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
      own_trigger_comments: [$s.own_trigger_comments[] | {author, createdAt, command, has_codex_eyes}]
    }
  | if .triage_hint == null then del(.triage_hint) else . end
  ' "$SNAPSHOT_FILE" > "$WORKDIR/index.json"

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
     hint: ("index identical to every poll since unchanged_since; run query.sh " + $pr + " index for the full index")}
  ' "$INDEX_FILE"
else
  jq -n --arg printed_at "$GENERATED_AT" --slurpfile idx "$WORKDIR/index.json" \
    '{printed_at: $printed_at, index: $idx[0]}' > "$WORKDIR/index_store.json"
  mv "$WORKDIR/index_store.json" "$INDEX_FILE"
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

# Last step on purpose: a straggler is new solely because its id is unseen, so the id must
# not be acknowledged until the index that surfaces it has been stored and printed —
# a ledger advanced before a failed/interrupted emission would fold the item into history
# unseen. Union with the staged previous ledger: a transiently missing id (partial API
# response) must not resurface as new on a later poll. Reactions carry no stable identity
# and stay out.
jq -c --slurpfile prev "$WORKDIR/seen.json" '
  [.coderabbit.other_comments[]?.id, .coderabbit.reviews[]?.id,
   .codex.reviews[]?.id, .codex.comments[]?.id,
   ((.inline_comments_by_user // {})[]?[]?.id)]
  | map(select(. != null) | tostring)
  | . + ($prev[0] // [])
  | unique
' "$SNAPSHOT_FILE" > "$WORKDIR/seen_next.json"
mv "$WORKDIR/seen_next.json" "$SEEN_FILE"
