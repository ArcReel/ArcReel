---
status: accepted
---

# Generation entry requires registered references with asset images: block unregistered references and stop substituting character originals

> The freshness-assessment part of "fail at execution before provider submission, do not project expected basis during freshness assessment, and show preview as unavailable" in this ADR is superseded by `docs/adr/0098`: gaps still cause execution and preview rejection, while freshness assessment projects the basis normally and records missing items as absent.

Video generation is ArcReel's most expensive generation action. Two earlier rules let it proceed with incomplete reference images: unregistered `@[名称]` in the body produced only warnings and were silently discarded; registered characters without asset images fell back to originals. Tighten both at **generation entry** (individual generation and batch admission): block references to unregistered asset names (including references left after asset deletion); block characters, scenes, and props without asset images. Originals are inputs only for generating asset images and do not enter storyboard or video requests; derivatives without asset images are likewise blocked. Products retain ADR 0034: originals are acceptance anchors and directly enter downstream references.

**Editing and saving stay unchanged**: editor and save paths still only warn, so authors need not create assets before writing. ADR 0064's rationale "do not force authors to complete asset tables before writing" constrains writing, not spending; moving blocking to generation entry resolves the conflict. This decision replaces ADR 0064's passages "unregistered `@[名称]` only warns without blocking" and "use all originals when no asset image exists"; the rest stays unchanged.

**To use your own image directly as a character image**: upload it through the character asset-image entry, registering it as an asset image instead of relying on original-image fallback.

## Use the same basis at execution time (revision)

Entry admission still checks only registration and asset-image fields without IO. Between enqueueing and execution, however, assets can be deleted, renamed, or lose files. Previously, executors silently discarded missing images and still incurred charges, while the target-state planner abandoned projection and marked newly generated artifacts stale: contradictory conclusions from the same data. Revise this so execution, freshness assessment, and final-prompt preview for storyboard images, asset images, derived asset images, and grids share the same "generation inputs," classified by gap type:

- **Reject**: unregistered references (`reference_asset_unregistered`); registered characters, scenes, and props lacking asset images, or with unavailable asset images due to absent files or missing inventory registration (`reference_asset_missing`); a product declaring an unavailable asset image is rejected with the same code, without falling back to originals only (products with no asset image still use originals only under ADR 0034); declared originals whose files cannot be read (`asset_original_missing`). Fail at execution before provider submission, do not project expected basis during freshness assessment, and show preview as unavailable.
- **Omit**: an unavailable previous storyboard image. It is an optional continuity input derived by the system from storyboard order, rather than an author-declared reference.

Criterion: **reject unreadable author-declared inputs; omit unreadable optional system-derived inputs.** Missing originals were previously handled as "historical causes such as archive migrations do not block." Originals represent authors' explicit appearance intentions, and product originals are also ADR 0034's acceptance anchors. Generating without them charges users for results contrary to intent, while repair requires only re-uploading or clearing the field, so tighten this too. Give missing originals a separate code instead of merging into `reference_asset_missing` for the same reason this ADR separates unregistered assets from absent asset images: the repairs differ.

Determine unavailable asset images and missing originals only at execution, without adding IO to entry admission: both arise only with damaged data, and failure before charging already ensures "incomplete inputs do not incur spending."

## Consequences

- In existing projects, characters "with originals but without asset images" change from generatable to blocked on reference-to-video, with production status reporting "image not generated" at the asset step. Storyboard routes already accept only asset images and are unaffected.
- `resolve_reference_assets` retains "use the asset image if present, otherwise all originals" only for products; `derive_references_from_text`'s `missing` set becomes blocking evidence at generation entry, while editor previews still display warnings.
- Asset deletion no longer requires automatic rewriting of leftover references: they block the next generation and request repair, following the same path as names never registered.
- After the execution-time revision, existing characters and products missing originals cannot generate asset images until originals are re-uploaded or their fields cleared; storyboard images referencing derivatives no longer become stale immediately after generation.
