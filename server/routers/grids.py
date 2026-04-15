"""
宫格图生成 API 路由

处理宫格图（grid-image）的生成、列表查询、单项查询和重新生成请求。
所有生成请求入队到 GenerationQueue，由 GenerationWorker 异步执行。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib import PROJECT_ROOT
from lib.generation_queue import get_generation_queue
from lib.grid.layout import calculate_grid_layout
from lib.grid.models import GridGeneration
from lib.grid.prompt_builder import build_grid_prompt
from lib.grid_manager import GridManager
from lib.project_manager import ProjectManager
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from server.auth import CurrentUser

router = APIRouter(prefix="/projects/{project_name}", tags=["grids"])

# 初始化管理器
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


def _build_grid_task_payload(
    *,
    prompt: str | None,
    script_file: str,
    scene_ids: list[str],
    grid_size: str,
    rows: int,
    cols: int,
    grid_aspect_ratio: str,
    video_aspect_ratio: str,
    backend_snapshot: dict,
) -> dict:
    """Build a consistent payload dict for grid generation tasks."""
    return {
        "prompt": prompt,
        "script_file": script_file,
        "scene_ids": scene_ids,
        "grid_size": grid_size,
        "rows": rows,
        "cols": cols,
        "grid_aspect_ratio": grid_aspect_ratio,
        "video_aspect_ratio": video_aspect_ratio,
        **backend_snapshot,
    }


# ==================== 请求/响应模型 ====================


class GenerateGridRequest(BaseModel):
    script_file: str
    scene_ids: list[str] | None = None


class GenerateGridResponse(BaseModel):
    success: bool
    grid_ids: list[str]
    task_ids: list[str]
    message: str


# ==================== 宫格图生成 ====================


@router.post("/generate/grid/{episode}", response_model=GenerateGridResponse)
async def generate_grid(
    project_name: str,
    episode: int,
    req: GenerateGridRequest,
    _user: CurrentUser,
):
    """
    提交宫格图生成任务到队列，按分段分组，每组 N>=4 个场景生成一个宫格图。

    立即返回 grid_ids 和 task_ids。生成由 GenerationWorker 异步执行。
    """
    try:
        from server.routers.generate import _snapshot_image_backend

        project = get_project_manager().load_project(project_name)
        script = get_project_manager().load_script(project_name, req.script_file)
        project_path = get_project_manager().get_project_path(project_name)

        items, id_field, _, _, _ = get_storyboard_items(script)
        aspect_ratio = project.get("aspect_ratio", "9:16")
        style = project.get("style", "")

        groups = group_scenes_by_segment_break(items, id_field)

        # 若指定了 scene_ids，只保留包含这些 scene 的分组
        if req.scene_ids:
            sid_set = set(req.scene_ids)
            groups = [g for g in groups if any(item[id_field] in sid_set for item in g)]

        grid_ids: list[str] = []
        task_ids: list[str] = []
        queue = get_generation_queue()
        gm = GridManager(project_path)

        # Pre-load existing grids for cleanup
        existing_grids = gm.list_all()

        for group in groups:
            all_scene_ids = [item[id_field] for item in group]
            n = len(all_scene_ids)
            layout = calculate_grid_layout(n, aspect_ratio)
            if layout is None:
                continue

            # 清理该组旧的 grid 记录（限定同脚本同集，scene_ids 是当前组子集的旧 grid）
            # 跳过 pending/generating 状态的记录，避免 worker 执行时找不到资源
            group_id_set = set(all_scene_ids)
            for old_grid in existing_grids:
                if (
                    old_grid.script_file == req.script_file
                    and old_grid.episode == episode
                    and old_grid.status not in ("pending", "generating")
                    and old_grid.scene_ids
                    and set(old_grid.scene_ids) <= group_id_set
                ):
                    gm.delete(old_grid.id)

            # 将大分组拆分为多个宫格批次（余下不足4个的场景也用 grid_4 + 占位符）
            chunks: list[list] = []
            if n > layout.cell_count:
                for i in range(0, n, layout.cell_count):
                    chunk = group[i : i + layout.cell_count]
                    chunks.append(chunk)
            else:
                chunks.append(group)

            backend_snapshot = _snapshot_image_backend(project_name)

            for chunk in chunks:
                chunk_ids = [item[id_field] for item in chunk]
                chunk_layout = calculate_grid_layout(len(chunk_ids), aspect_ratio)
                if chunk_layout is None:
                    continue

                grid = GridGeneration.create(
                    episode=episode,
                    script_file=req.script_file,
                    scene_ids=chunk_ids,
                    rows=chunk_layout.rows,
                    cols=chunk_layout.cols,
                    grid_size=chunk_layout.grid_size,
                    provider=backend_snapshot.get("image_provider", ""),
                    model=backend_snapshot.get("image_model", ""),
                )

                prompt = build_grid_prompt(
                    scenes=chunk,
                    id_field=id_field,
                    rows=chunk_layout.rows,
                    cols=chunk_layout.cols,
                    style=style,
                    aspect_ratio=aspect_ratio,
                    grid_aspect_ratio=chunk_layout.grid_aspect_ratio,
                )

                grid.prompt = prompt
                gm.save(grid)

                task = await queue.enqueue_task(
                    project_name=project_name,
                    task_type="grid",
                    media_type="image",
                    resource_id=grid.id,
                    payload=_build_grid_task_payload(
                        prompt=prompt,
                        script_file=req.script_file,
                        scene_ids=chunk_ids,
                        grid_size=chunk_layout.grid_size,
                        rows=chunk_layout.rows,
                        cols=chunk_layout.cols,
                        grid_aspect_ratio=chunk_layout.grid_aspect_ratio,
                        video_aspect_ratio=aspect_ratio,
                        backend_snapshot=backend_snapshot,
                    ),
                    script_file=req.script_file,
                    source="webui",
                    user_id=_user.id,
                )
                grid_ids.append(grid.id)
                task_ids.append(task["task_id"])

        return GenerateGridResponse(
            success=True,
            grid_ids=grid_ids,
            task_ids=task_ids,
            message=f"已提交 {len(grid_ids)} 个宫格生成任务",
        )

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("宫格生成请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 宫格图列表 ====================


@router.get("/grids")
async def list_grids(project_name: str, _user: CurrentUser):
    """列出项目下所有宫格图记录。"""
    try:
        project_path = get_project_manager().get_project_path(project_name)
        gm = GridManager(project_path)
        return [g.to_dict() for g in gm.list_all()]
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("列出宫格图失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 宫格图详情 ====================


@router.get("/grids/{grid_id}")
async def get_grid(project_name: str, grid_id: str, _user: CurrentUser):
    """获取单个宫格图记录。"""
    try:
        project_path = get_project_manager().get_project_path(project_name)
        gm = GridManager(project_path)
        grid = gm.get(grid_id)
        if grid is None:
            raise HTTPException(status_code=404, detail=f"Grid {grid_id} 不存在")
        return grid.to_dict()
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("获取宫格图失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 重新生成宫格图 ====================


@router.post("/grids/{grid_id}/regenerate")
async def regenerate_grid(project_name: str, grid_id: str, _user: CurrentUser):
    """重置宫格图状态并重新入队生成任务。"""
    try:
        from server.routers.generate import _snapshot_image_backend

        project_path = get_project_manager().get_project_path(project_name)
        gm = GridManager(project_path)
        grid = gm.get(grid_id)
        if grid is None:
            raise HTTPException(status_code=404, detail=f"Grid {grid_id} 不存在")

        grid.status = "pending"
        grid.error_message = None
        gm.save(grid)

        project = get_project_manager().load_project(project_name)
        aspect_ratio = project.get("aspect_ratio", "9:16")
        layout = calculate_grid_layout(len(grid.scene_ids), aspect_ratio)
        grid_aspect_ratio = layout.grid_aspect_ratio if layout else aspect_ratio

        backend_snapshot = _snapshot_image_backend(project_name)
        queue = get_generation_queue()
        task = await queue.enqueue_task(
            project_name=project_name,
            task_type="grid",
            media_type="image",
            resource_id=grid.id,
            payload=_build_grid_task_payload(
                prompt=grid.prompt,
                script_file=grid.script_file,
                scene_ids=grid.scene_ids,
                grid_size=grid.grid_size,
                rows=grid.rows,
                cols=grid.cols,
                grid_aspect_ratio=grid_aspect_ratio,
                video_aspect_ratio=aspect_ratio,
                backend_snapshot=backend_snapshot,
            ),
            script_file=grid.script_file,
            source="webui",
            user_id=_user.id,
        )

        return {"success": True, "task_id": task["task_id"]}

    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("重新生成宫格图失败")
        raise HTTPException(status_code=500, detail=str(e))
