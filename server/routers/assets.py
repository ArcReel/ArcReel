"""assets 全局资产库路由。"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from lib.api_errors import NotFoundError
from lib.artifact_activation import register_artifact_entries_atomically, resolve_current_artifact_target
from lib.artifact_manifest import ArtifactKey
from lib.asset_derivatives import (
    derivative_artifact_key,
    derivative_sheet_relative_path,
    derivative_table,
)
from lib.asset_types import (
    ASSET_SPECS,
    BUCKET_KEY,
    DERIVATIVES_FIELD,
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
from lib.db.models.asset import AssetDerivative
from lib.db.repositories.asset_repo import AssetRepository
from lib.i18n import Translator
from lib.project_manager import ProjectManager, get_project_manager
from server.routers._asset_router_factory import localize_project_asset_name_conflict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assets", tags=["全局资产库"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _validate_asset_name(name: str, _t: Translator) -> str:
    """HTTP 边界包装：路径不安全的名字（分隔符 / 空字节 / ..）返回 400。"""
    try:
        return validate_asset_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=name)) from exc


def _serialize(asset, derivatives: Sequence[AssetDerivative] = ()) -> dict:
    """把一条资产（连同它的衍生子表）序列化成 API 形状。

    衍生随本体整套进出，消费方只需要落到项目角色条目里的三样：名、变化描述、图片路径。
    未开启衍生能力的类型、以及刚建出来还没有衍生的资产，走缺省的空序列。
    """
    return {
        "id": asset.id,
        "type": asset.type,
        "name": asset.name,
        "description": asset.description,
        "voice_style": asset.voice_style,
        "image_path": asset.image_path,
        "audio_path": asset.audio_path,
        "source_project": asset.source_project,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
        "derivatives": [
            {"name": d.name, "description": d.description, "image_path": d.image_path} for d in derivatives
        ],
    }


async def _serialize_one(repo: AssetRepository, asset) -> dict:
    """读出一条资产的衍生并连同本体序列化。"""
    return _serialize(asset, await repo.list_derivatives(asset.id))


async def _copy_into_global_pool(source: Path, asset_type: str, default_ext: str) -> str:
    """把一个文件拷进 ``_global_assets/{type}/``，返回相对 projects_root 的登记路径。"""
    ext = source.suffix.lower() or default_ext
    root = get_project_manager().get_global_assets_root() / asset_type
    uid = uuid.uuid4().hex
    await asyncio.to_thread(shutil.copyfile, source, root / f"{uid}{ext}")
    return f"_global_assets/{asset_type}/{uid}{ext}"


def _project_file_if_present(project_dir: Path, rel_path: str) -> Path | None:
    """把项目内相对路径解析成存在的文件；越界、缺失或不是文件一律按「没有」处理。"""
    if not rel_path:
        return None
    try:
        ProjectManager._safe_subpath(project_dir, rel_path)
    except (ValueError, FileNotFoundError):
        return None
    candidate = project_dir / rel_path
    return candidate if candidate.exists() and candidate.is_file() else None


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
        repo = AssetRepository(s)
        items = await repo.list(type=type, q=q, limit=limit, offset=offset)
        by_asset = await repo.list_derivatives_by_asset_ids([a.id for a in items])
        return {"items": [_serialize(a, by_asset.get(a.id, ())) for a in items]}


@router.get("/{asset_id}")
async def get_asset(asset_id: str, _t: Translator):
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        return {"asset": await _serialize_one(repo, a)}


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
                await s.refresh(a)
            except IntegrityError as exc:
                await s.rollback()
                if image_path:
                    _delete_global_asset_file(image_path)
                    image_path = None
                raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=name)) from exc
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
        if "name" in patch and patch["name"] != a.name and await repo.exists(a.type, patch["name"]):
            raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=patch["name"]))
        try:
            a = await repo.update(asset_id, **patch)
            await s.commit()
            await s.refresh(a)
        except IntegrityError as exc:
            await s.rollback()
            raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=patch.get("name", ""))) from exc
        payload = await _serialize_one(repo, a)
    return {"asset": payload}


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(asset_id: str, _t: Translator):
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if a:
            # 衍生行由 repo.delete 一并删掉，但它们各自的图片文件只有这里知道路径；
            # 删本体前先取出来，否则那些文件再无入口、永久留在库存储里。
            derivative_images = [d.image_path for d in await repo.list_derivatives(asset_id) if d.image_path]
            if a.image_path:
                _delete_global_asset_file(a.image_path)
            if a.audio_path:
                _delete_global_asset_file(a.audio_path)
            for image_path in derivative_images:
                _delete_global_asset_file(image_path)
            await repo.delete(asset_id)
            await s.commit()
    return


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

    # 2) 先保存新图（会触发 415/413 校验）—— 旧文件仍完好
    new_path = await _save_upload(image, asset_type, _t)

    # 3) 更新 DB；若写入失败则清理已落盘的新文件（旧文件保留）
    try:
        async with async_session_factory() as s:
            repo = AssetRepository(s)
            a = await repo.update(asset_id, image_path=new_path)
            await s.commit()
            await s.refresh(a)
            payload = await _serialize_one(repo, a)
    except Exception:
        _delete_global_asset_file(new_path)
        raise

    # 4) DB 更新成功后才删除旧文件
    if old_path and old_path != new_path:
        _delete_global_asset_file(old_path)

    return {"asset": payload}


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
    except Exception as exc:
        logger.exception("Failed to load project '%s' for from-project", req.project_name)
        raise HTTPException(status_code=500, detail=_t("asset_load_project_failed")) from exc

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

    try:
        source_project_dir: Path | None = get_project_manager().get_project_path(req.project_name)
    except FileNotFoundError:
        source_project_dir = None

    sheet_key = SHEET_KEY[req.resource_type]
    # 非法路径、项目丢失或文件缺失：视作无源图继续流程
    source_sheet_path = (
        _project_file_if_present(source_project_dir, resource.get(sheet_key) or "")
        if source_project_dir is not None
        else None
    )

    # 衍生随本体整套入库：每条带上变化描述与自己那张资产图；图缺失的衍生仍然入库（名与
    # 描述才是它的身份，图可在目标项目里重新生成），与本体资产图缺失时的降级同口径。
    derivative_sources: list[tuple[str, str, Path | None]] = []
    if ASSET_SPECS[req.resource_type].supports_derivatives:
        for raw_name, raw_derivative in derivative_table(resource).items():
            if not isinstance(raw_derivative, dict):
                continue
            try:
                derivative_name = validate_asset_name(raw_name)
            except ValueError:
                logger.warning("from_project: skip derivative with unsafe name: %r", raw_name)
                continue
            raw_description = raw_derivative.get("description")
            derivative_sheet = raw_derivative.get(sheet_key)
            derivative_sources.append(
                (
                    derivative_name,
                    raw_description if isinstance(raw_description, str) else "",
                    _project_file_if_present(source_project_dir, derivative_sheet)
                    if source_project_dir is not None and isinstance(derivative_sheet, str)
                    else None,
                )
            )

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
                and os.path.realpath(candidate.parent) == os.path.realpath(audio_refs_dir)  # noqa: ASYNC240 -- 仅路径解析（realpath）做越界校验，不读文件内容
            ):
                source_audio_path = candidate
        except (ValueError, FileNotFoundError):
            source_audio_path = None

    # 4) DB 预检查（orphan-safe：先查再拷贝文件）
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        existing = await repo.get_by_type_name(req.resource_type, asset_name)
        # overwrite 会整表换掉衍生行；旧行的图片文件在 commit 成功后才删，故先取出路径。
        stale_derivative_images = (
            [d.image_path for d in await repo.list_derivatives(existing.id) if d.image_path]
            if existing is not None
            else []
        )
        conflict_payload = await _serialize_one(repo, existing) if existing is not None else None

    if conflict_payload is not None and not req.overwrite:
        raise HTTPException(
            status_code=409,
            detail={
                "message": _t("asset_already_exists", name=asset_name),
                "existing": conflict_payload,
            },
        )

    # 5) 拷贝源 sheet / 参考音频到 _global_assets/{type}/{uuid}.{ext}
    # 两次拷贝共用一个失败边界：任一失败都清理已落盘的另一个文件，不留孤儿。
    new_image_path: str | None = None
    new_audio_path: str | None = None
    # 已落盘的拷贝按发生顺序登记，失败路径统一按这张清单回删，不重复逐个变量判空。
    copied_paths: list[str] = []
    # 衍生的图与本体图共用同一个库存储池，落盘形状与清理入口因此完全一致。
    derivative_rows: list[tuple[str, str, str | None]] = []
    try:
        if source_sheet_path is not None:
            new_image_path = await _copy_into_global_pool(source_sheet_path, req.resource_type, ".png")
            copied_paths.append(new_image_path)

        if source_audio_path is not None:
            new_audio_path = await _copy_into_global_pool(source_audio_path, req.resource_type, ".wav")
            copied_paths.append(new_audio_path)

        for derivative_name, derivative_description, derivative_source in derivative_sources:
            derivative_image: str | None = None
            if derivative_source is not None:
                derivative_image = await _copy_into_global_pool(derivative_source, req.resource_type, ".png")
                copied_paths.append(derivative_image)
            derivative_rows.append((derivative_name, derivative_description, derivative_image))
    except Exception:
        for copied in copied_paths:
            _delete_global_asset_file(copied)
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
                await repo.replace_derivatives(a.id, derivative_rows)
                await s.commit()
                await s.refresh(a)
                payload = await _serialize_one(repo, a)
                if old_image:
                    _delete_global_asset_file(old_image)
                if old_audio:
                    _delete_global_asset_file(old_audio)
                for stale_image in stale_derivative_images:
                    _delete_global_asset_file(stale_image)
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
                    await repo.replace_derivatives(a.id, derivative_rows)
                    await s.commit()
                    await s.refresh(a)
                    payload = await _serialize_one(repo, a)
                except IntegrityError as exc:
                    await s.rollback()
                    for copied in copied_paths:
                        _delete_global_asset_file(copied)
                    raise HTTPException(
                        status_code=409,
                        detail=_t("asset_already_exists", name=asset_name),
                    ) from exc
    except HTTPException:
        raise
    except Exception:
        for copied in copied_paths:
            _delete_global_asset_file(copied)
        raise

    return {"asset": payload}


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
        repo = AssetRepository(s)
        assets = await repo.get_by_ids(asset_ids)
        derivatives_by_asset = await repo.list_derivatives_by_asset_ids([a.id for a in assets])
    assets_by_id = {a.id: a for a in assets}
    failed.extend({"id": asset_id, "reason": "not_found"} for asset_id in asset_ids if asset_id not in assets_by_id)

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

        # 衍生随本体整套落地：名与描述一定写进条目，图只在库里那张文件还在时才拷。
        # 衍生名不进项目命名空间（只在本体条目内唯一），故不参与 occupied 对账。
        derivative_plans: list[dict] = []
        if ASSET_SPECS[a.type].supports_derivatives:
            for derivative in derivatives_by_asset.get(a.id, ()):
                derivative_src: Path | None = None
                if derivative.image_path:
                    candidate = project_manager.projects_root / derivative.image_path
                    if candidate.exists() and candidate.is_file():
                        derivative_src = candidate
                    else:
                        logger.warning(
                            "apply_to_project: asset %s derivative %r image file missing on disk: %s",
                            a.id,
                            derivative.name,
                            derivative.image_path,
                        )
                derivative_plans.append(
                    {"name": derivative.name, "description": derivative.description, "copy_src": derivative_src}
                )

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
                "derivatives": derivative_plans,
                "derivative_sheet_names": [],
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
            plan["derivative_sheet_names"] = []
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
            # 整条替换前先把存量条目的衍生表接过来，再让库里带来的衍生按名覆盖上去：
            # 库里没有的存量衍生因此得以保留（覆盖导入不抹用户已登记的衍生），库里带来的
            # 同名衍生以库版本为准。新条目从空表起步，与创建路径和迁移同口径。
            if ASSET_SPECS[a_.type].supports_derivatives:
                previous = data[bk].get(key)
                inherited = previous.get(DERIVATIVES_FIELD) if isinstance(previous, dict) else None
                table = dict(inherited) if isinstance(inherited, dict) else {}
                for derivative in plan["derivatives"]:
                    derivative_name = derivative["name"]
                    derivative_sheet = ""
                    if derivative["copy_src"] is not None:
                        relative = derivative_sheet_relative_path(name_, derivative_name)
                        try:
                            ProjectManager._safe_subpath(project_dir, relative)
                        except ValueError:
                            # 两段名都已过 validate_asset_name，走到这里说明拼出的路径仍
                            # 越界：放弃这张图而不是整批失败，衍生的名与描述照常落地。
                            logger.warning(
                                "apply_to_project: unsafe derivative sheet path %r, importing without image",
                                relative,
                            )
                        else:
                            derivative_sheet = relative
                            file_copies.append((derivative["copy_src"], project_dir / relative))
                            plan["derivative_sheet_names"].append(derivative_name)
                    # 存量键可能是 NFD 等价形态；命中就写回同一个键，避免并存两条视觉同名衍生。
                    derivative_key = resolve_asset_key(table, derivative_name) or derivative_name
                    existing_derivative = table.get(derivative_key)
                    merged = dict(existing_derivative) if isinstance(existing_derivative, dict) else {}
                    merged["description"] = derivative["description"]
                    merged[sk] = derivative_sheet
                    table[derivative_key] = merged
                payload[DERIVATIVES_FIELD] = table
            data[bk][key] = payload
            applied_plans.append(plan)

        plans[:] = applied_plans

    if plans:

        def _register_imported_sheet_claims(_project_file: Path) -> None:
            keys = {ArtifactKey.asset_sheet(plan["asset"].type, plan["desired_name"]) for plan in plans}
            keys |= {
                derivative_artifact_key(plan["desired_name"], derivative_name)
                for plan in plans
                for derivative_name in plan["derivative_sheet_names"]
            }
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

    succeeded.extend({"id": plan["asset"].id, "name": plan["desired_name"]} for plan in plans)

    return {"succeeded": succeeded, "skipped": skipped, "failed": failed}
