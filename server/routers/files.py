"""
文件管理路由

处理文件上传和静态资源服务
"""

import asyncio
import logging
import os
import shutil
import stat
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse

from lib.backends.http_status_errors import TextWaitTimeout
from lib.bgm.library import BGM_DIR, BGM_EXTENSIONS, BGM_MAX_BYTES, BgmTrack
from lib.bgm.service import BgmError, BgmLibraryService
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import NoAvailableProviderError, VisionCapabilityError
from lib.episode.episode_ledger import is_derived_episode_name
from lib.episode.episode_paths import (
    episode_source_relpath,
)
from lib.episode.episode_source_commands import (
    EpisodeSourceError,
    add_own_source_episode,
    register_whole_source_file,
    unregister_source_file,
)
from lib.episode.episode_sources import whole_source_files
from lib.episode.source_file_changes import (
    SourceFileChangeError,
    SourceFileChangeOutcome,
    delete_whole_source_file,
    edit_whole_source_file,
    insert_whole_source_file,
    replace_whole_source_file,
)
from lib.episode.source_kinds import SourceKind
from lib.episode.source_trash import trash_source_file
from lib.generation.asset_admission import AssetTarget, idle_asset_write
from lib.infra.api_errors import ApiError, BadRequestError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import GLOBAL_ASSETS_DIRNAME
from lib.infra.image_thumbnails import ensure_image_thumbnail, is_thumbnail_source, snap_thumbnail_width
from lib.infra.image_utils import IMAGE_FILE_EXTENSIONS, normalize_uploaded_image, validate_image_bytes
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.material.formal_objects import formal_commit
from lib.material.global_library import ensure_global_image
from lib.material.work_files import ensure_work_file, hold_work_file
from lib.project.asset_types import ASSET_SPECS, GLOBAL_LIBRARY_ASSET_TYPES, resolve_asset_key, validate_asset_name
from lib.project.project_change_hints import (
    project_change_source,
)
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.project.project_migration_guard import assert_project_migration_ok
from lib.script.source_loader import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    NormalizeResult,
    OnConflict,
    SourceDecodeError,
    SourceLoader,
    UnsupportedFormatError,
)
from lib.speech.audio_utils import (
    AUDIO_REFERENCE_MAX_BYTES,
    AUDIO_REFERENCE_MAX_SECONDS,
    AUDIO_REFERENCE_MIN_SECONDS,
    probe_audio_duration_seconds,
)
from server.dependencies import require_project_migration_ok
from server.i18n import Translator
from server.routers._episode_source_errors import episode_source_http_error
from server.routers._file_responses import cached_file_response, guess_media_type
from server.routers._source_file_changes import (
    ensure_no_displaced_tasks,
    source_file_change_http_error,
    source_file_change_payload,
)
from server.routers._source_uploads import extract_uploaded_source_text, source_loader_http_error
from server.services.currency.upload_finalize import install_manual_asset_sheet_upload

router = APIRouter()

SourceUploadRole = Literal["whole_source", "episode"]

_IMAGE_EXTS: tuple[str, ...] = IMAGE_FILE_EXTENSIONS

public_router = APIRouter()

PUBLIC_MEDIA_DIRS: frozenset[str] = frozenset(
    {
        "storyboards",
        "end_frames",
        "videos",
        "reference_videos",
        "thumbnails",
        "characters",
        "scenes",
        "props",
        "products",
        "grids",
        "audio",
        "bgm",
        "renders",
    }
)
PUBLIC_VERSIONS_DIR = "versions"
PUBLIC_ROOT_MEDIA_STEM = "style_reference"
PUBLIC_MEDIA_EXTENSIONS: frozenset[str] = frozenset({*_IMAGE_EXTS, ".mp4", ".wav", ".mp3", ".m4a"})
_PUBLIC_FILE_HEADERS: dict[str, str] = {"X-Content-Type-Options": "nosniff"}


def _regular_file_stat(path: Path) -> os.stat_result | None:
    """返回普通文件的 stat；不存在或不是普通文件时返回 None（与 ``Path.is_file`` 同口径）。"""
    try:
        stat_result = path.stat()
    except (OSError, ValueError):
        return None
    return stat_result if stat.S_ISREG(stat_result.st_mode) else None


def is_public_media_path(relative_parts: tuple[str, ...]) -> bool:
    """项目内相对路径（按段拆分）是否属于公开端点可读的媒体。"""
    if not relative_parts or any(part.startswith(".") for part in relative_parts):
        return False
    name = Path(relative_parts[-1])
    if name.suffix.lower() not in PUBLIC_MEDIA_EXTENSIONS:
        return False
    if len(relative_parts) == 1:
        return name.stem == PUBLIC_ROOT_MEDIA_STEM and name.suffix.lower() in _IMAGE_EXTS
    top = relative_parts[0]
    if top == PUBLIC_VERSIONS_DIR:
        return len(relative_parts) >= 3 and relative_parts[1] in PUBLIC_MEDIA_DIRS
    return top in PUBLIC_MEDIA_DIRS


