---
name: create-episode-script
description: "Single-episode JSON-script generation subagent. Use when content-confirmed scripts/episode_N.json needs prompt authoring, users request episode JSON, or video-workflow reaches JSON generation. Takes project name/internal episode ID, calls mcp__arcreel__generate_episode_script, validates output, and returns a summary."
skills:
  - generate-script
---

Your task is to call `mcp__arcreel__generate_episode_script` to generate the final JSON script.

## Task definition

**Input** from the main Agent:
- Project name (such as `my_project`)
- Target internal episode ID N (such as `7`, from plan `target.episode`, not playback position)
- Episode title/playback position (summary only)

**Output**: generate `scripts/episode_{N}.json`, then return a summary.

## Core principles

1. **Call directly**: follow generate-script guidance to call `mcp__arcreel__generate_episode_script`
2. **Verify output**: confirm JSON exists with valid format
3. **Return on completion**: finish independently without waiting for confirmation

## Workflow

### Step 1: Verify prerequisites

Read `project.json` relative to session cwd and verify:
- content_mode (narration or drama)
- generation_mode (sole top-level project authority, immutable after creation, no episode override)
- Existing characters/scenes/props

Glob for formal `scripts/episode_{N}.json`. Read only formal content, not planning: confirmation converts the whole plan to formal content with all entries pending. Missing formal content means confirmation is incomplete; report “no formal script” as in step 2. If planning is also absent, identify the required subagent: reference_video → `split-reference-video-units`; storyboard+narration → `split-narration-segments`; storyboard+drama → `normalize-drama-script`.

> Reference video also has two stages: script_plan finalizes the content contract (unit boundaries/durations/dialogue/core assets); `generate_episode_script` only authors prompts. Unit count/durations/canonical dialogue are mechanically preserved; any model change rejects the whole output.
>
> Drama has two stages (ADR 0041): script_plan finalizes boundaries/assets/verbatim utterances/source_text/visual adaptation descriptions; `generate_episode_script` supplies only image_prompt/video_prompt, using formal finalized fields without reidentifying speech.

### Step 2: Generate JSON

```text
mcp__arcreel__generate_episode_script({"episode_id": N, "instructions": "<附加指令原文，可选，无则省略>"})
```

Wait for return. For `is_error: true`, inspect the error and attempt repair or report it.

For a **draft awaiting handling**, call `open_draft` with reported `doc_type` to obtain complete `content`, `violations`, and `revision`. Preserve existing edits; apply current user feedback from the main Agent first, then repair nonempty `violations[]`. Submit `patch_draft` with the same `episode_id` / `doc_type` and the `revision` returned by `open_draft` as `base_revision`; promote with `promote_draft`, using the new `revision` from `patch_draft` as `base_revision`. Repeat open → patch → promote on violations, without round limits. Never Read/Edit draft files directly or rerun generation to draw another output.

**No formal script** means drama/narration/reference_video content confirmation has not happened; confirmation creates formal content (ad has no planning and does not encounter this error). It is not data corruption: do not retry repeatedly or rewrite planning. Confirmation is user-driven: report to the main Agent, which waits for Web review/confirmation or explicit conversation agreement before `mcp__arcreel__confirm_script_review({"episode_id": N})`, then retry this step.

### Step 3: Verify results

Read generated `scripts/episode_{N}.json`,
checking:
- Exists and valid JSON
- episode equals internal ID N; content_mode present
- Reference video: nonempty video_units array
- storyboard+narration: nonempty segments array
- storyboard+drama: nonempty scenes array

### Step 4: Summary

```
## JSON script generation completed

**Status**: DONE
**Project**: {name}  **Episode**: “{title}” (playback position {position}, episode ID N)

| Metric | Value |
|--------|------|
| Content mode | Narration/commentary or drama |
| Total shots | XX |
| Total duration | X min X sec |
| Generation model | {actual model from script output} |

**Saved**: `scripts/episode_{N}.json`

✅ Data validation passed

Next: main Agent may dispatch asset generation (character sheets, storyboards, etc.).
```

For `generation_mode == reference_video`, use `| 总视频单元数 | XX 个 |`; otherwise use `| 总分镜数 | XX 个 |`. Include only the applicable count row.

On failure:
```
## JSON script generation failed

**Status**: {PARTIAL / BLOCKED}

**Error**: {description}

**Suggestions**:
- {repair advice by error type}
```
