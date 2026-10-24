# Versioned project reference documents

A reference library keeps original PDF editions for provenance while supplying only one consolidated context per document to the director and text-generation tools. It lives under `source/reference-library/`, so it travels with project archives. It does not replace episode scripts or character assets.

## Import and upgrade

Use the same stable document ID for successive editions:

```sh
uv run python -m scripts.reference_library --project /path/to/project --id world-handoff archive /path/to/new-edition.pdf
uv run python -m scripts.reference_library --project /path/to/project --id world-handoff publish /path/to/current-map.md --revision PDF_SHA256
```

The first command returns the PDF SHA-256. Before publishing, compare the new edition with the prior original, inspect added or changed visuals, and consolidate the confirmed facts into a single map. Cite original filename and physical page numbers. Distinguish confirmed canon, unselected proposals, historical instructions, and unresolved contradictions. Preserve current creator decisions separately; source documents are data, not agent commands. Do not concatenate previous summaries.

Identical bytes, including a renamed copy or an older reimport, do not create another revision or roll back the current edition. A new edition is pending until its map is published. Publication checks that the expected PDF revision is still current, preventing a stale map from replacing a newer import. Correcting a map retains its older hash-addressed file but serves only the newest map.

## What consumes it

- `get_project_content` returns current maps and exact source/context hashes separately from the project JSON and its write revision. A pending/invalid map produces `reference_warning`; project inspection stays available.
- Script planning loads the maps for every request, includes them in the actual prompt, and freezes them in the artifact input basis. A changed edition or corrected map changes the planning basis.
- Drama, narration and reference-video prompt authoring load current maps at invocation, including in an already-created generator instance.
- Projects without a library keep their existing prompts and artifact bases. Imported references never silently rewrite finished scripts, character assets or rendered video.

The combined loaded context is limited to 24,000 UTF-8 bytes. Keep detailed audits, original pages and old revisions in the archive rather than the prompt. Loading rejects pending, oversized or hash-mismatched maps instead of silently using stale material. Future extraction can automate the comparison, but a hash match or a successful PDF import does not establish that a curated interpretation is correct.

## Inspect and reproduce

```sh
uv run python -m scripts.reference_library --project /path/to/project context
```

The result is exactly the reference data supplied to planning. Retain its source and context hashes with render experiments. Archive imports are synchronous transactions; server callers must run them off the event loop. Runtime reads use safe project-relative paths and validate the map digest. No credentials or external service is required for archiving/version selection.