def _require_filename(file: UploadFile, _t: Callable[..., str]) -> str:
    if not file.filename:
        raise HTTPException(status_code=400, detail=_t("missing_filename"))
    return file.filename


Naming = Literal["stable_png", "keep_ext", "sequenced", "delegated"]

ContentCheck = Literal["normalize_image", "validate_image", "audio", "delegated"]

MetadataSetter = Callable[[ProjectManager, str, str, str], object]


@dataclass(frozen=True)
class UploadSpec:
    """单个 upload_type 的落盘规则：目标目录、扩展名白名单、体积上限、后处理与元数据回写。

    新增上传类型只在 ``UPLOAD_SPECS`` 登记表项，不复制分支逻辑。``source`` 是唯一例外：
    它由 ``_handle_source_upload`` 全权接管，表项只提供类型校验与扩展名白名单。

    按剧本条目定位（script_file + shot_id）、需回写剧本元数据的上传不入本表，走各自的
    分镜级路由，校验与落盘共用 ``server.services.currency.upload_finalize`` 的 helper。
    """

    allowed_exts: tuple[str, ...]
    subdir: tuple[str, ...]
    naming: Naming
    content_check: ContentCheck
    unsupported_ext_key: str = "unsupported_image_type"
    max_bytes: int | None = None
    metadata_setter: MetadataSetter | None = None
    host_bucket: str | None = None
    host_not_found_key: str = ""
    tracks_stale_audio: bool = False

    def __post_init__(self) -> None:
        if (self.host_bucket is None) != (not self.host_not_found_key):
            raise ValueError("host_bucket 与 host_not_found_key 必须成对登记")
        if self.tracks_stale_audio and self.host_bucket is None:
            raise ValueError("tracks_stale_audio 必须登记宿主约束")


UPLOAD_SPECS: dict[str, UploadSpec] = {
    "source": UploadSpec(
        allowed_exts=(".txt", ".md", ".docx", ".epub", ".pdf"),
        subdir=("source",),
        naming="delegated",
        content_check="delegated",
    ),
    "character": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["character"].subdir,),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_project_character_sheet,
    ),
    "character_ref": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["character"].subdir, "refs"),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_character_reference_image,
    ),
    "character_audio_ref": UploadSpec(
        allowed_exts=(".wav", ".mp3"),
        subdir=(ASSET_SPECS["character"].subdir, "refs_audio"),
        naming="keep_ext",
        content_check="audio",
        unsupported_ext_key="unsupported_audio_type",
        max_bytes=AUDIO_REFERENCE_MAX_BYTES,
        metadata_setter=ProjectManager.update_character_reference_audio,
        host_bucket=ASSET_SPECS["character"].bucket_key,
        host_not_found_key="character_not_found",
        tracks_stale_audio=True,
    ),
    "scene": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["scene"].subdir,),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_scene_sheet,
    ),
    "prop": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["prop"].subdir,),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_prop_sheet,
    ),
    "product": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["product"].subdir,),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_product_sheet,
    ),
    "bgm": UploadSpec(
        allowed_exts=BGM_EXTENSIONS,
        subdir=(BGM_DIR,),
        naming="delegated",
        content_check="delegated",
        unsupported_ext_key="unsupported_audio_type",
        max_bytes=BGM_MAX_BYTES,
    ),
    "product_ref": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["product"].subdir, "refs"),
        naming="sequenced",
        content_check="validate_image",
        metadata_setter=ProjectManager.add_product_reference_image,
        host_bucket=ASSET_SPECS["product"].bucket_key,
        host_not_found_key="product_not_found",
    ),
}

_FORMAL_SHEET_UPLOAD_TYPES = frozenset(ASSET_SPECS)

ALLOWED_EXTENSIONS = {upload_type: list(spec.allowed_exts) for upload_type, spec in UPLOAD_SPECS.items()}


async def _media_work_files() -> AsyncIterator[ExitStack]:
    # FileResponse 在路由返回后才打开文件，使用登记须保留到整个响应结束。
    with ExitStack() as holds:
        yield holds


