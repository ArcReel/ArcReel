"""assets 全局资产库路由。"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from lib import PROJECT_ROOT
from lib.asset_types import ASSET_TYPES, BUCKET_KEY, SHEET_KEY
from lib.db import async_session_factory
from lib.db.repositories.asset_repo import AssetRepository
from lib.i18n import Translator
from lib.project_manager import ProjectManager
from server.auth import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assets", tags=["全局资产库"])

# Module-level PM; overridable via monkeypatch in tests
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

_ILLEGAL_NAME_CHARS = ("/", "\\", "\0")


def _validate_asset_name(name: str, _t: Translator) -> str:
    """拒绝包含路径分隔符 / 空字节 / .. 的名字；防止路径穿越。"""
    cleaned = (name or "").strip()
    if not cleaned or ".." in cleaned or any(c in cleaned for c in _ILLEGAL_NAME_CHARS):
        raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=name))
    return cleaned


def _serialize(asset) -> dict:
    return {
        "id": asset.id,
        "type": asset.type,
        "name": asset.name,
        "description": asset.description,
        "voice_style": asset.voice_style,
        "image_path": asset.image_path,
        "source_project": asset.source_project,
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
    _user: CurrentUser,
    _t: Translator,
    type: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    async with async_session_factory() as s:
        items = await AssetRepository(s).list(type=type, q=q, limit=limit, offset=offset)
        return {"items": [_serialize(a) for a in items]}


@router.get("/{asset_id}")
async def get_asset(asset_id: str, _user: CurrentUser, _t: Translator):
    async with async_session_factory() as s:
        a = await AssetRepository(s).get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        return {"asset": _serialize(a)}


@router.post("")
async def create_asset(
    _user: CurrentUser,
    _t: Translator,
    type: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    voice_style: str = Form(""),
    image: UploadFile | None = File(None),
):
    if type not in ASSET_TYPES:
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
                await s.refresh(a)
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
    _user: CurrentUser,
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
async def delete_asset(asset_id: str, _user: CurrentUser, _t: Translator):
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if a:
            if a.image_path:
                _delete_global_asset_file(a.image_path)
            await repo.delete(asset_id)
            await s.commit()
    return None


@router.post("/{asset_id}/image")
async def replace_image(
    asset_id: str,
    _user: CurrentUser,
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

    # 2) 先保存新图（会触发 415/413 校验）—— 旧文件仍完好
    new_path = await _save_upload(image, asset_type, _t)

    # 3) 更新 DB；若写入失败则清理已落盘的新文件（旧文件保留）
    try:
        async with async_session_factory() as s:
            repo = AssetRepository(s)
            a = await repo.update(asset_id, image_path=new_path)
            await s.commit()
            await s.refresh(a)
    except Exception:
        _delete_global_asset_file(new_path)
        raise

    # 4) DB 更新成功后才删除旧文件
    if old_path and old_path != new_path:
        _delete_global_asset_file(old_path)

    return {"asset": _serialize(a)}


class FromProjectRequest(BaseModel):
    project_name: str
    resource_type: str
    resource_id: str
    override_name: str | None = None
    overwrite: bool = False


@router.post("/from-project")
async def from_project(
    req: FromProjectRequest,
    _user: CurrentUser,
    _t: Translator,
):
    # 1) 类型合法性
    if req.resource_type not in ASSET_TYPES:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_type"))

    # 2) 加载项目
    try:
        project = get_project_manager().load_project(req.project_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=_t("asset_target_project_not_found", project=req.project_name),
        )
    except Exception:
        logger.exception("Failed to load project '%s' for from-project", req.project_name)
        raise HTTPException(status_code=500, detail=_t("asset_load_project_failed"))

    # 3) 从对应 bucket 中读取资源
    bucket_key = BUCKET_KEY[req.resource_type]
    bucket = project.get(bucket_key) or {}
    resource = bucket.get(req.resource_id)
    if resource is None:
        raise HTTPException(
            status_code=404,
            detail=_t(
                "asset_source_resource_not_found",
                project=req.project_name,
                kind=req.resource_type,
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

    # 5) 拷贝源 sheet 到 _global_assets/{type}/{uuid}.{ext}
    new_image_path: str | None = None
    if source_sheet_path is not None:
        ext = source_sheet_path.suffix.lower() or ".png"
        root = get_project_manager().get_global_assets_root() / req.resource_type
        uid = uuid.uuid4().hex
        target = root / f"{uid}{ext}"
        await asyncio.to_thread(shutil.copyfile, source_sheet_path, target)
        new_image_path = f"_global_assets/{req.resource_type}/{uid}{ext}"

    # 6) 写 DB：失败路径清理拷贝文件
    try:
        async with async_session_factory() as s:
            repo = AssetRepository(s)
            if existing is not None:
                # overwrite：先记下旧文件路径，commit 成功后再删；回滚时旧文件保留
                old_image = (
                    existing.image_path if existing.image_path and existing.image_path != new_image_path else None
                )
                a = await repo.update(
                    existing.id,
                    description=description,
                    voice_style=voice_style,
                    image_path=new_image_path,
                    source_project=req.project_name,
                )
                await s.commit()
                await s.refresh(a)
                if old_image:
                    _delete_global_asset_file(old_image)
            else:
                try:
                    a = await repo.create(
                        type=req.resource_type,
                        name=asset_name,
                        description=description,
                        voice_style=voice_style,
                        image_path=new_image_path,
                        source_project=req.project_name,
                    )
                    await s.commit()
                    await s.refresh(a)
                except IntegrityError:
                    await s.rollback()
                    if new_image_path:
                        _delete_global_asset_file(new_image_path)
                    raise HTTPException(
                        status_code=409,
                        detail=_t("asset_already_exists", name=asset_name),
                    )
    except HTTPException:
        raise
    except Exception:
        if new_image_path:
            _delete_global_asset_file(new_image_path)
        raise

    return {"asset": _serialize(a)}


class ApplyToProjectRequest(BaseModel):
    asset_ids: list[str]
    target_project: str
    conflict_policy: str = "skip"  # 'skip' | 'overwrite' | 'rename'


@router.post("/apply-to-project")
async def apply_to_project(
    req: ApplyToProjectRequest,
    _user: CurrentUser,
    _t: Translator,
):
    # 1) 校验冲突策略（400 先于其它检查）
    if req.conflict_policy not in {"skip", "overwrite", "rename"}:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_conflict_policy"))

    # 2) 校验目标项目存在
    project_manager = get_project_manager()
    try:
        project = project_manager.load_project(req.target_project)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=_t("asset_target_project_not_found", project=req.target_project),
        )

    succeeded: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    # 3) 批量读取所有请求的 asset，缺失的直接归入 failed
    async with async_session_factory() as s:
        assets = await AssetRepository(s).get_by_ids(req.asset_ids)
    assets_by_id = {a.id: a for a in assets}
    for asset_id in req.asset_ids:
        if asset_id not in assets_by_id:
            failed.append({"id": asset_id, "reason": "not_found"})

    # 4) 先在内存里算好每条 asset 的目标名 + 是否需要拷贝文件，
    #    再一次性执行文件拷贝和 project.json 写回
    project_dir = project_manager.get_project_path(req.target_project)
    # 按 bucket 维护一份"已占用的名字"集合，用于 rename 策略的累积冲突检查
    bucket_names: dict[str, set[str]] = {bk: set((project.get(bk) or {}).keys()) for bk in BUCKET_KEY.values()}
    plans: list[dict] = []
    for asset_id in req.asset_ids:
        a = assets_by_id.get(asset_id)
        if a is None:
            continue  # 已在 failed

        bucket_key = BUCKET_KEY[a.type]
        sheet_key = SHEET_KEY[a.type]
        names = bucket_names[bucket_key]

        try:
            desired_name = _validate_asset_name(a.name, _t)
        except HTTPException:
            failed.append({"id": a.id, "reason": "invalid_name"})
            continue

        if desired_name in names:
            if req.conflict_policy == "skip":
                skipped.append({"id": a.id, "name": a.name})
                continue
            if req.conflict_policy == "rename":
                i = 2
                while f"{a.name} ({i})" in names:
                    i += 1
                desired_name = f"{a.name} ({i})"
            # overwrite: 保留原名，后续覆盖

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

        names.add(desired_name)
        plans.append(
            {
                "asset": a,
                "bucket_key": bucket_key,
                "sheet_key": sheet_key,
                "desired_name": desired_name,
                "target_sheet": target_sheet,
                "copy_src": copy_src,
                "copy_dst": copy_dst,
            }
        )

    # 5) 执行文件拷贝（off event loop）
    def _copy_all() -> None:
        for plan in plans:
            src = plan["copy_src"]
            dst = plan["copy_dst"]
            if src is None or dst is None:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)

    if plans:
        await asyncio.to_thread(_copy_all)

    # 6) 单次 update_project 把所有 bucket 变更一次性写回
    def _apply_all(data: dict) -> None:
        for plan in plans:
            a_ = plan["asset"]
            bk = plan["bucket_key"]
            sk = plan["sheet_key"]
            name_ = plan["desired_name"]
            ts = plan["target_sheet"]
            payload: dict = {"description": a_.description or ""}
            if a_.type == "character":
                payload["voice_style"] = a_.voice_style or ""
            if ts:
                payload[sk] = ts
            if bk not in data or not isinstance(data.get(bk), dict):
                data[bk] = {}
            data[bk][name_] = payload

    if plans:
        project_manager.update_project(req.target_project, _apply_all)

    for plan in plans:
        succeeded.append({"id": plan["asset"].id, "name": plan["desired_name"]})

    return {"succeeded": succeeded, "skipped": skipped, "failed": failed}
