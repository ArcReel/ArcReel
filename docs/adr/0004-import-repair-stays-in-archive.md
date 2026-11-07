---
status: accepted
---

# Keep import repair in archive, outside shared saving; consolidate only leaked shape constants

> Missing-asset-definition rejection is superseded by `docs/adr/0098`: add undefined character/scene/prop placeholders and report them. Keeping import repair in archive remains valid.

`project_archive` rescues incomplete/disordered uploads through `versions/` recovery, layout/path guessing, filename indexes, and missing-definition rejection. An architecture review proposed `ProjectManager.restore_from_staging()` to own repairs and unify import/save guarantees. **Reject it**: this is import-specific I/O, not PM's installed-normalized-project domain. Import repair/rejection differs from `_write_script_unlocked` no-worse acceptance of legacy invalid scripts (ADR-0002). Combining them confuses contracts and adds about 580 lines to the 94-method PM god module targeted by candidate 6.

The actual domain leak in `project_archive` is duplicated shape constants:

- **Canonical resource paths** (`resource_type` to relative paths such as `characters/{id}.png`/`videos/scene_{id}.mp4`) were copied in `MediaGenerator.OUTPUT_PATTERNS`, `versions.py::_resolve_resource_path`, and `project_archive._canonical_resource_path`. All three consume one function.
- **content_mode-to-field dispatch** (`segments`/`segment_id` versus `scenes`/`scene_id`) duplicated model declarations in archive `if/else`. Consume `lib/script/script_skeleton`'s registry (`SKELETONS`/`resolve_declared_kind`/`resolve_kind_items`) instead of deriving again.

The `generated_assets` template already delegates to `PM.create_generated_assets`, not a copy, and needs no change.

## Consequences

- **Independent of candidate 6**: PM is unchanged and this can land without its decomposition, reversing the original review's assumed coupling.
- Shared saves remain the single script guard (ADRs 0002/0003); import is an intentional pre-install repair exception. This ADR prevents well-intentioned consolidation from erasing it.
- Canonical paths span media (storyboards/videos/grids/reference_videos) and character/scene/prop sheets, now in `lib/project/resource_paths.py` `_PATTERNS`, accessed through `resource_relative_path` by MediaGenerator, versions, and archive. `characters/refs/{name}.png` (reference_image) stays outside.
