# Independent review of corrected runtime workflow translations

Baseline: 7b4f781ab2c57d3d4dd1d0a15bdc5c427262f250. Reviewed the complete source/candidate prose deltas, including examples and conditional branches, read-only. No new actionable translation-induced semantic loss found in this batch.

## Exact files reviewed

- agent_runtime_profile/.claude/agents/split-narration-segments.md
- agent_runtime_profile/.claude/agents/split-reference-video-units.md
- agent_runtime_profile/.claude/references/workflow-plan.md
- agent_runtime_profile/.claude/skills/video-workflow/SKILL.ad.md
- agent_runtime_profile/.claude/skills/video-workflow/SKILL.drama.md
- agent_runtime_profile/.claude/skills/video-workflow/SKILL.narration.md
- agent_runtime_profile/.claude/skills/generate-video/references/veo_prompts.md

## Evidence examined

Both splitters preserve initial-vs-existing-vs-invalid-draft routing, invalid-draft priority even on whole-episode redo, paid-output repair rather than resampling, tool-only lock-protected writes, required fields, new_assets dispositions, verbatim source constraints, no-round-limit promotion, post-confirmation read-only behavior, and overwrite consent responsibilities. Narration retains existing-draft Step1 skip, applying current feedback before violation repair even when violations is empty (line108), exact concatenated source coverage, and formal_revision conflict acceptance (lines144-146). Reference splitting retains available-image bucket selection (line75), no assumed containment between duration sets, speaker positions excluded from visual references, per-unit repeated scene references, screenplay dialogue fidelity and source_kind fallback (line149), and automatically derived unit IDs.

The plan reference retains sole-authority routing, per-query nonpersisted episode/duration choices (line19), admission-vs-next-action distinctions, user-selected branches, blocked-project repair whitelist, no raw writes, script path vs filename distinction, empty-ID omission, per-problem action dispatch, retry-download without generation resubmission, all-or-nothing batch admission, explicit paid-action consent, four independent state axes, stale-artifact reuse, and original-request resume.

Drama/narration workflows retain registered source upload requirements, immutable modes, main-agent source-context exclusion, plan refresh after each action, global-vs-episode ID distinctions, explicit source-language setting precedence, duration-derived reading-unit soft targets, all-batch instruction repetition, consumed-episode reset consent and artifact preservation, new episode IDs, stale-rebuild completion token, post-confirmation formal-script editing, revision conflicts, overwrite loss-list consent, blank-bucket validity, base-before-derivative generation, grid composite review and explicit split consent (drama192/narration194), selected-force-vs-missing-only video calls, no partial-batch bypass, user-authorized TTS initial fill versus explicit stale resynthesis, and no automatic paid-artifact regeneration (drama232/narration234).

The ad workflow retains single-episode/no novel planning, general-film no-product exception, original/sheet fidelity gates, optional selling-point drafting and confirmation, direct formal script generation, duration mismatch warning without save blocking, new-asset registration, review checkpoints, per-ID repair, batch admission, and final-render/export only when requested. Its preexisting numeric step cross-reference mismatch is unchanged and is not attributed to translation.

The Veo guide retains all seven prompt elements, no prompt duration/aspect-ratio fields (line17), dialogue formatting and delivery modifiers, naturally integrated effects, no BGM plus generator-managed exclusions (lines47-48), camera/shot-scale mappings, direct-list negative prompts, and all four examples' actions, visual details, effects, dialogue meaning, locations and tone.

## Limits

This is independent bilingual source review of the seven listed files, not review of every runtime-profile file, live-model execution, or proof of universal behavioral equivalence. Existing contradictions are preserved, not corrected under translation scope. Machine literal/frontmatter/structure checks remain separate evidence owned by the coordinator. No files were modified for this review.
