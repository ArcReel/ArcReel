# Workflow plan contract

`mcp__arcreel__get_workflow_plan` is the **sole authoritative orchestration entry point**. It returns a read-only plan: current content,
whether each AI operation may execute now (admission), ordered steps, active tasks, whole-batch video admission, and a suggested next action.

**Do not create another step table in the profile expanded by content type or generation mode.** Applicability, order, and current position across the six combinations (narration /
drama / ad × storyboard / reference_video) are entirely expressed by
plan `steps[]`; the Agent only executes controlled actions returned by the plan.

## Query

```text
mcp__arcreel__get_workflow_plan({
  "episode_id": X,                               // Optional: specified episode's episode ID
  "confirmed_request_durations": {"E1U1": 8}    // Optional: user-confirmed per-unit request tiers (keys are unit IDs)
})
```

Both fields belong only to **this query** and are not persisted. Include still-valid choices unchanged on every subsequent
query; omission withdraws them. Narration delivery is project configuration, read directly by the plan, with no query field.

Call on workflow entry, on “continue / next step / check progress”, and **after every tool or subagent completes**.
`Read` / `Glob` only retrieves content needed for the already-chosen action; never build another state machine. Do not override server conclusions from empty asset buckets,
filenames, old-file existence, or conversational memory.

## Named operations versus continue

Admission belongs to AI operations, not pipeline positions: an operation is executable when its own inputs are valid, regardless of whether it is
the recommended next action. Distinguish two request types:

