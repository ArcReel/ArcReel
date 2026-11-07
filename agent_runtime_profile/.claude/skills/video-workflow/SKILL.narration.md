---
name: video-workflow
description: End-to-end workflow orchestrator for turning novels into short videos. Must use when the user mentions making videos, creating or continuing projects, or checking progress. Triggers include but are not limited to turning a novel into video, starting a project, continuing, taking the next step, checking progress, starting over, splitting episodes, or running the whole workflow automatically. Even a brief “continue” or “next step” triggers it whenever the context involves a video project. Do not use for individual asset generation, such as redrawing one storyboard or regenerating one character image; those have dedicated skills.
---
<!-- mode: narration -->

# Video workflow orchestration

You (the main Agent) are the orchestration hub. You **do not directly** process novel source text or generate scripts. Instead:
1. Detect project state → 2. Read plan `next_action` → 3. Dispatch the appropriate subagent → 4. Present results → 5. Obtain user confirmation → 6. Repeat

**Core constraints**:
- Novel source text **never enters the main Agent context**; subagents read it themselves
- Pass only **file paths and key parameters** in dispatches, not large content blocks
- Each subagent returns after one focused objective; the main Agent connects actions

> See `.claude/references/generation-modes.md` for data structures and schema differences between storyboard-to-video (storyboard, with grid_storyboard toggle) and reference-to-video (reference_video); the plan expresses step applicability, which the reference does not duplicate.

---

## `collect_project_input`: project setup

**Important**: Web `POST /api/v1/projects` invokes `ProjectManager.create_project()` to create project directories (including all subdirectories, `project.json`, and the Agent profile materialized for content_mode). **The main Agent neither creates directories nor writes initial project.json fields**; at session startup, cwd is already bound to an existing project root.

### New projects

