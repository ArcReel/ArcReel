---
name: generate-storyboard
description: Generate storyboard images for script scenes. Use when the user says "generate storyboards", "preview scene visuals", wants to regenerate certain storyboard images, or when scenes in the script are missing storyboard images. Automatically maintains character and visual continuity.
---

# Generate Storyboard Images

Creates storyboard images through the generation queue; the aspect ratio is automatically set based on content_mode.

> For content mode specifications, see `.claude/references/content-modes.md`.

## Command-Line Usage

```bash
# Submit all missing storyboard images to the generation queue (automatically detects content_mode)
python .claude/skills/generate-storyboard/scripts/generate_storyboard.py script.json

# Regenerate for a single scene
python .claude/skills/generate-storyboard/scripts/generate_storyboard.py script.json --scene E1S05

# Regenerate for multiple scenes
python .claude/skills/generate-storyboard/scripts/generate_storyboard.py script.json --scene-ids E1S01 E1S02
```

> `--scene-ids` and `--segment-ids` are synonym aliases (the latter is the conventional name in narration mode); they have the same effect. `--scene-ids` is used consistently below.

> **Selection rules**: `--scene` regenerates one; `--scene-ids` regenerates multiple; if neither is provided, all missing items are submitted.

> **Note**: the script requires the generation worker to be online; the worker is responsible for actual image generation and rate control.

## Workflow

1. **Load project and script** — confirm all characters have `character_sheet` images
2. **Generate storyboard images** — the script auto-detects content_mode and chains dependency tasks based on adjacency
3. **Review checkpoint** — display each storyboard image; the user can approve or request regeneration
4. **Update script** — update the `storyboard_image` path and scene status

## Character Consistency Mechanism

The script automatically handles the following reference images without manual specification:
- **character_sheet**: design sheets for characters appearing in the scene, maintaining consistent appearance
- **clue_sheet**: design sheets for clues appearing in the scene
- **Previous storyboard image**: adjacent segments default to referencing this, improving visual continuity
- When a segment is marked `segment_break=true`, the previous storyboard image reference is skipped

## Prompt Template

The script reads the following fields from the script JSON to build the prompt:

```
Storyboard image for scene [scene_id/segment_id]:

- Visual description: [visual.description]
- Shot composition: [visual.shot_type]
- Camera movement start: [visual.camera_movement]
- Lighting conditions: [visual.lighting]
- Visual atmosphere: [visual.mood]
- Characters: [characters_in_scene]
- Action: [action]

Style requirement: cinematic storyboard style, set according to the project style.
Characters must exactly match the provided character reference images.
```

> The aspect ratio is set via API parameters and is not written into the prompt.

## Pre-Generation Checklist

- [ ] All characters have approved character_sheet images
- [ ] Scene visual descriptions are complete
- [ ] Character actions are specified

## Error Handling

- A single scene failure does not affect the batch; record the failed scene and continue
- Summarize all failed scenes and reasons after generation ends
- Supports incremental generation (skips scenes with existing images)
- Use `--scene-ids` to regenerate failed scenes
