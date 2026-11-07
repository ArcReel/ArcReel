---
status: accepted
---

# Holders retain formal objects through references; collect centrally after all references are lost

[ADR 0116](0116-remote-content-versions-saved-by-formal-commit-with-object-references.md) records formal-object ownership through references, leaving lifecycle operations to decide whether to delete unreferenced objects. Version deletion, project recycle bins, project deletion, and cross-project application all change references; independent deletion decisions would require every consumer to maintain its own rules. Decisions follow.

**Holders**: references belong to holders (`object_storage_formal_object_ref.project_name`), of two types:

- Projects use project names. Whole-project deletion or replacement by overwrite import reassigns references to tombstone names along with tasks, call records, and provider-material links, restoring names on recovery. Do not reassign in-progress commit holds: overwrite import commits new images under the project name before retiring the old project, and settlement must reference new images under that name. Before reassignment in the same transaction, retirement therefore backfills references actually held by the old project's current version history/recycle bin (read under project/version locks). Published but unsettled commits have only holds; backfilled references preserve their objects under tombstones. Their commits later settle holds, removing them if project directories have disappeared or been replaced. Retirement registers the directory before locked reads; until retirement commits or rolls back, new remote-image saves in it fail before upload (`material_project_retiring`). Tombstone remote versions thus all have backfilled references. This check runs under version locks, without holding locks across directory renames.
- Non-project consumers (such as global asset libraries) use colon-bearing keys (such as `asset-library:<id>`). Project names allow only letters, digits, and hyphens; tombstone names contain `#`, so colon-bearing keys cannot collide with either.
- When registration tasks directly issue formal-object download URLs, register references as `ingest:<任务 ID>` before issuance, retaining them from issuance under intermediary-copy retention ([ADR 0111](0111-asset-upload-job-and-link-share-one-record.md)): at least 1 hour after results; if results never arrive, links are removed, accounts become unavailable, or task records disappear, until 1 hour after URL expiry. The registration loop releases expired references. Objects survive permanent source-version deletion while providers retrieve images.

**Project-held references**: compute from current project state instead of incrementally adjusting per operation.

- Remote content versions in version history are `active`.
- Recoverable remote content versions in project recycle bins are `trashed`, including deleted historical versions and version histories in structural-deletion snapshots. Snapshots hold the full pre-deletion version history; extra versions merely retain objects until records are cleared.

`reconcile_project_references` reads version history and recycle bins together under project/version locks and rewrites references, preventing reference removal while versions move into recycle bins. Formal-commit settlement and startup recovery follow the same basis: read history/bins under both locks, converting holds into `active` or `trashed` references inside them. Versions deleted into bins after publication but before settlement retain references. If project directories, version histories, or bin snapshots are unreadable, abandon recalculation without removing references; settlement unable to read recycle bins preserves holds for the next startup.

**Centralized collection**: once objects lose all references (including in-progress commit holds), mark pending deletion under one rule and immediately attempt deletion. Lifecycle operations collect saved objects through `collect_unreferenced_objects`; formal-commit settlement likewise collects after removing holds and is the sole place collecting unconfirmed uploads. Record deletion failures and retry with backoff in maintenance; changed connection buckets produce `connection_changed` without deletion. Trigger only where references are removed:

- After permanent deletion, emptying, or expiration cleanup in project recycle bins, recalculate project references and collect (`reconcile_and_collect`).
- After permanent deletion of deleted projects, remove all tombstone-holder references and collect (`release_holder`).
- Cross-project consumers call `remove_reference` when no longer using content versions, and `release_holder` when disappearing entirely; retain `add_reference` before use.
- Every registration-loop round releases expired registration-task references (`release_ingest_holds`).
- Hourly maintenance recalculates every project as fallback. For deleted projects whose retention expired a day ago and whose directories are absent, release remaining tombstone references, covering explicit-release failures.

Deletion into project recycle bins, restoration, and version switching do not remove references or trigger collection. Formal-commit settlement collects after removing holds under the rules above (ADR 0116). Provider-material links and intermediary copies are not formal-object references; collection never touches them.

**Restoration preserves content versions**: restored remote historical versions retain original content identities and persistent locations without re-upload. Deletion records retain version digests; reuse original locations only when restored bytes match, otherwise save as local versions.

## Considered Options

- **Letting lifecycle operations decide when to delete zero-reference objects** (ADR 0116's original arrangement): separate rules for version deletion, recycle-bin clearing, project deletion, and global libraries drift; omissions cause deletion mistakes or leaks.
- **Registering/removing references per recycle-bin record**: every deletion/restoration path needs wiring, and degraded recovery, migration, or interruption can desynchronize records and references. Recalculation from current project state has one entry and heals drift.
- **Releasing references when maintenance finds tombstone directories absent**: restoring projects first moves directories back, then reassigns references, leaving a window with absent tombstone directories and tombstone-held references; release then would delete recoverable objects. Instead release explicitly at permanent deletion; fallback handles only expired tombstones.
- **No local copies in deletion records; retrieve from object storage on restoration**: saves local recycle-bin space but requires network restoration. Existing deletion paths already move local files into records, enabling restoration without object storage; retain that.

## Consequences

- Supersedes ADR 0116 Consequences' sentence "the operation decides whether to delete objects losing all references under its own retention rules; this decision only lets failure compensation delete objects."
- New cross-project or long-lived remote-content consumers maintain only their own references, without deletion rules.
- If explicit reference release fails at permanent project deletion, objects remain at most one extra day beyond retention.
- Hourly maintenance takes each project's lock once and reads history/bin records, at a cost comparable to working-file cleanup.
- File views still list remote images from version records after working-file cleanup, marking them absent locally. Listing downloads nothing; preview or deletion of deletable historical versions first retrieves files and registers use.

For scope and acceptance, see [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559).