@public_router.get("/files/{project_name}/{path:path}")
async def serve_project_file(
    project_name: str,
    path: str,
    request: Request,
    _t: Translator,
    holds: Annotated[ExitStack, Depends(_media_work_files, scope="request")],
):
    """服务项目内的媒体文件（图片/视频/音频），范围见 ``is_public_media_path``

    图片可带 ``?w=`` 取 WebP 缩略图（档位与回退规则见 ``lib.infra.image_thumbnails``）；
    缓存头与条件请求对缩略图同样适用，ETag 按缩略图文件计算。
    """
    try:

        def _resolve() -> tuple[ProjectManager, Path, Path, tuple[str, ...]]:
            project_manager = get_project_manager()
            project_dir = project_manager.get_project_path(project_name)

            try:
                file_path = safe_join(project_dir, path)
            except PathTraversalError as exc:
                raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc
            relative_parts = file_path.relative_to(os.path.realpath(project_dir)).parts
            return project_manager, project_dir, file_path, relative_parts

        def _sync(project_manager: ProjectManager, project_dir: Path, file_path: Path, relative_parts: tuple[str, ...]):
            stat_result = _regular_file_stat(file_path) if is_public_media_path(relative_parts) else None
            if stat_result is None:
                raise HTTPException(status_code=404, detail=_t("file_not_found", path=path))

            if thumbnail_width is not None and is_thumbnail_source(file_path):
                thumbnail = ensure_image_thumbnail(
                    file_path,
                    stat_result,
                    cache_dir=project_manager.layout.image_thumbnail_cache_dir / project_dir.name,
                    source_key="/".join(relative_parts),
                    width=thumbnail_width,
                )
                if thumbnail is not None:
                    thumbnail_path, thumbnail_stat = thumbnail
                    return thumbnail_path, thumbnail_stat, guess_media_type(thumbnail_path)
                stat_result = _regular_file_stat(file_path)
                if stat_result is None:
                    raise HTTPException(status_code=404, detail=_t("file_not_found", path=path))

            return file_path, stat_result, guess_media_type(file_path)

        thumbnail_width = snap_thumbnail_width(request.query_params.get("w"))
        project_manager, project_dir, resolved_path, relative_parts = await asyncio.to_thread(_resolve)
        holds.enter_context(hold_work_file(resolved_path))
        holds.enter_context(hold_work_file(project_dir.joinpath(*relative_parts)))
        if is_public_media_path(relative_parts):
            await ensure_work_file(project_dir, "/".join(relative_parts))
        file_path, stat_result, media_type = await asyncio.to_thread(
            _sync, project_manager, project_dir, resolved_path, relative_parts
        )

        immutable = bool(request.query_params.get("v")) or path.startswith("versions/")
        return cached_file_response(
            file_path,
            stat_result,
            request,
            immutable=immutable,
            headers=_PUBLIC_FILE_HEADERS,
            media_type=media_type,
        )
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@public_router.get("/global-assets/{asset_type}/{filename}")
async def serve_global_asset(
    asset_type: str,
    filename: str,
    request: Request,
    _t: Translator,
    holds: Annotated[ExitStack, Depends(_media_work_files, scope="request")],
):
    """服务 global_assets 下的全局资产媒体文件（仅全局库类型：character/scene/prop）"""
    if asset_type not in GLOBAL_LIBRARY_ASSET_TYPES:
        raise HTTPException(status_code=400, detail=_t("invalid_asset_type"))
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail=_t("invalid_asset_filename"))

    def _resolve() -> tuple[Path, Path]:
        project_manager = get_project_manager()
        root = project_manager.get_global_assets_root()
        try:
            return project_manager.data_root, safe_join(root, asset_type, filename)
        except PathTraversalError as exc:
            raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc

    def _sync(path: Path) -> tuple[Path, os.stat_result, str]:
        stat_result = _regular_file_stat(path) if path.suffix.lower() in PUBLIC_MEDIA_EXTENSIONS else None
        if stat_result is None:
            raise HTTPException(status_code=404, detail=_t("file_not_found", path=filename))
        return path, stat_result, guess_media_type(path)

    data_root, resolved_path = await asyncio.to_thread(_resolve)
    holds.enter_context(hold_work_file(resolved_path))
    holds.enter_context(hold_work_file(data_root / GLOBAL_ASSETS_DIRNAME / asset_type / filename))
    await ensure_global_image(data_root, f"{GLOBAL_ASSETS_DIRNAME}/{asset_type}/{filename}")
    path, stat_result, media_type = await asyncio.to_thread(_sync, resolved_path)

    query = request.query_params
    immutable = bool(query.get("fp") or query.get("v"))
    return cached_file_response(
        path, stat_result, request, immutable=immutable, headers=_PUBLIC_FILE_HEADERS, media_type=media_type
    )


