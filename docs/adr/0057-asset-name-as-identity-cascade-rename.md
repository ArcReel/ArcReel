---
status: accepted
---

# Assets use name as identity; renaming cascades rewrites instead of introducing stable IDs

Assets (character/scene/prop/product) use name as their unique identifier, and scripts, prompt rendering, and `@[名称]` mentions all use proper names. Scripts are documents read and written by LLMs, so directly readable proper names are central to usability. Renaming is therefore defined as an atomic cascading transaction: update together the asset-bucket key, references in every episode script (reference arrays, speaker, mention), associated files named after the asset and their path fields (the invariant "file stem = asset name"), and reference-image names and paths frozen in grid records (the basis of grids and sliced storyboard images is replayed from these; see "Storyboard/grid bases exclude asset names (revision)" in `docs/adr/0062`).

## Explicitly rejected

- **Introducing stable asset IDs and storing IDs in script references**: renaming would become a display-field change without cascades, but would require full script-schema and existing-project migrations, plus dereferencing in every layer of LLM script generation, mentions, and prompt rendering, damaging proper-name readability. Rejected: IDs spread complexity across the frequent generation/rendering paths, while cascades concentrate it in infrequent renames.

## Consequences

- Reject a target name that conflicts with an existing asset of the same type (in NFC coordinates); merging has different semantics and is not performed by renaming.
- The global asset library and projects have a snapshot-copy relationship; renames do not propagate between them.
- Renaming does not exclude ongoing generation tasks: a task running at the instant of the rename may produce output under the old name or fail; rerun it (in a single-user deployment, the occurrence rate is disproportionate to the cost of protection).
