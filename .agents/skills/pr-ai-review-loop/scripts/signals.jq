def is_failing_conclusion:
  IN("failure", "timed_out", "cancelled", "action_required", "startup_failure");

def cr_rate_limited_body:
  (. // "") | test("<!--\\s*This is an auto-generated comment:\\s*rate limited by coderabbit\\.ai\\s*-->");

def cr_skip_marker_body:
  (. // "") | test("<!--\\s*This is an auto-generated comment:\\s*skip review by coderabbit\\.ai\\s*-->");

def cr_no_reviewable_changes_body:
  cr_skip_marker_body and ((. // "") | test("did not have any reviewable changes"; "i"));

def cr_review_skipped_body:
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
  (. // "") | gsub("<!--[\\s\\S]*?-->"; "")
  | test("^\\s*Codex Review:\\s*Didn(\\x27|\\x{2019})t find any major issues\\."; "i");

def codex_summary_body:
  (. // "") | test("<!--\\s*codex-pull-request-review-summary\\s*-->");

def codex_reviewed_commit_from_body:
  (. // "")
  | ([capture("Reviewed commit:[* `]*(?<sha>[0-9a-fA-F]{7,40})"; "i")] | .[0].sha // null);

def codex_summary_commit:
  (. // "")
  | ([capture("\"code_review\":\\{\"commit_sha\":\"(?<sha>[0-9a-fA-F]{7,40})\"")] | .[0].sha // null);

def codex_summary_failed:
  (. // "") | test("\"code_review\":\\{[^}]*\"status\":\"failed\"");

def codex_summary_completed:
  (. // "") | test("\"code_review\":\\{[^}]*\"status\":\"completed\"");
