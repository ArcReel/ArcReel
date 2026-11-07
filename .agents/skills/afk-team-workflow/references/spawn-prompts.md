# Delegation prompt templates

Pass all paths as absolute paths. For Herdr delegation, also attach addressing context under [herdr-teammate.md](herdr-teammate.md).

## Implementer

```text
You are the implementer for issue #<N> in an afk-team-workflow batch. Read <absolute-skill-directory>/references/implementer.md and follow its contract.
Inputs: repo-root=<path>; stage-branch=<branch>; issue-branch=issue/<N>; handoff=<repo-root>/.afk/<batch-id>/handoff-<N>.md.
Additional context: <necessary background; omit if none>
```

For broad changes, optionally add: `First delegate reconnaissance to an independent exploration agent.`

## Stage reviewer

```text
You own integration review for stage <K> of an afk-team-workflow batch and have not participated in implementing any issue in this stage. Read <absolute-skill-directory>/references/stage-reviewer.md and follow its contract.
Inputs: stage-branch=<branch>; worktree=<path>; spec=#<Spec; omit for standalone batches>; issues=<N,...>; handoffs=<repo-root>/.afk/<batch-id>/; stage-handoff=<repo-root>/.afk/<batch-id>/handoff-stage-<K>.md.
Additional context: <necessary background; omit if none>
```

## QA verifier

```text
You own acceptance for <stage <K> or deployment smoke testing> of an afk-team-workflow batch and have not participated in any implementation in this stage. Read <absolute-skill-directory>/references/qa-verifier.md and follow its contract.
Inputs: mode=<full|recheck|smoke>; target-commit=<sha>; worktree=<path; omit for smoke>; spec=#<Spec; omit for standalone batches>; issues=<N,...>; handoffs=<repo-root>/.afk/<batch-id>/; stage-handoff=<repo-root>/.afk/<batch-id>/handoff-stage-<K>.md (omit for smoke); evidence=<repo-root>/.afk/<batch-id>/qa/<stage-<K>|smoke>/.
For recheck, also supply one of: items=<item identifiers to verify> (after fixer repairs) or range=<from>..<to> (after the review loop). For smoke, also supply: budget=<amount>; deferred=<paths to each stage's results.md>.
Additional context: <necessary background; omit if none>
```

Reuse this stage's qa-verifier for rechecks; delegate deployment smoke testing to a new qa-verifier.

## Fixer

```text
You own QA failure fixes for stage <K> of an afk-team-workflow batch. Read <absolute-skill-directory>/references/fixer.md and follow its contract.
Inputs: stage-branch=<branch>; worktree=<path>; qa-results=<absolute-results.md-path>; issues=<N,...>; handoffs=<repo-root>/.afk/<batch-id>/; stage-handoff=<repo-root>/.afk/<batch-id>/handoff-stage-<K>.md.
Additional context: <necessary background; omit if none>
```

## Review looper

```text
You own the AI review loop for stage <K> of an afk-team-workflow batch. Read <absolute-skill-directory>/references/review-looper.md and follow its contract.
Inputs: PR=#<M>; stage-branch=<branch>; worktree=<path>; issues=<N,...>; handoffs=<repo-root>/.afk/<batch-id>/; stage-handoff=<repo-root>/.afk/<batch-id>/handoff-stage-<K>.md; rounds=<evaluation-point/hard-stop; omit for the skill default, supply when extending a takeover>.
Additional context: <necessary background; omit if none>
```

If an issue's executor becomes unavailable before its commit reaches the remote stage branch, discard its local state and unintegrated handoff file, then delegate again from the latest stage branch commit. Do not pass partial-work takeover prompts.

Confirm the predecessor process has terminated before replacing it.
