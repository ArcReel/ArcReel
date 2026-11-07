---
name: video-workflow
description: Workflow entry point for ad/short-film projects. Must use when the user mentions making videos, continuing a project, or checking progress. Triggers include but are not limited to requests to make a product-promotion video, continue, take the next step, or check project progress. Even a brief “continue” or “next step” triggers it whenever the context involves a video project. Do not use for individual asset generation, such as redrawing one storyboard or regenerating one character image; those have dedicated skills.
---
<!-- mode: ad -->

# Ad/short-film workflow

This project is **ad/short-film** (ad): one video, always one episode (script `scripts/episode_1.json`), with shots planned against `target_duration`. **There is no episode-splitting concept**: do not plan episodes, split them, or process novel source files.

## Workflow steps

First call `mcp__arcreel__get_workflow_plan({})` for the authoritative plan. Treat `steps[]`, `blockers`, `status.operations`,
and `next_action` as the sole source of stage decisions (`plan.status` embeds snapshots of `project` / `target` / `content` / `issues` /
`gates` / `artifacts`); Read only supplements creative input and product soft-gate information. Refresh the plan after each action.
If `blockers` is nonempty, present them and stop mutations; `next_action.type == "none"` with `edit` marked `completed` in `steps` means the workflow
is complete. For other `none` cases without blockers, explain `status.issues` to the user.

For “continue”, follow `next_action`; if `next_alternatives` is nonempty, present options for the user to select. For an explicitly named operation,
execute if `status.operations` marks it `admitted` (or omits it); if `refused`, relay `reason`
(such as `ad_brief_and_products_missing`: neither creative brief nor products are supplied).

For plan fields, the complete controlled action table, narration delivery, whole-batch admission, four status axes, and stale/history discipline, see
[.claude/references/workflow-plan.md](../../references/workflow-plan.md). **This skill does not duplicate a step table expanded by generation
mode**: `plan.steps[].required` expresses which steps apply.

Enter the corresponding step directly from `next_action.type`:

- `next_action.type == "collect_project_input"` → step 2
- `next_action.type == "generate_script"` → step 5
- `next_action.type == "start_blank_script"` / `"add_script_items"` → guide the user to write manually or add items in Web (see workflow-plan reference)
- `next_action.type == "generate_asset_sheets"` → step 4
- `next_action.type == "repair_video_units"` → step 7 unit repair
- `next_action.type == "generate_storyboards"` → step 7 storyboard single-image path
- `next_action.type == "generate_grid"` → step 7 storyboard grid path
- `next_action.type == "generate_videos"` → step 7 video generation
- `next_action.type == "create_edit_timeline"` → step 8

Pass `target.episode`, `next_action.args`, and `requested_ids` when calling tools or dispatching subagents; do not recheck `generation_mode` or `grid_storyboard` to choose another stage. Product-original and sheet-review rules within steps are soft gates before action execution.

