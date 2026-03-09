"""
项目管理路由

处理项目的 CRUD 操作，复用 lib/project_manager.py
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Optional, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask


logger = logging.getLogger(__name__)

from lib import PROJECT_ROOT
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from lib.status_calculator import StatusCalculator
from server.auth import create_download_token, get_current_user, verify_download_token
from server.services.project_archive import (
    ProjectArchiveService,
    ProjectArchiveValidationError,
)

router = APIRouter()

# 初始化项目管理器和状态计算器
pm = ProjectManager(PROJECT_ROOT / "projects")
calc = StatusCalculator(pm)


def get_project_manager() -> ProjectManager:
    return pm


def get_status_calculator() -> StatusCalculator:
    return calc


def get_archive_service() -> ProjectArchiveService:
    return ProjectArchiveService(get_project_manager())


class CreateProjectRequest(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    style: Optional[str] = ""
    content_mode: Optional[str] = "narration"


class UpdateProjectRequest(BaseModel):
    title: Optional[str] = None
    style: Optional[str] = None
    content_mode: Optional[str] = None
    aspect_ratio: Optional[dict] = None


def _cleanup_temp_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


@router.post("/projects/import")
async def import_project_archive(
    _user: Annotated[dict, Depends(get_current_user)],
    file: UploadFile = File(...),
    conflict_policy: str = Form("prompt"),
):
    """从 ZIP 导入项目。"""
    upload_path: Optional[str] = None
    try:
        fd, upload_path = tempfile.mkstemp(prefix="arcreel-upload-", suffix=".zip")
        os.close(fd)

        with open(upload_path, "wb") as target:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)

        result = get_archive_service().import_project_archive(
            Path(upload_path),
            uploaded_filename=file.filename,
            conflict_policy=conflict_policy,
        )
        return {
            "success": True,
            "project_name": result.project_name,
            "project": result.project,
            "warnings": result.warnings,
            "conflict_resolution": result.conflict_resolution,
        }
    except ProjectArchiveValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "errors": exc.errors,
                "warnings": exc.warnings,
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
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """签发短时效下载 token，用于浏览器原生下载认证。"""
    try:
        if not get_project_manager().project_exists(name):
            raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在或未初始化")

        username = current_user["sub"]
        download_token = create_download_token(username, name)
        return {"download_token": download_token, "expires_in": 300}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}/export")
async def export_project_archive(
    name: str,
    download_token: str = Query(...),
    scope: str = Query("full"),
):
    """将项目导出为 ZIP。需要 download_token 认证（通过 POST /export/token 获取）。"""
    if scope not in ("full", "current"):
        raise HTTPException(status_code=422, detail="scope 必须为 full 或 current")

    # 验证 download_token
    import jwt as pyjwt

    try:
        verify_download_token(download_token, name)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="下载链接已过期，请重新导出")
    except ValueError:
        raise HTTPException(status_code=403, detail="下载 token 与目标项目不匹配")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="下载 token 无效")

    try:
        archive_path, download_name = get_archive_service().export_project(name, scope=scope)
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=download_name,
            background=BackgroundTask(_cleanup_temp_file, str(archive_path)),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在或未初始化")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects")
async def list_projects(_user: Annotated[dict, Depends(get_current_user)]):
    """列出所有项目"""
    manager = get_project_manager()
    calculator = get_status_calculator()
    projects = []
    for name in manager.list_projects():
        try:
            # 尝试加载项目元数据
            if manager.project_exists(name):
                project = manager.load_project(name)
                # 获取缩略图（第一个分镜图）
                project_dir = manager.get_project_path(name)
                storyboards_dir = project_dir / "storyboards"
                thumbnail = None
                if storyboards_dir.exists():
                    scene_images = sorted(storyboards_dir.glob("scene_*.png"))
                    if scene_images:
                        thumbnail = f"/api/v1/files/{name}/storyboards/{scene_images[0].name}"

                # 使用 StatusCalculator 计算进度（读时计算）
                progress = calculator.calculate_project_progress(name)
                current_phase = calculator.calculate_current_phase(progress)

                projects.append({
                    "name": name,
                    "title": project.get("title", name),
                    "style": project.get("style", ""),
                    "thumbnail": thumbnail,
                    "progress": progress,
                    "current_phase": current_phase
                })
            else:
                # 没有 project.json 的项目
                status = manager.get_project_status(name)
                projects.append({
                    "name": name,
                    "title": name,
                    "style": "",
                    "thumbnail": None,
                    "progress": {},
                    "current_phase": status.get("current_stage", "empty")
                })
        except Exception as e:
            # 出错时返回基本信息
            logger.warning("加载项目 '%s' 元数据失败: %s", name, e)
            projects.append({
                "name": name,
                "title": name,
                "style": "",
                "thumbnail": None,
                "progress": {},
                "current_phase": "error",
                "error": str(e)
            })

    return {"projects": projects}


@router.post("/projects")
async def create_project(req: CreateProjectRequest, _user: Annotated[dict, Depends(get_current_user)]):
    """创建新项目"""
    try:
        manager = get_project_manager()
        title = (req.title or "").strip()
        manual_name = (req.name or "").strip()
        if not title and not manual_name:
            raise HTTPException(status_code=400, detail="项目标题不能为空")
        project_name = manual_name or manager.generate_project_name(title)

        # 创建项目目录结构
        manager.create_project(project_name)
        # 创建项目元数据
        with project_change_source("webui"):
            project = manager.create_project_metadata(
                project_name,
                title or manual_name,
                req.style,
                req.content_mode,
            )
        return {"success": True, "name": project_name, "project": project}
    except FileExistsError:
        raise HTTPException(status_code=400, detail=f"项目 '{project_name}' 已存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}")
async def get_project(name: str, _user: Annotated[dict, Depends(get_current_user)]):
    """获取项目详情（含实时计算字段）"""
    try:
        manager = get_project_manager()
        calculator = get_status_calculator()
        if not manager.project_exists(name):
            raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在或未初始化")

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
                    # 使用纯文件名作为 key（去掉 scripts/ 前缀）
                    key = script_file.replace("scripts/", "", 1) if script_file.startswith("scripts/") else script_file
                    scripts[key] = script
                except FileNotFoundError:
                    pass

        return {
            "project": project,
            "scripts": scripts
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{name}")
async def update_project(name: str, req: UpdateProjectRequest, _user: Annotated[dict, Depends(get_current_user)]):
    """更新项目元数据"""
    try:
        manager = get_project_manager()
        project = manager.load_project(name)

        if req.content_mode is not None or req.aspect_ratio is not None:
            raise HTTPException(
                status_code=400,
                detail="项目创建后不支持修改 content_mode 或 aspect_ratio",
            )

        if req.title is not None:
            project["title"] = req.title
        if req.style is not None:
            project["style"] = req.style

        with project_change_source("webui"):
            manager.save_project(name, project)
        return {"success": True, "project": project}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{name}")
async def delete_project(name: str, _user: Annotated[dict, Depends(get_current_user)]):
    """删除项目"""
    try:
        project_dir = get_project_manager().get_project_path(name)
        shutil.rmtree(project_dir)
        return {"success": True, "message": f"项目 '{name}' 已删除"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}/scripts/{script_file}")
async def get_script(name: str, script_file: str, _user: Annotated[dict, Depends(get_current_user)]):
    """获取剧本内容"""
    try:
        script = get_project_manager().load_script(name, script_file)
        return {"script": script}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"剧本 '{script_file}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


class UpdateSceneRequest(BaseModel):
    script_file: str
    updates: dict


@router.patch("/projects/{name}/scenes/{scene_id}")
async def update_scene(name: str, scene_id: str, req: UpdateSceneRequest, _user: Annotated[dict, Depends(get_current_user)]):
    """更新场景"""
    try:
        manager = get_project_manager()
        script = manager.load_script(name, req.script_file)

        # 找到并更新场景
        scene_found = False
        for scene in script.get("scenes", []):
            if scene.get("scene_id") == scene_id:
                scene_found = True
                # 更新允许的字段
                for key, value in req.updates.items():
                    if key in ["duration_seconds", "image_prompt", "video_prompt",
                               "characters_in_scene", "clues_in_scene", "segment_break"]:
                        if value is None:
                            continue
                        scene[key] = value
                break

        if not scene_found:
            raise HTTPException(status_code=404, detail=f"场景 '{scene_id}' 不存在")

        with project_change_source("webui"):
            manager.save_script(name, script, req.script_file)
        return {"success": True, "scene": scene}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"剧本不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


class UpdateSegmentRequest(BaseModel):
    script_file: str
    duration_seconds: Optional[int] = None
    segment_break: Optional[bool] = None
    image_prompt: Optional[Union[dict, str]] = None
    video_prompt: Optional[Union[dict, str]] = None
    transition_to_next: Optional[str] = None


class UpdateOverviewRequest(BaseModel):
    synopsis: Optional[str] = None
    genre: Optional[str] = None
    theme: Optional[str] = None
    world_setting: Optional[str] = None


@router.patch("/projects/{name}/segments/{segment_id}")
async def update_segment(name: str, segment_id: str, req: UpdateSegmentRequest, _user: Annotated[dict, Depends(get_current_user)]):
    """更新说书模式片段"""
    try:
        manager = get_project_manager()
        script = manager.load_script(name, req.script_file)

        # 检查是否为说书模式
        if script.get('content_mode') != 'narration' and 'segments' not in script:
            raise HTTPException(status_code=400, detail="该剧本不是说书模式，请使用场景更新接口")

        # 找到并更新片段
        segment_found = False
        for segment in script.get("segments", []):
            if segment.get("segment_id") == segment_id:
                segment_found = True
                # 更新字段
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
                break

        if not segment_found:
            raise HTTPException(status_code=404, detail=f"片段 '{segment_id}' 不存在")

        with project_change_source("webui"):
            manager.save_script(name, script, req.script_file)
        return {"success": True, "segment": segment}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"剧本不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 项目概述管理 ====================

@router.post("/projects/{name}/generate-overview")
async def generate_overview(name: str, _user: Annotated[dict, Depends(get_current_user)]):
    """使用 AI 生成项目概述"""
    try:
        with project_change_source("webui"):
            overview = await get_project_manager().generate_overview(name)
        return {"success": True, "overview": overview}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{name}/overview")
async def update_overview(name: str, req: UpdateOverviewRequest, _user: Annotated[dict, Depends(get_current_user)]):
    """更新项目概述（手动编辑）"""
    try:
        manager = get_project_manager()
        project = manager.load_project(name)

        # 确保 overview 字段存在
        if "overview" not in project:
            project["overview"] = {}

        # 更新非空字段
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
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
