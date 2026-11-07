# Faults and reduced coverage

When required review or analysis results are unavailable, pause and refer the decision to the delegator with the affected reviewer / HEAD, error, and recommended disposition. Missing review due to a fault differs from a bot continuing to disagree after reviewing code; handle the latter under “Completion criteria” in SKILL.md, not as a fault.

## Faults requiring handoff

- A required reviewer or CodeQL result has not arrived after 30 minutes; legitimately reused reviews are not missing.
- Bot errors, `POLL_ERROR` / `QUERY_ERROR` / `WAIT_ERROR`, or authentication or permission failures. Preserve original errors; do not treat empty data as no problems.
- CodeQL analysis fails or the alerts API is unavailable. After analysis failure, alert data may still belong to an older HEAD and cannot support final checks. See [reviewers.md](reviewers.md) for confirming an unconfigured integration.

After `WAIT_TIMEOUT`, poll again; hand off as a timeout only if results are still missing.

## Quotas and missing integrations

First verify whether the block persists: a successful review after a quota notice means service has recovered. If Codex reports a quota limit with no subsequent successful review, disable it for this PR without waiting for recovery, and record the coverage gap in the exit report.

Handle CodeRabbit rate limits within the loop without asking the user: wait for recovery, then retry once with `@coderabbitai review`. If still limited, disable it for this PR and record that in the exit report. If no recovery signal arrives after 30 minutes, hand off as a timeout.

A reviewer reply or official notice explicitly confirming the App is not connected, requiring account creation or connection, or stating the service has stopped permits marking it nonparticipating for this PR and stopping triggers. Silence and generic errors are not such evidence.

Explicitly report why coverage was reduced and what remained unreviewed; do not describe that reviewer as passing. Skipping the CodeQL threshold always requires delegator confirmation.
