# Incremental scope for long-term object storage of materials

Date: 2026-11-01. Context: #2249 design discussion. Evaluation baseline: `origin/main` commit `3dffb86572cf2a27d6fc984a938c1edf28e6049d`; the local branch was not switched, and differing relevant files were read from that commit. Review rechecked through `5133bbb9acb50e011b91766d9021385a40b723e3`; only dependency lockfiles changed between baselines, without changes to code discussed here. This is source-scope analysis only: no implementation, test execution, or real-bucket access.

## Evaluation premises

Evaluate the incremental work for remote primary storage of new image content versions beyond user-owned upload-relay object storage. Exclude bulk legacy migration, audio/video, project text, databases, and Agent work directories. Long-term storage means durable remote content with removable complete local copies, not merely cloud mirrors. See [ADR 0108](../adr/0108-material-storage-independent-of-provider-registration.md); [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559) and its tasks govern delivery/acceptance.

## Assessment

Reuse relay connection configuration, credential reads, object uploads, and download signing. New work is durable commits, content reads, and complete lifecycles. Remote primary storage for new images alone is a medium-to-large cross-workflow increment, not one setting. All-media support/history migration substantially expand scope further. This is static analysis, not a schedule commitment.

Relay objects may expire after provider retrieval; formal material objects supply restoration, export, and future generation and cannot use relay expiration rules. Even with shared connections, distinguish retention/deletion responsibilities.

## Source evidence and incremental impact

| Path | Current evidence | Incremental long-term-storage impact |
|---|---|---|
| Generation/upload | `download_image_to_path`, `save_image_from_response_item` in [`image_backends/base.py`](../../lib/backends/image_backends/base.py), and `stage_uploaded_bytes` in [`upload_finalize.py`](../../server/services/currency/upload_finalize.py) write local staging files | Retain local staging; channels need not write directly to cloud. Upload at formal commit |
| Formal version commits | `_commit_staged_formal_image` in [`formal_image_commit.py`](../../server/services/tasks/formal_image_commit.py) centralizes versions/manifests; `commit_staged_version` in [`version_manager.py`](../../lib/artifacts/version_manager.py) relies on local copying/replacement/rollback | Local rollback does not cover uploaded objects. Remote commits need leftover cleanup and version/metadata/manifest consistency; compensation/verification: [#3566](https://github.com/ArcReel/ArcReel/issues/3566) |
| Reads/previews | `serve_project_file` in [`files.py`](../../server/routers/files.py) checks local files/stat; `ensure_image_thumbnail` in [`image_thumbnails.py`](../../lib/infra/image_thumbnails.py) reads local images | Obtain local caches or usable download URLs by content version; missing caches refill remotely, thumbnails can remain local |
| Generation inputs | `_try_read_image` in [`reference_compression.py`](../../lib/references/reference_compression.py) and `_build_image_content` in [`ark.py`](../../lib/backends/video_backends/ark.py) read local files | Unified reads retrieve/verify content versions and prepare working files, without teaching every PIL/backend call object storage |
| Manifest, freshness, planning | `read_artifact_content_digest` in [`artifact_currency.py`](../../lib/artifacts/artifact_currency.py) reads formal content through manifest adapters; `_read_open_artifact` in [`artifact_manifest.py`](../../lib/artifacts/artifact_manifest.py) reads local descriptors; `_track_dependency_digest` in [`artifact_planner.py`](../../lib/artifacts/artifact_planner.py) and `visual_references_match_snapshot` in [`visual_artifact_provenance.py`](../../lib/artifacts/visual_artifact_provenance.py) read local digests | Evaluate by content versions/persistent locations: missing caches are not missing formal materials. Trusted-digest reuse retains verification/concurrency consistency; retrieve through unified entries when needed. Cache clearing changes neither freshness nor planning |
| Version restoration/recycle bins | `restore_version` in [`version_manager.py`](../../lib/artifacts/version_manager.py) verifies snapshots/installs files/rolls back failures; `record_deletion` in [`project_trash.py`](../../lib/project/project_trash.py) moves files before records; restoration uses [`trash_restore.py`](../../lib/project/trash_restore.py) | Remote versions must remain retrievable. Removing project references never immediately deletes objects needed by bins/other references; restoration verifies before selection |
| Archives/cross-project copying | `_prepare_export_snapshot`, `_capture_stable_visible_tree` in [`project_archive.py`](../../server/services/project/project_archive.py) create consistent local snapshots; `_copy_into_global_pool`, `apply_to_project` in [`assets.py`](../../server/routers/assets.py) copy files/commit versions/manifests | Archives retrieve fixed versions with complete content, not temporary URLs. Imports/library applications preserve independent target usability after source deletion |

These readers depend on local files, so incremental scope exceeds uploading: content verification, cache concurrency/remote failures, and distinguishing formal commits from upload outcomes also matter.

## Evaluation boundaries and evidence limits

Restricting scope to new image versions while retaining local staging/caches narrows writes but does not remove version/bin/archive/library/freshness adaptations. See [#3571](https://github.com/ArcReel/ArcReel/issues/3571) for applying storage choices to new commits and retaining original locations on restoration.

Relevant existing tests include `tests/unit/lib/artifacts/test_version_manager.py`, `tests/integration/server/routers/test_files_router_asset_sheet_upload.py`, `tests/integration/server/routers/test_project_trash_router.py`, and `tests/integration/server/services/project/test_project_archive_service*.py`. These are extension points, not proof of remote-storage readiness; this research ran no functional tests and accessed no real buckets. Specs/implementation tasks own formal acceptance; static analysis never substitutes for runtime evidence.