- **The user names an operation** (such as generating episode 2's script plan or authoring prompts): query the plan and inspect
  `plan.status.operations[操作]`. Execute when `state == "admitted"`, even if `next_action` points elsewhere;
  for `refused`, relay `reason` (what is missing and how to supply it) without executing; for `not_applicable`, explain that this project
  has no such operation. `operations` describes only listed operations; generation admission remains determined by each entry point
  (ADR 0073); relay that entry point's rejection reason.
  Tools share the same decision: rejection returns `operation_not_admitted`, with `params.reason` matching the plan's reason code.
- **The user says “continue / next step”**: execute `next_action`. Nonempty `next_alternatives` means a **branch**
  (such as manual script writing versus supplying episode source for AI planning): explain `next_action` and alternatives,
  let the user choose, then execute; never choose for them.

## Read the plan

| Field | Meaning |
|---|---|
| `steps[]` | Ordered steps. `id` is a stable step name; `state` ∈ `completed` / `ready` / `active` / `blocked` / `pending` / `skipped`; `required=false` means inapplicable for this project's mode combination |
| `steps[].action` | This step's controlled action (possibly null) |
| `steps[].artifacts` | Step artifact freshness snapshot: ID buckets `current_ids` / `stale_ids` / `missing_ids`, plus aggregate `state` (`current` / `stale` / `partial` / `missing` / `blocked` / `not_applicable`). `blocked` is aggregate only; there is **no** per-ID blocked bucket |
| `steps[].tasks[]` | Queued/running tasks for this step, each with `unit_id`, `task_id`, `status`, and `provider_checkpoint` (only `submitted`). In-flight tasks represent progress, not problems |
| `steps[].admission` | Whole-batch admission for the video step (below) |
| `steps[].problems[]` | Per-step problems with stable `code`, closed-set `action`, owning `unit_id`, and copy parameters `params`; each appears once in its owning step. Per-unit video admission problems appear only in `steps[].admission.units[]` |
| `problems[]` | Project-level problems belonging to no step (currently only data migration failure), without duplicating step problems |
| `blockers[]` | Entire project unavailable (migration failure, unreadable project.json or artifact manifest, invalid content type or generation mode), with `code` / `path` / `reason` |
| `status.issues[]` | Content data issues (unreadable scripts, invalid fields, corrupted asset definitions, etc.), also with `code` / `path` / `reason`; block only operations dependent on that content |
| `status.content` | Current content: episode count, whole/episode source presence, target episode draft, formal script (`present` / `absent` / `invalid`) and entry count, entries awaiting authoring, units needing replanning, referenced assets lacking images; true `episode_plan_stale` means this episode's plan is stale and its script plan needs rebuilding |
| `status.operations` | Structural admission for listed AI operations; keyed by action, with `state` (`admitted` / `refused` / `not_applicable`) and stable `reason` (see “Named operations versus continue”) |
| `next_action` | Suggested next action. Route here on “continue”; never pick an earlier `steps[]` action yourself to run ahead |
| `next_alternatives[]` | Branch alternatives alongside `next_action`; when nonempty, the user chooses |

When `blockers` is nonempty, show blockers and **stop all mutations**. `next_action.type == "none"` with
`edit` marked `completed` in `steps` means the workflow is complete: every episode in query scope has at least one editing timeline. For other
`next_action.type == "none"` cases with empty `blockers`, explain `status.issues` and `status.content`
(such as unreadable episode scripts, stale episode planning awaiting rebuild, or unreadable timelines); explicitly named operations whose admission passes can still execute.

## Data migration failure: repair → retry

An incomplete data migration (including artifact backfill) blocks the entire project. The plan then returns only:

- One `blockers[]` entry, `code == "project_migration_failed"`, whose `reason` is the original failure text;
- One `problems[]` entry with the same code and `action == "retry_project_migration"`,
  with `params.details[]` identifying `episode` (episode ID) / `file` / `violation`: which episode, which file, which contract violation;
- `next_action.type == "retry_project_migration"`, with `args.details` as above.

All generation and formal-write tools return the same problem in this state, doing nothing and incurring no charge. The project remains
readable: `Read` scripts and inspect existing canvas images/videos normally.

Handling order:

1. Explain every `details[]` entry: which episode (resolve title and broadcast position through project details `episodes[]`), which file, and which violation;
   never compress this to “migration failed”.
2. The only writable tools while blocked are `mcp__arcreel__patch_project`, `mcp__arcreel__patch_episode_meta`,
   `mcp__arcreel__rename_asset`, and `mcp__arcreel__merge_asset`; `mcp__arcreel__patch_episode_script` and all generation
   tools are rejected. Repair what these tools can reach according to details; accurately report unreachable issues (such as script-body violations) under step 4.
   **There is no raw-file-write path**; do not `Edit` formal scripts directly.
3. Call `mcp__arcreel__retry_project_migration` to rerun the migration chain. It is idempotent; repeating it causes no loss.
4. On success, the tool returns a new production plan and unblocks the project; continue with `next_action`. Failure returns new structured
   details; return to step 1. If repair is impossible, explain the blocker honestly instead of repeatedly retrying without changes.

When the user clicks “Retry migration” on the Web project page, request text enters the conversation input for the user to send themselves. On receipt,
follow this path.

## Controlled action table

Route by `next_action.type`, passing `target.episode`, `next_action.args`, and `requested_ids` into the action.
The plan always serializes `requested_ids`: a nonempty array means explicit IDs; `[]` means none named. For optional tool
ID parameters, pass the former and **omit the parameter** for the latter; never forward `[]` unchanged (explicit empty arrays are invalid tool inputs).
`plan.status.target` supplies `episode` (target episode ID), `script`, `script_filename`, and `source`. Tool
`episode_id` always comes from `next_action.args.episode_id` or `target.episode`. The two script fields are not interchangeable:
`script` is project-root-relative (`scripts/episode_{集 ID}.json`), used with Read for script content;
`script_filename` is the bare filename without `scripts/`, used by the `script` parameter of all `mcp__arcreel__*` tools.

| `next_action.type` | Execution entry point |
|---|---|
| `collect_project_input` | Guide Web input completion (upload whole source; for ads, enter creative brief or register products) |
| `create_episode` | Guide the user to create an episode in Web |
| `draft_selling_points` | Draft on user request and write through `mcp__arcreel__patch_project` (ad) |
| `reset_episode_planning` | `mcp__arcreel__reset_episode_planning`, forwarding `next_action.args` unchanged (no `episode_id` means full reset) |
| `plan_episodes` | `mcp__arcreel__plan_episodes` |
| `resolve_draft` | Target episode has a draft (`args.draft_kind`): true `args.needs_repair` means a repair draft from a failed write; explain and rerun the corresponding operation. False means an Agent-editable draft; finish through its skill |
| `prepare_script_plan` | Dispatch the subagent named by `next_action.args.preprocessor` |
| `start_blank_script` | Guide the user to “Start from blank” on the Web episode page and handwrite formal script entries on the timeline |
| `provide_episode_source` | Guide the user to supply episode source (`source/episode_{集 ID}.txt`), enabling AI script planning |
| `confirm_script_plan` | `mcp__arcreel__confirm_script_review` |
| `generate_script` | Dispatch `create-episode-script` (for ads call `mcp__arcreel__generate_episode_script` directly) |
| `add_script_items` | Formal script empty: guide the user to add entries in Web, or insert through `mcp__arcreel__patch_episode_script` |
| `author_prompts` | Formal script has entries awaiting authoring, listed by `requested_ids`: call `mcp__arcreel__generate_episode_script` without `entry_ids` to author all pending entries (see generate-script) |
| `generate_asset_sheets` | Dispatch `generate-assets`, calling `mcp__arcreel__generate_assets({"episode_id": <args.episode_id>})` once for episode-referenced assets missing images. For assets `blocked` by missing description (`fix_input`), write source-grounded descriptions via `mcp__arcreel__patch_project` and repeat the call; without source evidence, confirm descriptions or ask for Web image uploads. For derivative `generation_dependency_failed` / `derivative_owner_sheet_missing`, resolve base images first |
| `generate_storyboards` | Dispatch `generate-assets`, call `mcp__arcreel__generate_storyboards` with `segment_ids` |
| `generate_grid` | Dispatch `generate-assets`, call `mcp__arcreel__generate_grid` without `scene_ids` (missing-only); after composite review and user consent, call `mcp__arcreel__split_grids` |
| `repair_video_units` | `mcp__arcreel__get_episode_script` + one `mcp__arcreel__patch_episode_script` for all changes, then explicitly regenerate |
| `patch_episode_script` | Plan-injected: `next_action.args` supplies `base_revision` and per-entry `problems`; fix all in one batch |
| `confirm_request_duration` | Plan-injected: see “Whole-batch admission” |
| `generate_videos` | Video generation tool (see `generate-video`) |
| `wait_for_task` | Plan-injected: active tasks exist; enqueue nothing new, wait and recheck the plan |
| `create_edit_timeline` | Episode videos complete, no editing timeline: `mcp__arcreel__create_timeline` (`episode` from `next_action.args.episode_id`, `from: "script"`) mechanically creates one from the script. “Editing” completes once at least one exists; then edit using `edit-video` |
| `retry_project_migration` | Incomplete project migration: repair per details, then `mcp__arcreel__retry_project_migration` (see “Data migration failure”) |
| `none` | Show nonempty `blockers` and stop mutations; `edit` marked `completed` in `steps` means completion; otherwise explain `status.issues` and `status.content` |

`next_action.args.preprocessor` is the authoritative script-planning subagent name. **Do not infer it yourself from content type ×
`generation_mode`**: the server derives it from the same rule table; repeating that inference creates a second source of truth.

### Per-problem actions returned on batch rejection

When whole-batch video admission rejects, the plan returns the **first problem's `action`** as `next_action.type`,
with the complete admission conclusion in `next_action.args.admission`. Thus actions below may also appear beyond the table above; they share
`problems[].action`'s closed set. Always read each unit's own `problems[].action` for handling, never guess from
`code`:

| `next_action.type` | Execution entry point |
|---|---|
| `fix_input` | Invalid script/declaration: locate via `admission.units[].problems[].detail`, correct with `mcp__arcreel__patch_episode_script`, then recheck |
| `replan_unit` | Unit needs replanning: follow the `repair_video_units` row |
| `generate_dependency` | Missing upstream artifacts (asset/reference images): fill dependencies, then recheck |
| `configure_provider` | Current provider or tier does not support the request: identify the setting to change; **retrying the same request yields the same rejection** |
| `repair_artifact_state` | Unreadable artifact state: report as a separate gap, never treat as missing and regenerate |
| `retry` | Safe to resend the same request |
| `retry_artifact_download` | Provider generated the artifact but retrieval failed: use `POST /tasks/{id}/retry-download` to resume retrieval; **never resubmit generation**, which creates another paid task |

For `retry` and `configure_provider`, explain the reason before enqueueing a new batch;
obtain explicit user consent for any action incurring new charges.

## Narration delivery

Narration delivery (`post_production` post-production voiceover / `use_tts` TTS voiceover) is project configuration, changed in project settings; the workflow
has no delivery-choice step. Video requests are independent: requested tiers, admission, and costs are identical under both methods.

Narration audio is not a video-generation prerequisite. `plan.status.artifacts.audio` reports missing/stale narration
only for TTS projects; post-production voiceover projects return `not_applicable`. TTS projects fill missing audio during the first automatic editing round;
selecting TTS already authorizes this, with no separate confirmation. Users may also request `generate-narration-audio` at any time.
Regenerate stale voiceover only on a user resynthesis request.

## Whole-batch admission

Video batch requests are **all or nothing**: `steps[].admission.decision` of `admitted` enqueues the whole batch;
`blocked` or `confirmation_required` **enqueues no tasks**. Web and Agent share admission and
request-selection semantics (explicit video IDs need additional `force: true` for regeneration / omission fills only gaps / empty arrays are invalid); there is no relaxed Agent-only path.

When `decision != "admitted"`:

- Report `admission.units[]` per unit: `unit_id`, whether `admitted`, `problems[].code`,
  and `problems[].action` (next action). Eligible units carry `generation_batch_admission_withheld`,
  whose `blocked_unit_ids` identifies blockers. Explain this causality accurately, rather than implying their own inputs are invalid.
- For `decision == "confirmation_required"`, `admission.confirmation.tiers[]` gives units and costs grouped by requested tier.
  After user confirmation, fill confirmed tiers into `confirmed_request_durations` and requery; include the same `confirmed_request_durations` when resubmitting `generate_videos`.
- **Never split the batch to “run the passing half first”.** That bypasses all-or-nothing admission and resubmits already-paid units
  after dependencies are filled. Fix rejected units and retry the whole batch.

## Report four status axes separately

The four plan axes are `steps[].state` (step progress), `steps[].tasks[].status` (queue tasks),
`steps[].tasks[].provider_checkpoint` (provider submission), and `steps[].artifacts`
(`current_ids` / `stale_ids` / `missing_ids` plus aggregate `state`). They are independent: report separately,
never translate one into another. Interpretations and meanings are in
[generation-results.md](generation-results.md).

## Stale artifacts and history

- **Stale artifacts remain previewable, exportable, and usable in final cuts**; the server reuses them without automatic regeneration.
- **The user explicitly decides** whether to redo them. The Agent must not automatically delete, overwrite, or regenerate paid artifacts, nor explicitly regenerate merely because
  they “look old”. Video regeneration requires both explicit IDs and `force: true`, and always incurs charges.
- Report unreadable artifact state (`blocked`) as a separate gap; never treat it as missing and regenerate, turning corruption
  into a duplicate charge.
- The server resumes interrupted tasks through their original request, never resubmitting requests already finalized at the provider.

See [generation-results.md](generation-results.md) for per-ID result structure, selection semantics, and problem codes.
