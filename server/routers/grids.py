"""
宫格图生成 API 路由

处理宫格图（grid-image）的生成、列表查询、单项查询和重新生成请求。
所有生成请求入队到 GenerationQueue，由 GenerationWorker 异步执行。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from lib.api_errors import BadRequestError, NotFoundError
from lib.generation_queue import get_generation_queue
from lib.grid.layout import grid_aspect_ratio_for, max_cell_count, plan_grid_chunks
from lib.grid.models import GridGeneration, build_grid_task_payload
from lib.grid.prompt_builder import build_grid_prompt
from lib.grid_manager import GridManager
from lib.i18n import Translator
from lib.json_io import domain_error_on_value_error
from lib.project_manager import get_project_manager, grid_storyboard_enabled
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from server.auth import CurrentUser
from server.services.grid_resolution import resolve_large_grid_allowed

router = APIRouter(prefix="/projects/{project_name}", tags=["grids"])


# ==================== 请求/响应模型 ====================


class GenerateGridRequest(BaseModel):
    script_file: str
    scene_ids: list[str] | None = None


class GenerateGridResponse(BaseModel):
    success: bool
    grid_ids: list[str]
    task_ids: list[str]
    # 批量语义：全部入队都命中既有任务（本次一个新任务都没建）才为 True
    deduped: bool
    message: str


# ==================== 宫格图生成 ====================


@router.post("/generate/grid/{episode}", response_model=GenerateGridResponse)
async def generate_grid(
    project_name: str,
    episode: int,
    req: GenerateGridRequest,
    user: CurrentUser,
    _t: Translator,
):
    """
    提交宫格图生成任务到队列，按分段分组，每组生成一张宫格图；场景数超过单张宫格
    格数上限的分组切为多张，末张不足一档时落到更小档并由占位格补齐。

    立即返回 grid_ids 和 task_ids。生成由 GenerationWorker 异步执行。
    """
    # 非法项目名（路径穿越等）是坏请求，不是「不存在」；project.json 损坏（JSONDecodeError）
    # 不能被误判为非法项目名，交由 app 级 catch-all 收口为通用 500
    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_project_name", name=project_name)):
        project = get_project_manager().load_project(project_name)
    # 广告/短片项目不开放宫格分镜（宫格单格分辨率与产品高保真目标冲突），
    # 写入边界（create/PATCH 拒 ad 开启 grid_storyboard）之外在动作端点再设一道防线
    if project.get("content_mode") == "ad":
        raise BadRequestError("ad_grid_not_supported")
    # 宫格开关是入队的唯一闸门，与 SDK 工具同用一个谓词：未开宫格（含 reference_video 路线）
    # 的项目直接拒绝，不让 HTTP 直调绕过开关产生计费任务
    if not grid_storyboard_enabled(project):
        raise BadRequestError("grid_storyboard_not_enabled")
    # 路径穿越等非法 script_file 是坏请求，400 而非落入下方 500 兜底；剧本文件损坏
    # （JSONDecodeError）不能被误判为非法 script_file，交由 app 级 catch-all 收口为通用 500
    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_script_file", name=req.script_file)):
        script = get_project_manager().load_script(project_name, req.script_file)
    project_path = get_project_manager().get_project_path(project_name)

    items, id_field, _, _, _ = get_storyboard_items(script)
    # project.json 中 aspect_ratio/style 允许显式写入 null（Pydantic 模型为 str | None），
    # dict.get(key, default) 遇到值为 None 的既有 key 不会回退默认值，须显式判空
    raw_aspect_ratio = project.get("aspect_ratio")
    aspect_ratio = raw_aspect_ratio if raw_aspect_ratio is not None else "9:16"
    raw_style = project.get("style")
    style = raw_style if raw_style is not None else ""

    # 4×4 / 5×5 只在图像分辨率档为 4K 时放行；判定与费用估算、前端预览同源
    allow_large_grid = await resolve_large_grid_allowed(project)

    groups = group_scenes_by_segment_break(items, id_field)

    # 若指定了 scene_ids，只保留包含这些 scene 的分组
    if req.scene_ids:
        sid_set = set(req.scene_ids)
        groups = [g for g in groups if any(item[id_field] in sid_set for item in g)]

    grid_ids: list[str] = []
    task_ids: list[str] = []
    deduped_flags: list[bool] = []
    queue = get_generation_queue()
    gm = GridManager(project_path)

    # Pre-load existing grids for cleanup
    existing_grids = gm.list_all()

    for group in groups:
        all_scene_ids = [item[id_field] for item in group]
        # 超上限分组切为多张宫格批次（末批不足一档时落小档 + 占位格），
        # 切块与预览、费用估算、SDK 工具同源（plan_grid_chunks）；
        # 空分组是唯一的空产出，此时连旧记录清理也一并跳过。
        plans = plan_grid_chunks(group, aspect_ratio, allow_large_grid=allow_large_grid)
        if not plans:
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

        for chunk, chunk_layout in plans:
            chunk_ids = [item[id_field] for item in chunk]

            # provider/model 由 execute_grid_task 在 image lane 解析之后回填，
            # 因为只有 task 层能根据 reference_images 判断走 T2I 还是 I2I 槽
            grid = GridGeneration.create(
                episode=episode,
                script_file=req.script_file,
                scene_ids=chunk_ids,
                rows=chunk_layout.rows,
                cols=chunk_layout.cols,
                grid_size=chunk_layout.grid_size,
                provider="",
                model="",
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
                payload=build_grid_task_payload(
                    prompt=prompt,
                    script_file=req.script_file,
                    scene_ids=chunk_ids,
                    grid_size=chunk_layout.grid_size,
                    rows=chunk_layout.rows,
                    cols=chunk_layout.cols,
                    grid_aspect_ratio=chunk_layout.grid_aspect_ratio,
                    video_aspect_ratio=aspect_ratio,
                ),
                script_file=req.script_file,
                source="webui",
                user_id=user.id,
            )
            grid_ids.append(grid.id)
            task_ids.append(task["task_id"])
            deduped_flags.append(bool(task.get("deduped", False)))

    return GenerateGridResponse(
        success=True,
        grid_ids=grid_ids,
        task_ids=task_ids,
        deduped=bool(task_ids) and all(deduped_flags),
        message=_t("grid_task_submitted", count=len(grid_ids)),
    )


# ==================== 宫格档位能力 ====================


class GridCapabilityResponse(BaseModel):
    large_grid_allowed: bool
    max_cell_count: int


@router.get("/grid-capability", response_model=GridCapabilityResponse)
async def get_grid_capability(project_name: str):
    """当前项目的宫格档位上限。

    前端批次预览据此镜像后端阶梯——预览与入队若各自判定 4K 门控，批次数会漂移。
    路径不挂在 ``/grids/`` 下，避免与 ``/grids/{grid_id}`` 抢匹配。
    """
    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_project_name", name=project_name)):
        project = get_project_manager().load_project(project_name)
    allowed = await resolve_large_grid_allowed(project)
    return GridCapabilityResponse(
        large_grid_allowed=allowed,
        max_cell_count=max_cell_count(allow_large_grid=allowed),
    )


# ==================== 宫格图列表 ====================


@router.get("/grids")
async def list_grids(project_name: str):
    """列出项目下所有宫格图记录。"""
    try:
        project_path = get_project_manager().get_project_path(project_name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    gm = GridManager(project_path)
    return [g.to_dict() for g in gm.list_all()]


# ==================== 宫格图详情 ====================


def _load_grid_or_404(project_path: Path, grid_id: str) -> GridGeneration:
    """按 ID 取宫格记录；ID 格式非法与记录不存在同样收口为 404，不泄漏格式细节。"""
    try:
        grid = GridManager(project_path).get(grid_id)
    except ValueError as exc:
        raise NotFoundError("grid_not_found", grid_id=grid_id) from exc
    if grid is None:
        raise NotFoundError("grid_not_found", grid_id=grid_id)
    return grid


@router.get("/grids/{grid_id}")
async def get_grid(project_name: str, grid_id: str):
    """获取单个宫格图记录。"""
    try:
        project_path = get_project_manager().get_project_path(project_name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    grid = _load_grid_or_404(project_path, grid_id)
    return grid.to_dict()


# ==================== 重新生成宫格图 ====================


@router.post("/grids/{grid_id}/regenerate")
async def regenerate_grid(project_name: str, grid_id: str, user: CurrentUser):
    """重置宫格图状态并重新入队生成任务。"""
    # project.json 损坏（JSONDecodeError）不能被误判为非法项目名，交由 app 级 catch-all 收口为通用 500
    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_project_name", name=project_name)):
        project = get_project_manager().load_project(project_name)
    # 广告/短片项目不开放宫格分镜：首次提交端点已封禁，重生成端点同样设防,
    # 否则残留的历史 grid 记录仍可被重新入队。宫格开关关闭后同理——历史 grid 不再可重生成
    if project.get("content_mode") == "ad":
        raise BadRequestError("ad_grid_not_supported")
    if not grid_storyboard_enabled(project):
        raise BadRequestError("grid_storyboard_not_enabled")
    project_path = get_project_manager().get_project_path(project_name)
    gm = GridManager(project_path)
    grid = _load_grid_or_404(project_path, grid_id)

    grid.status = "pending"
    grid.error_message = None
    # 清空旧 metadata，由 execute_grid_task 按 needs_i2i 重新回填
    grid.provider = ""
    grid.model = ""
    gm.save(grid)

    raw_aspect_ratio = project.get("aspect_ratio")
    aspect_ratio = raw_aspect_ratio if raw_aspect_ratio is not None else "9:16"
    # 重生成沿用记录自身的 rows/cols（含存量非方形记录）与冻结的 prompt，整图比例按该记录写入时的
    # 取值给出，与 prompt 描述的画布一致，不重走档位阶梯
    grid_aspect_ratio = grid_aspect_ratio_for(grid.rows, grid.cols, aspect_ratio)

    queue = get_generation_queue()
    task = await queue.enqueue_task(
        project_name=project_name,
        task_type="grid",
        media_type="image",
        resource_id=grid.id,
        payload=build_grid_task_payload(
            prompt=grid.prompt,
            script_file=grid.script_file,
            scene_ids=grid.scene_ids,
            grid_size=grid.grid_size,
            rows=grid.rows,
            cols=grid.cols,
            grid_aspect_ratio=grid_aspect_ratio,
            video_aspect_ratio=aspect_ratio,
        ),
        script_file=grid.script_file,
        source="webui",
        user_id=user.id,
    )

    return {"success": True, "task_id": task["task_id"], "deduped": task.get("deduped", False)}
