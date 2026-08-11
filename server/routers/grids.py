"""
宫格图生成 API 路由

处理宫格图（grid-image）的生成、列表查询、单项查询和重新生成请求。
所有生成请求入队到 GenerationQueue，由 GenerationWorker 异步执行。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from lib.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.generation_queue import get_generation_queue
from lib.grid.layout import grid_aspect_ratio_for, max_cell_count, plan_grid_chunks, video_aspect_ratio_of
from lib.grid.models import GridGeneration, build_grid_task_payload
from lib.grid.prompt_builder import build_grid_prompt
from lib.grid_manager import GridManager
from lib.i18n import Translator
from lib.image_utils import MAX_UPLOAD_PIXELS, ImagePixelLimitError, normalize_storyboard_upload
from lib.json_io import domain_error_on_value_error
from lib.project_change_hints import project_change_source
from lib.project_manager import get_project_manager
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from lib.version_manager import VersionManager
from server.auth import CurrentUser
from server.services.grid_access import ensure_grid_writable
from server.services.grid_resolution import resolve_large_grid_allowed
from server.services.grid_split import GridImageNotReadyError, apply_grid_split
from server.services.upload_finalize import (
    UploadTooLargeError,
    UploadValidationError,
    record_upload_version,
    save_uploaded_bytes,
    validate_upload,
)

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
    # 广告/短片项目与关闭宫格开关的项目在此一并拒绝：写入边界（create/PATCH 拒 ad 开启
    # grid_storyboard）之外，动作端点再设一道防线，不让 HTTP 直调绕过开关产生计费任务
    project = _load_project_for_grid_write(project_name)
    # 路径穿越等非法 script_file 是坏请求，400 而非落入下方 500 兜底；剧本文件损坏
    # （JSONDecodeError）不能被误判为非法 script_file，交由 app 级 catch-all 收口为通用 500
    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_script_file", name=req.script_file)):
        script = get_project_manager().load_script(project_name, req.script_file)
    project_path = get_project_manager().get_project_path(project_name)

    items, id_field, _, _, _ = get_storyboard_items(script)
    aspect_ratio = video_aspect_ratio_of(project)
    # style 同样允许显式 null，须显式判空而非依赖 dict.get 的默认值
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

    for group in groups:
        all_scene_ids = [item[id_field] for item in group]
        # 超上限分组切为多张宫格批次（末批不足一档时落小档 + 占位格），
        # 切块与预览、费用估算、SDK 工具同源（plan_grid_chunks）；
        # 空分组是唯一的空产出，此时连旧记录清理也一并跳过。
        plans = plan_grid_chunks(group, aspect_ratio, allow_large_grid=allow_large_grid)
        if not plans:
            continue

        # 清理该组旧的 grid 记录（限定同脚本同集，scene_ids 是当前组子集的旧 grid；
        # 跳过 pending/generating 状态的记录，避免 worker 执行时找不到资源）。
        # 规则唯一定义在 GridManager.cleanup_superseded，HTTP 路由与 SDK 工具
        # (generate_grid) 两条路径共用同一实现。
        gm.cleanup_superseded(req.script_file, episode, set(all_scene_ids))

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
                video_aspect_ratio=aspect_ratio,
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


def _load_project_for_grid_write(project_name: str) -> dict:
    """加载项目并校验宫格写操作闸门；判定与版本还原共用 ``ensure_grid_writable``。"""
    # project.json 损坏（JSONDecodeError）不能被误判为非法项目名，交由 app 级 catch-all 收口为通用 500
    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_project_name", name=project_name)):
        project = get_project_manager().load_project(project_name)
    ensure_grid_writable(project)
    return project


def _ensure_grid_idle(grid: GridGeneration) -> None:
    """生成在途（pending/generating）的宫格拒绝切分/上传：worker 完成时会覆写联合图，
    与刚上传的图或按旧图的切分互相踩踏。"""
    if grid.status in ("pending", "generating"):
        raise ConflictError("grid_generation_in_progress", grid_id=grid.id)


@router.post("/grids/{grid_id}/regenerate")
async def regenerate_grid(project_name: str, grid_id: str, user: CurrentUser):
    """重置宫格图状态并重新入队联合图生成任务（不隐含落格，切分另行显式触发）。"""
    project = _load_project_for_grid_write(project_name)
    project_path = get_project_manager().get_project_path(project_name)
    gm = GridManager(project_path)
    grid = _load_grid_or_404(project_path, grid_id)

    # 重生成是把同一次产出重跑一遍：rows/cols、prompt 与比例全部沿用记录上冻结的值，
    # 三者必须同源——prompt 里写死了画布比例，换用项目当前比例会让画布描述与下发参数矛盾。
    # 存量记录没有冻结值，回落到项目当前比例并就地补齐。想按新比例重排的用户重跑生成，
    # 那条路径会重新规划分组、prompt 与档位。
    aspect_ratio = grid.video_aspect_ratio or video_aspect_ratio_of(project)
    grid_aspect_ratio = grid_aspect_ratio_for(grid.rows, grid.cols, aspect_ratio)

    grid.status = "pending"
    grid.error_message = None
    # 清空旧 metadata，由 execute_grid_task 按 needs_i2i 重新回填
    grid.provider = ""
    grid.model = ""
    # 存量记录的冻结值在此补齐；已有冻结值时是恒等写入
    grid.video_aspect_ratio = aspect_ratio
    gm.save(grid)

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


# ==================== 切分落格 ====================


@router.post("/grids/{grid_id}/split")
async def split_grid(project_name: str, grid_id: str):
    """按当前联合图切分并覆写各分镜格——唯一覆写分镜格的操作，直接执行不设确认。

    逐格覆写前旧文件补登版本、覆写后登记新版本；frame_chain 中已不在剧本内的
    scene id 跳过（missing_scene_ids 返回）。切坏可在分镜格的版本史逐格回滚。
    """
    _load_project_for_grid_write(project_name)
    project_path = get_project_manager().get_project_path(project_name)
    grid = _load_grid_or_404(project_path, grid_id)
    _ensure_grid_idle(grid)
    if not grid.grid_image_path or not GridManager(project_path).image_path(grid_id).exists():
        raise BadRequestError("grid_image_not_ready", grid_id=grid_id)

    try:
        with project_change_source("webui"):
            result = await apply_grid_split(project_name, grid)
    except GridImageNotReadyError as exc:
        # 服务侧兜底（与上方预检间存在文件被并发删除的窗口）
        raise BadRequestError("grid_image_not_ready", grid_id=grid_id) from exc

    return {
        "success": True,
        "split_at": grid.split_at,
        "updated_scene_ids": result.updated_scene_ids,
        "missing_scene_ids": result.missing_scene_ids,
        "asset_fingerprints": result.asset_fingerprints,
    }


# ==================== 联合图上传 ====================


@router.post("/grids/{grid_id}/upload")
async def upload_grid_image(
    project_name: str,
    grid_id: str,
    _t: Translator,
    file: UploadFile = File(...),
):
    """上传联合图替换当前宫格图。

    仅做格式归一化（转 PNG、EXIF 方向矫正，不缩放不校验 rows×cols 布局，
    布局正确性由用户自行负责），登记为一个新的 grids 版本；不触发切分、
    不触碰任何分镜格。
    """
    project = _load_project_for_grid_write(project_name)
    project_path = get_project_manager().get_project_path(project_name)
    grid = _load_grid_or_404(project_path, grid_id)
    _ensure_grid_idle(grid)
    aspect_ratio = video_aspect_ratio_of(project)

    try:
        max_bytes = validate_upload(file.filename, file.size, kind="image")
        # 限定读入内存的字节数：Content-Length 缺失/被绕过时不至于 OOM
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise UploadTooLargeError(max_bytes)
    except UploadValidationError as e:
        raise HTTPException(status_code=e.status_code, detail=_t(e.key, **e.params))
    try:
        png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content, max_long_edge=None)
    except ImagePixelLimitError:
        raise BadRequestError("image_pixels_too_large", max_megapixels=MAX_UPLOAD_PIXELS // 1_000_000)
    except ValueError:
        raise BadRequestError("invalid_image_file")

    grid_manager = GridManager(project_path)
    target = grid_manager.image_path(grid_id)
    versions = VersionManager(project_path)

    with project_change_source("webui"):
        # 旧联合图若从未入版本库（历史迁移等），先补登，避免被覆盖后字节丢失
        await asyncio.to_thread(versions.ensure_current_tracked, "grids", grid_id, target, "")
        await save_uploaded_bytes(png_bytes, target)
        version = await asyncio.to_thread(
            record_upload_version,
            versions=versions,
            resource_type="grids",
            resource_id=grid_id,
            current_file=target,
            original_filename=file.filename,
        )

        def _finalize_record() -> dict[str, int]:
            from server.services.generation_tasks import emit_generation_success_batch

            # 手动补图等价于一次成功的联合图产出：failed 记录就此回到就绪态；
            # 联合图内容已变更，split_at 清空表示「待显式切分」。
            grid.mark_composite_replaced()
            # 补的图按用户当前的项目比例排布，冻结值随之改写；沿用旧值会在项目比例
            # 改过之后把新图按旧比例中心裁切。版本还原不适用：历史联合图当时的比例
            # 未随版本记录，只能沿用记录上的冻结值。
            grid.video_aspect_ratio = aspect_ratio
            grid_manager.save(grid)
            return emit_generation_success_batch(
                task_type="grid",
                project_name=project_name,
                resource_id=grid_id,
                payload={"script_file": grid.script_file},
            )

        fingerprints = await asyncio.to_thread(_finalize_record)

    return {
        "success": True,
        "path": f"grids/{grid_id}.png",
        "version": version,
        "asset_fingerprints": fingerprints,
    }
