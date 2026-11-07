# Generation-result contract

All generation tools (storyboard images, grids, videos, asset sheets, narration, image editing) return one structure.
Read structure, not prose: text is only its human-readable projection.

## Selection: explicit IDs or gap filling

| Arguments | Meaning |
|---|---|
| ID list (`target.ids` / `segment_ids` / `scene_ids` / `names` / `edits[].id`) | Explicit selection; video additionally requires `force: true` for forced redo, while other tools force by naming |
| No IDs | Missing-only: generate only units without artifacts |
| Empty `[]` | Invalid, not “all”; requests reject. Omit the parameter to fill gaps |

`generate_videos(target.scope="episode")` fills episode gaps and never forces existing clips;
redo named shots with `scene` / `selected` scope, explicit IDs, and `force: true`.

**Stale but usable old artifacts are reused, never automatically regenerated**; they remain viewable/exportable.
Redo by explicit IDs only when the user requests updates. Unreadable artifact state (blocked) is an independent gap,
never treated as missing for regeneration, which would turn corruption into duplicate charges.

## Results: per-ID accounting

```
requested = succeeded ∪ failed ∪ blocked   (mutually exclusive)
skipped                                    (reuse; outside requested)
```

- **succeeded**: completed
- **failed**: failed after queueing/execution (possibly charged); also includes targets whose enqueue failed,
  with `task_state` equal to `not_queued`, no task ever created, and no charge
- **blocked**: never reached queueing (free), such as missing dependencies, invalid requests, unreadable artifact state

If batch enqueue is interrupted, **already created tasks are not cancelled**: these admitted complete paid units
execute and produce outputs normally. Unreached targets report `failed` with an enqueue-interrupted problem; the next gap-fill
handles just those. Do not recommend rerunning the whole batch and paying again for units already running.

Each `failed` / `blocked` entry in `items` contains:

- `problem.code`: stable problem code
- `problem.action`: closed-set next action (`retry` / `fix_input` / `generate_dependency` /
  `replan_unit` / `configure_provider` / `confirm_request_duration` / …)
- `unit_id`, plus `artifact_key` / `artifact_path` when present

Grids account by **shot ID**: grouped shots share one composite, whose enqueue/task results project to
all covered shots. Grid generation produces only composites; successful `artifact_path` is `grids/<grid_id>.png`.
Storyboard images are written only after user review/agreement and splitting through `split_grids`.

**Decide next actions by `code` / `action`, never parse prose to determine retryability.**

## Read four state axes separately

Workflow-step state (`plan.steps[].state`), queue `task_state`,
`provider_checkpoint` (whether submitted), and `artifact_status` (current/stale/missing/blocked)
are independent. **Report separately; never translate one into another**:

- Task success ≠ artifact matches current basis
- Missing artifact ≠ task failure: it may never have queued (`blocked`, free)
- `provider_checkpoint.submitted` means submitted and likely charged; `task_state` equal to
  `interrupted` means no provider verdict. Blind retries may duplicate charges; always follow `problem.action`,
  usually `wait_for_task` because work may still finish normally. Do not independently change it to `retry`
- Artifact-persistence failure marks that ID `failed`, preserving old paid artifacts; never mark ready before the file is committed

Artifact history is another axis: `current` is selected, `stale` has changed basis but still exists, and history holds
other previously paid versions. Never automatically delete, overwrite, or regenerate paid artifacts/history; users explicitly decide redo.

Answer “is it done?” across these four axes, not a single “success”. See
[workflow-plan.md](workflow-plan.md) for the overall plan.
