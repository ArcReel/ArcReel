---
status: accepted
---

# Import actual provider content first; commit links with formal writes

Selecting existing provider images must prove ArcReel displays that content. Use `GetAsset` signed URLs, download originals, validate PNG/JPEG/WebP, save a new sheet/derivative version as finished manual content (`manual_upload`, same currency), then link that version. Failed retrieval rejects imports without ID-only placeholders or attaching IDs to unrelated images. Only `Active` image assets import; select available links with origin `import`, bypassing upload jobs/object-storage relays.

URLs are download-only: HTTPS/public addresses, revalidate every redirect, 30 MB response cap. Never return/store/log them, including diagnostics. httpx logs full URLs, so logging strips query before recording.

Formal files use file transactions while links use DB, with no shared transaction. Persist links in final-write commit hooks after sheet, pointer, version, and registration writes, using `EventLoopBridge` to commit on the loop. DB failures roll back the whole formal write and preserve old selection. Post-DB cleanup failures also roll back; missing version identity makes links unselectable and import best-effort deletes them. Reject image-then-link (failed linking leaves new images despite error) and link-then-image (brief unsaved references and IDs generated only by history).

## Consequences

Preserve validated original bytes, not lossy upload compression: changed content would no longer match provider originals.

All four sheets and character derivatives use existing manual-finished paths with shared link/rollback orchestration. `commit_manual_derivative_upload` hooks run last too. Canonical derivative paths are `.png` (ADR 0072): preserve PNG bytes; repackage JPEG/WebP decoded pixels as PNG without resizing/loss. Reject PNG-only imports because JPEG is common and repackaging preserves pixels. Reject other derivative extensions because paths/snapshots/planning all address `.png`, making scope disproportionate. Scope/acceptance: [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559).
