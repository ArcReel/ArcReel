"""用户自主上传资产图、衍生资产图、分镜图与视频的 finalize 服务层。

复用生成链路的元数据回写、缩略图与版本记录原语，让上传产出的资产
在状态推导、SSE 刷新、版本回滚上与 AI 生成的资产行为一致。
"""

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

# 版本记录里标记「用户手动上传」的 source 值；前端按此显示翻译文案
UPLOAD_VERSION_SOURCE = MANUAL_UPLOAD_VERSION_SOURCE

# 上传策略（shot_uploads 与 reference_videos 两个路由共用，避免口径漂移）。
# 视频宽松校验：只看扩展名与大小上限，宽高比/时长不阻塞——用户自主上传即自己负责。
UPLOAD_IMAGE_EXTENSIONS: tuple[str, ...] = IMAGE_FILE_EXTENSIONS
# 不收 webm：字节原样存为 canonical .mp4，VP8/VP9 在 Safari/剪映一侧解码不可用，
# 错误扩展名还会误导排查；mp4/mov/m4v 同属 ISO BMFF 容器家族
UPLOAD_VIDEO_EXTENSIONS: tuple[str, ...] = (".mp4", ".mov", ".m4v")
UPLOAD_IMAGE_MAX_BYTES = 30 * 1024 * 1024
UPLOAD_VIDEO_MAX_BYTES = 200 * 1024 * 1024

_COPY_CHUNK_SIZE = 1024 * 1024


class UploadValidationError(Exception):
    """上传校验失败。路由层按 (status_code, key, params) 翻译为 HTTP 响应。"""

    def __init__(self, key: str, *, status_code: int = 400, **params: object):
        super().__init__(key)
        self.key = key
        self.status_code = status_code
        self.params = params


class UploadTooLargeError(UploadValidationError):
    """上传字节数超过上限。"""

    def __init__(self, max_bytes: int):
        super().__init__("upload_too_large", status_code=413, max_mb=max_bytes // (1024 * 1024))
        self.max_bytes = max_bytes


def validate_upload(filename: str | None, declared_size: int | None, *, kind: Literal["image", "video"]) -> int:
    """按上传策略校验文件名扩展名与申报大小，返回该类型的字节上限。

    declared_size 只做 Content-Length 预拒；实际写入字节数由落盘函数兜底。
    """
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
    """同目录 dot-tmp 路径，带每次调用唯一的后缀，避免并发上传交错写同一 tmp 文件。"""
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
    """把上传流分块写入 target（不整体读入内存）。

    先写同目录 dot-tmp 再 ``Path.replace``：跨卷 rename 在 Windows 会失败，
    且避免半截文件被 canonical 路径的读取方看到。超限抛 UploadTooLargeError。
    改名前尽力做 faststart（moov 挪到 mdat 之前），失败时保留上传的原样字节。
    """
    tmp_path = _upload_tmp_path(target)

    def _write_tmp() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_limited(src, tmp_path, max_bytes)

    try:
        await run_sync_transaction(_write_tmp)
        await ensure_faststart(tmp_path)
        await run_sync_transaction(tmp_path.replace, target)
    except BaseException:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)  # hard-delete: temp -- 上传临时文件
        raise


async def stage_uploaded_video_stream(src: BinaryIO, target: Path, *, max_bytes: int) -> Path:
    """把上传视频写入 canonical 同目录的唯一 staging 路径，尚不改变正式选择。"""

    staged_file = _upload_tmp_path(target)
    try:
        await save_uploaded_video_stream(src, staged_file, max_bytes=max_bytes)
        return staged_file
    except BaseException:
        await asyncio.to_thread(staged_file.unlink, missing_ok=True)  # hard-delete: temp -- 上传暂存文件
        raise