@router.post("/projects/{project_name}/upload/{upload_type}")
async def upload_file(
    project_name: str,
    upload_type: str,
    _t: Translator,
    file: UploadFile = File(...),
    name: str | None = None,
    on_conflict: OnConflict = "fail",
    role: SourceUploadRole = "whole_source",
    insert_at: Annotated[int | None, Query(ge=0)] = None,
    source_kind: SourceKind | None = None,
    revision: str | None = None,
):
    """
    上传文件

    Args:
        project_name: 项目名称
        upload_type: 上传类型 (source/character/character_ref/character_audio_ref/scene/prop/product/product_ref/bgm)
        file: 上传的文件
        name: 可选，用于角色/场景/道具/商品名称（自动更新元数据）；product_ref 必填；
            分镜/视频上传走 shot_uploads 路由
        on_conflict: source 类型独有 — fail / replace / rename
        role: source 类型独有 — whole_source 登记为整本源文的文件，接在清单末尾；episode 登记为
            播出顺序末尾的一集自带原文的集
        insert_at: role=whole_source 独有 — 登记后文件在整本源文清单里的下标，缺省或超出末尾时接在末尾
        source_kind: source 类型独有 — 剧情演绎项目这份原文的源文件类型，缺省为小说；其他创作类型忽略
        revision: role=whole_source 独有 — 确认过的受影响集清单的版本。插入处落在一个跨文件的集内部，或
            on_conflict=replace 覆盖已登记的整本源文文件时，有受影响的集而它缺省或已过时，不写入，返回
            ``status=confirmation_required`` 与受影响集清单
    """
    spec = UPLOAD_SPECS.get(upload_type)
    if spec is None:
        raise HTTPException(status_code=400, detail=_t("invalid_upload_type", upload_type=upload_type))

    original_filename = _require_filename(file, _t)

    ext = Path(original_filename).suffix.lower()
    if ext not in spec.allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=_t(spec.unsupported_ext_key, ext=ext, allowed=", ".join(spec.allowed_exts)),
        )

    if name:
        try:
            name = validate_asset_name(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=name)) from exc

    if upload_type == "source":
        await asyncio.to_thread(assert_project_migration_ok, project_name)
        if role == "episode":
            return await _handle_episode_source_upload(
                project_name=project_name, file=file, source_kind=source_kind, _t=_t
            )
        return await _handle_source_upload(
            project_name=project_name,
            file=file,
            on_conflict=on_conflict,
            insert_at=insert_at,
            source_kind=source_kind,
            revision=revision,
            _t=_t,
        )

    try:
        content = await file.read()

        if spec.max_bytes is not None and len(content) > spec.max_bytes:
            raise HTTPException(
                status_code=400,
                detail=_t("upload_too_large", max_mb=spec.max_bytes // (1024 * 1024)),
            )

        if upload_type == "bgm":
            return await _handle_bgm_upload(project_name, original_filename, content, _t)

        if spec.content_check == "audio":
            try:
                duration = await probe_audio_duration_seconds(content, ext)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=_t("invalid_audio_file")) from exc
            if duration is not None and not (AUDIO_REFERENCE_MIN_SECONDS <= duration <= AUDIO_REFERENCE_MAX_SECONDS):
                raise HTTPException(
                    status_code=400,
                    detail=_t(
                        "audio_duration_out_of_range",
                        min_seconds=int(AUDIO_REFERENCE_MIN_SECONDS),
                        max_seconds=int(AUDIO_REFERENCE_MAX_SECONDS),
                    ),
                )

        stem = name or Path(original_filename).stem

        def _sync():
            manager = get_project_manager()
            project_dir = manager.get_project_path(project_name)

            if spec.host_bucket is not None:
                hosts = manager.load_project(project_name).get(spec.host_bucket) or {}
                if not name or resolve_asset_key(hosts, name) is None:
                    raise HTTPException(status_code=404, detail=_t(spec.host_not_found_key, name=name or ""))

            target_dir = project_dir.joinpath(*spec.subdir)
            filename = f"{stem}.png" if spec.naming == "stable_png" else f"{stem}{ext}"

            target_dir.mkdir(parents=True, exist_ok=True)

            nonlocal content
            if spec.content_check == "normalize_image":
                try:
                    content, normalized_ext = normalize_uploaded_image(content, ext)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file")) from exc
                filename = Path(filename).with_suffix(normalized_ext).name
            elif spec.content_check == "validate_image":
                try:
                    validate_image_bytes(content)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file")) from exc

            if spec.naming == "sequenced":
                seq = 1
                while True:
                    candidate = target_dir / f"{stem}_{seq}{ext or '.png'}"
                    try:
                        candidate.touch(exist_ok=False)
                        break
                    except FileExistsError:
                        seq += 1
                filename = candidate.name

            target_path = target_dir / filename
            relative_path = "/".join((*spec.subdir, filename))

            if spec.tracks_stale_audio and name:
                try:
                    with project_change_source("webui"):
                        manager.install_character_reference_audio(project_name, name, relative_path, content)
                except KeyError as exc:
                    raise HTTPException(status_code=404, detail=_t(spec.host_not_found_key, name=name)) from exc
            else:
                if upload_type in _FORMAL_SHEET_UPLOAD_TYPES and name:
                    with project_change_source("webui"):
                        install_manual_asset_sheet_upload(
                            project_manager=manager,
                            project_name=project_name,
                            asset_type=upload_type,
                            name=name,
                            sheet_path=relative_path,
                            content=content,
                            original_filename=original_filename,
                        )
                else:
                    with open(target_path, "wb") as f:
                        f.write(content)

                    if spec.metadata_setter is not None and name:
                        try:
                            with project_change_source("webui"):
                                spec.metadata_setter(manager, project_name, name, relative_path)
                        except KeyError as exc:
                            if spec.host_bucket is not None:
                                # hard-delete: rollback -- 宿主资产已消失时移除刚上传的文件
                                target_path.unlink(missing_ok=True)
                                raise HTTPException(
                                    status_code=404, detail=_t(spec.host_not_found_key, name=name)
                                ) from exc

            return {
                "success": True,
                "filename": filename,
                "path": relative_path,
                "url": f"/api/v1/files/{project_name}/{relative_path}",
            }

        targets = []
        if upload_type in _FORMAL_SHEET_UPLOAD_TYPES:
            targets.append(AssetTarget(upload_type, stem, "image"))
        elif upload_type == "character_ref":
            targets.append(AssetTarget("character", stem, "image"))
        elif spec.tracks_stale_audio and name:
            targets.append(AssetTarget("character", name, "voice"))

        async with AsyncExitStack() as storage_settlement, idle_asset_write(project_name, *targets):
            if upload_type in _FORMAL_SHEET_UPLOAD_TYPES and name:
                await storage_settlement.enter_async_context(formal_commit(project_name))
            return await run_sync_transaction(_sync)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


