---
name: generate-storyboard
description: Generate storyboard images for shots. Use when the user asks to generate or preview storyboards, regenerate particular storyboard images, or when script shots lack images. Automatically maintains character and visual continuity.
---

# Generate storyboard images

Create storyboard images through the generation queue; aspect ratio is set automatically from content_mode.

> See `.claude/references/generation-modes.md` for generation-mode specifications.

## Tool calls

**Important: enqueue storyboard generation through the MCP tools below. This skill provides no Python/Shell scripts; do not use BASH to call `python .../scripts/*.py`.**

Enqueue through MCP tools:

| Operation | Tool |
|------|------|
| Submit all missing storyboard images | `mcp__arcreel__generate_storyboards({"script": "episode_1.json"})` |
| Regenerate a specified ID | `mcp__arcreel__generate_storyboards({"script": "episode_1.json", "segment_ids": ["E1S05"]})` |
| Regenerate multiple IDs | `mcp__arcreel__generate_storyboards({"script": "episode_1.json", "segment_ids": ["E1S01", "E1S02"]})` |

> **Selection rules**: `segment_ids` supports narration segment_id and drama scene_id; omitting it submits all missing items.
>
> **Dependency**: the generation worker must be online (independent image/video channels); it handles actual generation and rate control.

## Workflow

1. **Load project and script**: confirm every character has a `character_sheet` image
2. **Generate storyboard images**: MCP tools detect content_mode automatically and chain task dependencies by adjacency
3. **Review checkpoint**: dispatch image-review subagents following “Review checkpoint: image review”, then present reports and regeneration recommendations for user confirmation; the user may approve, request regeneration, or request editing
4. **Update script**: update `storyboard_image` paths and scene status

## Review checkpoint: image review

After generation, first have read-only `review-images` subagents inspect the images, then present conclusions for user confirmation. Review triggers no generation or editing and incurs no generation charges; subagent image-viewing model calls count toward Agent session usage normally. For grid projects, review after splitting, assigning cells, and writing storyboard images.

1. **Dispatch**: the main Agent dispatches (subagents cannot dispatch further subagents: when this skill runs in a generation subagent, return results to the main Agent for dispatch). Include only storyboards successfully generated this round, grouped by adjacency, at most 20 images per group; invoke one `review-images` call per group in the same message for parallel execution. Specify image type `storyboard`, script file (such as `scripts/episode_1.json`), this group's storyboard IDs, and user concerns.
2. **Present to the user**: consolidate reports, identifying usable images, recommended regeneration or edits and their reasons, and uncertain cases. Refer to a storyboard by its position in the episode (such as “storyboard 5”), not its ID. For regeneration, editing, or uncertain cases, use AskUserQuestion multi-select (`multiSelect: true`) to let the user select storyboards to address, with each option stating position, reason, and recommended treatment. Do not regenerate or edit before user confirmation.
3. **Execute**: handle selected storyboards according to “Editing versus regeneration” below, using storyboard IDs in tool calls. Afterward, optionally dispatch `review-images` again to accept the new versions.

When the user asks about particular storyboard images, inspect them directly with `mcp__arcreel__inspect_images` and answer; no subagent dispatch is needed.

## Review checkpoint: editing versus regeneration

- **Local changes only** (such as malformed hands, background clutter, or lighting deviations), with satisfactory composition/character consistency →
  Use `mcp__arcreel__edit_images({"resource_type": "storyboard", "script_file": "episode_1.json", "edits": [{"id": "E1S05", "instruction": "去掉背景里多余的路人"}]})`
  Refine the existing image without changing `image_prompt`; multiple storyboards can be submitted in a batch
- **Replace composition or character/reference-image relationships**, or change `image_prompt` itself → use
  `patch_episode_script` to change `image_prompt`, immediately followed by `generate_storyboards` to regenerate
- Editing does not update `image_prompt`; a later `generate_storyboards` redraws using the original `image_prompt`,
  and the edited result can only be recovered from version history

## Character consistency mechanism

MCP tools supply these reference images automatically; manual specification is unnecessary:
- **character_sheet**: asset images of characters appearing in the scene, maintaining appearance consistency
- **scene_sheet / prop_sheet**: asset images of scenes/props appearing in the shot
- **Product references (ad/short-film projects)**: nonempty storyboard `products_in_shot` automatically injects product references before all others (sheet + originals when a product sheet exists; direct originals otherwise). The declaration labels their positions as product references and requires an exact product match; image_prompt need not repeat product appearance
- **Previous storyboard image**: adjacent segments reference it by default, last in the reference list, for composition and color only
- Skip the previous storyboard reference when the segment is marked `segment_break=true`

The model receives references as an ordered image array; the prompt identifies them as “图N” (N is array position, with products first,
then character/scene/prop asset images, and the previous storyboard last):
- The generator inserts a `Reference_Images` type declaration between `Style` and `Scene`, for example
  `图1、图2为角色参考图；图3为场景参考图；图4为上一分镜图，只参考构图与色调。`; for a product storyboard:
  `图1为商品参考图，画面中的商品须与之完全一致；……`
- In `image_prompt.scene`, identify assets with `@[登记名]` (derivatives use `@[角色/衍生]`); the generator replaces mentions with the corresponding “图N” above;
  also put the name in the entry's reference fields (`characters_in_*` / `scenes` / `props` / `products_in_shot`); only these fields determine references, and `@[]` adds no extra images
- Unregistered `@[名称]` mentions or names absent from reference fields are sent unchanged; `warnings` from `generate_episode_script` / `patch_episode_script` list such references. Register them or add reference fields as instructed, then regenerate

## Prompt template

The generator renders the final prompt using project style, reference list, and entry `image_prompt` (inspect exact text with `get_prompt_preview`):

```
Style: [项目 style]
Reference_Images: 图1、图2为角色参考图；图3为场景参考图；图4为上一分镜图，只参考构图与色调。
Scene: [image_prompt.scene，其中 @[登记名] 已换成对应的图N]
Composition:
  shot_type: [image_prompt.composition.shot_type]
  lighting: [image_prompt.composition.lighting]
  ambiance: [image_prompt.composition.ambiance]
Avoid: 水印、多余文字、Logo
```

> Aspect ratio is set through API parameters, not written into the prompt.

## Pre-generation checks

- [ ] Every character has an approved character_sheet image
- [ ] Scene visual descriptions are complete
- [ ] Character actions are specified

## Error handling

See `.claude/references/generation-results.md` for result structure and per-ID problem codes.

- A single scene failure does not affect the batch; tools return per-ID `requested / succeeded / failed / blocked` results
- Decide whether to retry or first fix input using each item's `problem.code` and `problem.action`; do not guess from prose
- Omitting `segment_ids` fills only missing images; stale but usable old storyboard images are reused without automatic regeneration
- Explicitly regenerate retryable scenes with `mcp__arcreel__generate_storyboards({"script": "...", "segment_ids": [...]})`
