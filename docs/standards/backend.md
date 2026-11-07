---
paths:
  - "lib/**"
  - "server/**"
  - "scripts/**"
  - "alembic/**"
  - "packages/**"
---

# Backend Python

## Concurrency

For cancellation and interruption sources in tasks and request handling, see “取消与中断的来源” (`#cancellation-sources`) in `website/docs/dev/architecture.md`.

### Recheck state prerequisites after `await`; when stale, record current state or skip side effects

Another path can change state across an await: a session becomes running while idle settlement is being saved; an epoch increment after admission failure makes queued idle stale; a connection lock is removed between release and a waiter waking. Recheck epoch, lock identity, or metadata after await and write only if prerequisites still hold. State-machine changes also invalidate prerequisites: after opening a state to new operations (such as sending while running), revisit its failure branches' assumptions. For count invariants, list every asynchronous read sequence before choosing a fix. Synchronous code that checks under a lock, releases it for expensive work such as project migration, and reacquires it to write has the same window. Recheck inside the write lock, or concurrent actions both commit based on old state, such as restoring one deletion record twice and leaving objects pointing to absent files.

### The event-loop thread schedules only

An `async def` chain doing synchronous file I/O, hashing, large parsing, or lock waits puts the complete synchronous segment in `asyncio.to_thread`; use asynchronous locks when available. One blocking operation delays every request in the process. Chunked media delivery crosses the loop many times, multiplying the delay. Ruff `ASYNC` checks only direct calls inside `async def` functions, not called functions; blockbuster in `tests/blocking_guard` guards the complete chain. Lint passing does not prove absence of blocking.

Exception: one-time startup/shutdown blocking recorded with a reason in `tests/blocking_guard/exemptions.toml`.

### Use `run_sync_transaction` for threaded formal-file writes or temporary-artifact creation

In request/task contexts, use `run_sync_transaction` from `lib/infra/async_thread.py` when moving synchronous writes, replacements, or deletions of formal files, or creation of temporary artifacts handed back for cleanup, into a thread. It waits for a started thread transaction to finish with a definite result before propagating cancellation. Cancelling bare `asyncio.to_thread` immediately raises `CancelledError` while its thread continues, racing cleanup in two ways:

- Temporary-artifact leaks: the thread creates a directory, zip, or other artifact after caller cleanup has finished.
- External success with internal failure: cleanup in `finally` races the thread's `replace`; the file exists, but the caller treats the operation as cancelled.

If the thread can consume the artifact itself, create and delete it there. If it must return to the async caller, register it immediately on creation in a caller-owned list and clean that list in the caller's `finally`. When `run_sync_transaction` is cancelled, `run_sync_transaction` waits and then raises, discarding its return value; registration is the only way the caller sees the artifact.

Exception: use bare `asyncio.to_thread` for read-only files or disposable intermediate results.

### The settlement segment after provider return is noninterruptible

The cancellation definition in `GLOSSARY.md` requires submitted provider calls to finish and their results to become artifacts. From provider return or artifact commit until executor return, every threaded call uses `run_noninterruptible_sync` in `lib/infra/async_thread.py`. It waits for completion and returns the result, consuming that cancellation. Known failure shapes are reference-copy cleanup racing the thread and deleting a paid image, and completion callbacks racing failure compensation.

Because `run_noninterruptible_sync` consumes cancellation, call it outside `asyncio.timeout` and `asyncio.wait_for` blocks, or timeouts stop working.

Image, video, and audio backends write files as follows:

- When provider bytes are already in memory (`b64_json`, SDK response, fully read response body), decoding, writing, and transcoding all use `run_noninterruptible_sync`. The shared image-backend `lib/backends/image_backends/base.py::save_image_from_response_item` b64 path already satisfies this; reuse it.
- During artifact downloads (`stream_to_file`), keep chunk writes on `run_sync_transaction`. The artifact is not complete yet; cancellation rolls back the download by removing `.part`, and incomplete output is disposable. Network retrieval stays interruptible. Backends using `with_artifact_retry` have a wall-clock `asyncio.timeout` budget; putting downloads in a noninterruptible segment removes that limit. The final atomic `.part` rename uses `run_noninterruptible_sync` so cancellation cannot fall between completion and caller recognition. Threaded work after download, such as PNG conversion or writing, is outside the budget and also noninterruptible.

This protects only threaded calls in the segment. Async writes guarantee their own completion but still propagate cancellation, skipping later steps: cancelled `Ledger.record()` waits for settlement then reraises, so artifact commit is skipped. Bare `asyncio.shield` raises immediately while writes race later handling. Generation timeouts currently surround provider calls; only loop teardown reaches this segment. Before adding `cancel()` for executing tasks, wrap the complete segment in a noninterruptible async boundary with `run_noninterruptible_async`.

## Exceptions

### Handle exceptions carrying guidance or identity separately before broad catches

Exceptions with user guidance (such as `ModelNotInCatalogError` or `NoAvailableProviderError`) cross existing `except Exception` fallbacks. For new or changed raise sites, inspect the chain to its entry point. Routes map them to guided business errors before the fallback. Background enqueue/claim provider resolution uses the identity carried by exceptions such as `ModelNotInCatalogError`, without defaulting to another provider/model. Exceptions without identity, such as `NoAvailableProviderError`, keep that entry point's existing fallback. Do not raise guidance as bare `ValueError`: define a dedicated exception so it can be caught separately. Otherwise users see only “internal server error” or generic failure, and failed tasks may be attributed to another provider (#3248, #3663, #3671).

