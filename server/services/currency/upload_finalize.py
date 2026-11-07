"""Finalization services for manually uploaded asset/derivative/storyboard images and videos.

Reuse generation metadata writeback, thumbnails and version primitives so uploaded assets share AI-generation
state derivation, SSE refresh and version rollback behavior."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

from lib.artifacts.artifact_activation import register_current_resource_artifact
from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.image_utils import IMAGE_FILE_EXTENSIONS
from lib.infra.mp4_faststart import ensure_faststart
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.thumbnail import extract_video_thumbnail
from lib.material.formal_objects import formal_commit
from lib.material.storage import ContentVersion
from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE, DerivativeSheetTarget, resolve_derivative_target
from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import ProjectManager, get_project_manager
from server.services.tasks.derivative_sheet_tasks import derivative_sheet_commit_callback
from server.services.tasks.formal_image_commit import storyboard_formal_image_callback
from server.services.tasks.generation_tasks import emit_generation_success_batch

logger = logging.getLogger(__name__)

# Version source identifying manual uploads; frontend uses it for translated labels.
UPLOAD_VERSION_SOURCE = MANUAL_UPLOAD_VERSION_SOURCE

# Upload policy shared by shot_uploads/reference_videos to prevent drift.
# Lenient video validation checks extension/size only; ratio/duration do not block user-controlled uploads.
UPLOAD_IMAGE_EXTENSIONS: tuple[str, ...] = IMAGE_FILE_EXTENSIONS
# Reject webm: bytes persist unchanged as canonical .mp4, but Safari/Jianying cannot decode VP8/VP9,
# and wrong extensions mislead diagnosis; mp4/mov/m4v share the ISO BMFF container family.
UPLOAD_VIDEO_EXTENSIONS: tuple[str, ...] = (".mp4", ".mov", ".m4v")
UPLOAD_IMAGE_MAX_BYTES = 30 * 1024 * 1024
UPLOAD_VIDEO_MAX_BYTES = 200 * 1024 * 1024

_COPY_CHUNK_SIZE = 1024 * 1024


class UploadValidationError(Exception):
    """Upload validation failed; routes translate (status_code, key, params) into HTTP responses."""

    def __init__(self, key: str, *, status_code: int = 400, **params: object):
        super().__init__(key)
        self.key = key
        self.status_code = status_code
        self.params = params


class UploadTooLargeError(UploadValidationError):
    """Uploaded bytes exceed the limit."""

    def __init__(self, max_bytes: int):
        super().__init__("upload_too_large", status_code=413, max_mb=max_bytes // (1024 * 1024))
        self.max_bytes = max_bytes


def validate_upload(filename: str | None, declared_size: int | None, *, kind: Literal["image", "video"]) -> int:
    """Validate extension/declared size against upload policy and return this type’s byte limit.

    declared_size only prechecks Content-Length; persistence functions enforce actual written bytes."""
    if kind == "image":
        extensions, max_bytes = UPLOAD_IMAGE_EXTENSIONS, UPLOAD_IMAGE_MAX_BYTES
        type_error_key = "unsupported_image_type"
    else:
        extensions, max_bytes = UPLOAD_VIDEO_EXTENSIONS, UPLOAD_VIDEO_MAX_BYTES
        type_error_key = "unsupported_video_type"

    if not filename:
        raise UploadValidationError("missing_filename")
    ext = Path(filename).suffix.lower()
    if ext not in extensions:
        raise UploadValidationError(type_error_key, ext=ext, allowed=", ".join(extensions))
    if declared_size is not None and declared_size > max_bytes:
        raise UploadTooLargeError(max_bytes)
    return max_bytes


def _upload_tmp_path(target: Path) -> Path:
    """Same-directory dot-tmp path with a unique per-call suffix, preventing concurrent uploads from interleaving writes."""
    return target.with_name(f".{target.stem}.{uuid.uuid4().hex[:8]}.tmp{target.suffix}")


def _copy_limited(src: BinaryIO, dst_path: Path, max_bytes: int) -> None:
    total = 0
    with open(dst_path, "wb") as out:
        while True:
            chunk = src.read(_COPY_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise UploadTooLargeError(max_bytes)
            out.write(chunk)


async def save_uploaded_video_stream(src: BinaryIO, target: Path, *, max_bytes: int) -> None:
    """Write uploaded streams to target in chunks without loading the whole file into memory.

    Write same-directory dot-tmp files before ``Path.replace`` to avoid Windows cross-volume rename failures and
    partially visible canonical files. Exceeding the limit raises UploadTooLargeError. Before renaming, attempt
    faststart by moving moov before mdat; retain original uploaded bytes if that fails."""
    tmp_path = _upload_tmp_path(target)

    def _write_tmp() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_limited(src, tmp_path, max_bytes)

    try:
        await run_sync_transaction(_write_tmp)
        await ensure_faststart(tmp_path)
        await run_sync_transaction(tmp_path.replace, target)
    except BaseException:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)  # hard-delete: temp -- temporary upload file
        raise


async def stage_uploaded_video_stream(src: BinaryIO, target: Path, *, max_bytes: int) -> Path:
    """Write uploaded video to a unique staging path beside canonical output, without changing formal selection yet."""

    staged_file = _upload_tmp_path(target)
    try:
        await save_uploaded_video_stream(src, staged_file, max_bytes=max_bytes)
        return staged_file
    except BaseException:
        await asyncio.to_thread(staged_file.unlink, missing_ok=True)  # hard-delete: temp -- staged upload file
        raise


def write_bytes_atomic(content: bytes, target: Path) -> None:
    """Synchronous atomic in-memory write to target using dot-tmp, replace and failure cleanup.

    Blocking I/O lets callers choose threading boundaries. Operations sharing one critical section with other blocking
    work, such as script locks (`server/services/project/end_frame.py`), cannot each use separate `asyncio.to_thread` calls."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _upload_tmp_path(target)
    try:
        tmp_path.write_bytes(content)
        tmp_path.replace(target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)  # hard-delete: temp -- temporary upload file
        raise


