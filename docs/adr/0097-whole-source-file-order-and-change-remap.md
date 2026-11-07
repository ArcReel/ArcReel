---
status: accepted
---

# Record whole-source file order explicitly; remap split episodes by file and text alignment after changes

A whole source can concatenate multiple files. Decision: the project explicitly records file order, adjustable by the creator; filenames do not determine sequence. Split episodes occupy continuous source ranges in that order, each endpoint recording "file + offset within file," possibly spanning files. Adding, deleting, replacing, editing, or reordering a file affects only split episodes whose source ranges touch it. When file content changes, align old and new text to map these episodes' boundaries to the new text, marking stale only episodes whose range text actually changed. Changing the whole source does not require replanning from episode 1. Most projects have a single source file; invalidating the entire book's split for one typo is disproportionate to the change.

## Rules

- Record the whole-source file list and order in the project. Append new uploads; creators can reorder them. Backfill existing projects once using their previous filename order.
- A file's identity is its list entry. Replacement preserves its position, filename, and source-file type; the type can change during replacement (see `docs/adr/0036`).
- Source ranges can span files; file boundaries are not episode boundaries. AI-planning windows read in file order and stop at source-file-type changes. A split episode takes the type of the file containing its range start; changing type does not change the ledger (see `docs/adr/0036`).
- When replacing or editing a file, align normalized old and new text and map each episode's source-range boundaries:
  - Episodes with unchanged text within their ranges only shift offsets, retaining status;
  - Episodes whose range text changes take the mapped ranges and become stale: existing artifacts, as well as titles, hooks, and episode outlines before artifacts exist, were based on old text;
  - Episodes whose entire range text is deleted follow split-removal handling: those with artifacts become episodes without source text and become stale; those without artifacts are removed directly (see `docs/adr/0032`).
  - New text inserted exactly at the boundary between two episodes belongs to the preceding episode. New text outside every split episode (before the first, after the last, or in empty gaps between them) remains unsplit source.
- Deleting a file is equivalent to deleting all its text, following the rules above: split episodes entirely within that file follow split-removal handling; episodes spanning into it retain the remaining parts and become stale.
- If a new file is inserted at a boundary between episodes or outside split episodes, it remains unsplit source without changing any episode. If inserted inside a cross-file episode, it belongs to that episode, which becomes stale.
- On reordering, split episodes in the moved file move together as a block in broadcast order, preserving source order between split episodes; episodes from other sources follow the story using the anchor rules in `docs/adr/0032`. Episodes whose content is unchanged do not become stale. An episode crossing a moved-file boundary retains only the part within its starting file that remains contiguous after reordering; the rest becomes unsplit source, and the episode becomes stale.
- Retain an internal normalized-text snapshot for every file that has registered split episodes. If a file changes outside the service and its fingerprint disagrees with the record, use the snapshot as old text for the same alignment/remapping process, notifying creators which episodes were affected.
- Agent writes to `source/` use the same service command as Web; direct writes through file tools or Bash are prohibited (see `docs/adr/0026`).

## Considered Options

- **Replan from episode 1 whenever a registered file changes**: a single-source-file project must redo the whole book even for a typo.
- **Release every split episode in a file when its content changes**: simple rules, but again equivalent to redoing the whole book for single-file projects.
- **Prevent source ranges from crossing files**: easiest impact calculation, but file boundaries become forced episode boundaries. Story arcs in chapter-split novels often span chapters, leaving only hard cuts at chapter ends.
- **Sort by filename**: `第10章.txt` precedes `第2章.txt`; uploading a file sorting before already split ranges forces restarting from episode 1.
- **Forbid reordering files containing split episodes**: reordering changes no episode's content, so forcing creators to release and re-split is unnecessary.
- **Store no snapshots; mark all split episodes in a file stale and block planning for out-of-service changes**: desktop users frequently edit files directly, and alignment/remapping must already be implemented; storing snapshots adds only one write occasion.

## Consequences

- Source fingerprints detect out-of-service changes and trigger alignment/remapping instead of rejecting planning.
- Alignment is approximate: after extensive rewriting, mapped ranges may not preserve complete story arcs. These episodes are already stale, leaving creators to review boundaries.
- Snapshots double source-text storage, negligible for text.