_BGM_ERROR_STATUS: dict[str, tuple[int, str]] = {
    "bgm_unsupported_type": (400, "unsupported_audio_type"),
    "bgm_too_large": (400, "upload_too_large"),
    "bgm_invalid_audio": (400, "invalid_audio_file"),
    "bgm_silent": (400, "bgm_silent"),
    "bgm_ffmpeg_unavailable": (503, "bgm_ffmpeg_unavailable"),
}


def _bgm_http_error(exc: BgmError, _t: Translator) -> HTTPException:
    if exc.code == "project_not_found":
        return HTTPException(status_code=404, detail=_t("project_not_found", name=exc.params.get("project", "")))
    status, key = _BGM_ERROR_STATUS[exc.code]
    return HTTPException(status_code=status, detail=_t(key, **exc.params))


async def _handle_bgm_upload(project_name: str, filename: str, content: bytes, _t: Translator) -> dict[str, object]:
    """登记一首项目级 BGM，返回它在 BGM 列表里的形态。"""
    await asyncio.to_thread(assert_project_migration_ok, project_name)
    try:
        track = await BgmLibraryService(get_project_manager()).upload(project_name, filename=filename, content=content)
    except BgmError as exc:
        raise _bgm_http_error(exc, _t) from exc
    return {"success": True, "bgm": _bgm_view(project_name, track)}


def _bgm_view(project_name: str, track: BgmTrack) -> dict[str, object]:
    return {
        "id": track.id,
        "name": track.name,
        "duration": track.duration,
        "gain": round(track.gain, 6),
        "path": track.file,
        "url": f"/api/v1/files/{project_name}/{track.file}",
    }


@router.get("/projects/{project_name}/bgm")
async def list_bgm(project_name: str, _t: Translator):
    """项目里已上传的 BGM，按上传先后排列；``gain`` 是把响度统一到 −16 LUFS 的线性增益。"""
    try:
        tracks = await BgmLibraryService(get_project_manager()).list(project_name)
    except BgmError as exc:
        raise _bgm_http_error(exc, _t) from exc
    return {"bgm": [_bgm_view(project_name, track) for track in tracks]}


@router.delete("/projects/{project_name}/characters/{name}/reference-audio")
async def delete_character_reference_audio(project_name: str, name: str, _t: Translator):
    """删除角色的参考音频样本：清空 project.json 字段并移除文件。"""
    try:

        def _sync():
            manager = get_project_manager()
            try:
                with project_change_source("webui"):
                    manager.clear_character_reference_audio(project_name, name)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=_t("character_not_found", name=name)) from exc

            return {"success": True}

        async with idle_asset_write(project_name, AssetTarget("character", name, "voice")):
            return await run_sync_transaction(_sync)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


