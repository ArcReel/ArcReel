"""
项目管理路由

处理项目的 CRUD 操作，复用 lib/project_manager.py
"""

import logging
import shutil
from typing import Optional, List, Union
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from lib import PROJECT_ROOT
from lib.project_manager import ProjectManager
from lib.status_calculator import StatusCalculator

router = APIRouter()

# 初始化项目管理器和状态计算器
pm = ProjectManager(PROJECT_ROOT / "projects")
calc = StatusCalculator(pm)


def get_project_manager() -> ProjectManager:
    return pm


def get_status_calculator() -> StatusCalculator:
    return calc


class CreateProjectRequest(BaseModel):
    name: str
    title: str
    style: Optional[str] = ""
    content_mode: Optional[str] = "narration"


class UpdateProjectRequest(BaseModel):
    title: Optional[str] = None
    style: Optional[str] = None
    content_mode: Optional[str] = None
    aspect_ratio: Optional[dict] = None


@router.get("/projects")
async def list_projects():
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
async def create_project(req: CreateProjectRequest):
    """创建新项目"""
    try:
        manager = get_project_manager()
        # 创建项目目录结构
        manager.create_project(req.name)
        # 创建项目元数据
        project = manager.create_project_metadata(req.name, req.title, req.style, req.content_mode)
        return {"success": True, "project": project}
    except FileExistsError:
        raise HTTPException(status_code=400, detail=f"项目 '{req.name}' 已存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{name}")
async def get_project(name: str):
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
async def update_project(name: str, req: UpdateProjectRequest):
    """更新项目元数据"""
    try:
        manager = get_project_manager()
        project = manager.load_project(name)

        if req.title is not None:
            project["title"] = req.title
        if req.style is not None:
            project["style"] = req.style
        if req.content_mode is not None:
            project["content_mode"] = req.content_mode
        if req.aspect_ratio is not None:
            project["aspect_ratio"] = req.aspect_ratio

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
async def delete_project(name: str):
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
async def get_script(name: str, script_file: str):
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
async def update_scene(name: str, scene_id: str, req: UpdateSceneRequest):
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
async def update_segment(name: str, segment_id: str, req: UpdateSegmentRequest):
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
async def generate_overview(name: str):
    """使用 AI 生成项目概述"""
    try:
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
async def update_overview(name: str, req: UpdateOverviewRequest):
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

        manager.save_project(name, project)
        return {"success": True, "overview": project["overview"]}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
