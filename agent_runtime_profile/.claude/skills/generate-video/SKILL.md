---
name: generate-video
description: Generate videos for storyboards or self-contained video units. Use when the user requests video generation or regeneration; supports whole episodes, individual items, and selected batches.
---

# Generate video

## Routing

Have MCP tools read `project.json`, dispatch by `generation_mode` × `content_mode`, and validate the script skeleton:

| Generation mode × content type | Required skeleton | Dispatch | Output directory |
|---|---|---|---|
| `reference_video` × narration / drama / ad | `video_units[]` | `task_type="reference_video"` → `execute_reference_video_task` | `reference_videos/{unit_id}.mp4` |
| `storyboard` × narration | `segments[]` | `task_type="video"` → `execute_video_task` | `videos/scene_{segment_id}.mp4` |
| `storyboard` × drama | `scenes[]` | Same as above | `videos/scene_{scene_id}.mp4` |
| `storyboard` × ad | `shots[]` | Same as above | `videos/scene_{shot_id}.mp4` |

Stop enqueueing on a skeleton mismatch and regenerate the script for the project's generation mode. Reference-to-video directly consumes self-contained `video_units[]`, skipping storyboard images.

### Reference-to-video

Treat each `video_units[]` entry as an independent generation call:

- Construct a unified-reference-syntax prompt from the unit body (`text`).
- At execution time, resolve reference images from body `@[名称]` mentions in first-mention order, without special sorting; use the asset image if available, otherwise all original images for that asset.
- Let generation preflight project the unit's planned duration onto the provider's request tiers.
- Stop the unit on `needs_replan` or speech ownership problems, and fix planning content first.
- Whole-episode generation reuses only the current clip explicitly referenced by `generated_assets.video_clip`; a same-named orphan file does not mean the unit is complete.

Let project configuration, script models, and video capabilities determine aspect ratio, duration, and reference-image limits; do not specify a second set of values in call parameters.

## Tool calls

Enqueue using MCP tools; this skill provides no Python or Shell generation scripts.

| Operation | Tool |
|------|------|
| Generate the whole episode (default) | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "episode", "episode_id": 1}})` |
| Single storyboard | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "scene", "ids": ["E1S01"]}})` |
| Selected batch | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "selected", "ids": ["E1S01", "E1S05", "E1S10"]}})` |
| All pending | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "all"}})` |

Interpret `target.ids` as storyboard IDs for storyboard-to-video and as `unit_id` for reference-to-video. Whole-episode `target.episode_id` is the script's episode ID (the same number as in `episode_{集 ID}.json`, taken from plan `target.episode`), not its ordinal position.

### Regenerate named video units

For reference-to-video, pass `video_units[].unit_id`:

| Operation | Tool |
|------|------|
| Regenerate one unit | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "scene", "ids": ["E1U2"]}, "force": true})` |
| Regenerate multiple units | `mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "selected", "ids": ["E1U2", "E1U3"]}, "force": true})` |

One call enqueues work and returns a durable batch; call `get_generation_batch` according to returned `poll_after_seconds` until `done: true`, then process results:

- Treat explicit naming as forced regeneration, replacing existing clips.
- Do not automatically force regeneration while a task is in flight; wait for and read its batch results to avoid paying again for a just-completed target.
- Generate only named self-contained units present in the script; unmatched IDs are `blocked` with `generation_unit_not_found`.
- After a call interruption, query the durable batch; resend only unsuccessful IDs with `selected` and `force: false`; completed items become `skipped`.
- Results return per ID as `requested / succeeded / failed / blocked`;
  see `.claude/references/generation-results.md` for structure and problem codes.

### Switch the current version

A unit's current version determines which version previews, editing, and exports use. To choose a better version, call
`mcp__arcreel__select_video_version({"unit_id": "E1S01", "version": 2})`; it takes effect immediately, without user confirmation or cost:

- After first-round review, switch if another candidate for a unit is better.
- During acceptance after regeneration, switch back if the new version is worse.
- If a version number does not exist, select again from returned `params.available_versions`.

### Video and narration

Video requests depend only on the script: always request a tier based on unit planned duration; admission, quotes, and recovery are independent of the project's narration delivery method.
Projects without TTS still generate and recover videos normally. Generate narration audio separately during editing (`generate-narration-audio`).
`generate_videos` has no `narration_delivery` parameter; supplying it is rejected.

### Whole-batch admission and duration-tier confirmation

Video batch requests are **all or nothing**: `admitted` enqueues the entire batch; `blocked` or `confirmation_required`
**enqueues no tasks**. Web and Agent use the same admission and request-selection semantics, with no relaxed Agent-only path.

Reference-to-video selects effective tiers from each unit's reference state, projecting planned duration onto model-supported request tiers. If requested and planned duration differ,
preflight returns `reference_duration_confirmation_required`. Explain each affected tier's units, planned seconds, requested seconds,
and whether duration increases or decreases. After confirmation, use `confirmed_request_durations` (tiers keyed by unit_id) to **resubmit the original target set as one batch**:

```text
mcp__arcreel__generate_videos({"script": "episode_1.json", "target": {"scope": "episode", "episode_id": 1},
                               "confirmed_request_durations": {"E1U1": 8}})
