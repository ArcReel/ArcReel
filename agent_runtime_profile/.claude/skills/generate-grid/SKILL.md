---
name: generate-grid
description: Generate storyboard grids when users request grids, grid images, or storyboards using grid assembly. Automatically group by segment_break, choose optimal grid sizes, and generate composites of chained transition frames; split into shot first frames only after user review and agreement.
---

# Generate storyboard grids

Generate storyboard grids for projects with grid assembly enabled. Automatically group by segment_break and generate one composite per group (split groups above the per-grid panel limit across images; an undersized final image uses a smaller tier with placeholder panels).

Grid generation has two stages, separated by user review:

1. `generate_grid` generates only composites and records versions, writing no storyboard images.
2. The user reviews composites in the Web grid panel (regenerate, replace by upload, or roll back). **Only after explicit agreement to split**, call `split_grids`: it overwrites all covered storyboard images, retaining old images in version history for rollback. Do not split automatically after generation.

Split storyboard images still use i2v with the same input contract as individually generated storyboard images.

## Prerequisites

- Project `generation_mode` is `"storyboard"` and `grid_storyboard` is `true` (the user toggles grid assembly in Web settings; Agents cannot change it after project creation)
- The script exists (scripts/episode_N.json)
- Character/scene/prop sheets (generated ones become references; without any, generation degrades to text-only and visual consistency worsens significantly)

## Tool calls

| Operation | Tool |
|------|------|
| Fill episode gaps | `mcp__arcreel__generate_grid({"script": "episode_1.json"})` |
| Redo grids containing specified shots | `mcp__arcreel__generate_grid({"script": "episode_1.json", "scene_ids": ["E1S01", "E1S02", "E1S03"]})` |
| Preview plan and existing grid state | `mcp__arcreel__generate_grid({"script": "episode_1.json", "list_only": true})` |
| Split after user agreement | `mcp__arcreel__split_grids({"grid_ids": ["grid_a1b2c3d4e5f6"]})` |

- Without `scene_ids`, fill gaps only: reuse groups with complete storyboard images, do not regenerate ready unsplit grids, and reuse in-flight tasks, without duplicate charges.
- `scene_ids` requests a redo: grids containing these shots regenerate and incur charges. Use only for explicit redo requests.
- Admission is whole-batch: any blocked shot (reference gaps, pending prompts, partial overlap with an in-flight grid, etc.) prevents tasks for the entire batch. Otherwise healthy shots carry `generation_batch_admission_withheld`; in-flight grids finish normally, with their shots carrying `generation_active_task_conflict` (`wait_for_task`). Repair all gaps, then retry as one submission.

Results return `requested / succeeded / failed / blocked` per **shot** ID: grouped shots share a grid, whose enqueue/task result projects to every covered shot. A successful shot’s `artifact_path` is its composite `grids/<grid_id>.png`; unsplit grids also appear in `grid_ids_awaiting_split`. See `.claude/references/generation-results.md` for structure.

`split_grids` returns per-grid results: split grids list written shots; generating grids, grids without composites, and absent grids are skipped with reasons.

## Output

- Composites are saved to `grids/{grid_id}.png` (`grid_id` already includes the `grid_` prefix, such as `grids/grid_a1b2c3d4e5f6.png`); every generation/upload records one grids version
- Frame-chain metadata is saved to `grids/{grid_id}.json` (`split_at` records the latest split using the current composite)
- Split cells are assigned by `next_scene_id` and persisted with normal storyboard filenames `storyboards/scene_{id}.png` (no first/last suffixes); versions are recorded before and after every panel overwrite