1. Ask the user to create a project in Web first, **specifying content_mode (narration / drama) and generation_mode (storyboard / reference_video) at creation**; both are immutable afterward, and the Agent cannot write them. Session cwd is bound to the corresponding root at startup
2. Read `project.json` with Read to confirm `title`, `content_mode`, and `generation_mode` (this session's content_mode is `narration`, immutable after creation)
3. Ask the user to upload the novel text (whole-book source) in Web, or write through `mcp__arcreel__upload_source`; files placed in `source/` without registration are not source text
4. **Automatically generate the project overview after upload** (synopsis, genre, theme, world_setting)

> `create_project()` automatically creates standard subdirectories: `source/`, `scripts/`, `drafts/`, `characters/`, `scenes/`, `props/`, `storyboards/`, `grids/`, `videos/`, `reference_videos/`, `thumbnails/`, `output/`.

### Existing projects

1. Session cwd is already bound to the target project root
2. Call `mcp__arcreel__get_workflow_plan({})` for the authoritative server plan
3. Continue according to returned `next_action` (branch alternatives are in `next_alternatives`)

---

## Query the plan

On workflow entry, when the user says “continue/next step/check progress”, and after each tool or subagent finishes, call
`mcp__arcreel__get_workflow_plan({})` for the authoritative plan (for a specified episode, find it by the user's broadcast position or title in project `episodes[]`, then pass its episode ID as `{"episode_id": X}`),
then route by `next_action.type` to the same-named section below.

For plan field meanings, the complete controlled action table, narration delivery, whole-batch admission, four status axes, and stale/history discipline, see
[.claude/references/workflow-plan.md](../../references/workflow-plan.md). **This skill does not duplicate a step table expanded by content type
or generation mode**: always read `plan.steps[]`,
`plan.next_action`, and `plan.status.operations` for applicable steps, recommended next actions, and currently available operations; they are the sole source of stage decisions. `plan.status` embeds the complete state snapshot
(`project` / `target` / `content` / `operations` / `blockers` / `issues` / `gates` / `artifacts`), so no separate status query is needed.

After the call, use `plan.status.target.episode` as the target episode (its episode ID; N in paths and parameters below) and pass `next_action.args` and `requested_ids` unchanged to
the action. Read / Glob supplies only content needed for the chosen action, not a separate state machine; do not override server conclusions using empty asset buckets,
filenames, old-file existence, or conversational memory.

Sections below are headed by `next_action.type`; actions without dedicated sections (such as `resolve_draft`, `start_blank_script`,
`add_script_items`) follow the workflow-plan controlled action table. For `none` with `blockers`, show them and stop mutations;
`edit` marked `completed` in `steps` means workflow completion; otherwise explain `status.issues` and `status.content` to the user.
See “Flexible entry” for the distinction between named operations and “continue”.

> Batch narration audio is not driven by `next_action`; see “Batch narration audio” for its triggers.

---

## Confirmation between actions

**After every subagent returns**, the main Agent:

1. **Presents its summary**: show the returned summary to the user
2. **Obtains confirmation**: use AskUserQuestion with options:
   - **Continue to the next action** (recommended)
   - **Redo this action** (redispatch with additional requested changes)
   - **Skip this action**
3. **Acts on the user's choice**

---

## `plan_episodes` / `reset_episode_planning`: episode planning

**Recovery trigger**: when `next_action.type` is `"reset_episode_planning"`, first call
`mcp__arcreel__reset_episode_planning` with `next_action.args`. If it requires confirmation for consumed episodes, show the scope of impact and obtain explicit user confirmation,
then retry with `confirm_consumed: true`; after successful reset, refresh the plan and follow its new authoritative action.

**Trigger**: `next_action.type == "plan_episodes"`

**Skip this section for user-split episodes**: register each supplied episode's source through `mcp__arcreel__upload_source` (`role=episode`) as an episode with its own source, or enter it directly on the episode page; these bypass episode planning and go directly to script planning individually. Register each episode unchanged, preserving supplied divisions and order; combine into a whole book and recut using `plan_episodes` only upon explicit user request. Episode planning and resets affect only whole-source-derived episodes; own-source and source-free episodes remain unchanged.

Server tools perform episode planning: follow `whole_source_files` order, read a source window starting at the last derived episode's end, and call the project's configured text model once to plan every complete story arc in the window (title/hook/source range). Under one project lock, write the ledger, derive `source/episode_{N}.txt`, and clean leftover derived files. **The main Agent makes only one tool call and receives only a summary**, never reading novel source or choosing split points:

1. Before planning, quickly check `project.json`:
   - Does `source_language` match the actual source language? Priority: **explicit user configuration > automatic inference** (normally persisted by overview generation). On mismatch, **warn the user (WARN), explain consequences, and recommend correction** (wrong configuration distorts size metrics and language premises). If not corrected, continue using explicit configuration without blocking. If missing or user-confirmed wrong, write through `mcp__arcreel__patch_project({"settings": {"source_language": "en"|"vi"|"zh"}})`
   - `episode_target_units` (episode target size, interpreted as reading units using `source_language`): keep existing values; if absent but the user explicitly supplied a count in conversation, write through `mcp__arcreel__patch_project({"settings": {"episode_target_units": N}})`. If absent but `episode_target_duration` exists, the tool converts duration to size using speech rate: **do not ask again for a word/character count**. If both are absent, planning may still proceed (the tool sizes episodes for short-video pacing); do not force a question
2. Call `mcp__arcreel__plan_episodes({})`. The tool determines window size and batch episode count from the text model's maximum output length; neither is configurable. If this batch reports no complete split point, ask the user to split this passage manually before continuing; see tool descriptions for output-truncation recovery. **When the user gives additional episode instructions before planning** (such as “strictly split by chapter, one chapter per episode” or “end each episode at a particular point”), pass them verbatim through `instructions`: `mcp__arcreel__plan_episodes({"instructions": "附加指令原文"})`. They enter the prompt's additional-instructions section unchanged; wording expresses compliance strength. If strict compliance is explicitly required, include that strength in the text (such as “Must implement all: one chapter per episode”). Long sources require multiple batches (one call each); instructions **are not persisted**, so **repeat the same `instructions` on every batch call** until complete
3. **Batch review**: show the returned ledger summary and ask for feedback; review is complete only when every episode's opening and ending source text has been shown, allowing the user to check boundaries
4. User feedback (one sentence may contain any number of changes, including global preferences) → “reset + replan”: first call `mcp__arcreel__reset_episode_planning({"episode_id": X})`, setting `episode_id` to the earliest affected episode's ID in broadcast order, preserving preceding episodes. If the earliest affected one is the first derived episode, omit `episode_id` for a full reset
5. **Consumed-episode warning confirmation**: if resetting affects consumed episodes (existing script_plan/scripts/media), the tool returns the affected list without executing. Explain the scope, obtain explicit confirmation, and call again with `"confirm_consumed": true`; after confirmation, these become source-free episodes, are marked stale and moved to the end of broadcast order, with artifacts and registrations preserved
6. After reset, explicitly write global feedback (such as episode size) using `mcp__arcreel__patch_project({"settings": {"episode_target_units": N}})`, then call `mcp__arcreel__plan_episodes` with adjusted `instructions` to plan batches after the retained portion and present results for review again. Replanned episodes always receive new episode IDs; cleared IDs are never reused. Old scripts/media are not attached to new episodes; downstream artifacts must be produced anew. **The final planning return includes global verification material** (cumulative episode count, smallest episodes by size, median size, target size; duration-derived targets identify `episode_target_duration` as their source and must be treated as soft targets). Check against user-supplied structural preferences such as total episode count or chapter alignment, explicitly disclose deviations, and optionally guide another reset/replan
7. Refresh the plan after the user approves the batch. **With explicit authorization for full autonomy** (such as “run the entire workflow without step-by-step confirmation”), batch review may be skipped

---

## `prepare_script_plan`: single-episode script planning

**Trigger**: `next_action.type == "prepare_script_plan"`

Dispatch the subagent named by `next_action.args.preprocessor` to produce the corresponding script_plan
intermediate file under `drafts/episode_{N}/`. **Do not infer the choice yourself from `generation_mode` × `content_mode`**: the server derives
`preprocessor` from the same rule table; repeating that inference in the profile creates a second source of truth. See
`.claude/references/generation-modes.md` for script_plan files and corresponding schemas.

Common dispatch prompt parameters: project name, project path, target episode ID (`target.episode`), title and broadcast position, and this episode's novel file path (`target.source`); optional additional instructions (user requirements or any temporary context the subagent needs, passed verbatim).

If `next_action.args` contains `expected_stale_script_plan_revision`, after the subagent successfully produces the formal script_plan, you must call
`mcp__arcreel__complete_script_plan_rebuild({"episode_id": N, "expected_stale_script_plan_revision": next_action.args.expected_stale_script_plan_revision})`.
Do not infer completion from whether file contents changed: deterministic rebuilds can produce identical JSON. On tool conflict, refresh the plan;
never retry with old parameters.

(The two script-planning subagents read project.json themselves and call
`mcp__arcreel__get_video_capabilities({})`
for model capabilities and user preferences; the main Agent need not inject character/scene/prop lists or
`supported_durations` / `max_duration` / `max_reference_images` / `default_duration` / `episode_target_duration` data beforehand.)

**New episode assets emerge from planning**: recognize assets during per-episode script planning. Subagents identify registered assets by names, aliases, and descriptions, writing unregistered characters/scenes/props to top-level script_plan `new_assets`, each with a disposition (`register` new asset / `merge` into existing asset / `derivative` character derivative / `skip` registration) and a one-sentence rationale. Dispatch script planning normally even if asset tables are empty.

**After content confirmation, edit the formal script**: confirmation converts the entire script plan into formal `scripts/episode_{N}.json`, thereafter the sole source of episode content. The script plan becomes read-only (Web saving and Agent retrieval/modification/promotion of editing copies all return `script_plan_confirmed`).

- **Edit content**: for changes to storyboard narration body `novel_text`, or reference-unit body `text` and corresponding `source_text`, first fetch body and revision with `mcp__arcreel__get_episode_script`, then write once through `mcp__arcreel__patch_episode_script` `update`; do not edit the script plan or rerun authoring. Nonempty source correspondence must be a verbatim substring of episode source `source/episode_{N}.txt` (whitespace-normalized comparison; trim ends but never omit or modify the middle; fall back to project source if episode source is absent). With project source, the server validates and rejects mismatches as `source_text_not_verbatim`; clearing source correspondence is not validated. After narration-body edits, let the user decide whether to explicitly rewrite those entries using `generate_episode_script` with `entry_ids` and `rewrite: true` so prompts follow. Reference-to-video authoring rewrites unit bodies; explicitly rewriting a just-edited body overwrites that change, so explain this first
- **Redo the whole episode**: redispatch script planning (operation type “Whole-episode redo”) only when the user wants the entire episode replaced. After the new plan is written, content confirmation returns to pending (`gates.script_plan_review.state` is `pending`), but the old formal script remains active; the plan does not stop at `confirm_script_plan`, and `next_action` still targets its downstream work. During a whole-episode redo, do not follow that action or call `generate_episode_script`, which would author the old script about to be overwritten; call `confirm_script_review` directly following this process. If a formal script exists, confirmation overwrites it: `confirm_script_review` returns `script_overwrite_required` with a server-generated loss list (the same text as Web confirmation, including voiceover, tail frames, and grid ownership). Relay that list verbatim and obtain explicit consent before reconfirming with `overwrite_revision=params.script_overwrite.revision`; after overwrite all entries await authoring
- **Prompt authoring**: `generate_episode_script` defaults to filling gaps, preserving existing image/video prompts, including user-written ones. `entry_ids` only bounds scope; add `rewrite: true` only for explicitly requested overwrites, then obtain consent from the returned loss list and pass back the token

---

## `confirm_script_plan` / `generate_script`: JSON script generation

**Triggers**:

- `next_action.type == "confirm_script_plan"` → complete content confirmation below, refresh the plan, then route again
- `next_action.type == "generate_script"` → dispatch script generation

**script_plan→prompt_authoring content confirmation (blocking)**: plans from `prepare_script_plan` require **explicit confirmation** before prompt authoring (all three structured variants, drama / narration / reference_video; `reference_video` `script_plan_reference_units.json` also requires confirmation, never skip it. Ads have no script_plan and require no content confirmation). Two equivalent paths: the user reviews/edits and confirms in Web, or explicitly agrees in conversation to visual generation, after which you call `mcp__arcreel__confirm_script_review({"episode_id": N})` (full-autonomy mode uses the user's overall authorization). Without a formal script, unconfirmed plans stop at `confirm_script_plan`, never routing to authoring (existing formal scripts do not block downstream; see “Redo the whole episode” above); without a formal script, `generate_episode_script` reports that none exists. Confirmation converts the entire plan into a formal script with all entries awaiting authoring; if a formal script exists, it is overwritten and first requires consent as above.

At confirmation, `new_assets` register alongside the formal script: create assets, add aliases to existing ones, or register derivatives per disposition, rewriting references accordingly; same-named new items of the same asset type automatically merge into existing assets. Before conversational confirmation, explain every new asset and disposition. To change dispositions, direct the user to “New assets in this episode” in Web content confirmation, or retrieve the script-plan draft, edit `new_assets`, and promote it. Unresolvable dispositions (missing merge target or derivative base) return `invalid_new_assets`; correct per receipt and confirm again.

**Dispatch `create-episode-script`**: pass project name, project path, target episode ID (`target.episode`), title and broadcast position; optional additional instructions (user requirements or temporary context needed by the subagent, passed verbatim).

---

## `generate_asset_sheets`: episode asset images

**Trigger**: `next_action.type == "generate_asset_sheets"`; `next_action.args.episode_id` is the target episode ID.
Assets register at content confirmation; empty asset buckets are valid and must not cause fallback.

The server determines which assets this episode references: call `generate_assets` once by episode ID to generate all referenced assets still missing images
(characters/scenes/props/products and derivatives), using the same list as Web episode-level “Generate pending assets”. Bases and derivatives share a batch; derivatives submit only after base images succeed.

```text
dispatch `generate-assets` subagent:
  Task type: asset_sheets
  Project name: {project_name}
  Tool call:
    mcp__arcreel__generate_assets({"episode_id": <next_action.args.episode_id>})
  Verification: report requested / succeeded / failed / blocked results per ID
```

After the subagent returns, follow [generate-assets](../generate-assets/SKILL.md), “Review checkpoint: image review”, dispatching image review for successfully generated assets through
`review-images`. Show generation summaries and review reports together, then enter confirmation between actions; handle selected assets as that section specifies.
For `blocked` items, see the `generate_asset_sheets` row in [workflow-plan](../../references/workflow-plan.md).

---

## `generate_storyboards` / `generate_grid`: storyboard image generation

**Trigger**: `next_action.type` is `"generate_storyboards"` or `"generate_grid"`; the server never returns these actions for
reference-to-video.

Choose tools directly from the action, without rechecking `generation_mode` or `grid_storyboard`:

- `next_action.type == "generate_storyboards"` → dispatch `generate-assets`, calling
  `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": requested_ids})`
- `next_action.type == "generate_grid"` → dispatch `generate-assets`, calling
  `mcp__arcreel__generate_grid({"script": target.script_filename})`; **omit `scene_ids`**:
  missing-only generation selects precisely the groups containing `requested_ids`, skips grids whose composite is ready but unsplit, and reuses in-flight grids;
  naming `scene_ids` treats them as regeneration requests and charges again

For `generate_storyboards`, pass `next_action.args` and `requested_ids` unchanged to the subagent; for `generate_grid`, pass only the script filename.

After the subagent returns, follow [generate-storyboard](../generate-storyboard/SKILL.md), “Review checkpoint: image review”, dispatching review for storyboards whose images were written through
`review-images` (after splitting and cell assignment for grids). Show generation summaries and review reports together, then enter confirmation between actions.

> **Grids have two stages**: `generate_grid` produces only the composite; storyboard images are written after splitting and cell assignment, so before splitting the plan continues
> returning `generate_grid`. Result `grid_ids_awaiting_split` (or grids summarized as “composite ready, unsplit”)
> lists unsplit grids. Ask the user to review composites in the grid panel (regenerate, upload replacements, or roll back), and **only after explicit consent to split**,
> call `mcp__arcreel__split_grids({"grid_ids": [...]})`. Do not split automatically after generation; splitting overwrites every storyboard image covered by the grid,
> retaining older images in version history for rollback.

> **Redo after changing `grid_storyboard`**: the usual trigger is missing storyboard images, but toggling this setting does not invalidate existing images, and scripts do not record their assembly method; checking only missing images would mark the episode complete. If the user toggles it in a project with existing images and requests the new method, confirm the storyboard scope and explicitly regenerate by ID: after switching to grids use `mcp__arcreel__generate_grid({"script": target.script_filename, "scene_ids": [...]})`; after switching to single images use `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": [...]})` (`script` is required; omitting IDs fills only gaps and cannot redo existing images). Existing videos also do not automatically become stale; after new storyboard images, rerun `generate_videos` for the corresponding shots to use them.

## `generate_videos`: video generation

**Trigger**: `next_action.type == "generate_videos"`

Before enqueueing, the plan may return `confirm_request_duration`: whole-batch admission requires requested-tier confirmation. Show
`admission.confirmation.tiers[]` per tier with affected units and costs; after confirmation, pass
`confirmed_request_durations` into the next `mcp__arcreel__get_workflow_plan` (see
[workflow-plan](../../references/workflow-plan.md)).

Enqueue only when `plan.steps[].admission.decision == "admitted"`; for `blocked` or `confirmation_required`,
**enqueue no tasks**. Report each `admission.units[]` unit's `unit_id`, `problems[].code`, reason, and
`problems[].action`; units held back by others carry `generation_batch_admission_withheld`. Explain accurately that
`blocked_unit_ids` held them back, rather than implying their own inputs are invalid. Fix rejected units and **retry the whole batch**, never split to run the passing half,
as that resubmits already-paid units.

**Dispatch `generate-assets`**: request selection is identical to Web. Plan `requested_ids` is always an array:
nonempty means **explicit forced regeneration (always charged)**; `[]` means none named, so **omit the ID parameter** in the tool call to fill only missing artifacts.
Explicit empty arrays are invalid tool inputs; never forward plan `[]` unchanged. Choose exactly one call based on those two values; do not try both:

```text
dispatch `generate-assets` subagent:
  Task type: video
  Project name: {project_name}
  Tool call:
    requested_ids nonempty →
      mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "selected", "ids": requested_ids},
                                             "force": true})
    requested_ids == [] (none named in plan; omit scene_ids in tool call) →
      mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "episode", "episode_id": target.episode}})
  Verification: reread target.script and check each shot's video_clip field
```

Report results per ID (`succeeded` / `failed` / `blocked` / `skipped`) and **separately report** the four axes: workflow-step status,
queue task, provider checkpoint, and artifact freshness. “Task succeeded” does not mean “current artifact valid”.
Stale artifacts remain previewable, exportable, and usable in final cuts; the user explicitly decides whether to redo them. Never automatically delete, overwrite, or regenerate paid artifacts.

---

## `create_edit_timeline`: editing

**Trigger**: `next_action.type == "create_edit_timeline"`; episode videos are complete, with no editing timeline yet.

For TTS projects (nonempty `plan.status.artifacts.audio.missing_ids`), first fill missing narration following “Batch narration audio”. Once a timeline exists,
this step is complete; the workflow will not return here to fill audio afterward.

Then use `mcp__arcreel__create_timeline({"from": "script", "episode": target.episode, "name": "完整版"})` to mechanically create
an editing timeline from the script. The step completes once at least one timeline exists. Then edit it using `edit-video`,
rendering a final cut or exporting a Jianying draft only when requested.

---

## Batch narration audio

**Triggers**: fill missing audio during a TTS project's first automatic editing round, or on an explicit request for narration audio. Selecting TTS already authorizes
the initial fill, without separate confirmation; resynthesize stale audio only on user request. For post-production voiceover projects requesting narration audio, first ask the user to
switch project settings to TTS. This is not driven by plan `next_action` and does not block video generation;
`plan.status.artifacts.audio` reports missing or stale audio in TTS projects and `not_applicable` for post-production voiceover projects.

Narration audio synthesizes each segment's verbatim `novel_text` separately; it depends only on the script, independently of storyboard images/videos:
on user request, run it any time after `generate_script` produces the script.

`generation_mode == "reference_video"` **skips only storyboard images**: reference-to-video has no segment-batch TTS
entry point (no `segments[]`); generate narration audio per video unit.

**Dispatch `generate-assets`**:

```text
dispatch `generate-assets` subagent:
  Task type: narration_audio
  Project name: {project_name}
  Tool call:
    Fill the whole episode:
      mcp__arcreel__generate_narration_audio({"script": target.script_filename})
    User-named segments for resynthesis (after changing voice/speed):
      mcp__arcreel__generate_narration_audio({"script": target.script_filename,
                                              "segment_ids": [点名的段 ID]})
  Verification: reread target.script and check each segment's generated_assets.narration_audio field
```

**Resynthesis must include `segment_ids`.** Omitting it means missing-only fill; stale audio is reusable and will be
skipped. Trying to resynthesize without IDs does nothing.

After interruption, redispatch the whole-episode fill call to resume: segments with audio are skipped automatically, filling only missing ones.

---

## `repair_video_units` / `patch_episode_script`: edit scripts, then regenerate

**Trigger**: `next_action.type` is `"repair_video_units"` or `"patch_episode_script"`.

Read `target.script`, **handling only entries in `requested_ids`**. Obtain revision according to action:
`patch_episode_script` `next_action.args` directly supplies `base_revision` and per-entry `problems`;
use them without another query. `repair_video_units` args lack it; first call
`mcp__arcreel__get_episode_script({"script": target.script_filename})` for body and revision.
Then use **one** `mcp__arcreel__patch_episode_script({"script": target.script_filename,
"base_revision": <上面取到的 revision>, "operations": [...]})` to modify all entries, one ordered `update` per entry.
The tool recomputes flags such as `needs_replan`; never write them manually. On revision conflicts, refresh the plan and restart; never retry with old
revision values. After changes, explicitly regenerate these IDs using the request-selection semantics above, then refresh the plan.

---

## `wait_for_task`: tasks are running

**Trigger**: `next_action.type == "wait_for_task"`.

Tasks already run in the queue or at the provider. **Enqueue no new tasks**. Accurately report `steps[].tasks[]` `task_id`, `status`,
and `provider_checkpoint` to the user, wait, then recheck with `mcp__arcreel__get_workflow_plan`.
`provider_checkpoint.submitted == true` means provider submission occurred and likely incurred charges; resubmission would pay twice.

---

## Flexible entry

The workflow **does not require starting over**. Automatically enter the correct action from the plan:

- “Continue” → execute plan `next_action`; nonempty `next_alternatives` marks a branch, so present options for user selection
- Named operation (such as analyzing novel characters, planning episode 2's script, or generating storyboards) → query the plan first; if `plan.status.operations`
  marks it `admitted` (or omits it), execute even if `next_action` points elsewhere. For `refused`, relay `reason`,
  explain what is missing and how to supply it. Never execute with `blockers`
- “Episode N” means Nth in broadcast order: obtain its episode ID from `episodes[]` order, then query its plan

---

## Data layers

- Full character/scene/prop definitions **exist only in project.json**; scripts reference names only
- Derived project-summary `episodes[]` fields (item_count, status, progress) are **computed on read**, not stored
- Episode metadata (episode ID / title / script_file) is **synchronized on write** when scripts save