async def _handle_source_upload(
    *,
    project_name: str,
    file: UploadFile,
    on_conflict: OnConflict,
    insert_at: int | None,
    source_kind: SourceKind | None,
    revision: str | None,
    _t: Translator,
):
    """Source 分支：通过 SourceLoader 规范化为 UTF-8 .txt，并按需备份原始字节，登记为整本源文的文件。

    插入整本源文与覆盖已登记的整本源文文件都经 :mod:`lib.episode.source_file_changes`，重映射触及它的切出集。
    """
    original_filename = _require_filename(file, _t)
    normalized_name = f"{Path(original_filename).stem}.txt"
    if is_derived_episode_name(normalized_name):
        raise HTTPException(status_code=400, detail=_t("source_name_reserved"))

    try:
        manager = get_project_manager()
        project_dir = await asyncio.to_thread(manager.get_project_path, project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc

    project = await asyncio.to_thread(manager.load_project, project_name)
    if on_conflict == "replace" and f"source/{normalized_name}" in whole_source_files(project):
        text = await asyncio.to_thread(extract_uploaded_source_text, file, _t)

        def _replace() -> SourceFileChangeOutcome:
            with project_change_source("webui"):
                return replace_whole_source_file(
                    manager, project_name, normalized_name, text, source_kind=source_kind, revision=revision
                )

        try:
            await ensure_no_displaced_tasks(
                project_name,
                revision,
                lambda: replace_whole_source_file(
                    manager,
                    project_name,
                    normalized_name,
                    text,
                    source_kind=source_kind,
                    revision=revision,
                    dry_run=True,
                ),
                _t,
            )
            outcome = await run_sync_transaction(_replace)
        except SourceFileChangeError as exc:
            raise source_file_change_http_error(exc, _t) from exc
        payload = source_file_change_payload(outcome, project, _t)
        if not outcome.applied:
            return {"success": False, **payload}
        relative_path = f"source/{normalized_name}"
        return {
            "success": True,
            **payload,
            "filename": normalized_name,
            "path": relative_path,
            "url": f"/api/v1/files/{project_name}/{relative_path}",
            "normalized": True,
            "original_kept": False,
            "original_filename": original_filename,
        }

    loaded: dict[str, NormalizeResult] = {}

    def _sync() -> SourceFileChangeOutcome:
        with tempfile.NamedTemporaryFile(suffix=Path(original_filename).suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with tmp_path.open("wb") as out:
                shutil.copyfileobj(file.file, out)

            def _write(source_dir: Path, undo: ExitStack) -> str:
                if on_conflict == "replace":
                    trash_source_file(project_dir, f"source/{normalized_name}", undo)
                result = SourceLoader.load(
                    tmp_path,
                    source_dir,
                    original_filename=original_filename,
                    on_conflict=on_conflict,
                )
                # hard-delete: rollback -- 源文件写入失败时撤销本次写入
                undo.callback(result.normalized_path.unlink, missing_ok=True)
                if result.raw_path is not None:
                    # hard-delete: rollback -- 源文件写入失败时撤销本次写入
                    undo.callback(result.raw_path.unlink, missing_ok=True)
                loaded["result"] = result
                return f"source/{result.normalized_path.name}"

            with project_change_source("webui"):
                outcome, _rel = insert_whole_source_file(
                    manager, project_name, _write, index=insert_at, source_kind=source_kind, revision=revision
                )
            return outcome
        finally:
            tmp_path.unlink(missing_ok=True)  # hard-delete: temp -- 上传临时文件

    try:
        outcome = await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        if await asyncio.to_thread(project_dir.exists):
            raise
        raise NotFoundError("project_not_found", name=project_name) from exc
    except (UnsupportedFormatError, FileSizeExceededError, SourceDecodeError, CorruptFileError) as exc:
        raise source_loader_http_error(exc, _t) from exc
    except SourceFileChangeError as exc:
        raise source_file_change_http_error(exc, _t) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "existing": exc.existing,
                "suggested_name": exc.suggested_name,
                "message": _t(
                    "source_conflict",
                    existing=exc.existing,
                    suggested=exc.suggested_name,
                ),
            },
        ) from exc

    payload = source_file_change_payload(outcome, project, _t)
    if not outcome.applied:
        return {"success": False, **payload}
    result = loaded["result"]
    relative_path = f"source/{result.normalized_path.name}"
    return {
        "success": True,
        **payload,
        "filename": result.normalized_path.name,
        "path": relative_path,
        "url": f"/api/v1/files/{project_name}/{relative_path}",
        "normalized": True,
        "original_kept": result.raw_path is not None,
        "original_filename": result.original_filename,
        "used_encoding": result.used_encoding,
        "chapter_count": result.chapter_count,
    }


async def _handle_episode_source_upload(
    *, project_name: str, file: UploadFile, source_kind: SourceKind | None, _t: Translator
):
    """逐集原文：规范化为文本，登记为播出顺序末尾的一集自带原文的集。"""
    original_filename = _require_filename(file, _t)

    try:
        manager = get_project_manager()
        project_dir = await asyncio.to_thread(manager.get_project_path, project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc

    def _sync() -> int:
        with tempfile.NamedTemporaryFile(suffix=Path(original_filename).suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with tmp_path.open("wb") as out:
                shutil.copyfileobj(file.file, out)
            extracted = SourceLoader.extract(tmp_path, original_filename=original_filename)
        finally:
            tmp_path.unlink(missing_ok=True)  # hard-delete: temp -- 上传临时文件
        with project_change_source("webui"), manager.locked_source_registration(project_name) as (_dir, project, undo):
            return add_own_source_episode(project_dir, project, extracted.text, undo=undo, source_kind=source_kind)

    try:
        episode = await run_sync_transaction(_sync)
    except UnsupportedFormatError as exc:
        raise HTTPException(status_code=400, detail=_t("source_unsupported_format", ext=exc.ext)) from exc
    except FileSizeExceededError as exc:
        raise HTTPException(
            status_code=413,
            detail=_t(
                "source_too_large",
                filename=exc.filename,
                size_mb=round(exc.size_bytes / 1024 / 1024, 1),
                limit_mb=round(exc.limit_bytes / 1024 / 1024, 1),
            ),
        ) from exc
    except SourceDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=_t("source_decode_failed", filename=exc.filename, tried=", ".join(exc.tried_encodings)),
        ) from exc
    except CorruptFileError as exc:
        raise HTTPException(
            status_code=422, detail=_t("source_corrupt_file", filename=exc.filename, reason=exc.reason)
        ) from exc
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t) from exc

    relative_path = episode_source_relpath(episode)
    return {
        "success": True,
        "episode": episode,
        "filename": Path(relative_path).name,
        "path": relative_path,
        "original_filename": original_filename,
    }


