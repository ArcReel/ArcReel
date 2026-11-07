# Handoff

Each issue has one `.afk/<batch-id>/handoff-<N>.md`; stage integration review and the review loop share `.afk/<batch-id>/handoff-stage-<K>.md`. Handoffs are transfer notes, not work logs.

Each role appends its own “Implementation”, “Integration review”, “QA”, “QA fixes”, “Base sync”, or “Review loop” section. Retain judgments the next executor needs that cannot be reconstructed from diffs, issues, or PRs: key tradeoffs, environment limitations, unresolved risks, and the reasoning behind unchanged findings, failures, or conflict handling. Existing records may be referenced.

In Base sync, record remote HEADs before and after rebase. At a review hard stop, record the remote HEAD, progress, unresolved matters, and recommended next step. Report other delivery information according to each role's contract.

Record unresolved problems or improvement opportunities and supporting evidence as follow-ups. They may concern engineering, product design, or execution methods. Distinguish facts from guesses and leave value assessment to the retrospective. The team-lead owns within-batch cleanup and issue creation, routing by scope and [escalation.md](../../../../docs/agents/escalation.md).

Write lesson candidates found during the closing self-check in a separate section using the entry format in [lessons.md](../../../../docs/agents/lessons.md); the team-lead posts them to the stage PR.

Leave other retrospective leads alongside the relevant judgments as you go. A handoff need not contain retrospective candidates; leave knowledge classification to the retrospective.