def write_bytes_atomic(content: bytes, target: Path) -> None:
    """同步版本：把内存中的内容原子写入 target（同 dot-tmp + replace + 失败清理）。

    阻塞 I/O，供调用方自行决定线程化时机——需要与其它阻塞操作（如剧本锁）共享
    同一临界区时（见 `server/services/project/end_frame.py`），不能各自套一层 `asyncio.to_thread`。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _upload_tmp_path(target)
    try:
        tmp_path.write_bytes(content)
        tmp_path.replace(target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)  # hard-delete: temp -- 上传临时文件
        raise


def stage_uploaded_bytes(content: bytes, target: Path) -> Path:
    """Write normalized upload bytes beside the canonical target without selecting them."""

    target.parent.mkdir(parents=True, exist_ok=True)
    staged_file = _upload_tmp_path(target)
    try:
        staged_file.write_bytes(content)
        return staged_file
    except BaseException:
        staged_file.unlink(missing_ok=True)  # hard-delete: temp -- 上传暂存文件
        raise


async def save_uploaded_bytes(content: bytes, target: Path) -> None:
    """把内存中的上传内容原子写入 target（同 dot-tmp + replace + 失败清理）。"""
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
        await asyncio.to_thread(staged_image.unlink, missing_ok=True)  # hard-delete: temp -- 上传暂存文件


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
    """把作者上传的衍生资产图作为成品选中：衍生条目指针、版本记录与清单登记同一次提交。

    上传只替换资产图，变化描述不动；登记由规划器按上传字节投影，本体资产图与描述都不参与。
    新内容版本按全局存储选择保存（``formal_commit``），远端没有确认保存时不发布新版本。
    ``on_commit`` 在同一次提交的最后收到新选中的内容版本，抛错时整次提交回滚；
    导入供应商素材在这里记录关联。
    """

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
        await asyncio.to_thread(staged_image.unlink, missing_ok=True)  # hard-delete: temp -- 上传暂存文件


@dataclass(frozen=True, slots=True)
class DerivativeUploadTarget:
    """一次衍生资产图上传的写入坐标：落盘真名、规范路径上的当前文件与版本管理器。"""

    target: DerivativeSheetTarget
    current_image: Path
    versions: VersionManager


@dataclass(frozen=True, slots=True)
class ManualDerivativeUpload:
    version: int
    #: 受影响文件的指纹，与衍生资产图生成完成的变更事件同一份。
    asset_fingerprints: dict[str, int]


def resolve_derivative_upload_target(
    project_manager: ProjectManager, project_name: str, owner_name: str, derivative_name: str
) -> DerivativeUploadTarget:
    """解析衍生的落盘坐标。阻塞 I/O，由调用方线程化。

    Raises:
        NotFoundError: 项目、本体或衍生不存在。
        BadRequestError: 规范路径越出项目目录。
    """
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
    """把一张 PNG 作为衍生资产图的成品写入：暂存、正式提交，再发出与衍生生成完成同形状的变更事件。

    Web 上传与导入供应商素材共用这条路径。变更来源取调用方的 ``project_change_source``。函数返回即表示
    正式写入已提交：变更事件发送失败只记日志，返回的指纹为空，由调用方刷新项目数据取得。

    Raises:
        NotFoundError: 解析之后、提交之前本体或衍生被并发删除。
    """
    # 暂存文件一落盘就登记：暂存进行中被取消时返回值随之丢弃，按登记回收；
    # 暂存成功后由 commit_manual_derivative_upload 的 finally 回收。
    staged_images: list[Path] = []

    def _stage() -> None:
        staged_images.append(stage_uploaded_bytes(png_bytes, upload.current_image))

    try:
        await run_sync_transaction(_stage)
    except BaseException:
        for path in staged_images:
            await run_noninterruptible_sync(path.unlink, missing_ok=True)  # hard-delete: temp -- 上传暂存文件
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
        # 正式写入已经提交，事件只用于提示其他页面刷新；失败不改写这次写入的结果
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
    """把手动视频、版本选择、缩略图、剧本元数据与清单登记作为一个正式提交。

    缩略图先从不可见的 staging 视频生成。正式提交随后复用 ProjectManager 的剧本/项目
    事务、VersionManager 的 staged activation 与 Manifest 自身的事务补偿；任一步失败
    都会逐层恢复旧选择。同步提交一旦开始便不可被请求取消打断。
    """

    staged_thumbnail = _upload_tmp_path(thumbnail_file)
    try:
        extracted = await extract_video_thumbnail(staged_video, staged_thumbnail)

        def _commit() -> int:
            selected_thumbnail = thumbnail_rel if extracted is not None and staged_thumbnail.is_file() else None
            committed_version: list[int] = []

            def _activate(_script_path: Path) -> None:
                def _claim() -> None:
                    # 选中的已是这次上传，规划器按上传字节投影依据，投影不出即遗忘。
                    register_current_resource_artifact(
                        project_path,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        script_file=script_file,
                    )

                with formal_write_transaction(thumbnail_file):
                    if selected_thumbnail is None:
                        # hard-delete: derived -- 新上传的视频没有缩略图时清掉旧缩略图
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
            asyncio.to_thread(staged_video.unlink, missing_ok=True),  # hard-delete: temp -- 上传暂存文件
            asyncio.to_thread(staged_thumbnail.unlink, missing_ok=True),  # hard-delete: temp -- 上传暂存文件
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
    """通过共享正式写入 seam 提交分镜视频及其所有 sidecar。"""

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
