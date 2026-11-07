---
status: accepted
---

# Global asset-library entries hold formal-object references and link copies under entry keys

[ADR 0116](0116-remote-content-versions-saved-by-formal-commit-with-object-references.md) saves new images through formal commits, registering formal-object references by (holder, content version). [ADR 0118](0118-formal-objects-retained-by-holder-references-and-collected-in-one-place.md) requires non-project consumers to use colon-bearing holder keys and centrally collects objects losing all references. [ADR 0108](0108-material-storage-independent-of-provider-registration.md) and [ADR 0109](0109-content-version-identity-on-version-records.md) attach links to specific content versions with holder-bearing keys. The global asset library is not a project and has no version history; it must save remote images and carry provider-material links when saving/applying them. Decisions follow.

**Entries record content facts**: asset and derivative entries gain three columns.

- `image_digest`: digest of the file stored in the library.
- `image_content_id`: the image's content identity in the library, randomly generated whenever a new image is written; replacing the image changes identity.
- `image_storage`: persistent remote location, shaped like version-record `storage`.

With `image_storage`, `image_path` is only a working file: retrieve it from object storage by digest before preview/application, and clean it through the same cleanup entry point when idle. Existing entries with all three columns empty retain local-file interpretation without backfill.

**Save new images through formal commits**: saving to the library, uploading, or replacing library images enters `formal_commit` through `global_image_commit` in `lib/material/global_library.py`. Save new images locally or in object storage according to global settings; identical bytes within one connection share a formal object. Application to a project likewise commits the target's new content version through `formal_commit` under current global settings. Whether library images are remote/local does not constrain where targets are saved, or vice versa.

**Entry keys**: each library entry uses `asset-library:<条目 ID>` (`library_holder(asset_id)`) as holder and `image_content_id` as content-version identity, reusing two existing tables. Derivative and base images belong to one entry and share this key.

- Formal-object references: register `active` references through `add_reference` before writing entries; remove old-image references through `remove_reference` after replacement/overwrite (image replacement, overwrite, and deletion within an entry are serialized in-process, removing the image actually displaced by this commit). After entry deletion, `release_holder` removes all its references. These two entry points centrally collect objects losing all references; the library does not delete objects itself.
- Link copies: store in `provider_asset_link`, with `project_name` set to the same entry key and the same link shape as projects. Deleting entries removes every link record under the key without deleting provider materials.

Colon-bearing keys cannot collide with project or tombstone names. Reads enumerating, recalculating, or cleaning by project name therefore do not mistake entries for missing projects, including startup settlement, maintenance-loop retention correction, project-retirement rekeying, and permanent deletion of deleted projects. Registration background processing handles only links awaiting reconciliation, excluding copied links.

**Links copy with content**: copying occurs only between identical bytes, verified by digest through `copy_usable_links`.

- Saving to the library copies links from the source resource's currently selected version to the new library image. Applying to a project copies library links to the target's new content version, persisting them at the end of version commit; persistence failure rolls back the whole application.
- Copy only selected, usable links, preserving provider-material IDs and frozen invocation scopes unchanged. Do not copy registering or failed tasks, which would make background processing register the same image repeatedly.
- Each holder independently owns its copy; unlinking or replacing images changes only its own copy. Generation still selects by the target's actual inference-credential binding scope; copying grants no eligibility to new accounts.

## Considered Options

- **One retention holder for the entire library**: deleting entries would require listing each image to remove references; missed references would prevent collection forever. Entry keys let deletion call `release_holder` directly to remove every owned reference, matching project usage.
- **Separate link/reference tables for the global asset library**: generation, panels, and background registration all query links by holder. Separate tables require either another branch at every read point or separate copying/cleanup rules; reusing existing tables directly applies the same functions and unique indexes.
- **Using entry IDs as link content identity**: entry IDs survive image replacement, so new images would inherit old links, violating ADR 0108.
- **Using content digests as library content identity**: byte-identical images would share links, so unlinking one affects the other, the same rationale as ADR 0109's rejection of hashes as identity.
- **Copying registering tasks**: background processing advances registration by record, so copies would submit the same image twice.

## Consequences

- Deleting source projects/assets affects neither library nor targets: library entries and targets independently hold object references and link copies. Deleting a library entry removes only its references; objects still referenced by targets remain, while objects losing all references are centrally collected.
- New entries receive IDs before saving images so references can be registered under them.
- Existing library entries lack content identity and carry no links until saved again.
- Remote-library working files share project working-file cleanup: both `POST /api/v1/object-storage/work-cache/cleanup` and background maintenance clean both locations.
