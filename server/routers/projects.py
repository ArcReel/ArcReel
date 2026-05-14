"""
项目管理路由

处理项目的 CRUD 操作，复用 lib/project_manager.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

if TYPE_CHECKING:
    from server.services.jianying_draft_service import JianyingDraftService

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi import Path as FastAPIPath
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.background import BackgroundTask

logger = logging.getLogger(__name__)

from lib.app_data_dir import app_data_dir
from lib.asset_fingerprints import compute_asset_fingerprints
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.i18n import Translator
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from lib.status_calculator import StatusCalculator
from lib.style_templates import is_known_template, resolve_template_prompt
from server.auth import CurrentUser, create_download_token, verify_download_token
from server.routers._validators import validate_backend_value
from server.services.project_archive import (
    ProjectArchiveService,
    ProjectArchiveValidationError,
)
from server.services.project_cover import resolve_project_cover

router = APIRouter()

# 初始化项目管理器和状态计算器
pm = ProjectManager(app_data_dir())
calc = StatusCalculator(pm)

# episode 字段白名单：只允许持久化合法的 on-disk 字段。
# StatusCalculator 注入的统计字段（scenes_count / status / storyboards / videos 等）
# 是读时计算值，禁止写回 project.json。
EPISODE_PERSIST_FIELDS = {"title", "script_file", "generation_mode"}


def get_project_manager() -> ProjectManager:
    return pm


def get_status_calculator() -> StatusCalculator:
    return calc


def get_archive_service() -> ProjectArchiveService:
    return ProjectArchiveService(get_project_manager())


class CreateProjectRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    style: str | None = ""  # 保留但不再是用户入口
    content_mode: str | None = "narration"
    aspect_ratio: str | None = "9:16"
    default_duration: int | None = None
    generation_mode: str | None = None
    # ===== 新增 =====
    style_template_id: str | None = None
    video_backend: str | None = None
    image_backend: str | None = None
    image_provider_t2i: str | None = None
    image_provider_i2i: str | None = None
    text_backend_script: str | None = None
    text_backend_overview: str | None = None
    text_backend_style: str | None = None
    model_settings: dict[str, dict[str, str | None]] | None = None


class EpisodePatch(BaseModel):
    """PATCH body entry for a single episode.

    Only whitelisted fields persist; computed fields (scenes_count, status,
    storyboards, etc.) are silently dropped via extra='ignore'.
    """

    model_config = ConfigDict(extra="ignore")
    episode: int
    title: str | None = None
    script_file: str | None = None
    generation_mode: Literal["storyboard", "grid", "reference_video"] | None = None


class UpdateProjectRequest(BaseModel):
    title: str | None = None
    style: str | None = None
    content_mode: str | None = None
    aspect_ratio: str | None = None
    default_duration: int | None = None
    generation_mode: str | None = None
    video_backend: str | None = None
    image_backend: str | None = None
    image_provider_t2i: str | None = None
    image_provider_i2i: str | None = None
    video_generate_audio: bool | None = None
    text_backend_script: str | None = None
    text_backend_overview: str | None = None
    text_backend_style: str | None = None
    style_template_id: str | None = None
    clear_style_image: bool | None = None
    episodes: list[EpisodePatch] | None = None
    model_settings: dict[str, dict[str, str | None]] | None = None


def _cleanup_temp_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def _cleanup_temp_dir(dir_path: str) -> None:
    shutil.rmtree(dir_path, ignore_errors=True)


@router.post("/projects/import")
async def import_project_archive(
    _user: CurrentUser,
    _t: Translator,
    file: UploadFile = File(...),
    conflict_policy: str = Form("prompt"),
):
    """从 ZIP 导入项目。"""
    upload_path: str | None = None
    try:
        fd, upload_path = tempfile.mkstemp(prefix="arcreel-upload-", suffix=".zip")
        os.close(fd)

        # 使用底层 SpooledTemporaryFile 的同步句柄，整循环 offload 到线程，
        # 避免 async 读取 + 同步写入的混合模式阻塞事件循环 (#230)
        raw_file = file.file

        def _write_upload():
            with open(upload_path, "wb") as target:
                while True:
                    chunk = raw_file.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)

        await asyncio.to_thread(_write_upload)

        def _sync():
            return get_archive_service().import_project_archive(
                Path(upload_path),
                uploaded_filename=file.filename,
                conflict_policy=conflict_policy,
            )

        result = await asyncio.to_thread(_sync)
        return {
            "success": True,
            "project_name": result.project_name,
            "project": result.project,
            "warnings": result.warnings,
            "conflict_resolution": result.conflict_resolution,
            "diagnostics": result.diagnostics,
        }
    except ProjectArchiveValidationError as exc:
        diagnostics = exc.extra.get(
            "diagnostics",
            {"blocking": [], "auto_fixable": [], "warnings": []},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "errors": exc.errors,
                "warnings": exc.warnings,
                "diagnostics": diagnostics,
                **exc.extra,
            },
        )
    except Exception as e:
        logger.exception("请求处理失败")
        return JSONResponse(
            status_code=500,
            content={"detail": str(e), "errors": [], "warnings": []},
        )
    finally:
        await file.close()
        if upload_path:
            _cleanup_temp_file(upload_path)


@router.post("/projects/{name}/export/token")
async def create_export_token(
    name: str,
    current_user: CurrentUser,
    _t: Translator,
    scope: str = Query("full"),
):
    """签发短时效下载 token，用于浏览器原生下载认证。"""
    try:
        if scope not in ("full", "current"):
            raise HTTPException(status_code=422, detail=_t("scope_invalid"))

        def _sync():
            if not get_project_manager().project_exists(name):
                raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
            return get_archive_service().get_export_diagnostics(name, scope=scope)

        diagnostics = await asyncio.to_thread(_sync)
        username = current_user.sub
        download_token = create_download_token(username, name)
        return {
            "download_token": download_token,
            "expires_in": 300,
            "diagnostics": diagnostics,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}/export")
async def export_project_archive(
    name: str,
    _t: Translator,
    download_token: str = Query(...),
    scope: str = Query("full"),
):
    """将项目导出为 ZIP。需要 download_token 认证（通过 POST /export/token 获取）。"""
    if scope not in ("full", "current"):
        raise HTTPException(status_code=422, detail=_t("scope_invalid"))

    # 验证 download_token
    import jwt as pyjwt

    try:
        verify_download_token(download_token, name)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail=_t("download_expired"))
    except ValueError:
        raise HTTPException(status_code=403, detail=_t("download_token_mismatch"))
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail=_t("download_token_invalid"))

    try:
        archive_path, download_name = await asyncio.to_thread(
            lambda: get_archive_service().export_project(name, scope=scope)
        )
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=download_name,
            background=BackgroundTask(_cleanup_temp_file, str(archive_path)),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# --- 剪映草稿导出 ---


def get_jianying_draft_service() -> JianyingDraftService:
    from server.services.jianying_draft_service import JianyingDraftService

    return JianyingDraftService(get_project_manager())


def _validate_draft_path(draft_path: str, _t: Callable[..., str]) -> str:
    """校验 draft_path 合法性"""
    if not draft_path or not draft_path.strip():
        raise HTTPException(status_code=422, detail=_t("jianying_path_invalid"))
    if len(draft_path) > 1024:
        raise HTTPException(status_code=422, detail=_t("jianying_path_too_long"))
    if any(ord(c) < 32 for c in draft_path):
        raise HTTPException(status_code=422, detail=_t("jianying_path_illegal"))
    return draft_path.strip()


@router.get("/projects/{name}/export/jianying-draft")
def export_jianying_draft(
    name: str,
    _t: Translator,
    episode: int = Query(..., description="集数编号"),
    draft_path: str = Query(..., description="用户本地剪映草稿目录"),
    download_token: str = Query(..., description="下载 token"),
    jianying_version: str = Query("6", description="剪映版本：6 或 5"),
):
    """导出指定集的剪映草稿 ZIP"""
    import jwt as pyjwt

    # 1. 验证 download_token
    try:
        verify_download_token(download_token, name)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail=_t("download_expired"))
    except ValueError:
        raise HTTPException(status_code=403, detail=_t("download_token_mismatch"))
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail=_t("download_token_invalid"))

    # 2. 校验 draft_path
    draft_path = _validate_draft_path(draft_path, _t)

    # 3. 调用服务
    svc = get_jianying_draft_service()
    try:
        zip_path = svc.export_episode_draft(
            project_name=name,
            episode=episode,
            draft_path=draft_path,
            use_draft_info_name=(jianying_version != "5"),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logger.exception("剪映草稿导出失败: project=%s episode=%d", name, episode)
        raise HTTPException(status_code=500, detail=_t("jianying_export_failed"))

    download_name = f"{name}_episode_{episode}_jianying_draft.zip"

    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=download_name,
        background=BackgroundTask(_cleanup_temp_dir, str(zip_path.parent)),
    )


@router.get("/projects")
async def list_projects(_user: CurrentUser):
    """列出所有项目"""

    def _sync():
        manager = get_project_manager()
        calculator = get_status_calculator()
        projects = []
        for name in manager.list_projects():
            try:
                # 尝试加载项目元数据
                if manager.project_exists(name):
                    project = manager.load_project(name)
                    # 一次性预加载每集剧本，喂给 cover + status 两路下游，去除重复 JSON I/O。
                    # key 为 episode['script_file'] 原值（match resolve_project_cover /
                    # StatusCalculator 对 key 的期望）。任何一集加载失败都不影响列表：
                    # 仅跳过入 map，下游消费者自然按"缺失"路径兜底。
                    preloaded_scripts: dict[str, dict] = {}
                    for ep in project.get("episodes") or []:
                        script_file = ep.get("script_file")
                        if not script_file:
                            continue
                        try:
                            preloaded_scripts[script_file] = manager.load_script(name, script_file)
                        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError) as load_err:
                            # 与 resolve_project_cover / StatusCalculator._load_episode_script
                            # 对齐：I/O 缺失 + JSON/schema 解析失败 → 跳过此集，继续预加载其他集；
                            # 非预期异常（RuntimeError/MemoryError 等）让其冒泡到外层 try，走 basic info 兜底行。
                            logger.debug(
                                "list_projects 预加载剧本失败 project=%s script=%s err=%s",
                                name,
                                script_file,
                                load_err,
                            )

                    # 封面走 resolve_project_cover fallback 链：
                    # video_thumbnail → storyboard_image → scene_sheet → character_sheet
                    # —— 兼顾 reference / grid / storyboard 三种生成模式。
                    thumbnail = resolve_project_cover(manager, name, project, preloaded_scripts=preloaded_scripts)

                    # 使用 StatusCalculator 计算进度（读时计算）
                    status = calculator.calculate_project_status(name, project, preloaded_scripts=preloaded_scripts)

                    projects.append(
                        {
                            "name": name,
                            "title": project.get("title", name),
                            "style": project.get("style", ""),
                            "style_template_id": project.get("style_template_id"),
                            "style_image": project.get("style_image"),
                            "thumbnail": thumbnail,
                            "status": status,
                        }
                    )
                else:
                    # 没有 project.json 的项目
                    projects.append(
                        {
                            "name": name,
                            "title": name,
                            "style": "",
                            "thumbnail": None,
                            "status": {},
                        }
                    )
            except Exception as e:
                # 出错时返回基本信息
                logger.warning("加载项目 '%s' 元数据失败: %s", name, e)
                projects.append(
                    {"name": name, "title": name, "style": "", "thumbnail": None, "status": {}, "error": str(e)}
                )

        return {"projects": projects}

    return await asyncio.to_thread(_sync)


@router.post("/projects")
async def create_project(
    req: CreateProjectRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """创建新项目"""
    try:

        def _sync():
            manager = get_project_manager()
            title = (req.title or "").strip()
            manual_name = (req.name or "").strip()
            if not title and not manual_name:
                raise HTTPException(status_code=400, detail=_t("title_required"))
            project_name = manual_name or manager.generate_project_name(title)

            style_prompt = req.style or ""
            if req.style_template_id:
                if not is_known_template(req.style_template_id):
                    raise HTTPException(
                        status_code=400,
                        detail=_t("unknown_style_template", template_id=req.style_template_id),
                    )
                style_prompt = resolve_template_prompt(req.style_template_id)

            # 与 update 路径对称：校验所有 backend 字段
            for field_name in (
                "video_backend",
                "image_backend",
                "image_provider_t2i",
                "image_provider_i2i",
                "text_backend_script",
                "text_backend_overview",
                "text_backend_style",
            ):
                value = getattr(req, field_name)
                if value:
                    validate_backend_value(value, field_name, _t)

            try:
                manager.create_project(project_name)
            except FileExistsError:
                raise HTTPException(status_code=400, detail=_t("project_exists", name=project_name))
            extras = {
                field: value
                for field in (
                    "video_backend",
                    "image_backend",
                    "image_provider_t2i",
                    "image_provider_i2i",
                    "text_backend_script",
                    "text_backend_overview",
                    "text_backend_style",
                )
                if (value := getattr(req, field))
            }
            if req.model_settings is not None:
                extras["model_settings"] = req.model_settings
            with project_change_source("webui"):
                project = manager.create_project_metadata(
                    project_name,
                    title or manual_name,
                    style_prompt,
                    req.content_mode,
                    aspect_ratio=req.aspect_ratio,
                    default_duration=req.default_duration,
                    style_template_id=req.style_template_id,
                    extras=extras or None,
                )
                if req.generation_mode is not None:
                    project["generation_mode"] = req.generation_mode
                    manager.save_project(project_name, project)
            return {"success": True, "name": project_name, "project": project}

        return await asyncio.to_thread(_sync)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}/video-capabilities")
async def get_video_capabilities(
    name: str,
    _user: CurrentUser,
    _t: Translator,
):
    """解析当前项目视频模型能力 + 用户项目偏好。

    三级模型选择（项目 > 系统设置 > 系统默认）后，读 model 的 `supported_durations`
    并派生 `max_duration`；同时带回 `project.json.default_duration`（用户偏好）。
    所有 generation_mode（storyboard/grid/reference_video）都可复用。
    """
    resolver = ConfigResolver(async_session_factory)
    try:
        return await resolver.video_capabilities(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=name)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_t("video_capabilities_unresolved", name=name, reason=str(exc)),
        ) from exc


@router.get("/projects/{name}")
async def get_project(
    name: str,
    _user: CurrentUser,
    _t: Translator,
):
    """获取项目详情（含实时计算字段）"""
    try:

        def _sync():
            manager = get_project_manager()
            calculator = get_status_calculator()
            if not manager.project_exists(name):
                raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))

            project = manager.load_project(name)

            # 注入计算字段（不写入 JSON，仅用于 API 响应）
            project = calculator.enrich_project(name, project)

            # 加载所有剧本并注入计算字段
            scripts = {}
            for ep in project.get("episodes", []):
                script_file = ep.get("script_file", "")
                if script_file:
                    try:
                        script = manager.load_script(name, script_file)
                        script = calculator.enrich_script(script)
                        key = (
                            script_file.replace("scripts/", "", 1)
                            if script_file.startswith("scripts/")
                            else script_file
                        )
                        scripts[key] = script
                    except FileNotFoundError:
                        logger.debug("剧本文件不存在，跳过: %s/%s", name, script_file)

            # 计算媒体文件指纹（用于前端内容寻址缓存）
            project_path = manager.get_project_path(name)
            fingerprints = compute_asset_fingerprints(project_path)

            return {
                "project": project,
                "scripts": scripts,
                "asset_fingerprints": fingerprints,
            }

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{name}")
async def update_project(name: str, req: UpdateProjectRequest, _user: CurrentUser, _t: Translator):
    """更新项目元数据"""
    try:

        def _sync():
            manager = get_project_manager()
            project = manager.load_project(name)

            if req.content_mode is not None:
                raise HTTPException(
                    status_code=400,
                    detail=_t("project_id_not_editable"),
                )

            if req.title is not None:
                project["title"] = req.title
            if req.style is not None:
                project["style"] = req.style
            for field in (
                "video_backend",
                "image_backend",
                "image_provider_t2i",
                "image_provider_i2i",
                "text_backend_script",
                "text_backend_overview",
                "text_backend_style",
            ):
                if field in req.model_fields_set:
                    value = getattr(req, field)
                    if value:
                        validate_backend_value(value, field, _t)
                        project[field] = value
                    else:
                        project.pop(field, None)

            # 用户显式清空 t2i/i2i 任一时，同步清掉 legacy `image_backend`：否则 ProjectManager
            # 的 lazy upgrade 会在下次 load_project 时用 legacy 值回填新字段，让"清空 → 跟随
            # 全局默认"的语义失效。仅当客户端没在同一请求里写入 image_backend 才执行（避免
            # 撤掉用户刚写入的值）。
            if "image_backend" not in req.model_fields_set:
                cleared_t2i = "image_provider_t2i" in req.model_fields_set and not req.image_provider_t2i
                cleared_i2i = "image_provider_i2i" in req.model_fields_set and not req.image_provider_i2i
                if cleared_t2i or cleared_i2i:
                    project.pop("image_backend", None)
            if "video_generate_audio" in req.model_fields_set:
                if req.video_generate_audio is None:
                    project.pop("video_generate_audio", None)
                else:
                    project["video_generate_audio"] = req.video_generate_audio
            if "aspect_ratio" in req.model_fields_set and req.aspect_ratio is not None:
                project["aspect_ratio"] = req.aspect_ratio
            if "generation_mode" in req.model_fields_set:
                if req.generation_mode is None:
                    project.pop("generation_mode", None)
                else:
                    project["generation_mode"] = req.generation_mode
            if "default_duration" in req.model_fields_set:
                if req.default_duration is None:
                    project.pop("default_duration", None)
                else:
                    project["default_duration"] = req.default_duration

            if "style_template_id" in req.model_fields_set:
                if req.style_template_id is None:
                    # 取消模版选择：同时清掉展开的 style prompt，避免遗留孤儿文本
                    project.pop("style_template_id", None)
                    project["style"] = ""
                else:
                    if not is_known_template(req.style_template_id):
                        raise HTTPException(
                            status_code=400,
                            detail=_t("unknown_style_template", template_id=req.style_template_id),
                        )
                    project["style_template_id"] = req.style_template_id
                    project["style"] = resolve_template_prompt(req.style_template_id)
                    # 强互斥:模版与参考图二选一
                    project.pop("style_image", None)
                    project.pop("style_description", None)

            if req.clear_style_image:
                # 显式清除自定义参考图，用于"取消风格"流程
                project.pop("style_image", None)
                project.pop("style_description", None)

            if "model_settings" in req.model_fields_set:
                if req.model_settings is None:
                    project.pop("model_settings", None)
                else:
                    project["model_settings"] = req.model_settings

            if "episodes" in req.model_fields_set and req.episodes is not None:
                # 合并 episodes：保留现有 episode 的完整数据，仅更新请求中显式提供的字段。
                # 使用 model_fields_set（而非 exclude_none）判断字段是否显式出现，使得
                # `generation_mode: null` 可用于清空集级覆盖、回退到项目级模式继承。
                # 白名单同时拦截 StatusCalculator 注入的计算字段（scenes_count / status
                # / storyboards / videos 等），防止写回 project.json。
                existing_list = project.get("episodes", [])
                patch_map: dict[int, EpisodePatch] = {}
                for ep in req.episodes:
                    patch_map[ep.episode] = ep  # 重复编号：后者覆盖前者

                new_episodes: list[dict] = []
                for existing_ep in existing_list:
                    ep_num = existing_ep.get("episode")
                    patch = patch_map.pop(ep_num, None)
                    if patch is None:
                        new_episodes.append(existing_ep)
                        continue
                    updated = dict(existing_ep)
                    for field_name in EPISODE_PERSIST_FIELDS:
                        if field_name not in patch.model_fields_set:
                            continue
                        value = getattr(patch, field_name)
                        if value is None:
                            updated.pop(field_name, None)
                        else:
                            updated[field_name] = value
                    new_episodes.append(updated)

                for unknown_ep in patch_map:
                    logger.warning("Skipping patch for unknown episode %s", unknown_ep)

                project["episodes"] = new_episodes

            with project_change_source("webui"):
                manager.save_project(name, project)
            return {"success": True, "project": project}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{name}")
async def delete_project(name: str, _user: CurrentUser, _t: Translator):
    """删除项目"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(name)
            shutil.rmtree(project_dir)
            return {"success": True, "message": _t("project_deleted", name=name)}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}/scripts/{script_file}")
