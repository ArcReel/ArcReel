# Independent review of remaining runtime instructions

Compared bilingual full diffs against 7b4f781ab2c57d3d4dd1d0a15bdc5c427262f250. This reviewer did not author these files. Review is source-level semantic evidence, not real-model behavioral release.

Exact Markdown coverage under agent_runtime_profile/.claude/:

- agents/create-episode-script.md
- agents/generate-assets.md
- agents/normalize-drama-script.md
- agents/review-footage.md
- agents/review-images.md
- references/generation-modes.md
- references/generation-results.md
- skills/adapt-custom-endpoint/SKILL.md
- skills/adapt-custom-endpoint/references/definition-format.md
- skills/edit-video/SKILL.md
- skills/generate-assets/SKILL.md
- skills/generate-grid/SKILL.md
- skills/generate-narration-audio/SKILL.md
- skills/generate-script/SKILL.md
- skills/generate-storyboard/SKILL.md
- skills/generate-video/SKILL.md
- skills/manage-project/SKILL.md

Additionally reviewed skills/setup-arcreel-skills/SKILL.md and its agents/openai.yaml, plus translated evaluation descriptions in agent_runtime_profile/skill-optimization-workspace/evals/evals.json. Parsed evaluation comparison confirms prompts, IDs, target skills, assertion names/types and all fields other than expected_output/assertion text remain unchanged.

Findings reported to coordinator:

1. manage-project's merge example translated 黑衣人 as a masked disguise, adding a mask absent from the source. Recommended a black-clad figure as the protagonist in disguise. Coordinator corrected it to “a black-clad figure who is the protagonist in disguise”; rechecked the current clause and accepted.
2. manage-project's patch_project overview call gained an extra closing brace, introducing invalid JSON into a previously valid tool example. The initial visual review misidentified which version was invalid; coordinator verified with json.loads. Coordinator restored the exact baseline call; rechecked with json.loads. This was a candidate-introduced example syntax regression, without an executable application source change.

No additional lost constraint found in listed files. In particular, checked all of these against original clauses:

- Source-kind lookup for own-source versus extracted episodes, novel adaptation versus verbatim screenplay speech, original anchors, extras exclusion, derivative appearance versus base speaker names.
- Invalid-draft precedence even on whole-episode redo, repair of paid outputs rather than resampling, current feedback before violation repair, full-content submission with proper revision handoff, merge of concurrent formal changes with accept_formal_revision, no direct file writes, read-only planning after content confirmation, user-driven reconfirmation and overwrite consequences.
- Author only pending or explicitly selected gaps by default; explicit rewrite permission and exact loss-list/revision acceptance; preservation of content/notes/media; ad full regeneration separate from authoring with new-assets decisions, untouched prior assets, and rejection behavior.
- Read-only review scopes, same-message grouped dispatch, 20-image limits and 6-10-unit groups, candidate version inspection, BLACK/FREEZE as suspected defects and CUT as structural, uncertainty reporting, position-based creator labels and ID-based tool calls.
- User-approved regeneration/editing, local-edit versus full-redraw distinction and history-only recovery, prompt/description persistence rules, exact product fidelity and exclusion of reference people, reference order and previous-frame composition/color-only limits.
- Grid composite generation versus explicit user-approved split, overwrites/history retention, missing-only reuse, in-flight reuse and all-or-nothing admission.
- TTS initial authorization, independent video/audio progress, project snapshot settings, null speed semantics, missing-only resume, stale audio only regenerated on user request, post-production delivery remaining available without TTS.
- Whole-video-batch admission, tier selection from live reference availability, explicit consent before tier change, same original target set resubmitted as one batch, no paid partial-batch bypass, durable recovery by structured action, paid artifacts/history preservation and four independent state axes.
- Editing timeline selection, duplicate before major restructuring, latest revision and whole-batch resubmission, direct free version selection, user approval for paid regeneration and source rewrites, no automatic second regeneration, complete issue-linked delivery and cost-bearing checklists, BGM joining arithmetic, warnings versus blocking, narration-only applicability, final-cut versus requested Jianying output.
- Custom endpoint protocol limits, validator authority, credential confinement, previews before explicitly approved paid trials, terminal success versus expressly accepted unverified save, copy-by-default and approved overwrite, same-instance configuration/no localhost fallback, JWT expiration handling.
- External setup reuses matching connections, asks before choosing conflicts, HTTPS/loopback rules, native persistence and timeout, repo-local ignore plus untracked requirements, consent before index/ignore mutations, refusal/no-answer preservation, historical token revocation reminder, and structured empty project-list success.

Scope limits: the seven workflow/splitter/Veo files were reviewed in an earlier separate note; top-level CLAUDE profiles were reviewed by the coordinator previously. Also compared public/agent-installation-guide.md in full: install command, required setup/workflow skills, BASE_URL token/settings/MCP links, explicit selected-agent credential delivery, immediate setup, persisted same-instance connection and empty structured projects-list success remain intact. No finding. Chinese user/example literals, compatibility syntax and existing output-language policies remain intentionally present. Syntax check additionally parsed 49 strict baseline-valid single-line MCP JSON payloads and 3 strict baseline-valid fenced JSON blocks across changed profile/setup/guide files; all current counterparts parse. Placeholder-bearing or multiline pseudo-calls are outside that syntax check. No paid API/model run, rendered UI inspection or universal equivalence claim.