## Paths and deletion

### Verify ownership before recursively deleting directories from parameters or configuration

Before `shutil.rmtree` on a directory from CLI arguments, environment variables, or configuration (such as `--data-root`), verify that the tool owns it: accept only an empty directory or one with the tool's creation sentinel; otherwise fail. Parameters may point anywhere, including real data, and a mistake deletes all user projects. See `.arcreel-perf-bench` in `scripts/perf/harness.py`.

Exception: a directory this process just created with `tempfile`.

### Reject dot segments server-side in IDs interpolated into `:path` routes

For user-supplied or third-party IDs such as provider-model IDs used in `/{id:path}`, split on `/` and reject segments exactly `.` or `..` at add, rename, and import boundaries. Length checks alone are insufficient. WHATWG URL normalizes both literal and percent-encoded `.` / `..` segments: `a/../b` arrives as `b`, so frontend encoding cannot protect it. It can read, change, or delete another object in the same scope. See `has_dot_segment` in `lib/catalog/model_id.py`.

## Deletion and restoration

Project deletions become project-trash deletion records; restoration adds without overwriting, per `docs/adr/0107-deletion-goes-to-trash-restore-only-adds.md`. Violating the rules below makes content users expect to recover unrecoverable.

### Deletion records include the object and metadata needed to restore usability, and only exclusively owned files

Create a deletion record whenever an object exists in structure data, regardless of attached-file count. Episodes and storyboard items without media or source still hold user titles, hooks, and prompts; skipping them loses that content permanently. Include all fields needed after restoration, not just identity, such as historical generation basis. Missing fields can make restored versions unselectable. Exclude files still referenced by other objects before moving attached files, or deleting one object removes another's files.

### Consume a deletion record only after reader prerequisites and relationships commit together

Before writing, verify reader prerequisites under the lock: parent episode/project exists, names validate, references point to existing objects. If not, degrade as ADR 0107 prescribes or fail while retaining the record. Restore files, structures, and record consumption in the same lock and write transaction; roll back files and retain the record on any failure. Do not save structures in lock-exit handling or callbacks outside the lock. If degradation adds nothing, fail and retain the record. Otherwise restoration appears successful but objects cannot open or reconnect and the record is gone.

### Compensate every change on failure or cancellation when deletion/restoration spans directories, DB, and structure files

If moving directories precedes DB commit, move them back on commit failure; see `move_back` in `lib/project/deleted_projects.py`. Cancellation must preserve their locations too: register them in a caller-owned list (see threaded-write rules above), or wrap the complete segment in `run_noninterruptible_async`. File rollback covers every rewritten file, including `versions/versions.json`. Omissions misalign directories, tasks, and sessions, cause same-name new projects to inherit old records, or permanently lose version history on failed registration.

### After moving files into deletion records, move only listed files and never `rmtree` the original directory

New content may appear between enumeration and cleanup, such as Agent project memory or generated output. Unlisted files are hard-deleted by `rmtree` without recovery. Move only listed files and use `rmdir` only for directories that became empty. A `# hard-delete` reason must remain valid with concurrent writes after enumeration.

Exception: all writers share the same lock with enumeration and deletion.

## Identity and references

### Identity changes also rewrite records keyed by or referring to the old identity

Names, entry numbers, model IDs, and absolute directory paths are identities. Renaming on restore, renumbering, and model changes must update keys derived from the old identity (SDK `project_key` from absolute project path; artifact registrations by item ID) and settings referencing it (project/global model-setting keys and `model_settings`) together. Search all holders of the old identity first and list them in the PR. Omissions leave SDK conversations unavailable, existing artifacts missing with regeneration requested, or dangling model references silently taken over by a new same-ID model. Another object may already occupy the old identity: constrain rewrites to records of the actual object, such as session_id, rather than all records under a key. Align identities at startup when changes happen externally, such as moving the data root. See `lib/agent/agent_session_store/rekey.py`, `lib/agent/agent_session_store/key_alignment.py`, and `rename_project_model_refs` in `server/routers/custom_providers.py`.

## Windows compatibility

Development primarily uses macOS/Linux, but server and workspace dependencies must also create projects and support basic flows on native Windows. CI runs Linux only; the following issues pass silently there and fail on users' Windows machines.

### Without `O_NOFOLLOW`, verify file identity after opening untrusted paths

POSIX constants such as `O_NOFOLLOW` use `getattr(os, "<常量名>", 0)` (enforced by ruff TID251). On Windows this returns zero, so `os.open()` no longer rejects symlinks. A preflight `is_symlink()` alone has a TOCTOU window in which the path can be replaced by a link outside the data root, crossing the security boundary.

For paths users or Agents can influence, use two steps. Before `os.open()`, reject symlinks in every component and confine the path to the data root: `os.lstat` follows intermediate links. After opening, compare `st_dev` / `st_ino` from `os.fstat` and `os.lstat`. See `_resolve_business_file` and `_read_business_file` in `server/tool_runtime.py` and parent-directory checks in `lib/artifacts/artifact_manifest.py`. Reject the operation on platforms where this cannot be guaranteed.

Exception: server-generated paths in directories where externally written symbolic links cannot appear, such as profile locks in `lib/agent/profile_manifest.py` or atomically saved server artifacts. These need only prechecks.
