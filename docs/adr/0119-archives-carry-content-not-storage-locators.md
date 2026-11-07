---
status: accepted
---

# Archives carry image content and usable provider-material links, not storage locations; imports resave under target-instance global settings

[ADR 0116](0116-remote-content-versions-saved-by-formal-commit-with-object-references.md) records write-time object-storage connections, buckets, and keys on remote content versions; local files are cleanable working files. Archives must import independently on other instances, so they must carry content itself. Decisions follow.

**Export**: current-version and full archives retain existing scope semantics. First retrieve working files for included remote content versions (formal files and in-scope version snapshots), registering use during packaging so cleanup cannot remove them. Verify retrieval by digest; after packaging, verify every remote version snapshot in the snapshot is present and matches its digest. Any failure returns an explicit export error without an archive. Strip `storage` from packaged `versions.json`; archives contain no source-instance connection IDs, buckets, keys, signed URLs, or credentials.

**Import**: strip `storage` from every packaged version record, never reading target-instance buckets through packaged locations. If target global `material_storage_target` is local, imported content versions are local. If object storage, after choosing the target project name and before installation, save staged image versions through formal commits as target-instance formal objects, then record locations.

- Backfill identity and digests for records predating content-version identity. Versions whose snapshots disagree with recorded digests remain local as before.
- Current-version-only archives remove generated-image version records (asset, derivative, storyboard, grid), carrying only current formal files. First register content versions for these images, then save as above. Local imports do not register them, preserving current-version-only scope semantics. End frames bypass storage selection and are not registered.
- Install nothing on save failure. Settlement compensation deletes newly created objects without any version references.
- Settlement reads the installed project directory. If overwrite import fails and restores the old project unchanged, settle references against its version history.

Synchronous import in worker threads enters formal commits through `formal_commit_on`: dispatch entry/exit to the event loop, explicitly call `ObjectContentStorage.store` inside, then use `track` to identify the settlement-read project.

**Usable provider-material links travel with archives**: links are instance-database records keyed by project name plus content-version identity ([ADR 0111](0111-asset-upload-job-and-link-share-one-record.md), [ADR 0115](0115-provider-asset-link-scope-frozen-by-cloud-account-identity.md)). Export usable links for every packaged version record's content version into archive-envelope `provider_asset_links`, with digests matching records. Current-version-only packages trim history under existing rules; versions absent from packages carry no links.

- Include only selected, usable links. Tasks awaiting upload, submitting, processing, or awaiting result confirmation depend on source-instance intermediary copies/background queries; failed tasks lack materials and removed links no longer apply, so exclude them all.
- Freeze cloud-account identity, site, region, provider project, and origin unchanged alongside provider-material IDs/groups. Exclude account-record IDs, secrets, signed URLs, intermediary copies, and source-instance connection locations. Account-record IDs have meaning only in the source instance; import records `0`, matching no account record.
- Validate each imported link: content-version identity must match a packaged version record, and both recorded digest and actual snapshot content must match the link digest. Discard mismatches while importing images normally. Write verified links under target project names after installation, using global-library link-copy rules ([ADR 0120](0120-global-asset-library-entries-hold-content-references-and-link-copies.md)): select them as usable, preserving an already selected link in the same scope. Write failures do not undo imports; return a warning in import results.
- Target instances select by material-library accounts actually bound to inference credentials ([ADR 0113](0113-generation-selects-provider-assets-by-actual-inference-credential.md)). Matching account identities send material references; mismatches preserve but do not select links, sending image content normally. Image preview, editing, and version restoration are unaffected.

Providers authenticate material-ID usability by account. ArcReel account-identity matching only selects correct links, falling back to ordinary images when inapplicable without granting additional eligibility: target instances registering the same cloud accounts already can select these materials in their libraries.

## Considered Options

- **Keeping `storage` in archives and reading packaged locations at import**: target instances lack source buckets/credentials; worse, manually modified packages could read arbitrary objects from target buckets.
- **Including signed URLs for importers to download**: URLs expire and export source-instance access capabilities.
- **Excluding links, then re-uploading or selecting from material libraries after import**: this ADR's initial choice, justified as "registering the same cloud account automatically grants use." Providers already grant that eligibility by account; omitting links merely forces repeated registration.
- **Including links in every state**: in-progress registration depends on source intermediary copies/background queries, absent from target instances, which also cannot continue queries using their own account records. Failed/removed records provide no generation value.
- **Rewriting scopes to target accounts on import**: attaches one cloud account's material IDs to another's scope, violating ADR 0115.

## Consequences

- Exports with many remote images and cleaned working files first download them, with concurrency capped at 4. Retrieved files remain working files for subsequent cleanup.
- Object-storage imports upload synchronously within requests, increasing duration with image count. Byte-identical versions share objects and upload only once.
- Current-version-only archives omit version records for generated asset images and similar images, therefore omitting links. Object-storage import registers new content versions that cannot match source links; re-upload or select materials in target projects when needed.
- During overwrite import, old-project links move to tombstone names on retirement; imported projects hold only archived links.
- Overwrite import turns replaced projects into deleted projects. Retirement first backfills actually held formal-object references within the same transaction, then reassigns them to tombstones, retaining objects until permanent project deletion ([ADR 0118](0118-formal-objects-retained-by-holder-references-and-collected-in-one-place.md)). New-image holds remain under project names and settle into new-project references, unaffected by permanent tombstone deletion.

For scope and acceptance, see [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559).
