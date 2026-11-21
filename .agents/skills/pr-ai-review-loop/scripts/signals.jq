# signals.jq — reviewer-body and check-run judgments shared by poll.sh and wait.sh.
#
# Load with `jq -L <this dir> 'include "signals"; ...'`. One definition per judgment keeps
# the index poll.sh prints and the wake-up decision wait.sh makes from drifting apart.
# Every body-consuming def opens with (. // ""): jq string functions raise fatal errors on
# null input, and a null body must not kill a poll or a probe.

def is_failing_conclusion:
  IN("failure", "timed_out", "cancelled", "action_required", "startup_failure");

def cr_rate_limited_body:
  # CodeRabbit wraps its rate-limit banner in a dedicated HTML marker. One match backs both
  # quota_alerts and walkthrough.is_rate_limited so the two judgments cannot drift apart.
  (. // "") | test("<!--\\s*This is an auto-generated comment:\\s*rate limited by coderabbit\\.ai\\s*-->");

def cr_skip_marker_body:
  (. // "") | test("<!--\\s*This is an auto-generated comment:\\s*skip review by coderabbit\\.ai\\s*-->");

def cr_no_reviewable_changes_body:
  # A skip because the new commits have no reviewable changes leaves nothing to review: the
  # head counts as covered once the skip notice names it.
  cr_skip_marker_body and ((. // "") | test("did not have any reviewable changes"; "i"));

def cr_review_skipped_body:
  # Any other skip notice (draft, bot author) means the head was never reviewed.
  cr_skip_marker_body and (cr_no_reviewable_changes_body | not);

def cr_paused_body:
  (. // "") | test("(review[s]?\\s+paused|paused\\s+by\\s+coderabbit|automatic reviews are paused|paused\\s+for\\s+this\\s+PR)"; "i");

def quota_alert_body:
  # Match ONLY explicit quota/rate-limit ERROR phrases, restricted to the body head. Bare
  # keywords like "quota" / "rate limit" produce false positives when a bot reply merely
  # discusses quota as a topic. Real alerts pair a keyword with a verb like "exceeded" /
  # "reached" / "exhausted", or use a fixed phrase like "You have reached your ... limit".
  # The CodeRabbit banner marker is CodeRabbit-authored and unambiguous, so that check is
  # not restricted to the head: a preceding change-stack link banner can push the phrase
  # itself past the 500-char window.
  (. // "") as $qb
  | (($qb | cr_rate_limited_body)
     or ($qb[0:500] | test("you(\\x27ve|\\x{2019}ve|\\s+have)\\s+reached your[^\\n]*?limit"; "i"))
     or ($qb[0:500] | test("(usage|rate|api|daily|monthly)\\s+limit[^\\n]*?(exceeded|reached|hit|reset)"; "i"))
     or ($qb[0:500] | test("quota[^\\n]*?(exceeded|exhausted|reached|reset|limit hit)"; "i"))
     or ($qb[0:500] | test("(http\\s*)?429\\b|too many requests"; "i")));

def codex_comment_has_pass_marker:
  # Codex prefixes its receipt with HTML comments (<!-- codex-review-receipt:v1 -->); strip
  # them so the phrase is matched at the start of the visible text.
  (. // "") | gsub("<!--[\\s\\S]*?-->"; "")
  | test("^\\s*Codex Review:\\s*Didn(\\x27|\\x{2019})t find any major issues\\."; "i");

def codex_summary_body:
  # Codex keeps one status comment per PR and rewrites it in place on every review.
  (. // "") | test("<!--\\s*codex-pull-request-review-summary\\s*-->");

def codex_reviewed_commit_from_body:
  # The commit a Codex review body or clean-pass receipt says it reviewed. null when it names none.
  (. // "")
  | ([capture("Reviewed commit:[* `]*(?<sha>[0-9a-fA-F]{7,40})"; "i")] | .[0].sha // null);

def codex_summary_commit:
  # The commit the summary comment says the code review is running on or has finished
  # (codex-review-state JSON, code_review.commit_sha). null when the body carries none.
  (. // "")
  | ([capture("\"code_review\":\\{\"commit_sha\":\"(?<sha>[0-9a-fA-F]{7,40})\"")] | .[0].sha // null);

def codex_summary_failed:
  # The summary reports the code review of its commit as failed ("We couldn't complete this
  # review. Comment `@codex review` to try again."). Nothing was reviewed and nothing is running.
  (. // "") | test("\"code_review\":\\{[^}]*\"status\":\"failed\"");

def is_trigger_command($command):
  # Trigger-command match: the comment STARTS with the command (case-insensitive, leading
  # spaces/tabs tolerated, trailing text allowed). See poll.sh PITFALL 4.
  (. // "") | test("^[ \\t]*" + $command + "(\\s|$)"; "i");
