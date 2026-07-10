#!/usr/bin/env bash
# query.sh — layer-2 detail lookups against the snapshot staged by poll.sh.
#
# USAGE
#   bash query.sh <PR_NUMBER> details <id>...      # full bodies by comment/review id, any collection
#   bash query.sh <PR_NUMBER> gemini-latest-body   # latest Gemini review summary body (raw markdown)
#   bash query.sh <PR_NUMBER> quality-all          # ALL github-code-quality[bot] inline comments, full body
#   bash query.sh <PR_NUMBER> history              # every comment/review as {source,author,id,created_at,head(400)}
#   bash query.sh <PR_NUMBER> unacked <bot[bot]>   # OLD (is_new==false) inline comments of <bot> with is_ack==false
#   bash query.sh <PR_NUMBER> index                # re-print the last fully-printed poll index (recovers the
#                                                  # decision facts a no_change line stands in for, e.g. after
#                                                  # context compaction)
#
# Reads the snapshot poll.sh staged for the same round; field semantics live in poll.sh's
# header. Failures are LOUD (never silent-empty): snapshot missing, unknown subcommand,
# unknown bot name, id not found => non-zero exit + `QUERY_ERROR:` on stderr. An empty
# JSON result from a fixed subcommand therefore genuinely means "no such data".
#
# Every call echoes snapshot provenance (head SHA + generated_at) to stderr — cross-check
# it against the round's poll output; when they disagree, re-run poll.sh rather than
# trusting the file.

set -euo pipefail

usage() {
  echo "QUERY_ERROR: usage: bash query.sh <PR_NUMBER> {details <id>...|gemini-latest-body|quality-all|history|unacked <bot[bot]>|index}" >&2
  exit 2
}

