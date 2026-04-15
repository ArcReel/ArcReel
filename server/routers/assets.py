"""assets 全局资产库路由。"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from lib import PROJECT_ROOT
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


VALID_TYPES = {"character", "scene", "prop"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# 资源类型 → project.json 中的 bucket key
BUCKET_KEY = {"character": "characters", "scene": "scenes", "prop": "props"}
# 资源类型 → bucket 项内的 sheet 字段名
SHEET_KEY = {"character": "character_sheet", "scene": "scene_sheet", "prop": "prop_sheet"}


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
    target.write_bytes(data)
    # 存相对路径（相对 projects_root）
    return f"_global_assets/{asset_type}/{uid}{ext}"


def _delete_global_asset_file(rel_path: str) -> None:
    path = get_project_manager().projects_root / rel_path
    try:
        path.unlink()
    except FileNotFoundError:
        pass
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
    if type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_type"))

    # 1) DB 预检查先行，避免写文件后才发现重复导致 orphan
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        if await repo.exists(type, name):
            raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=name))

    # 2) 预检查通过后再落盘（session 外）
    image_path: str | None = None
    if image is not None and image.filename:
        image_path = await _save_upload(image, type, _t)

    # 3) 真正 create；任何失败路径都必须清理已落盘文件，保证 DB/磁盘一致
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
    if req.resource_type not in VALID_TYPES:
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
        raise HTTPException(status_code=500, detail="internal error loading project")

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

    asset_name = req.override_name or req.resource_id
    description = resource.get("description") or ""
    voice_style = resource.get("voice_style", "") if req.resource_type == "character" else ""

    sheet_rel = resource.get(SHEET_KEY[req.resource_type]) or ""
    source_sheet_path: Path | None = None
    if sheet_rel:
        candidate = get_project_manager().projects_root / req.project_name / sheet_rel
        if candidate.exists() and candidate.is_file():
            source_sheet_path = candidate

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
        shutil.copyfile(source_sheet_path, target)
        new_image_path = f"_global_assets/{req.resource_type}/{uid}{ext}"

    # 6) 写 DB：失败路径清理拷贝文件
    try:
        async with async_session_factory() as s:
            repo = AssetRepository(s)
            if existing is not None:
                # overwrite：删旧文件（若不同）+ 更新
                if existing.image_path and existing.image_path != new_image_path:
                    _delete_global_asset_file(existing.image_path)
                a = await repo.update(
                    existing.id,
                    description=description,
                    voice_style=voice_style,
                    image_path=new_image_path,
                    source_project=req.project_name,
                )
                await s.commit()
                await s.refresh(a)
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
        raise HTTPException(status_code=400, detail="invalid conflict_policy")

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

    for asset_id in req.asset_ids:
        # 3a) 查找资产
        async with async_session_factory() as s:
            a = await AssetRepository(s).get_by_id(asset_id)
        if a is None:
            failed.append({"id": asset_id, "reason": "not_found"})
            continue

        # 3b) 解析 bucket / sheet key
        bucket_key = BUCKET_KEY[a.type]
        sheet_key = SHEET_KEY[a.type]
        bucket = project.get(bucket_key) or {}

        # 3c) 冲突处理，决定目标名
        desired_name = a.name
        if desired_name in bucket:
            if req.conflict_policy == "skip":
                skipped.append({"id": a.id, "name": a.name})
                continue
            if req.conflict_policy == "rename":
                i = 2
                while f"{a.name} ({i})" in bucket:
                    i += 1
                desired_name = f"{a.name} ({i})"
            # overwrite: 保留原名，后续覆盖

        # 3d) 拷贝图片
        target_sheet: str | None = None
        if a.image_path:
            src = project_manager.projects_root / a.image_path
            if src.exists() and src.is_file():
                ext = src.suffix.lower() or ".png"
                rel_sheet = f"{bucket_key}/{desired_name}{ext}"
                dst = project_manager.projects_root / req.target_project / rel_sheet
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                target_sheet = rel_sheet
            else:
                # 资产 DB 记录有 image_path,但磁盘文件已丢失 → 记为失败,跳过 project.json 变更
                logger.warning(
                    "apply_to_project: asset %s image file missing on disk: %s",
                    a.id,
                    a.image_path,
                )
                failed.append({"id": a.id, "reason": "image_missing"})
                continue

        # 3e) 更新 project.json（default-arg 冻结本次迭代的变量）
        def _mut(
            data: dict,
            _n=desired_name,
            _bk=bucket_key,
            _sk=sheet_key,
            _ts=target_sheet,
            _a=a,
        ) -> None:
            payload: dict = {"description": _a.description or ""}
            if _a.type == "character":
                payload["voice_style"] = _a.voice_style or ""
            if _ts:
                payload[_sk] = _ts
            if _bk not in data or not isinstance(data.get(_bk), dict):
                data[_bk] = {}
            data[_bk][_n] = payload

        project_manager.update_project(req.target_project, _mut)

        succeeded.append({"id": a.id, "name": desired_name})

        # 3g) 刷新 project 快照，下一轮冲突检查使用最新状态
        project = project_manager.load_project(req.target_project)

    return {"succeeded": succeeded, "skipped": skipped, "failed": failed}