async def get_script(name: str, script_file: str, _user: CurrentUser, _t: Translator):
    """获取剧本内容"""
    try:
        script = await asyncio.to_thread(get_project_manager().load_script, name, script_file)
        return {"script": script}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("script_not_found", name=script_file))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


class UpdateSceneRequest(BaseModel):
    script_file: str
    updates: dict


@router.patch("/projects/{name}/script-scenes/{scene_id}")
async def update_scene(name: str, scene_id: str, req: UpdateSceneRequest, _user: CurrentUser, _t: Translator):
    """更新 drama 模式剧本中的单个场景镜头（按 scene_id 定位）。

    路径与项目场景资产 CRUD（``/projects/{name}/scenes/{entry_name}``）做明确区分，
    避免 FastAPI 按注册顺序优先匹配本端点导致 SceneCard 保存请求被截获、Pydantic
    必填字段校验返回双 "Field required"。
    """
    try:

        def _sync():
            manager = get_project_manager()
            script = manager.load_script(name, req.script_file)

            # 找到并更新场景
            scene_found = False
            for scene in script.get("scenes", []):
                if scene.get("scene_id") == scene_id:
                    scene_found = True
                    # 更新允许的字段
                    for key, value in req.updates.items():
                        if key in [
                            "duration_seconds",
                            "image_prompt",
                            "video_prompt",
                            "characters_in_scene",
                            "scenes",
                            "props",
                            "segment_break",
                            "note",
                        ]:
                            if value is None and key != "note":
                                continue
                            scene[key] = value
                    break

            if not scene_found:
                raise HTTPException(status_code=404, detail=_t("scene_not_found", id=scene_id))

            with project_change_source("webui"):
                manager.save_script(name, script, req.script_file)
            return {"success": True, "scene": scene}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("script_not_found", name=req.script_file))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