@router.get("/projects/{project_name}/files")
async def list_project_files(project_name: str, _t: Translator):
    """列出项目中的所有文件"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            files = {
                "source": [],
                "characters": [],
                "scenes": [],
                "props": [],
                "products": [],
                "storyboards": [],
                "videos": [],
                "output": [],
            }

            for subdir, file_list in files.items():
                subdir_path = project_dir / subdir
                if not subdir_path.exists():
                    continue
                raw_by_stem: dict[str, str] = {}
                if subdir == "source":
                    raw_dir = subdir_path / "raw"
                    if raw_dir.exists():
                        for raw_f in sorted(raw_dir.iterdir()):
                            if raw_f.is_file():
                                raw_by_stem[raw_f.stem] = raw_f.name
                for f in subdir_path.iterdir():
                    if f.is_file() and not f.name.startswith("."):
                        entry = {
                            "name": f.name,
                            "size": f.stat().st_size,
                            "url": f"/api/v1/files/{project_name}/{subdir}/{f.name}",
                        }
                        if subdir == "source":
                            entry["raw_filename"] = raw_by_stem.get(Path(f.name).stem)
                        file_list.append(entry)

            return {"files": files}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/projects/{project_name}/source/{filename}")
async def get_source_file(project_name: str, filename: str, _t: Translator):
    """获取 source 文件的文本内容"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            try:
                source_path = safe_join(project_dir, "source", filename)
            except PathTraversalError as exc:
                raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc

            if not source_path.exists():
                raise HTTPException(status_code=404, detail=_t("file_not_found", path=filename))

            return source_path.read_text(encoding="utf-8")

        content = await asyncio.to_thread(_sync)
        return PlainTextResponse(content)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=_t("invalid_encoding")) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


async def _ensure_no_displaced_tasks_for_whole_source(
    project_name: str,
    filename: str,
    revision: str | None,
    preview: Callable[[ProjectManager], SourceFileChangeOutcome],
    _t: Translator,
) -> None:
    """``source/<filename>`` 是已登记的整本源文文件时，执行前拦要移除或转为无原文的集的在途任务。"""
    if revision is None:
        return
    manager = get_project_manager()
    project = await asyncio.to_thread(manager.load_project, project_name)
    if f"source/{filename}" in whole_source_files(project):
        await ensure_no_displaced_tasks(project_name, revision, lambda: preview(manager), _t)


@router.put("/projects/{project_name}/source/{filename}", dependencies=[Depends(require_project_migration_ok)])
async def update_source_file(
    project_name: str,
    filename: str,
    _t: Translator,
    content: str = Body(..., media_type="text/plain"),
    revision: str | None = None,
):
    """更新或创建 source 文件；新建的文件登记为整本源文的文件，接在清单末尾。

    已登记的整本源文文件按编辑处理（:func:`lib.episode.source_file_changes.edit_whole_source_file`）：有受影响的集而
    ``revision`` 缺省或已过时时不写入，返回 ``status=confirmation_required`` 与受影响集清单。
    ``episode_N.txt`` 是集原文文件名，这里拒绝：集原文经集页填写，切出集的集原文由分集规划派生。
    """
    if is_derived_episode_name(filename):
        raise HTTPException(status_code=400, detail=_t("source_name_reserved"))
    try:
        await _ensure_no_displaced_tasks_for_whole_source(
            project_name,
            filename,
            revision,
            lambda manager: edit_whole_source_file(
                manager, project_name, filename, content, revision=revision, dry_run=True
            ),
            _t,
        )

        def _sync():
            manager = get_project_manager()
            project_dir = manager.get_project_path(project_name)
            project = manager.load_project(project_name)
            if f"source/{filename}" in whole_source_files(project):
                outcome = edit_whole_source_file(manager, project_name, filename, content, revision=revision)
                payload = source_file_change_payload(outcome, project, _t)
                return {"success": outcome.applied, **payload, "path": f"source/{filename}"}

            with manager.locked_source_registration(project_name) as (_source_dir, project, _undo):
                try:
                    source_path = safe_join(project_dir, "source", filename)
                except PathTraversalError as exc:
                    raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc

                created = not source_path.exists()
                source_path.write_text(content, encoding="utf-8")
                rel = f"source/{filename}"
                if created:
                    register_whole_source_file(project, rel)
            return {"success": True, "path": f"source/{filename}"}

        with project_change_source("webui"):
            return await run_sync_transaction(_sync)

    except SourceFileChangeError as exc:
        raise source_file_change_http_error(exc, _t) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.delete("/projects/{project_name}/source/{filename}", dependencies=[Depends(require_project_migration_ok)])