1. **Confirm project state**: confirm `content_mode=ad` and project-level `generation_mode` from the plan; Read `project.json` for `title`, `target_duration`, `brief`, and `products`. Generation mode cannot change after creation.
2. **Creative input**: if a product-promotion project has no registered product or lacks originals, guide the user to upload in WebUI; originals are fidelity anchors. Use `mcp__arcreel__patch_project` for product descriptions, brands, and `brief`. Do not request products for general short films.
3. **Draft selling points**: selling points do not block script generation. If `status.content.products_without_selling_points` is nonempty, offer to draft them; after agreement or an explicit request, draft from brief, descriptions, and originals, confirm with the user, and write back with `patch_project`.
4. **One-click script generation**: once at least one of `brief` or products exists, call `mcp__arcreel__generate_episode_script({"episode_id": target.episode})`. Ads do not use script_plan; results become formal scripts directly. Storyboard-to-video produces `shots[]`, reference-to-video produces self-contained `video_units[]`. Newly referenced characters, scenes, and props are registered as assets awaiting generation and listed in the receipt. Warn if total duration differs from `target_duration`, without blocking saving. For a whole-script redo pass `regenerate: true`; see generate-script for loss-list confirmation and contract-failure handling.
5. **Asset images**: call `mcp__arcreel__generate_assets({"episode_id": <next_action.args.episode_id>})`: the server generates all episode-referenced assets still lacking images (including products, derivatives, and assets registered during script generation), using the same list as Web's episode level. After completion, dispatch `review-images` under [generate-assets](../generate-assets/SKILL.md), “Review checkpoint: image review”, and present reports for user confirmation.
6. **Sheet review (soft gate)**: if a product has `product_sheet`, ask the user to confirm fidelity to the real product before first storyboard or reference-to-video generation; continue directly when only originals exist.
7. **Planning and generation**:

   - `repair_video_units`: read body and revision with `mcp__arcreel__get_episode_script({"script": target.script_filename})`, addressing only units in `requested_ids`; then use one `mcp__arcreel__patch_episode_script({"script": target.script_filename, "base_revision": revision, "operations": [{"op": "update", "id": unit_id, "fields": {"text": "...", "duration_seconds": ...}}]})` to write complete plans for all these units (one ordered update per unit). Let the tool recompute `needs_replan`; do not edit flags directly. Keep one speech owner per unit and use `@[名称]` for products/characters/scenes/props. Immediately regenerate repaired units using `generate_videos`, `selected` scope, and `force: true`, then refresh state.
   - `next_action.type == "generate_storyboards"` → call
     `mcp__arcreel__generate_storyboards({"script": target.script_filename, "segment_ids": requested_ids})`
   - `next_action.type == "generate_grid"` → call
     `mcp__arcreel__generate_grid({"script": target.script_filename})` (omit `scene_ids`: generate missing only; do not redo grids whose composite image is ready but not yet split)
   - After storyboard images are written (for grids, after splitting and assigning cells), dispatch `review-images` following [generate-storyboard](../generate-storyboard/SKILL.md), “Review checkpoint: image review”, and present reports for user confirmation
   - `next_action.type == "confirm_request_duration"` → show affected units and costs per tier from `admission.confirmation.tiers[]`,
     then return confirmation through `confirmed_request_durations` in the next `mcp__arcreel__get_workflow_plan`
   - `next_action.type == "generate_videos"` → first inspect `plan.steps[].admission.decision`: enqueue only when `admitted`;
     for `blocked` / `confirmation_required`, **enqueue no tasks**. Report each unit's `unit_id`,
     `problems[].code`, reason, and `problems[].action` (units held back by `blocked_unit_ids` carry
     `generation_batch_admission_withheld`; explain that they have no intrinsic problem). Fix rejected units, then retry the whole batch;
     do not split to run the passing half. When `requested_ids` is nonempty, enqueue with
     `mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "selected", "ids": requested_ids}, "force": true})`;
     only when `requested_ids` is empty use `mcp__arcreel__generate_videos({"script": target.script_filename, "target": {"scope": "episode", "episode_id": target.episode}})`.
     Report results per ID (`succeeded` / `failed` / `blocked` / `skipped`), keeping workflow-step
     status, queue task, provider checkpoint, and artifact freshness as four separate axes: “task succeeded” does not mean “current artifact valid”.
     Stale artifacts remain usable; the user decides whether to redo them. Never automatically delete or regenerate paid artifacts
   - For product-promotion storyboard-to-video, review product-storyboard fidelity before incurring video costs; general short films have no product storyboards and no such checkpoint.
   - Reference-to-video generates self-contained units and skips storyboards; references resolve at execution time from body `@[名称]` mentions in first-mention order, using the same rules for products, characters, scenes, and props. Regenerate by explicit `unit_id` when the user is dissatisfied.

8. **Editing**: for TTS projects (nonempty `plan.status.artifacts.audio.missing_ids`), first follow `generate-narration-audio`
   and call `mcp__arcreel__generate_narration_audio({"script": target.script_filename})` to fill missing narration, omitting
   `segment_ids`; choosing TTS already authorizes this. This step completes as soon as an editing timeline exists; the workflow will not return here later to fill audio.
   Then call `mcp__arcreel__create_timeline({"from": "script", "episode": target.episode, "name": "完整版"})`
   to mechanically create a timeline from the script; the step completes once at least one timeline exists. Then use `edit-video`
   to edit it, rendering a final cut or exporting a Jianying draft only when requested. Server presentation results determine speech ownership and subtitle timing:
   **do not estimate subtitle timelines yourself or decide for the user whether TTS is necessary**. Stale artifacts remain usable; output neither clears nor
   overwrites old paid media.

## General short films (no products)

Empty `products` means a general short film. Skip product upload, selling points, and product sheets; enrich `brief` enough to express theme, emotion, visual style, and rhythm. Character, scene, and prop assets remain available normally.

## Shot duration constraints

Storyboard-to-video shot durations must be members of the model's `supported_durations`, queryable with `mcp__arcreel__get_video_capabilities`. Reference-to-video units use positive integer planned durations allowed by the script model; generation preflight then projects them to provider tiers. Correct invalid values first with `patch_episode_script`.

## Boundaries

- Storyboard ads use `shots[]` as the sole source of truth; reference ads use self-contained `video_units[]`.
- Reference-to-video units own their reference-syntax body, planned duration, generated assets, and planning state; refresh the plan after editing these fields.
- Use `patch_episode_script` for unit reordering and field repair, and `generate-video` for video generation.