class UpdateSegmentRequest(BaseModel):
    script_file: str
    duration_seconds: int | None = None
    segment_break: bool | None = None
    image_prompt: dict | str | None = None
    video_prompt: dict | str | None = None
    transition_to_next: str | None = None
    note: str | None = None
    characters_in_segment: list[str] | None = None
    scenes: list[str] | None = None
    props: list[str] | None = None


class UpdateOverviewRequest(BaseModel):
    synopsis: str | None = None
    genre: str | None = None
    theme: str | None = None
    world_setting: str | None = None


@router.patch("/projects/{name}/segments/{segment_id}")
async def update_segment(name: str, segment_id: str, req: UpdateSegmentRequest, _user: CurrentUser, _t: Translator):
    """更新说书模式片段"""
    try:

        def _sync():
            manager = get_project_manager()
            script = manager.load_script(name, req.script_file)

            # 检查是否为说书模式
            if script.get("content_mode") != "narration" and "segments" not in script:
                raise HTTPException(status_code=400, detail=_t("narration_mode_required"))

            # 找到并更新片段
            segment_found = False
            for segment in script.get("segments", []):
                if segment.get("segment_id") == segment_id:
                    segment_found = True
                    if req.duration_seconds is not None:
                        segment["duration_seconds"] = req.duration_seconds
                    if req.segment_break is not None:
                        segment["segment_break"] = req.segment_break
                    if req.image_prompt is not None:
                        segment["image_prompt"] = req.image_prompt
                    if req.video_prompt is not None:
                        segment["video_prompt"] = req.video_prompt
                    if req.transition_to_next is not None:
                        segment["transition_to_next"] = req.transition_to_next
                    if "note" in req.model_fields_set:
                        segment["note"] = req.note
                    for field in ("characters_in_segment", "scenes", "props"):
                        if field in req.model_fields_set:
                            segment[field] = getattr(req, field) or []
                    break

            if not segment_found:
                raise HTTPException(status_code=404, detail=_t("segment_not_found", id=segment_id))

            with project_change_source("webui"):
                manager.save_script(name, script, req.script_file)
            return {"success": True, "segment": segment}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("script_not_found", name=req.script_file))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 源文件管理 ====================