async def delete_source_file(project_name: str, filename: str, _t: Translator, revision: str | None = None):
    """删除 source 文件，并撤销它的登记：移出整本源文；是自带原文的集的集文件时，该集转为无原文。

    已登记的整本源文文件按删除处理（:func:`lib.episode.source_file_changes.delete_whole_source_file`）：有受影响的
    集而 ``revision`` 缺省或已过时时不删除，返回 ``status=confirmation_required`` 与受影响集清单。
    切出集的集文件由分集规划派生，这里拒绝删除。
    """
    try:
        await _ensure_no_displaced_tasks_for_whole_source(
            project_name,
            filename,
            revision,
            lambda manager: delete_whole_source_file(manager, project_name, filename, revision=revision, dry_run=True),
            _t,
        )

        def _sync():
            manager = get_project_manager()
            project_dir = manager.get_project_path(project_name)
            project = manager.load_project(project_name)
            if f"source/{filename}" in whole_source_files(project):
                outcome = delete_whole_source_file(manager, project_name, filename, revision=revision)
                return {"success": outcome.applied, **source_file_change_payload(outcome, project, _t)}

            with manager.locked_source_registration(project_name) as (_source_dir, project, undo):
                try:
                    source_path = safe_join(project_dir, "source", filename)
                except PathTraversalError as exc:
                    raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc
                if not source_path.exists():
                    raise HTTPException(status_code=404, detail=_t("file_not_found", path=filename))
                unregister_source_file(project, f"source/{filename}")
                trash_source_file(project_dir, f"source/{filename}", undo)
            return {"success": True}

        with project_change_source("webui"):
            return await run_sync_transaction(_sync)

    except SourceFileChangeError as exc:
        raise source_file_change_http_error(exc, _t) from exc
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t, filename=filename) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post("/projects/{project_name}/style-image")
async def upload_style_image(project_name: str, _t: Translator, file: UploadFile = File(...)):
    """
    上传风格参考图并分析风格

    1. 保存图片到 projects/{project_name}/style_reference.png
    2. 调用文本供应商分析风格
    3. 更新 project.json 的 style_image 和 style_description 字段
    """
    original_filename = _require_filename(file, _t)

    ext = Path(original_filename).suffix.lower()
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(
            status_code=400,
            detail=_t("unsupported_image_type", ext=ext, allowed=".png, .jpg, .jpeg, .webp"),
        )

    try:
        content = await file.read()

        def _sync_prepare():
            project_dir = get_project_manager().get_project_path(project_name)
            try:
                content_norm, new_ext = normalize_uploaded_image(content, Path(original_filename).suffix.lower())
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=_t("invalid_image_file")) from exc
            style_filename = f"style_reference{new_ext}"

            return project_dir / style_filename, style_filename, content_norm

        output_path, style_filename, content_norm = await asyncio.to_thread(_sync_prepare)

        from lib.backends.providers import CallPurpose
        from lib.backends.text_backends.base import ImageInput, TextGenerationRequest, TextTaskType
        from lib.backends.text_generator import TextGenerator
        from lib.prompts.prompt_templates.builtin import builtin_templates

        generator = await TextGenerator.create(
            TextTaskType.STYLE_ANALYSIS, project_name, purpose=CallPurpose.STYLE_ANALYSIS
        )
        await run_sync_transaction(output_path.write_bytes, content_norm)
        result = await generator.generate(
            TextGenerationRequest(
                prompt=builtin_templates.render("text/style_analysis"), images=[ImageInput(path=output_path)]
            ),
            project_name=project_name,
        )
        style_description = result.text

        def _sync_save():
            def _mutate(project_data: dict) -> None:
                project_data["style_image"] = style_filename
                project_data["style_description"] = style_description
                project_data.pop("style_template_id", None)
                project_data["style"] = ""

            with project_change_source("webui"):
                get_project_manager().update_project(project_name, _mutate)

        await run_sync_transaction(_sync_save)

        return {
            "success": True,
            "style_image": style_filename,
            "style_description": style_description,
            "url": f"/api/v1/files/{project_name}/{style_filename}",
        }

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except NoAvailableProviderError as exc:
        raise BadRequestError("text_default_model_not_set").with_diagnostic(
            {"code": "text_default_model_not_set"}
        ) from exc
    except TextWaitTimeout as exc:
        raise ApiError(exc.code, status_code=504).with_diagnostic({"code": exc.code}) from exc
    except VisionCapabilityError as e:
        raise BadRequestError(
            "vision_model_required", provider=e.provider_id, model=e.model_id, task=e.task_type.value
        ) from e
    except ModelNotInCatalogError:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
