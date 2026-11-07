---
status: accepted
---

# Evicted work files remain present by recorded digest; fetch through shared entries before reading bytes

[ADR 0116](0116-remote-content-versions-saved-by-formal-commit-with-object-references.md) makes remote work files evictable caches, leaving references/manifests/currency/editing direct readers for later migration. Some consumers ask presence/identity, others need bytes for providers or image processing. Decide as follows.

**Presence/content identity**: absent local work files for remote versions (snapshot or selected formal positions) count as present with version-record `content_digest`.

- Centralize decisions in manifest project-directory adapters (`inspect_artifact` family) and `visual_file_digest` through `lib/material/work_files.py::material_file_digest`. Planning, currency, input assembly, reuse, and provider links share them.
- Existing files still hash/compare actual bytes; external edits remain stale/unusable.
- Derive remote work-file maps from history and `project.json` without version locks, cached by both stat identities. Submission retains existing stability rechecks; changed records change their observed digests.
- Byte snapshots/chunked bypass reads do not use this rule: only scripts/archive imports need them, not remote images.

**Read bytes**: consumers sending, snapshotting, or processing images use `local_work_files` before reading and register use, preventing cleanup during use.

- Fetch before input freezes (storyboards/sheets/derivatives), grid references, editing sources, staged/unstaged video references, splitting, and image viewing.
- Frozen/staged outputs are task-private copies; delayed providers see copies, never later replacements.

**Output location**: formal image tasks (storyboards/sheets/derivatives/edits through `run_formal_image_task`), grids, and splits use `formal_commit`, applying global storage settings to new versions.

## Considered Options

- **Fetch everything before judgment**: unchanged answers but workspace/status refresh downloads all remote images, defeating eviction.
- **Records-only presence without reading local files**: avoids hashes but misses external formal-file edits and weakens validation.
- **Per-consumer remote recognition**: scattered planning/currency/reference/projection sites drift or omit checks, disagreeing for one image.

## Consequences

- Missing formal work files still show current/stale in status/planning and fetch before generation/editing. Missing objects/connections fail on actual read with `material_remote_fetch_failed`/`material_object_connection_unavailable`.
- Evicted versions lack local stat/file facts. Only video/audio record them; both stay local and unaffected.
- Other upload/import entrances subsequently adopted `formal_commit` too. Unversioned end frames, character/product originals, and style references remain local (ADR 0116).