@router.post("/projects/{name}/source")
async def set_project_source(
    name: Annotated[str, FastAPIPath(pattern=r"^[a-zA-Z0-9_-]+$")],
    _user: CurrentUser,
    _t: Translator,
    generate_overview: Annotated[bool, Form()] = True,
    content: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
):
    """上传小说源文件或直接提交文本内容，可选触发 AI 概述生成。

    两种输入方式（互斥，均使用 multipart/form-data）：
    - file：上传 .txt/.md 文件，文件名取自上传文件
    - content：直接提交文本内容，自动命名为 novel.txt

    最大 200000 字符（约 10 万汉字）。
    """
    MAX_CHARS = 200_000
    ALLOWED_SUFFIXES = {".txt", ".md"}

    if not content and not file:
        raise HTTPException(status_code=400, detail=_t("content_or_file_required"))
    if content and file:
        raise HTTPException(status_code=400, detail=_t("one_of_content_or_file"))

    try:
        manager = get_project_manager()

        # 异步读取上传文件
        raw: bytes | None = None
        if file:
            original_name = file.filename or "novel.txt"
            suffix = Path(original_name).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(status_code=400, detail=_t("unsupported_file_type", name=suffix))
            if file.size is not None and file.size > MAX_CHARS * 4:
                raise HTTPException(status_code=400, detail=_t("file_too_large", max_chars=MAX_CHARS))
            raw = await file.read()

        # 同步文件 I/O 在线程中执行
        def _sync_write():
            if not manager.project_exists(name):
                raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
            project_dir = manager.get_project_path(name)
            source_dir = project_dir / "source"
            source_dir.mkdir(parents=True, exist_ok=True)

            if raw is not None:
                safe_filename = Path(original_name).name
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    raise HTTPException(status_code=400, detail=_t("invalid_encoding"))
                if len(text) > MAX_CHARS:
                    raise HTTPException(status_code=400, detail=_t("file_too_large", max_chars=MAX_CHARS))
                (source_dir / safe_filename).write_text(text, encoding="utf-8")
                return safe_filename, len(text)
            else:
                if len(content) > MAX_CHARS:
                    raise HTTPException(status_code=400, detail=_t("file_too_large", max_chars=MAX_CHARS))
                safe_filename = "novel.txt"
                (source_dir / safe_filename).write_text(content, encoding="utf-8")
                return safe_filename, len(content)

        safe_filename, chars = await asyncio.to_thread(_sync_write)

        result: dict = {"success": True, "filename": safe_filename, "chars": chars}

        if generate_overview:
            try:
                with project_change_source("webui"):
                    overview = await manager.generate_overview(name)
                result["overview"] = overview
            except Exception as ov_err:
                result["overview"] = None
                result["overview_error"] = str(ov_err)

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if file:
            await file.close()


# ==================== 项目概述管理 ====================


@router.post("/projects/{name}/generate-overview")
async def generate_overview(name: str, _user: CurrentUser, _t: Translator):
    """使用 AI 生成项目概述"""
    try:
        with project_change_source("webui"):
            overview = await get_project_manager().generate_overview(name)
        return {"success": True, "overview": overview}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{name}/overview")
async def update_overview(name: str, req: UpdateOverviewRequest, _user: CurrentUser, _t: Translator):
    """更新项目概述（手动编辑）"""
    try:

        def _sync():
            manager = get_project_manager()
            project = manager.load_project(name)

            if "overview" not in project:
                project["overview"] = {}

            if req.synopsis is not None:
                project["overview"]["synopsis"] = req.synopsis
            if req.genre is not None:
                project["overview"]["genre"] = req.genre
            if req.theme is not None:
                project["overview"]["theme"] = req.theme
            if req.world_setting is not None:
                project["overview"]["world_setting"] = req.world_setting

            with project_change_source("webui"):
                manager.save_project(name, project)
            return {"success": True, "overview": project["overview"]}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