[[ $# -ge 2 ]] || usage

PR="$1"
shift
CMD="$1"
shift

if ! [[ "$PR" =~ ^[0-9]+$ ]]; then
  echo "QUERY_ERROR: PR_NUMBER must be a number, got: $PR" >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "QUERY_ERROR: gh CLI not found on PATH" >&2
  exit 3
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "QUERY_ERROR: jq not found on PATH" >&2
  exit 3
fi

OWNER_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner) || {
  echo "QUERY_ERROR: gh repo view failed (auth? wrong cwd?)" >&2
  exit 4
}

# Keep in sync with poll.sh's SNAP_DIR/SNAPSHOT_FILE derivation (user-private subdir).
SNAP_BASE="${TMPDIR:-/tmp}"
SNAP_DIR="${SNAP_BASE%/}/pr-ai-review-loop-$(id -u)"
SNAPSHOT_FILE="$SNAP_DIR/poll-${OWNER_REPO//\//-}-${PR}.json"

if [[ ! -f "$SNAPSHOT_FILE" ]]; then
  echo "QUERY_ERROR: snapshot not found: $SNAPSHOT_FILE — run poll.sh $PR first" >&2
  exit 4
fi

# Provenance to stderr on every call.
jq -r '"QUERY_SNAPSHOT: repo=\(.repo) pr=\(.pr) head=\(.head) generated_at=\(.generated_at)"' "$SNAPSHOT_FILE" >&2

# Bots that appear in inline_comments_by_user. Keep in sync with poll.sh's bot-login regex.
KNOWN_BOTS=("coderabbitai[bot]" "gemini-code-assist[bot]" "github-code-quality[bot]" "github-advanced-security[bot]")

case "$CMD" in

  details)
    [[ $# -ge 1 ]] || { echo "QUERY_ERROR: details needs at least one id" >&2; exit 2; }
    IDS_JSON=$(jq -n '$ARGS.positional' --args "$@")
    RESULT=$(jq --argjson ids "$IDS_JSON" '
      [ (.coderabbit.walkthrough // empty
           | {source: "coderabbit_walkthrough", id, created_at, body}),
        (.coderabbit.other_comments[]?
           | {source: "coderabbit_comment", id, created_at: .createdAt, body}),
        (.coderabbit.reviews[]?
           | {source: "coderabbit_review", id, created_at: .submittedAt, state, body}),
        (.gemini.reviews[]?
           | {source: "gemini_review", id, created_at: .submittedAt, state, has_pass_marker, body}),
        (.gemini.comments[]?
           | {source: "gemini_comment", id, created_at: .createdAt, body}),
        ((.inline_comments_by_user // {}) | to_entries[] | .key as $bot | .value[]
           | {source: ("inline " + $bot), id, path, created_at,
              severity_alt, cr_markers, is_ack, body})
      ] as $all
      | ($ids | map(tostring)) as $want
      | [$all[] | select((.id | tostring) as $i | ($want | index($i)) != null)] as $found
      | {found: $found, missing: ($want - ($found | map(.id | tostring)))}
    ' "$SNAPSHOT_FILE")
    MISSING=$(jq -r '.missing | join(",")' <<<"$RESULT")
    if [[ -n "$MISSING" ]]; then
      echo "QUERY_ERROR: ids not found in snapshot: $MISSING — stale snapshot? re-run poll.sh $PR" >&2
      exit 6
    fi
    jq '.found' <<<"$RESULT"
    ;;

  gemini-latest-body)
    if ! jq -e -r '(.gemini.reviews // []) | sort_by(.submittedAt) | last | .body' "$SNAPSHOT_FILE"; then
      echo "QUERY_ERROR: no gemini reviews in snapshot" >&2
      exit 6
    fi
    ;;

  quality-all)
    jq '.inline_comments_by_user["github-code-quality[bot]"] // []' "$SNAPSHOT_FILE"
    ;;

  history)
    # clean_head = poll.sh mk_preview with a 400-char cut (keep the stripping in sync):
    # raw heads of bot bodies open with HTML-comment/badge boilerplate that buries the
    # actual topic text.
    jq '
      def clean_head:
        (. // "")
        | gsub("<!--[\\s\\S]*?-->"; "")
        | gsub("!\\[[^\\]]*\\]\\([^\\)]*\\)"; "")
        | gsub("\\s+"; " ")
        | sub("^ +"; "")
        | .[0:400];
      [ (.coderabbit.walkthrough // empty
           | {source: "coderabbit_walkthrough", author: "coderabbitai[bot]", id,
              created_at, head: (.body | clean_head)}),
        (.coderabbit.other_comments[]?
           | {source: "coderabbit_comment", author: "coderabbitai[bot]", id,
              created_at: .createdAt, head: (.body | clean_head)}),
        (.coderabbit.reviews[]?
           | {source: "coderabbit_review", author: "coderabbitai[bot]", id,
              created_at: .submittedAt, head: (.body | clean_head)}),
        (.gemini.reviews[]?
           | {source: "gemini_review", author: "gemini-code-assist[bot]", id,
              created_at: .submittedAt, head: (.body | clean_head)}),
        (.gemini.comments[]?
           | {source: "gemini_comment", author: "gemini-code-assist[bot]", id,
              created_at: .createdAt, head: (.body | clean_head)}),
        ((.inline_comments_by_user // {}) | to_entries[] | .key as $bot | .value[]
           | {source: "inline", author: $bot, id, path,
              created_at, head: (.body | clean_head)})
      ] | sort_by(.created_at)
    ' "$SNAPSHOT_FILE"
    ;;

  index)
    INDEX_FILE="${SNAPSHOT_FILE%.json}.index.json"
    if [[ ! -f "$INDEX_FILE" ]]; then
      echo "QUERY_ERROR: index file not found: $INDEX_FILE — run poll.sh $PR first" >&2
      exit 4
    fi
    jq -r '"QUERY_INDEX: printed_at=\(.printed_at)"' "$INDEX_FILE" >&2
    jq '.index' "$INDEX_FILE"
    ;;

  unacked)
    [[ $# -eq 1 ]] || { echo "QUERY_ERROR: unacked needs exactly one bot name" >&2; exit 2; }
    BOT="$1"
    KNOWN=false
    for b in "${KNOWN_BOTS[@]}"; do
      [[ "$b" == "$BOT" ]] && KNOWN=true
    done
    if [[ "$KNOWN" != true ]]; then
      echo "QUERY_ERROR: unknown bot: $BOT (known: ${KNOWN_BOTS[*]})" >&2
      exit 2
    fi
    jq --arg bot "$BOT" '
      [ ((.inline_comments_by_user[$bot]) // [])[]
        | select((.is_new | not) and (.is_ack | not)) ]
    ' "$SNAPSHOT_FILE"
    ;;

  *)
    echo "QUERY_ERROR: unknown subcommand: $CMD" >&2
    usage
    ;;
esac
