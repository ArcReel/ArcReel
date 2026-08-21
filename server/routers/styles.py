"""用户自定义风格库路由。

风格卡片复用现有 ``assets`` 表的通用字段，但使用独立 ``type=style`` 与 API，
不会混入角色/场景/道具资产库。项目保存的是风格快照；卡片删除或后续修改不会
反向改变已经创建的项目。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib.api_errors import NotFoundError
from lib.db import async_session_factory
from lib.db.repositories.asset_repo import AssetRepository
from lib.i18n import Translator
from lib.path_safety import PathTraversalError, safe_join
from lib.project_change_hints import project_change_source
from lib.project_manager import get_project_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/styles", tags=["自定义风格库"])

STYLE_ASSET_TYPE = "style"


def _serialize(style) -> dict:
    return {
        "id": style.id,
        "name": style.name,
        "description": style.description,
        "image_path": style.image_path,
        "source_project": style.source_project,
        "updated_at": style.updated_at.isoformat() if style.updated_at else None,
    }


def _delete_library_image(rel_path: str | None) -> None:
    if not rel_path:
        return
    try:
        safe_join(get_project_manager().projects_root, rel_path).unlink(missing_ok=True)
    except (OSError, PathTraversalError):
        logger.warning("删除自定义风格库图片失败: %s", rel_path)


def _copy_project_style_image(project_name: str, style_image: str | None) -> str | None:
    if not style_image:
        return None
    manager = get_project_manager()
    project_dir = manager.get_project_path(project_name)
    source = safe_join(project_dir, style_image, require_file=True)
    ext = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    root = manager.get_global_assets_root() / STYLE_ASSET_TYPE
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{uuid.uuid4().hex}{ext}"
    shutil.copyfile(source, target)
    return f"_global_assets/{STYLE_ASSET_TYPE}/{target.name}"


async def _unique_style_name(repo: AssetRepository, base: str) -> str:
    normalized = base.strip() or "自定义风格"
    candidate = normalized
    suffix = 2
    while await repo.exists(STYLE_ASSET_TYPE, candidate):
        candidate = f"{normalized} ({suffix})"
        suffix += 1
    return candidate


@router.get("")
async def list_styles(_t: Translator):
    async with async_session_factory() as session:
        items = await AssetRepository(session).list(type=STYLE_ASSET_TYPE, q=None, limit=200, offset=0)
        return {"items": [_serialize(item) for item in items]}


class SaveProjectStyleRequest(BaseModel):
    project_name: str
    name: str | None = None


@router.post("/from-project")
async def save_style_from_project(req: SaveProjectStyleRequest, _t: Translator):
    """把项目当前自定义风格新增或更新为一张可跨项目复用的卡片。"""
    manager = get_project_manager()
    try:
        project = await asyncio.to_thread(manager.load_project, req.project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=req.project_name) from exc

    if project.get("style_template_id"):
        raise HTTPException(status_code=400, detail=_t("style_library_requires_custom"))
    description = str(project.get("style_description") or "").strip()
    style_image = project.get("style_image") if isinstance(project.get("style_image"), str) else None
    if not description and not style_image:
        raise HTTPException(status_code=400, detail=_t("style_library_empty"))

    try:
        new_image_path = await asyncio.to_thread(_copy_project_style_image, req.project_name, style_image)
    except (FileNotFoundError, PathTraversalError):
        new_image_path = None

    existing_id = project.get("style_preset_id") if isinstance(project.get("style_preset_id"), str) else None
    old_image_path: str | None = None
    try:
        async with async_session_factory() as session:
            repo = AssetRepository(session)
            existing = await repo.get_by_id(existing_id) if existing_id else None
            if existing is not None and existing.type != STYLE_ASSET_TYPE:
                existing = None

            if existing is not None:
                old_image_path = existing.image_path if existing.image_path != new_image_path else None
                style = await repo.update(
                    existing.id,
                    description=description,
                    image_path=new_image_path,
                    source_project=req.project_name,
                )
            else:
                base_name = (req.name or "").strip() or f"{project.get('title') or req.project_name} · 风格"
                name = await _unique_style_name(repo, base_name)
                style = await repo.create(
                    type=STYLE_ASSET_TYPE,
                    name=name,
                    description=description,
                    image_path=new_image_path,
                    source_project=req.project_name,
                )
            await session.commit()
            await session.refresh(style)
    except Exception:
        _delete_library_image(new_image_path)
        raise

    if old_image_path:
        _delete_library_image(old_image_path)

    def _link_project(project_data: dict) -> None:
        project_data["style_preset_id"] = style.id

    with project_change_source("webui"):
        await asyncio.to_thread(manager.update_project, req.project_name, _link_project)
    linked_project = await asyncio.to_thread(manager.load_project, req.project_name)
    return {"style": _serialize(style), "project": linked_project}


class ApplyStyleRequest(BaseModel):
    project_name: str


@router.post("/{style_id}/apply")
async def apply_style_to_project(style_id: str, req: ApplyStyleRequest, _t: Translator):
    """把风格卡片内容复制成项目快照，并记录卡片关联。"""
    async with async_session_factory() as session:
        style = await AssetRepository(session).get_by_id(style_id)
        if style is None or style.type != STYLE_ASSET_TYPE:
            raise HTTPException(status_code=404, detail=_t("style_library_item_not_found"))
        serialized = _serialize(style)

    manager = get_project_manager()
    try:
        project_dir = await asyncio.to_thread(manager.get_project_path, req.project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=req.project_name) from exc

    style_filename: str | None = None
    if style.image_path:
        try:
            source = safe_join(manager.projects_root, style.image_path, require_file=True)
            ext = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
            style_filename = f"style_reference{ext}"
            await asyncio.to_thread(shutil.copyfile, source, project_dir / style_filename)
        except (FileNotFoundError, PathTraversalError):
            style_filename = None

    def _apply(project_data: dict) -> None:
        project_data.pop("style_template_id", None)
        project_data["style"] = ""
        project_data["style_description"] = style.description
        project_data["style_preset_id"] = style.id
        if style_filename:
            project_data["style_image"] = style_filename
        else:
            project_data.pop("style_image", None)

    with project_change_source("webui"):
        await asyncio.to_thread(manager.update_project, req.project_name, _apply)
    applied_project = await asyncio.to_thread(manager.load_project, req.project_name)
    return {"style": serialized, "project": applied_project}
