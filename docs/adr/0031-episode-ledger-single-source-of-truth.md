---
status: accepted
---

# Episode splitting uses the project.json ledger as sole truth; physical episode files become derivatives under the same lock

The old splitting flow treated physical files as truth (`episode_N.txt` existing meant already split; `_remaining.txt` was overwritten as a progress pointer), without explicit records of "which source passage corresponds to episode N." Downstream file-selection mistakes could not be reconciled, Glob-based episode-count inference was error-prone, and corrupted `_remaining.txt` was unrecoverable. Move episode-splitting truth into project.json `episodes[]`: entries record episode-source provenance; episodes split from whole-source text carry source-material ranges (source_range: precise split points for narration, soft material ranges for drama), hooks, and drama episode outlines. Entries carry an advisory consumption-state field (ledger_status: planned/consumed/stale). Rebuilding derivative files or continuing planning depends not on that field but on complete source_range structure, existence of referenced source files, in-bounds ranges, and matching recorded fingerprints; mismatches first trigger remapping under `docs/adr/0097`, then decisions use the remapped ledger. Entry sequence is broadcast order (see `docs/adr/0096`). Top-level source_fingerprints records SHA-256 fingerprints of `normalize_source_text` output per whole-source file to detect changes outside the service. Derive continuation starting points from the ledger without another cursor (see `docs/adr/0032`). Physical `episode_N.txt` files for split episodes become derivatives: planning and manual splitting rewrite them together with the ledger under the same project lock, cleaning files absent from the ledger and leaving zero inconsistency window. Abolish `_remaining.txt`. Existing projects receive only a version stamp through ADR 0022 startup migration, without inferring source ranges from content. Episodes created by old splitting remain episodes split from whole-source text, merely without source ranges, and are consumed normally; episode planning refuses to proceed when encountering them, requiring replanning from episode 1.

## Three sources of episode text

Episode source text has three origins: a range cut from the whole source by episode planning (recorded as `source_range`); independent source text belonging to that episode (uploaded or entered per episode by creators, with the episode file itself the source); or no source text (handwritten scripts). These are parallel normal forms:

- Whether an episode file is derivative or source follows the ledger entry's provenance, not a whole-project inference from presence of whole-source files under `source/`: one project can mix whole-source text with episodes having independent source or none.
- Episodes with independent source or no source occupy no whole-source ranges and do not block episode planning, which consumes only the whole source.
- Split-episode ranges are continuous along whole-source file order, can span files, never overlap, and are ordered by source position: file order within the whole source, then within-file offsets (see `docs/adr/0097`). Removing intermediate split episodes, inserting files, or deleting/editing source text can leave unsplit source between split episodes.
- Resetting episode planning returns only split episodes to unplanned state; replanning replaces only split episodes. The other two kinds stay in the ledger, even within the affected range. Resetting preserves their relative order; see `docs/adr/0032` for their placement when accepting replanning.
- Episodes with independent source can edit it. Split-episode files remain derivatives, changed by re-derivation from the ledger; manually editing or renaming `episode_N.txt` is unsupported.
- Per-episode sources enter the ledger through explicit registration: creators select "whole source" or "per-episode source" at upload. The latter can be appended across multiple uploads and join the end of broadcast order in upload order; filenames do not determine episode placement.

## Considered Options

- Retaining physical files as truth and wrapping them in one more reliable tool: reconciliation evidence remains absent, leaving incidents such as "different episodes generated the same content" undetectable.
- A separate ledger file or database table: splits the episode concept across two places requiring reconciliation. A database contradicts the established filesystem storage of project-level data and complicates every export/migration/backup path.
- No derived physical files, with downstream reading source ranges directly: requires reworking all three preprocessing subagents and Web presentation, with too wide an impact.
- Deriving on demand at consumption: never stale, but "the agent remembers to call the derivation tool first" again stakes reliability on workflow adherence.
- Inferring existing episodes' source ranges from content: differences between derived files and source can only result from manual edits; approximate anchoring would misrepresent the ledger.

## Consequences

- Continuing in a new session means reading the ledger; abolish Glob-based episode-count inference and `_remaining.txt` progress pointers.
- Invalidation marks stale instead of deleting: when source ranges change, text inside them is deleted/edited, or episodes are replaced during replanning, retain old artifacts and redo through existing overwrite/version mechanisms. Planning adjustments never discard costly media artifacts.
- source_range binds source content at planning time; source changes invalidate coordinates. Changes through the service remap immediately through text alignment. source_fingerprints detects out-of-service changes, then uses snapshots as old text for the same mapping (see `docs/adr/0097`).