def stage_uploaded_bytes(content: bytes, target: Path) -> Path:
    """Write normalized upload bytes beside the canonical target without selecting them."""

    target.parent.mkdir(parents=True, exist_ok=True)
    staged_file = _upload_tmp_path(target)
    try:
        staged_file.write_bytes(content)
        return staged_file
    except BaseException:
        staged_file.unlink(missing_ok=True)  # hard-delete: temp -- staged upload file
        raise


async def save_uploaded_bytes(content: bytes, target: Path) -> None:
    """Atomically write in-memory uploaded content to target using dot-tmp, replace and failure cleanup."""
    await run_sync_transaction(write_bytes_atomic, content, target)


def install_manual_asset_sheet_upload(
    *,
    project_manager: ProjectManager,
    project_name: str,
    asset_type: str,
    name: str,
    sheet_path: str,
    content: bytes,
    original_filename: str | None,
    on_commit: Callable[[ContentVersion], None] | None = None,
    require_asset: bool = False,
) -> ContentVersion | None:
    """Install an author-uploaded asset sheet as finished content.

    For an existing asset the sheet bytes, its metadata pointer, a selected
    ``manual_upload`` version, and the Manifest claim commit as one formal write.
    The claim is resolved by the target-state planner, which projects an
    uploaded sheet's basis from the upload alone, so description and project
    style never enter it.  An asset that does not exist yet receives unclaimed
    bytes only and ``None`` is returned.

    ``on_commit`` runs last inside the same formal write with the new selected
    content version; if it raises, the bytes, pointer, version and claim are all
    restored.  Importing a provider asset records its link there, and passes
    ``require_asset`` so an asset deleted meanwhile receives no bytes at all.
    """

    bucket_key = ASSET_SPECS[asset_type].bucket_key
    project_dir = project_manager.get_project_path(project_name)
    metadata: dict[str, str] = {"source": UPLOAD_VERSION_SOURCE}
    if original_filename:
        metadata["original_filename"] = original_filename
    committed: list[ContentVersion] = []

    def _claim() -> None:
        register_current_resource_artifact(project_dir, resource_type=bucket_key, resource_id=name)
        manager = VersionManager(project_dir)
        content_version = manager.ensure_content_version(
            bucket_key, name, manager.get_current_version(bucket_key, name)
        )
        if on_commit is not None:
            on_commit(content_version)
        committed.append(content_version)

    def _select_upload(target: Path) -> None:
        VersionManager(project_dir).commit_installed_version(
            bucket_key, name, "", current_file=target, on_commit=_claim, **metadata
        )

    project_manager.install_asset_sheet_bytes(
        asset_type,
        project_name,
        name,
        sheet_path,
        content,
        on_commit=_select_upload,
        require_asset=require_asset,
    )
    return committed[0] if committed else None