```

To obtain user cost confirmation before submission (such as the `edit-video` checklist), preflight with `"preview": true`:
it performs the same admission without enqueueing and returns per-unit estimated costs and tier changes. After confirmation, submit with
`confirmed_request_durations` copied unchanged from preflight to avoid another tier confirmation.

When rejected, report each unit's `unit_id`, `problem.code`, reason, and `problem.action`; otherwise eligible units carry
`generation_batch_admission_withheld`, whose `blocked_unit_ids` identifies the blocking units. Explain that causal relationship accurately.
**Do not split the batch to run the passing half first**: doing so bypasses all-or-nothing admission and can resubmit units already paid for.
If capabilities cannot be resolved, treat the tool error as a blocker and fix the model capability declaration first.

### Interpret and report results

`task_state` (queue task), `provider_checkpoint` (provider submission), `artifact_status` (artifact
current / stale / missing / blocked), and workflow step status are independent; **report them separately**: “task succeeded” does not mean
“current artifact is valid”. A true `provider_checkpoint.submitted` means the provider likely already charged; task
`interrupted` means no provider verdict exists. Always follow `problem.action`; this usually hands control back to
`wait_for_task` (the task may still be running and complete normally), rather than changing it to `retry` yourself.

Stale artifacts remain previewable, exportable, and usable in final cuts; the server reuses them without automatic regeneration. The user explicitly decides whether to redo them.
Do not automatically delete, overwrite, or regenerate paid artifacts or historical versions.

## Workflow

1. Load the project and script; confirm the skeleton matches the generation mode.
2. For storyboard-to-video, confirm storyboard images are available; for reference-to-video, confirm nonempty unit bodies and valid planned durations.
3. Enqueue through MCP tools, handling admission rejection and duration-tier confirmation.
4. Present results, and regenerate unsatisfactory storyboards or units explicitly selected by the user.
5. Use tool-written `generated_assets.video_clip` as the clip's ownership record.

## Prompt construction

Let MCP tools construct prompts according to generation mode:

- Storyboard-to-video reads `image_prompt`, `video_prompt`, and storyboard images.
- Reference-to-video reads the unit body (`text`) and planned duration.
- Narration/commentary storyboard-to-video does not include `novel_text` in video prompts; narration uses a separate audio workflow.
- Audio switches, character speech ownership, and negative-prompt rules apply automatically.

## Pre-generation checks

Check according to the project's generation mode:

- storyboard: every target storyboard has an available image and executable action and speech.
- Reference-to-video: every target unit has a nonempty body, valid planned duration, a single speech owner, and is not marked `needs_replan`.
- reference: at execution time, the server resolves references from body `@[名称]` mentions in first-mention order; unregistered mentions produce warnings without blocking enqueueing. Let the server truncate to `max_reference_images`.
- reference: the output path is `reference_videos/{unit_id}.mp4`.
