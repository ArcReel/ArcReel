"""Service layer for setting and clearing storyboard end-frame snapshots.

An end frame is persistent user intent on a storyboard (``end_frame_image``), not runtime output, so
it is excluded from ``generated_assets``. Both setting channels (uploading any image / specifying a
relative path to an existing project image) converge here: normalize to PNG and copy a snapshot to
``end_frames/scene_{id}.png``; the script entry stores only that fixed relative path. Source image and
snapshot are completely decoupled: regeneration, version rollback, or deletion of the source cannot
change the chosen end frame, structurally preventing dangling references.

Setting writes snapshot + field; clearing moves the snapshot into project trash + nulls the field.
Each entire operation stays within the same script-lock critical section (`ProjectManager.locked_script`),
mutually excluding the other. One cannot finish writing a file only for the other to delete it before
field writeback, structurally preventing dangling references. If the target storyboard is deleted
mid-flight and the critical section fails, skip the entire writeback without partial state.

`locked_script` persists the script inside the lock, but releases it before validation/persistence
exceptions reach the caller. Thus filesystem side effects (snapshot writes / deletions) cannot simply
roll back in an outer try/except: rollback would race with another concurrent request successfully
persisted in the same gap. Instead reacquire the lock and roll back conditionally on whether this
storyboard's operation generation still matches the failed operation (see `_restore_after_persist_failure`).
Undo only its own effects, never overwrite another request's persisted result. Use generations rather
than file contents: content comparison has degenerate values. Another concurrent clear also leaves
the file absent, indistinguishable from the expected untouched value (None), wrongly appearing
untaken-over and restoring an orphan file. Increment the generation on every critical-section entry,
regardless of ultimate success. Any entry between failure and rollback reacquisition advances it,
reliably indicating whether to skip. Orphan snapshots (e.g. process killed after writing but before
lock release) can remain; they are harmless, like existing storyboards, and no cleanup mechanism is added.

Normalize script filenames in generation keys with `ProjectManager.normalize_script_filename`:
`episode_1.json` and `scripts/episode_1.json` are valid aliases of one script. Otherwise aliases get
separate generation keys and concurrent requests using different aliases for one storyboard cannot
see each other. Use the public `ProjectManager` entry point rather than duplicating internal rules,
avoiding silent drift and split keys when upstream adds alias forms.

Read the rollback baseline (`old_bytes`) after acquiring the script lock and before mutation, never
before locking. Otherwise a concurrent write that successfully persists ahead of this operation makes
the preread older than the actual pre-operation state; failure would overwrite that successful write
even with correct generation checks.

Set the compensatable marker (`entered`) only *after* successfully reading the baseline. Setting it
beforehand leaves `old_bytes` at initial `None` if reading fails (permissions / transient I/O), so
compensation mistakes read failure for an originally absent target and unlinks an existing snapshot
this operation never touched.

Advance the operation generation (`_advance_shot_generation`) only after matching the storyboard,
immediately before the first filesystem mutation. Advancing earlier lets requests exiting empty-handed
because no storyboard was found advance it too. Another request genuinely awaiting compensation after
persistence failure would mistake that empty exit for takeover and a consistent state, skip rollback,
and leave its own partial effects on disk."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from lib.infra.async_thread import run_sync_transaction
from lib.infra.image_utils import normalize_storyboard_upload
from lib.infra.json_io import domain_error_on_value_error
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.material.work_files import local_work_files
from lib.project.media_trash import trash_end_frame
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager, get_project_manager, is_reference_video_project
from lib.project.resource_paths import END_FRAME_RESOURCE_TYPE, resource_relative_path
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.currency.upload_finalize import UPLOAD_IMAGE_MAX_BYTES, write_bytes_atomic

logger = logging.getLogger(__name__)

_ShotKey = tuple[str, str, str]
_generation_lock = threading.Lock()
_shot_generations: dict[_ShotKey, int] = {}


def _current_shot_generation(key: _ShotKey) -> int:
    with _generation_lock:
        return _shot_generations.get(key, 0)


def _advance_shot_generation(key: _ShotKey) -> int:
    """Advance this storyboard's operation generation and return it. Call inside the critical section before
    mutation: regardless of later failure, it records an operation entering the critical section so failed
    operations can determine whether compensation rollback has been taken over."""
    with _generation_lock:
        generation = _shot_generations.get(key, 0) + 1
        _shot_generations[key] = generation
        return generation


class EndFrameError(Exception):
    """Domain error for end-frame operations. Routers translate (status_code, key, params) into HTTP responses."""

    def __init__(self, key: str, *, status_code: int = 400, **params: object):
        super().__init__(key)
        self.key = key
        self.status_code = status_code
        self.params = params


def _locate_shot(project_name: str, script_file: str, shot_id: str) -> Path:
    """Validate that this storyboard can have an end frame and return the absolute project path; otherwise raise a domain error.

    Reference-to-video has no first/end-frame concept and is always rejected. Use project.json generation
    mode (``is_reference_video_project``), not the script-level ``generation_mode`` stamp: ad script
    skeletons lack that stamp (see ``script_generator``), so script-only checks allow “ad + reference-to-video”
    and let users set an end frame never consumed by generation. All content modes share this rule."""
    manager = get_project_manager()
    try:
        project_path = manager.get_project_path(project_name)
    except FileNotFoundError as exc:
        raise EndFrameError("project_not_found", status_code=404, name=project_name) from exc
    except ValueError as exc:
        raise EndFrameError("invalid_project_name", status_code=400, name=project_name) from exc

    # Corrupted script files (invalid JSON syntax or non-UTF-8 bytes) must not be mistaken for invalid script_file;
    # let the _translated_errors fallback return 500.
    with domain_error_on_value_error(
        lambda _exc: EndFrameError("invalid_script_file", status_code=400, name=script_file),
        extra_passthrough=(UnicodeDecodeError,),
    ):
        script = manager.load_script(project_name, script_file)

    try:
        project = manager.load_project(project_name)
    except FileNotFoundError as exc:
        raise EndFrameError("project_not_found", status_code=404, name=project_name) from exc
    if is_reference_video_project(project):
        raise EndFrameError("end_frame_reference_video_unsupported")

    items, id_field, _, _, _ = get_storyboard_items(script)
    if find_storyboard_item(items, id_field, shot_id) is None:
        raise EndFrameError("segment_not_found", status_code=404, id=shot_id)
    return project_path


def _snapshot_target(project_path: Path, shot_id: str) -> tuple[Path, str]:
    """Return the end-frame snapshot (absolute path, project-relative path); raise a domain error if the path built from shot_id escapes the boundary."""
    relative = resource_relative_path(END_FRAME_RESOURCE_TYPE, shot_id)
    try:
        target = safe_join(project_path, relative)
    except PathTraversalError as exc:
        raise EndFrameError("invalid_resource_id", resource_id=shot_id) from exc
    return target, relative


class _RollbackDone(Exception):
    """Sentinel indicating compensation completed, skipping normal script writeback on `locked_script` exit.

    `locked_script` uses `@contextmanager`: whether the `with` body exits normally or via `return`, the
    generator resumes after `yield` and runs `_write_script_unlocked`. Compensation changes only the
    snapshot file, never reads/modifies `script`. Normal exit would trigger redundant persistence
    (updating `metadata["updated_at"]`, triggering project synchronization/change events, and on failure
    being mislogged by this `except Exception` as rollback failure). Throwing this sentinel instead makes
    the generator rethrow at `yield`, bypassing writeback; `except _RollbackDone: pass` below swallows it."""


def _restore_after_persist_failure(
    manager: ProjectManager,
    project_name: str,
    script_file: str,
    target: Path,
    old_bytes: bytes | None,
    key: _ShotKey,
    expected_generation: int,
) -> None:
    """Compensate for script persistence failure: conditionally roll back the snapshot in a new script-lock critical section.

    Compensation must reacquire the lock: `locked_script` persists inside it but releases before exceptions
    propagate. Directly rewriting caller-held old bytes outside the lock races with concurrent requests
    successfully persisted in the same gap. Before rollback, check `expected_generation` against this
    storyboard's operation generation before failure. If unchanged, nobody entered the critical section
    meanwhile and rollback is safe. If advanced, a concurrent operation took over (regardless of its own
    success), potentially making current state consistent; rollback would overwrite its result with stale
    bytes, so skip it."""
    try:
        with manager.locked_script(project_name, script_file):
            if _current_shot_generation(key) != expected_generation:
                raise _RollbackDone
            if old_bytes is not None:
                write_bytes_atomic(old_bytes, target)
            else:
                target.unlink(
                    missing_ok=True
                )  # hard-delete: rollback -- Remove the snapshot created by this operation when setting the end frame fails.
            raise _RollbackDone
    except _RollbackDone:
        pass
    except Exception:
        logger.exception(
            "尾帧快照回滚失败：project=%s script=%s target=%s（原持久化异常见上一条 traceback）",
            project_name,
            script_file,
            target,
        )


def _write_snapshot_and_field(
    project_name: str,
    script_file: str,
    shot_id: str,
    target: Path,
    png_bytes: bytes,
    relative: str,
) -> None:
    """Write snapshot + field within the script-lock critical section, mutually excluding clearing.

    If script persistence fails (validation/write error), restore the snapshot to its pre-operation state:
    otherwise field writeback rolls back but old snapshot contents are silently replaced, leaving callers
    with a failure response and unnoticed content loss. Read old bytes after acquiring the script lock and
    before writing. Prereading outside it can capture stale bytes if a concurrent write persists first;
    rollback would then overwrite that successful write."""
    manager = get_project_manager()
    key = (project_name, ProjectManager.normalize_script_filename(script_file), shot_id)
    generation = 0
    old_bytes: bytes | None = None
    entered = False
    try:
        with project_change_source("webui"), manager.locked_script(project_name, script_file) as script:
            old_bytes = target.read_bytes() if target.exists() else None
            items, id_field, _, _, _ = get_storyboard_items(script)
            matched = find_storyboard_item(items, id_field, shot_id)
            if matched is None:
                raise EndFrameError("segment_not_found", status_code=404, id=shot_id)
            generation = _advance_shot_generation(key)
            entered = True
            write_bytes_atomic(png_bytes, target)
            matched[0]["end_frame_image"] = relative
    except EndFrameError:
        raise
    except BaseException:
        if entered:
            _restore_after_persist_failure(manager, project_name, script_file, target, old_bytes, key, generation)
        raise


def _clear_snapshot_and_field(project_name: str, script_file: str, shot_id: str, target: Path) -> None:
    """Null the field + move the snapshot to project trash within the script-lock critical section, mutually excluding setting.

    Move the snapshot only after script writeback; on persistence failure it remains in place. Compensation
    still rewrites conditionally by generation so a rolled-back field never points to a missing file. Read
    old bytes only after acquiring the script lock, for the same reason as `_write_snapshot_and_field`."""
    manager = get_project_manager()
    key = (project_name, ProjectManager.normalize_script_filename(script_file), shot_id)
    generation = 0
    old_bytes: bytes | None = None
    entered = False
    trash = False

    def prepare_trash(script: dict) -> Callable[[Path], None] | None:
        # Create the deletion record after script writeback within the same project lock: any failure rolls back the script and leaves the snapshot in place.
        if not trash:
            return None

        def record(_output: Path) -> None:
            trash_end_frame(manager.get_project_path(project_name), key[1], shot_id, script)

        return record

    try:
        with (
            project_change_source("webui"),
            manager.locked_script(project_name, script_file, prepare_on_commit=prepare_trash) as script,
        ):
            old_bytes = target.read_bytes() if target.exists() else None
            items, id_field, _, _, _ = get_storyboard_items(script)
            matched = find_storyboard_item(items, id_field, shot_id)
            if matched is None:
                raise EndFrameError("segment_not_found", status_code=404, id=shot_id)
            generation = _advance_shot_generation(key)
            entered = True
            matched[0]["end_frame_image"] = None
            trash = target.is_file() and not target.is_symlink()
    except EndFrameError:
        raise
    except BaseException:
        if entered:
            _restore_after_persist_failure(manager, project_name, script_file, target, old_bytes, key, generation)
        raise


def read_project_image(project_path: Path, source_path: str) -> bytes:
    """Read an existing project image's bytes; raise a domain error for out-of-bounds paths, missing files, or size-limit violations.

    Do not restrict source subdirectories: storyboard / character / scene / grid-cell images are all directly
    selectable. ``safe_join`` centralizes boundary protection (as in data_validator path-field validation).

    Use bounded reads with the upload-channel limit (``UPLOAD_IMAGE_MAX_BYTES``), rather than loading
    the whole file, preventing unbounded memory use if a legitimate large project file (e.g. video) is selected as an image."""
    normalized = source_path.strip().replace("\\", "/")
    if not normalized:
        raise EndFrameError("invalid_end_frame_source", path=source_path)
    try:
        resolved = safe_join(project_path, normalized)
    except PathTraversalError as exc:
        raise EndFrameError("invalid_end_frame_source", path=source_path) from exc
    if not resolved.is_file():
        raise EndFrameError("end_frame_source_not_found", status_code=404, path=normalized)
    with resolved.open("rb") as f:
        content = f.read(UPLOAD_IMAGE_MAX_BYTES + 1)
    if len(content) > UPLOAD_IMAGE_MAX_BYTES:
        raise EndFrameError(
            "end_frame_source_too_large", max_mb=UPLOAD_IMAGE_MAX_BYTES // (1024 * 1024), path=normalized
        )
    return content


async def _apply_snapshot(
    *,
    project_path: Path,
    project_name: str,
    script_file: str,
    shot_id: str,
    content: bytes,
) -> str:
    """Shared destination for both setting channels: normalize to PNG → write fixed path → write back field; return relative path.

    Replacing an image overwrites the same path: the field is unchanged, and the frontend cache-busts with asset fingerprints."""
    target, relative = _snapshot_target(project_path, shot_id)

    try:
        png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content)
    except ValueError as exc:
        raise EndFrameError("invalid_image_file") from exc

    await run_sync_transaction(
        _write_snapshot_and_field, project_name, script_file, shot_id, target, png_bytes, relative
    )
    return relative


async def set_end_frame_from_bytes(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    content: bytes,
) -> str:
    """Upload channel: save uploaded image bytes as this storyboard's end-frame snapshot."""
    project_path = await run_sync_transaction(_locate_shot, project_name, script_file, shot_id)
    return await _apply_snapshot(
        project_path=project_path,
        project_name=project_name,
        script_file=script_file,
        shot_id=shot_id,
        content=content,
    )


async def set_end_frame_from_project_image(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    source_path: str,
) -> str:
    """Project-image selection channel: read source bytes, then use exactly the same normalization and snapshot destination as uploads.

    For remote content versions with cleared caches, retrieve from object storage and verify the digest
    before reading; the end-frame snapshot itself always remains local."""
    project_path = await run_sync_transaction(_locate_shot, project_name, script_file, shot_id)
    async with local_work_files(project_path, [source_path.strip().replace("\\", "/")]):
        content = await asyncio.to_thread(read_project_image, project_path, source_path)
    return await _apply_snapshot(
        project_path=project_path,
        project_name=project_name,
        script_file=script_file,
        shot_id=shot_id,
        content=content,
    )


async def clear_end_frame(*, project_name: str, script_file: str, shot_id: str) -> None:
    """Clear a storyboard end frame: null the field and move the snapshot into project trash within one script-lock critical section, mutually excluding setting."""
    project_path = await run_sync_transaction(_locate_shot, project_name, script_file, shot_id)
    target, _ = _snapshot_target(project_path, shot_id)
    await run_sync_transaction(_clear_snapshot_and_field, project_name, script_file, shot_id, target)