async def commit_manual_storyboard_upload(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    asset_path: str,
    staged_image: Path,
    current_image: Path,
    versions: VersionManager,
    original_filename: str | None,
) -> int:
    """Atomically select an uploaded storyboard with its metadata, version, and claim.

    The new content version is saved where the global storage choice puts new images
    (``formal_commit``); a remote save that is not confirmed publishes nothing.
    """

    metadata: dict[str, str] = {"source": UPLOAD_VERSION_SOURCE}
    if original_filename:
        metadata["original_filename"] = original_filename
    outcomes = []

    def _commit() -> int:
        commit = storyboard_formal_image_callback(
            project_name=project_name,
            script_file=script_file,
            resource_id=shot_id,
            artifact_path=asset_path,
            prompt="",
            versions=versions,
            task_id=None,
            basis=None,
            outcome_box=outcomes,
            project_manager=get_project_manager(),
            selected_upload=True,
        )
        version = commit(staged_image, current_image, metadata)
        if len(outcomes) != 1 or outcomes[0].version != version:
            raise RuntimeError("manual storyboard upload skipped formal image activation")
        return version

    try:
        async with formal_commit(project_name):
            return await run_noninterruptible_sync(_commit)
    finally:
        await asyncio.to_thread(staged_image.unlink, missing_ok=True)  # hard-delete: temp -- staged upload file


async def commit_manual_derivative_upload(
    *,
    project_name: str,
    target: DerivativeSheetTarget,
    staged_image: Path,
    current_image: Path,
    versions: VersionManager,
    original_filename: str | None,
    project_manager: ProjectManager | None = None,
    on_commit: Callable[[ContentVersion], None] | None = None,
) -> int:
    """Select creator-uploaded derivative images as finished content, committing derivative pointers, versions and manifest registration together.

    Uploads replace only images, preserving change descriptions. Planners project basis from uploaded bytes without base
    images/descriptions. New versions follow global storage through ``formal_commit`` and publish only after confirmed
    remote persistence. ``on_commit`` receives newly selected content last in the same commit; errors roll back the whole
    commit. Provider imports record their links here."""

    metadata: dict[str, str] = {"source": UPLOAD_VERSION_SOURCE}
    if original_filename:
        metadata["original_filename"] = original_filename
    outcomes = []

    def _commit() -> int:
        commit = derivative_sheet_commit_callback(
            project_name=project_name,
            target=target,
            prompt="",
            versions=versions,
            task_id=None,
            basis=None,
            outcome_box=outcomes,
            project_manager=project_manager or get_project_manager(),
            selected_upload=True,
            on_commit=on_commit,
        )
        version = commit(staged_image, current_image, metadata)
        if len(outcomes) != 1 or outcomes[0].version != version:
            raise RuntimeError("manual derivative sheet upload skipped formal image activation")
        return version

    try:
        async with formal_commit(project_name):
            return await run_noninterruptible_sync(_commit)
    finally:
        await asyncio.to_thread(staged_image.unlink, missing_ok=True)  # hard-delete: temp -- staged upload file


