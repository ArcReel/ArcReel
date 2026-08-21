"""assets 全局资产库路由。"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from lib.api_errors import NotFoundError
from lib.artifact_activation import register_artifact_entries_atomically, resolve_current_artifact_target
from lib.artifact_manifest import ArtifactKey
from lib.asset_types import (
    BUCKET_KEY,
    GLOBAL_LIBRARY_ASSET_TYPES,
    SHEET_KEY,
    ProjectAssetNameConflictError,
    asset_name_comparison_key,
    ensure_project_asset_name_available,
    find_project_asset_name,
    localize_asset_type,
    resolve_asset_key,
    validate_asset_name,
)
from lib.db import async_session_factory
from lib.db.repositories.asset_repo import AssetRepository
from lib.db.repositories.asset_resource_repo import AssetResourceRepository
from lib.i18n import Translator
from lib.project_manager import ProjectManager, get_project_manager
from server.routers._asset_router_factory import localize_project_asset_name_conflict
from server.services.project_asset_links import (
    ProjectAssetLinkError,
    ProjectAssetLinkNotFound,
)
from server.services.project_asset_links import (
    configure_project_asset_link as configure_project_asset_link_service,
)
from server.services.project_asset_links import (
    link_project_asset as link_project_asset_service,
)
from server.services.project_asset_links import (
    unlink_project_asset as unlink_project_asset_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assets", tags=["全局资产库"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _validate_asset_name(name: str, _t: Translator) -> str:
    """HTTP 边界包装：路径不安全的名字（分隔符 / 空字节 / ..）返回 400。"""
    try:
        return validate_asset_name(name)
    except ValueError:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=name))


def _serialize(asset) -> dict:
    resources = [
        {
            "id": resource.id,
            "key": resource.resource_key,
            "origin": resource.origin,
            "media_type": resource.media_type,
            "mime_type": resource.mime_type,
            "path": resource.path,
            "byte_size": resource.byte_size,
            "is_primary": resource.path in {asset.image_path, asset.audio_path},
        }
        for resource in asset.resources
    ]
    return {
        "id": asset.id,
        "type": asset.type,
        "name": asset.name,
        "description": asset.description,
        "voice_style": asset.voice_style,
        "image_path": asset.image_path,
        "audio_path": asset.audio_path,
        "source_project": asset.source_project,
        "external_source": asset.external_source,
        "external_id": asset.external_id,
        "voice_id": asset.voice_id,
        "aliases": [alias.alias for alias in asset.aliases],
        "resources": resources,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


async def _save_upload(file: UploadFile, asset_type: str, _t: Translator) -> str:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=415, detail=_t("asset_unsupported_format"))

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_t("asset_upload_too_large"))

    root = get_project_manager().get_global_assets_root() / asset_type
    uid = uuid.uuid4().hex
    target = root / f"{uid}{ext}"
    await asyncio.to_thread(target.write_bytes, data)
    # 存相对路径（相对 projects_root）
    return f"_global_assets/{asset_type}/{uid}{ext}"


def _delete_global_asset_file(rel_path: str) -> None:
    path = get_project_manager().projects_root / rel_path
    try:
        path.unlink()
    except FileNotFoundError:
        # 文件已不存在（并发删除或 create 回滚）视为成功，忽略即可
        return
    except OSError:
        logger.warning("delete global asset file failed: %s", rel_path)


@router.get("")
async def list_assets(
    _t: Translator,
    type: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    async with async_session_factory() as s:
        items = await AssetRepository(s).list(type=type, q=q, limit=limit, offset=offset)
        return {"items": [_serialize(a) for a in items]}


class ProjectAssetLinkRequest(BaseModel):
    project_name: str
    resource_type: str
    resource_id: str
    asset_id: str


@router.post("/project-links")
async def link_project_asset(req: ProjectAssetLinkRequest, _t: Translator):
    try:
        entry, asset = await link_project_asset_service(
            req.project_name,
            req.resource_type,
            req.resource_id,
            req.asset_id,
            manager=get_project_manager(),
            session_factory=async_session_factory,
        )
    except ProjectAssetLinkNotFound as exc:
        raise HTTPException(status_code=404, detail=_t("asset_not_found", name=str(exc))) from exc
    except ProjectAssetLinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("asset_target_project_not_found", project=req.project_name) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_t("asset_not_found", name=req.resource_id)) from exc
    return {"success": True, "project_asset": entry, "asset": _serialize(asset)}


@router.delete("/project-links/{project_name}/{resource_type}/{resource_id}")
async def unlink_project_asset(project_name: str, resource_type: str, resource_id: str, _t: Translator):
    """Remove both the confirmed link and its stale extraction match."""

    try:
        entry = await unlink_project_asset_service(
            project_name, resource_type, resource_id, manager=get_project_manager()
        )
    except ProjectAssetLinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("asset_target_project_not_found", project=project_name) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_t("asset_not_found", name=resource_id)) from exc
    return {"success": True, "project_asset": entry}


class ProjectAssetLinkConfigRequest(BaseModel):
    project_name: str
    resource_type: str
    resource_id: str
    image_usage: str | None = None
    voice_source: str | None = None


@router.patch("/project-links")
async def configure_project_asset_link(req: ProjectAssetLinkConfigRequest, _t: Translator):
    try:
        entry, asset = await configure_project_asset_link_service(
            req.project_name,
            req.resource_type,
            req.resource_id,
            image_usage=req.image_usage,
            voice_source=req.voice_source,
            manager=get_project_manager(),
            session_factory=async_session_factory,
        )
    except ProjectAssetLinkNotFound as exc:
        raise HTTPException(status_code=404, detail=_t("asset_not_found", name=str(exc))) from exc
    except ProjectAssetLinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("asset_target_project_not_found", project=req.project_name) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_t("asset_not_found", name=req.resource_id)) from exc
    return {"success": True, "project_asset": entry, "asset": _serialize(asset)}


@router.get("/{asset_id}")
async def get_asset(asset_id: str, _t: Translator):
    async with async_session_factory() as s:
        a = await AssetRepository(s).get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        return {"asset": _serialize(a)}


@router.post("")
async def create_asset(
    _t: Translator,
    type: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    voice_style: str = Form(""),
    image: UploadFile | None = File(None),
):
    if type not in GLOBAL_LIBRARY_ASSET_TYPES:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_type"))
    name = _validate_asset_name(name, _t)

    # 1) 先落盘再 create；IntegrityError 路径负责清理 orphan
    image_path: str | None = None
    if image is not None and image.filename:
        image_path = await _save_upload(image, type, _t)

    # 2) 真正 create；任何失败路径都必须清理已落盘文件，保证 DB/磁盘一致
    try:
        async with async_session_factory() as s:
            repo = AssetRepository(s)
            try:
                a = await repo.create(
                    type=type,
                    name=name,
                    description=description,
                    voice_style=voice_style,
                    image_path=image_path,
                    source_project=None,
                )
                await s.commit()
                a = await repo.get_by_id(a.id)
                assert a is not None
            except IntegrityError:
                await s.rollback()
                if image_path:
                    _delete_global_asset_file(image_path)
                    image_path = None
                raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=name))
    except HTTPException:
        raise
    except Exception:
        # 其它错误路径也不留 orphan
        if image_path:
            _delete_global_asset_file(image_path)
        raise

    return {"asset": _serialize(a)}


class UpdateAssetRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    voice_style: str | None = None


@router.patch("/{asset_id}")
async def update_asset(
    asset_id: str,
    req: UpdateAssetRequest,
    _t: Translator,
):
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if "name" in patch:
        patch["name"] = _validate_asset_name(patch["name"], _t)
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        if "name" in patch and patch["name"] != a.name:
            if await repo.exists(a.type, patch["name"]):
                raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=patch["name"]))
        try:
            a = await repo.update(asset_id, **patch)
            await s.commit()
            await s.refresh(a)
        except IntegrityError:
            await s.rollback()
            raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=patch.get("name", "")))
    return {"asset": _serialize(a)}


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(asset_id: str, _t: Translator):
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if a:
            file_paths = {resource.path for resource in a.resources}
            file_paths.update(path for path in (a.image_path, a.audio_path) if path)
            for path in file_paths:
                _delete_global_asset_file(path)
            await repo.delete(asset_id)
            await s.commit()
    return None


@router.post("/{asset_id}/image")
async def replace_image(
    asset_id: str,
    _t: Translator,
    image: UploadFile = File(...),
):
    # 1) 先取资产并校验存在
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        old_path = a.image_path
        asset_type = a.type
        old_path_is_resource = any(resource.path == old_path for resource in a.resources)
        keeps_image_variants = a.type == "character" and a.external_source is not None

    # 2) 先保存新图（会触发 415/413 校验）—— 旧文件仍完好
    new_path = await _save_upload(image, asset_type, _t)

    # 3) 更新 DB；若写入失败则清理已落盘的新文件（旧文件保留）
    try:
        async with async_session_factory() as s:
            repo = AssetRepository(s)
            a = await repo.update(asset_id, image_path=new_path)
            if keeps_image_variants:
                await AssetResourceRepository(s).create(
                    asset_id=asset_id,
                    resource_key=f"local:{uuid.uuid4().hex}",
                    origin="local",
                    media_type="image",
                    mime_type=image.content_type,
                    path=new_path,
                    sort_order=len(a.resources),
                )
            await s.commit()
            # create() 通过 asset_id 写入，已装载的 relationship 不会自动追加；显式
            # 过期后重查，保证响应立即包含用户刚上传的本地图片资源。
            s.expire(a, ["resources"])
            a = await repo.get_by_id(asset_id)
            assert a is not None
    except Exception:
        _delete_global_asset_file(new_path)
        raise

    # 4) DB 更新成功后才删除旧文件
    if old_path and old_path != new_path and not old_path_is_resource:
        _delete_global_asset_file(old_path)

    return {"asset": _serialize(a)}


class SetPrimaryResourceRequest(BaseModel):
    resource_id: str


@router.put("/{asset_id}/primary-resource/{media_type}")
async def set_primary_resource(
    asset_id: str,
    media_type: str,
    req: SetPrimaryResourceRequest,
    _t: Translator,
):
    if media_type not in {"image", "audio"}:
        raise HTTPException(status_code=400, detail=_t("asset_primary_resource_invalid_type"))
    async with async_session_factory() as s:
        asset_repo = AssetRepository(s)
        asset = await asset_repo.get_by_id(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        resource = await AssetResourceRepository(s).get_by_id(req.resource_id)
        if resource is None or resource.asset_id != asset_id or resource.media_type != media_type:
            raise HTTPException(status_code=422, detail=_t("asset_primary_resource_invalid"))
        field = "image_path" if media_type == "image" else "audio_path"
        await asset_repo.update(asset_id, **{field: resource.path})
        await s.commit()
        asset = await asset_repo.get_by_id(asset_id)
        assert asset is not None
    return {"asset": _serialize(asset)}


class FromProjectRequest(BaseModel):
    project_name: str
    resource_type: str
    resource_id: str
    override_name: str | None = None
    overwrite: bool = False


@router.post("/from-project")
async def from_project(
    req: FromProjectRequest,
    _t: Translator,
):
    # 1) 类型合法性
    if req.resource_type not in GLOBAL_LIBRARY_ASSET_TYPES:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_type"))

    # 2) 加载项目
    try:
        project = get_project_manager().load_project(req.project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("asset_target_project_not_found", project=req.project_name) from exc
    except Exception:
        logger.exception("Failed to load project '%s' for from-project", req.project_name)
        raise HTTPException(status_code=500, detail=_t("asset_load_project_failed"))

    # 3) 从对应 bucket 中读取资源
    bucket_key = BUCKET_KEY[req.resource_type]
    bucket = project.get(bucket_key) or {}
    # 存量 key 与请求名可能是 NFC/NFD 中的任一形态，按坐标系解析
    resource_key = resolve_asset_key(bucket, req.resource_id)
    resource = bucket.get(resource_key) if resource_key is not None else None
    if resource is None:
        raise HTTPException(
            status_code=404,
            detail=_t(
                "asset_source_resource_not_found",
                project=req.project_name,
                kind=localize_asset_type(req.resource_type, _t),
                name=req.resource_id,
            ),
        )

    asset_name = _validate_asset_name(req.override_name or req.resource_id, _t)
    description = resource.get("description") or ""
    voice_style = resource.get("voice_style", "") if req.resource_type == "character" else ""

    sheet_rel = resource.get(SHEET_KEY[req.resource_type]) or ""
    source_sheet_path: Path | None = None
    if sheet_rel:
        try:
            project_dir = get_project_manager().get_project_path(req.project_name)
            ProjectManager._safe_subpath(project_dir, sheet_rel)
            candidate = project_dir / sheet_rel
            if candidate.exists() and candidate.is_file():
                source_sheet_path = candidate
        except (ValueError, FileNotFoundError):
            # 非法路径或项目丢失：视作无源图继续流程
            source_sheet_path = None

    # 音频只有 character 类型有意义（reference_audio，不是 sheet 概念）；缺失/路径非法同图片一样
    # 静默降级为「无源音频」，不中断入库流程。
    audio_rel = resource.get("reference_audio") or "" if req.resource_type == "character" else ""
    source_audio_path: Path | None = None
    if audio_rel:
        try:
            project_dir = get_project_manager().get_project_path(req.project_name)
            ProjectManager._safe_subpath(project_dir, audio_rel)
            candidate = project_dir / audio_rel
            # reference_audio 可经通用角色 PATCH 被写成项目内任意字符串（extra_string_fields
            # 只做类型校验），仅 _safe_subpath 防越界不足以防止把 project.json 等其它项目
            # 文件当作音频复制进全局库；额外校验父目录命中 characters/refs_audio，与
            # server/routers/files.py::_resolve_audio_ref_path 同一口径。
            audio_refs_dir = project_dir / "characters" / "refs_audio"
            if (
                candidate.exists()
                and candidate.is_file()
                and os.path.realpath(candidate.parent) == os.path.realpath(audio_refs_dir)
            ):
                source_audio_path = candidate
        except (ValueError, FileNotFoundError):
            source_audio_path = None

    # 4) DB 预检查（orphan-safe：先查再拷贝文件）
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        existing = await repo.get_by_type_name(req.resource_type, asset_name)

    if existing is not None and not req.overwrite:
        raise HTTPException(
            status_code=409,
            detail={
                "message": _t("asset_already_exists", name=asset_name),
                "existing": _serialize(existing),
            },
        )

    # 5) 拷贝源 sheet / 参考音频到 _global_assets/{type}/{uuid}.{ext}
    # 两次拷贝共用一个失败边界：任一失败都清理已落盘的另一个文件，不留孤儿。
    new_image_path: str | None = None
    new_audio_path: str | None = None
    try:
        if source_sheet_path is not None:
            ext = source_sheet_path.suffix.lower() or ".png"
            root = get_project_manager().get_global_assets_root() / req.resource_type
            uid = uuid.uuid4().hex
            target = root / f"{uid}{ext}"
            await asyncio.to_thread(shutil.copyfile, source_sheet_path, target)
            new_image_path = f"_global_assets/{req.resource_type}/{uid}{ext}"

        if source_audio_path is not None:
            ext = source_audio_path.suffix.lower() or ".wav"
            root = get_project_manager().get_global_assets_root() / req.resource_type
            uid = uuid.uuid4().hex
            target = root / f"{uid}{ext}"
            await asyncio.to_thread(shutil.copyfile, source_audio_path, target)
            new_audio_path = f"_global_assets/{req.resource_type}/{uid}{ext}"
    except Exception:
        if new_image_path:
            _delete_global_asset_file(new_image_path)
        if new_audio_path:
            _delete_global_asset_file(new_audio_path)
        raise

    # 6) 写 DB：失败路径清理拷贝文件
    try:
        async with async_session_factory() as s:
            repo = AssetRepository(s)
            if existing is not None:
                # overwrite：先记下旧文件路径，commit 成功后再删；回滚时旧文件保留
                old_image = (
                    existing.image_path if existing.image_path and existing.image_path != new_image_path else None
                )
                old_audio = (
                    existing.audio_path if existing.audio_path and existing.audio_path != new_audio_path else None
                )
                a = await repo.update(
                    existing.id,
                    description=description,
                    voice_style=voice_style,
                    image_path=new_image_path,
                    audio_path=new_audio_path,
                    source_project=req.project_name,
                )
                await s.commit()
                await s.refresh(a)
                if old_image:
                    _delete_global_asset_file(old_image)
                if old_audio:
                    _delete_global_asset_file(old_audio)
            else:
                try:
                    a = await repo.create(
                        type=req.resource_type,
                        name=asset_name,
                        description=description,
                        voice_style=voice_style,
                        image_path=new_image_path,
                        audio_path=new_audio_path,
                        source_project=req.project_name,
                    )
                    await s.commit()
                    await s.refresh(a)
                except IntegrityError:
                    await s.rollback()
                    if new_image_path:
                        _delete_global_asset_file(new_image_path)
                    if new_audio_path:
                        _delete_global_asset_file(new_audio_path)
                    raise HTTPException(
                        status_code=409,
                        detail=_t("asset_already_exists", name=asset_name),
                    )
    except HTTPException:
        raise
    except Exception:
        if new_image_path:
            _delete_global_asset_file(new_image_path)
        if new_audio_path:
            _delete_global_asset_file(new_audio_path)
        raise

    return {"asset": _serialize(a)}


class ApplyToProjectRequest(BaseModel):
    asset_ids: list[str]
    target_project: str
    conflict_policy: str = "skip"  # 'skip' | 'overwrite' | 'rename'


@router.post("/apply-to-project")
async def apply_to_project(
    req: ApplyToProjectRequest,
    _t: Translator,
):
    # 1) 校验冲突策略（400 先于其它检查）
    if req.conflict_policy not in {"skip", "overwrite", "rename"}:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_conflict_policy"))
    asset_ids = list(dict.fromkeys(req.asset_ids))

    # 2) 校验目标项目存在
    project_manager = get_project_manager()
    try:
        project = project_manager.load_project(req.target_project)
    except ProjectAssetNameConflictError as exc:
        raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("asset_target_project_not_found", project=req.target_project) from exc

    succeeded: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    # 3) 批量读取所有请求的 asset，缺失的直接归入 failed
    async with async_session_factory() as s:
        assets = await AssetRepository(s).get_by_ids(asset_ids)
    assets_by_id = {a.id: a for a in assets}
    for asset_id in asset_ids:
        if asset_id not in assets_by_id:
            failed.append({"id": asset_id, "reason": "not_found"})

    # 4) 先在内存里算好每条 asset 的目标名 + 是否需要拷贝文件，
    #    再一次性执行文件拷贝和 project.json 写回
    project_dir = project_manager.get_project_path(req.target_project)
    # 四类资产共用一份名称占用表；owner 用于区分同类 overwrite 与不可覆盖的跨类型冲突。
    occupied: dict[str, tuple[str, str]] = {}
    for asset_type, bucket_key in BUCKET_KEY.items():
        bucket = project.get(bucket_key)
        if isinstance(bucket, dict):
            for raw_name in bucket:
                if isinstance(raw_name, str):
                    occupied[asset_name_comparison_key(raw_name)] = (asset_type, raw_name)
    plans: list[dict] = []
    for asset_id in asset_ids:
        a = assets_by_id.get(asset_id)
        if a is None:
            continue  # 已在 failed

        bucket_key = BUCKET_KEY[a.type]
        sheet_key = SHEET_KEY[a.type]
        try:
            desired_name = _validate_asset_name(a.name, _t)
        except HTTPException:
            failed.append({"id": a.id, "reason": "invalid_name"})
            continue

        existing = occupied.get(asset_name_comparison_key(desired_name))
        if existing is not None:
            same_type = existing[0] == a.type
            if req.conflict_policy == "skip":
                skipped.append({"id": a.id, "name": a.name})
                continue
            if req.conflict_policy == "rename":
                base_name = desired_name
                i = 2
                while asset_name_comparison_key(f"{base_name} ({i})") in occupied:
                    i += 1
                desired_name = f"{base_name} ({i})"
            elif not same_type:
                failed.append({"id": a.id, "reason": "project_name_conflict"})
                continue
            # overwrite 只能覆盖同类型条目。

        # 规划图片拷贝
        target_sheet: str | None = None
        copy_src: Path | None = None
        copy_dst: Path | None = None
        if a.image_path:
            src = project_manager.projects_root / a.image_path
            if src.exists() and src.is_file():
                ext = src.suffix.lower() or ".png"
                rel_sheet = f"{bucket_key}/{desired_name}{ext}"
                try:
                    ProjectManager._safe_subpath(project_dir, rel_sheet)
                except ValueError:
                    failed.append({"id": a.id, "reason": "invalid_name"})
                    continue
                target_sheet = rel_sheet
                copy_src = src
                copy_dst = project_dir / rel_sheet
            else:
                logger.warning(
                    "apply_to_project: asset %s image file missing on disk: %s",
                    a.id,
                    a.image_path,
                )
                failed.append({"id": a.id, "reason": "image_missing"})
                continue

        # 规划参考音频拷贝：与图片同口径（缺失即整条 failed，不中断整批）；只有 character 有意义
        target_audio: str | None = None
        copy_audio_src: Path | None = None
        copy_audio_dst: Path | None = None
        if a.type == "character" and a.audio_path:
            audio_src = project_manager.projects_root / a.audio_path
            if audio_src.exists() and audio_src.is_file():
                audio_ext = audio_src.suffix.lower() or ".wav"
                rel_audio = f"characters/refs_audio/{desired_name}{audio_ext}"
                try:
                    ProjectManager._safe_subpath(project_dir, rel_audio)
                except ValueError:
                    failed.append({"id": a.id, "reason": "invalid_name"})
                    continue
                target_audio = rel_audio
                copy_audio_src = audio_src
                copy_audio_dst = project_dir / rel_audio
            else:
                logger.warning(
                    "apply_to_project: asset %s audio file missing on disk: %s",
                    a.id,
                    a.audio_path,
                )
                failed.append({"id": a.id, "reason": "audio_missing"})
                continue

        occupied[asset_name_comparison_key(desired_name)] = (a.type, desired_name)
        plans.append(
            {
                "asset": a,
                "requested_name": _validate_asset_name(a.name, _t),
                "bucket_key": bucket_key,
                "sheet_key": sheet_key,
                "desired_name": desired_name,
                "target_sheet": target_sheet,
                "copy_src": copy_src,
                "copy_dst": copy_dst,
                "target_audio": target_audio,
                "copy_audio_src": copy_audio_src,
                "copy_audio_dst": copy_audio_dst,
            }
        )

    # 5) 单次事务把所有文件替换与 bucket 变更一次性写回。锁外规划只用于快速失败；
    #    锁内必须从 requested_name 重施策略，覆盖快照之后出现的同类型占用。
    file_copies: list[tuple[Path, Path]] = []

    def _apply_all(data: dict) -> None:
        applied_plans: list[dict] = []
        for plan in plans:
            a_ = plan["asset"]
            bk = plan["bucket_key"]
            sk = plan["sheet_key"]
            name_ = plan["requested_name"]
            existing = find_project_asset_name(data, name_)
            if existing is not None:
                if req.conflict_policy == "skip":
                    skipped.append({"id": a_.id, "name": a_.name})
                    continue
                if req.conflict_policy == "rename":
                    base_name = name_
                    index = 2
                    while find_project_asset_name(data, f"{base_name} ({index})") is not None:
                        index += 1
                    name_ = f"{base_name} ({index})"
                    existing = None
                elif existing.asset_type != a_.type:
                    raise ProjectAssetNameConflictError(name_, existing, a_.type)

            plan["desired_name"] = name_
            if plan["copy_src"] is not None:
                extension = plan["copy_src"].suffix.lower() or ".png"
                plan["target_sheet"] = f"{bk}/{name_}{extension}"
                plan["copy_dst"] = project_dir / plan["target_sheet"]
                file_copies.append((plan["copy_src"], plan["copy_dst"]))
            if plan["copy_audio_src"] is not None:
                extension = plan["copy_audio_src"].suffix.lower() or ".wav"
                plan["target_audio"] = f"characters/refs_audio/{name_}{extension}"
                plan["copy_audio_dst"] = project_dir / plan["target_audio"]
                file_copies.append((plan["copy_audio_src"], plan["copy_audio_dst"]))

            ts = plan["target_sheet"]
            ta = plan["target_audio"]
            ensure_project_asset_name_available(
                data,
                name_,
                requested_asset_type=a_.type,
                exclude_asset_type=a_.type,
                exclude_name=existing.name if existing is not None and existing.asset_type == a_.type else None,
            )
            payload: dict = {"description": a_.description or ""}
            if a_.type == "character":
                payload["voice_style"] = a_.voice_style or ""
                if a_.voice_id:
                    payload["voice_id"] = a_.voice_id
                if ta:
                    payload["reference_audio"] = ta
                    # 资产即开关：导入即等效「设置了这个声音」，存量过渡横幅计数须能感知
                    payload["voice_updated_at"] = datetime.now(UTC).isoformat()
            if ts:
                payload[sk] = ts
            if bk not in data or not isinstance(data.get(bk), dict):
                data[bk] = {}
            # overwrite 策略要落在存量真实 key 上（可能是 NFD），否则会并存两条视觉同名条目
            key = existing.name if existing is not None and existing.asset_type == a_.type else name_
            data[bk][key] = payload
            applied_plans.append(plan)

        plans[:] = applied_plans

    if plans:

        def _register_imported_sheet_claims(_project_file: Path) -> None:
            keys = {ArtifactKey.asset_sheet(plan["asset"].type, plan["desired_name"]) for plan in plans}
            register_artifact_entries_atomically(
                project_dir,
                {key: resolve_current_artifact_target(project_dir, key) for key in keys},
            )

        try:
            await asyncio.to_thread(
                project_manager.update_project_with_file_copies,
                req.target_project,
                _apply_all,
                file_copies,
                on_commit=_register_imported_sheet_claims,
            )
        except ProjectAssetNameConflictError as exc:
            raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc

    for plan in plans:
        succeeded.append({"id": plan["asset"].id, "name": plan["desired_name"]})

    return {"succeeded": succeeded, "skipped": skipped, "failed": failed}
