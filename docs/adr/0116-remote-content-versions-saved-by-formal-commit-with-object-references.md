---
status: accepted
---

# Save remote content versions through formal commits; name formal objects by content digest and record ownership through references

[ADR 0109](0109-content-version-identity-on-version-records.md) records content-version identity, digest, and persistent location on version records, adding remote locations when remote primary storage arrives. Decisions follow.

**Persistent locations**: remote version records gain `storage`, recording write-time object-storage connection IDs, buckets, and object keys. Records without it retain local interpretation. Read only from recorded connections/buckets; explicitly reject deleted connections or changed buckets without reinterpreting old locations against new buckets. Signed URLs go only to image-retrieving parties, never into locations.

**Formal commits**: `formal_commit` in `lib/material/formal_objects.py` wraps callers' existing formal-write orchestration. Global `material_storage_target` chooses local/object storage for newly written image content versions, default local, editable by administrators under "New image save location" (`PUT /api/v1/object-storage/new-image-target`). Object storage requires a connection, which cannot be deleted while selected. Configuring connections for upload relay does not change this setting. Remote saving precedes version-record writes at the single `_persist_snapshot` seam; return locations only after upload and HEAD confirmation. On failure, existing manager/caller rollback preserves old versions, inventories, and formal files. Only images use object storage; videos/audio remain local. Selection applies only to version-recorded image content versions: storyboard end frames, character/product originals, and project style references write files directly without version records/content identities/persistent-location fields, always remaining local. Selecting/restoring old versions uses recorded locations, not new-image settings.

**Objects and ownership**: formal keys are `arcreel-materials/sha256/<前两位>/<摘要><后缀>`, separate from relay prefix `arcreel-relay/`. Byte-identical versions within one connection share objects. Register objects in `object_storage_formal_object` before upload; `object_storage_formal_object_ref` holds in-progress commit reservations (`pending`) and version-history content usage (`active`).

- Never delete objects while any references remain.
- After commits finish, successfully or not, settle against project version history: convert used holds to `active`, releasing others.
- After releasing holds, delete objects used/created by this commit if no references remain, regardless of which commit created them; retain any referenced objects. Concurrent identical-byte commits delete only when the last hold is released.
- Record deletion failures and retry with backoff in maintenance.
- After process exit mid-commit, complete settlement at next startup.

**Working files**: remote versions keep local files at version-snapshot and selected formal-file locations so existing readers need not distinguish storage. These files are caches only.

- Readers retrieve through `ensure_work_file` or `ensure_content_version_file` in `lib/material/work_files.py`. Preview, thumbnails, and version switching are wired: public file routes/version restoration retrieve missing working files before reading.
- Verify retrieved digests; concurrent retrieval of one file downloads once. Installation rechecks unchanged records and continued file absence under version locks, without overwriting newly committed concurrent files.
- `hold_work_file` registers in-use files, skipped by cleanup.
- Two cleanup entries: `POST /api/v1/object-storage/work-cache/cleanup` and hourly background maintenance deleting only files idle over 24 hours. Both remove only remotely confirmed files whose local bytes match digests, never objects.

**Connection protection**: while connections have potentially readable formal objects (unconfirmed in-progress uploads, saved objects, or deletion-pending objects still referenced), prohibit bucket/Endpoint/region changes and connection deletion; allow access-key updates alone. Unreferenced objects awaiting compensation deletion are unreadable and do not block changes, or erroneous connections (such as unreachable Endpoints) could never be repaired because deletion always fails. On connection change/deletion, mark those objects detached like relay copies (`connection_changed`, preserving locations), without retrying against changed connections or allowing references to revive them. Settings show counts; users clean objects actually written to old buckets through storage consoles. Later same-byte commits in the same connection/bucket may reuse records, updating Endpoint to current. Connection changes/deletion, new-image-location settings, and formal object registration share one in-process connection lock. Commits recheck that their initial connection remains current inside it, otherwise failing before upload without new versions, preventing writes into replaced buckets even before formal objects exist.

**Provider registration**: when versions already exist as formal objects in current connection buckets, issue URLs directly without relay copies. Digest-derived keys fix URLs to content versions; relay cleanup uses only relay records and cannot delete them. Registration tasks acquire their own references before issuance, under [ADR 0118](0118-formal-objects-retained-by-holder-references-and-collected-in-one-place.md) retention.

## Considered Options

- **Random object names per commit, each belonging to one version**: simple compensation, but identical-byte cross-project/library use repeatedly uploads and cannot express later shared ownership.
- **Upload outside commits, then pass objects to version records**: avoids version locks during upload, but generation, editing, import, and upload each have separate commit paths requiring extra steps. Saving at the single `_persist_snapshot` seam requires only entering `formal_commit` everywhere.
- **Separate content-addressed caches outside project directories**: existing readers use project paths. Keeping working files there requires only ensuring presence before reads during reader migration.
- **Allow bucket changes and reinterpret old locations through new connections**: silently points old objects at new buckets.
- **Delete by scanning object prefixes**: affects unregistered objects; compensation/cleanup use only registered locations.

## Consequences

- Upload under version locks makes other version writes in the same project wait. Deployments use one uvicorn process (see [ADR 0007](0007-orphan-tasks-not-requeued-on-restart.md), [ADR 0083](0083-generation-queue-stays-in-house-for-now.md)); in-process locks serialize objects.
- Later lifecycles maintain project/other-holder references under [ADR 0118](0118-formal-objects-retained-by-holder-references-and-collected-in-one-place.md); centralized collection handles zero-reference objects, superseding this decision's original operation-specific deletion timing.
- Existing consumers use [ADR 0117](0117-evicted-work-files-count-as-present-by-recorded-digest.md) for content presence/retrieval; archives carry bytes under [ADR 0119](0119-archives-carry-content-not-storage-locators.md). Calls needing bytes register use while reading.

For scope and acceptance, see [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559).