@dataclass(frozen=True, slots=True)
class DerivativeUploadTarget:
    """Derivative-upload coordinates: actual persisted names, current canonical file and version manager."""

    target: DerivativeSheetTarget
    current_image: Path
    versions: VersionManager


@dataclass(frozen=True, slots=True)
class ManualDerivativeUpload:
    version: int
    #: Affected-file fingerprints, shared with derivative-generation completion events.
    asset_fingerprints: dict[str, int]


def resolve_derivative_upload_target(
    project_manager: ProjectManager, project_name: str, owner_name: str, derivative_name: str
) -> DerivativeUploadTarget:
    """Resolve persisted derivative coordinates with blocking I/O; callers handle threading.

    Raises:
        NotFoundError: Missing project, base or derivative.
        BadRequestError: Canonical path escapes the project."""
    try:
        project_path = project_manager.get_project_path(project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    try:
        target = resolve_derivative_target(project_manager.load_project(project_name), owner_name, derivative_name)
    except KeyError as exc:
        raise NotFoundError("character_not_found", name=owner_name) from exc
    try:
        current_image = safe_join(project_path, target.sheet_path)
    except PathTraversalError as exc:
        raise BadRequestError("invalid_resource_id", resource_id=target.artifact_id) from exc
    return DerivativeUploadTarget(target=target, current_image=current_image, versions=VersionManager(project_path))


async def install_manual_derivative_upload(
    *,
    project_name: str,
    upload: DerivativeUploadTarget,
    png_bytes: bytes,
    original_filename: str | None,
    project_manager: ProjectManager | None = None,
    on_commit: Callable[[ContentVersion], None] | None = None,
) -> ManualDerivativeUpload:
    """Persist a finished derivative PNG through staging/formal submission, then emit the same change event shape as derivative generation.

    Web uploads/provider imports share this path using caller ``project_change_source``. Return means formal persistence
    committed: event failures only log and return empty fingerprints, which callers recover by refreshing project data.

    Raises:
        NotFoundError: Base/derivative deleted concurrently between resolution and submission."""
    # Register staged files immediately; cancellation loses staging returns, so registration drives cleanup;
    # after successful staging, commit_manual_derivative_upload finally cleans up.
    staged_images: list[Path] = []

    def _stage() -> None:
        staged_images.append(stage_uploaded_bytes(png_bytes, upload.current_image))

    try:
        await run_sync_transaction(_stage)
    except BaseException:
        for path in staged_images:
            await run_noninterruptible_sync(path.unlink, missing_ok=True)  # hard-delete: temp -- staged upload file
        raise
    target = upload.target
    try:
        version = await commit_manual_derivative_upload(
            project_name=project_name,
            target=target,
            staged_image=staged_images[0],
            current_image=upload.current_image,
            versions=upload.versions,
            original_filename=original_filename,
            project_manager=project_manager,
            on_commit=on_commit,
        )
    except KeyError as exc:
        raise NotFoundError("asset_derivative_not_found", name=target.derivative_key) from exc
    try:
        fingerprints = await run_sync_transaction(
            emit_generation_success_batch,
            task_type=DERIVATIVE_TASK_TYPE,
            project_name=project_name,
            resource_id=target.artifact_id,
            payload={},
        )
    except Exception:
        # Formal writes committed already; events only refresh other views, so failures do not alter write outcomes.
        logger.warning("衍生资产图上传已提交，变更事件发送失败", exc_info=True)
        fingerprints = {}
    return ManualDerivativeUpload(version=version, asset_fingerprints=fingerprints)


ManualVideoMetadataCommit = Callable[[str | None, Callable[[Path], None]], None]


async def commit_manual_video_upload(
    *,
    project_path: Path,
    versions: VersionManager,
    resource_type: Literal["videos", "reference_videos"],
    resource_id: str,
    script_file: str,
    staged_video: Path,
    current_video: Path,
    thumbnail_file: Path,
    thumbnail_rel: str,
    original_filename: str | None,
    commit_metadata: ManualVideoMetadataCommit,
) -> int:
    """Commit manual video, version selection, thumbnail, script metadata and manifest registration as one formal submission.

    First generate thumbnails from invisible staged video. Submission reuses ProjectManager script/project transactions,
    VersionManager staged activation and Manifest transaction compensation; any failure restores prior selection through
    each layer. Once synchronous commit starts, request cancellation cannot interrupt it."""

    staged_thumbnail = _upload_tmp_path(thumbnail_file)
    try:
        extracted = await extract_video_thumbnail(staged_video, staged_thumbnail)

        def _commit() -> int:
            selected_thumbnail = thumbnail_rel if extracted is not None and staged_thumbnail.is_file() else None
            committed_version: list[int] = []

            def _activate(_script_path: Path) -> None:
                def _claim() -> None:
                    # This upload is selected; planners project basis from its bytes, forgetting basis when projection fails.
                    register_current_resource_artifact(
                        project_path,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        script_file=script_file,
                    )

                with formal_write_transaction(thumbnail_file):
                    if selected_thumbnail is None:
                        # hard-delete: derived -- remove old thumbnails when new uploaded videos have none
                        thumbnail_file.unlink(missing_ok=True)
                    else:
                        thumbnail_file.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(staged_thumbnail, thumbnail_file)

                    metadata: dict[str, str] = {"source": UPLOAD_VERSION_SOURCE}
                    if original_filename:
                        metadata["original_filename"] = original_filename
                    committed_version.append(
                        versions.commit_staged_version(
                            resource_type,
                            resource_id,
                            "",
                            staged_file=staged_video,
                            current_file=current_video,
                            on_commit=_claim,
                            **metadata,
                        )
                    )

            commit_metadata(selected_thumbnail, _activate)
            if not committed_version:
                raise RuntimeError("manual video metadata commit skipped staged activation")
            return committed_version[0]

        return await run_noninterruptible_sync(_commit)
    finally:
        await asyncio.gather(
            asyncio.to_thread(staged_video.unlink, missing_ok=True),  # hard-delete: temp -- staged upload file
            asyncio.to_thread(staged_thumbnail.unlink, missing_ok=True),  # hard-delete: temp -- staged upload file
        )


async def finalize_shot_video_upload(
    *,
    project_name: str,
    script_file: str,
    shot_id: str,
    project_path: Path,
    video_rel: str,
    staged_video: Path,
    versions: VersionManager,
    original_filename: str | None,
) -> int:
    """Submit storyboard video and all sidecars through the shared formal-write interface."""

    def _commit_metadata(thumb_rel: str | None, on_commit: Callable[[Path], None]) -> None:
        get_project_manager().batch_update_scene_assets(
            project_name=project_name,
            script_filename=script_file,
            updates=[
                (shot_id, "video_clip", video_rel),
                (shot_id, "video_uri", None),
                (shot_id, "video_thumbnail", thumb_rel),
            ],
            on_commit=on_commit,
        )

    return await commit_manual_video_upload(
        project_path=project_path,
        versions=versions,
        resource_type="videos",
        resource_id=shot_id,
        script_file=script_file,
        staged_video=staged_video,
        current_video=project_path / video_rel,
        thumbnail_file=project_path / "thumbnails" / f"scene_{shot_id}.jpg",
        thumbnail_rel=f"thumbnails/scene_{shot_id}.jpg",
        original_filename=original_filename,
        commit_metadata=_commit_metadata,
    )
