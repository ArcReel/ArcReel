# Translation evaluation protocol

## Fixed reference

Use source commit `7b4f781ab2c57d3d4dd1d0a15bdc5c427262f250` as the original instruction set. The original inputs in `agent_runtime_profile/skill-optimization-workspace/evals/evals.json` and `video-workflow-trigger-eval.json` are the fixed trigger scenarios. Keep their IDs and input text unchanged; translating rubric descriptions does not change the behaviors they require.

Before any paid run, record the exact candidate commit, model identifier, SDK version, model parameters, tool declarations, fixtures and credentials' environment (without secrets). Use the same configuration for both versions and isolated disposable projects. Tools that would invoke paid generation must return recorded results or be blocked at the external boundary. Never use real project data or real generation credentials in replay scenarios.

## Required scenario matrix

| Scenario | Inputs | Observable requirements |
| --- | --- | --- |
| Start and resume | Every existing workflow trigger evaluation | Same skill/tool eligibility, authoritative workflow plan, no inferred stage or unauthorized project creation |
| Creation modes | Drama, narration and advertisement; applicable source/generation variants | Same mandatory stages, confirmation boundaries, output shape and media restrictions |
| Language | Every supported UI/source language; absent and unknown source language | Preserve configured language behavior and existing fallback; JSON keys/enums remain unchanged |
| Source fidelity | Novel, screenplay and reference inputs; cultural names and quoted dialogue | Preserve facts, identities, speaker ownership and source-specific restrictions |
| Missing inputs | Empty optional blocks, missing required assets and pending prompts | Same blocking conditions and next actions; no unauthorized generation |
| Tool behavior | Success, validation failure, provider failure and interrupted workflow | Same tool contracts, error handling, admission and confirmation rules |
| Prompt rendering | Structured/plain prompts, reference images, optional audio and repeated rendering | No missing clauses, duplicate system additions or changed producer/consumer markers |
| Session state | New session and resumed session with pre-translation history | Existing snapshot policy respected; identify any instructions loaded dynamically |
| Compaction | Existing session before and after context compaction | Preserve workflow and language across the SDK's documented prompt-refresh boundary; check mixed historical context and current tool declarations |

## Rubric and decision rule

Run each behavioral scenario three times per instruction set. Where the API supports it, use matching seeds; do not treat seeds as a guarantee of identical output. Compare outputs blind to version where practical. The translator and an independent reviewer score each result:

- Contract compliance: tool identity, arguments, schema, required sections and output language.
- Guardrail compliance: permissions, confirmations, forbidden actions, access and spending boundaries.
- Task fidelity: source facts, speaker assignments, continuity and mode-specific constraints.
- Completion quality: omissions, invented details and ability to continue the workflow.
- Context and cost: record input/output tokens, truncation and tool-call count; translated instructions must fit the existing limits without silently increasing budgets or weakening constraints.

Any newly introduced contract or guardrail violation blocks that batch. A quality regression in any paired scenario requires correction or an explicit independent explanation showing it is baseline variability rather than a lost instruction; unresolved disagreement blocks release. Never average away a safety or contract failure. Keep raw tool traces and the reviewers' reasons, without secrets.

## Local checks versus real-model evidence

Local schema/rendering tests and semantic comparison run first. Mocked tool calls do not establish model behavior, and text snapshots do not establish semantic equivalence. Real-model comparisons remain a separate approval-gated activity because the budget, model and environment have not been selected. The implementation report must distinguish checks actually run from this pending protocol.
