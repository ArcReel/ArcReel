"""
宫格图生成 API 路由

处理宫格图（grid-image）的生成、列表查询、单项查询和重新生成请求。
所有生成请求入队到 GenerationQueue，由 GenerationWorker 异步执行。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from lib.artifacts.artifact_activation import (
    register_current_resource_artifact,
    resolve_artifact_episode,
    resolve_current_resource_artifact_basis,
)
from lib.artifacts.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD
from lib.artifacts.version_manager import VersionManager
from lib.generation.generation_queue import get_generation_queue
from lib.generation.generation_result import GenerationProblemCode
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.image_utils import MAX_UPLOAD_PIXELS, ImagePixelLimitError, normalize_storyboard_upload
from lib.infra.json_io import domain_error_on_value_error
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from lib.script.grid.grid_access import ensure_grid_writable
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.grid_resolution import resolve_large_grid_allowed
from lib.script.grid.layout import grid_aspect_ratio_for, max_cell_count, video_aspect_ratio_of
from lib.script.grid.models import GridGeneration, build_grid_task_payload
from lib.script.grid.prompt_builder import pending_grid_prompt_ids
from lib.script.storyboard_sequence import get_storyboard_items
from server.auth import CurrentUser
from server.i18n import Translator
from server.services.admission.reference_admission import require_admitted_storyboard_references
from server.services.currency.upload_finalize import (
    UPLOAD_VERSION_SOURCE,
    UploadTooLargeError,
    UploadValidationError,
    stage_uploaded_bytes,
    validate_upload,
)
from server.services.grid.grid_split import GridImageNotReadyError, apply_grid_split
from server.services.grid.grid_submission import (
    BlockedScene,
    GridSubmissionPlan,
    commit_grid_submission,
    ensure_grid_submittable,
    grid_submission_section,
    plan_grid_submission,
    queue_active_grid_tasks,
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
    # 逐宫格给出它自己的任务行：调用方的乐观占用标记要各等各的，拿整批清单会让每一张
    # 宫格都等到全批落库为止；未产出宫格的分组不进映射，调用方据此不给它们打标。
    task_ids_by_grid: dict[str, str]
    # 缺失即生成时联合图已就绪、尚未切分落格而跳过的宫格
    unsplit_grid_ids: list[str]
    # 批量语义：全部入队都命中既有任务（一个新任务都没建）才为 True
    deduped: bool
    message: str


def _load_admitted_grid_script(
    project_name: str,
    project: dict,
    script_file: str,
    episode: int,
) -> dict:
    """Load a grid script and prove its active project/episode binding."""

    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_script_file", name=script_file)):
        script = get_project_manager().load_script(project_name, script_file)
        script_episode = resolve_artifact_episode(
            project=project,
            script=script,
            script_filename=script_file,
        )
        if script_episode != episode:
            raise ValueError(f"script episode {script_episode!r} does not match requested episode {episode}")
    return script


# ==================== 宫格图生成 ====================


@router.post("/generate/grid/{episode}", response_model=GenerateGridResponse)
async def generate_grid(
    project_name: str,
    episode: int,
    req: GenerateGridRequest,
    user: CurrentUser,
    _t: Translator,
):
    """提交宫格联合图生成任务到队列，立即返回 grid_ids、task_ids 与两者的逐项映射。

    不传 scene_ids 时缺失即生成：只为仍缺分镜图的分组出图，联合图已就绪而未切分落格的宫格
    跳过（``unsplit_grid_ids``）；传 scene_ids 时重生成包含这些分镜的宫格。同一组分镜的宫格
    正在生成时沿用在途记录，入队去重、不重复计费。任务只产出联合图，切分落格由用户审阅后另行发起。

    规划与落地见 :mod:`server.services.grid.grid_submission`；准入是整批的，任一分镜受阻即
    不建任何任务。
    """
    # 广告/短片项目与关闭宫格开关的项目在此一并拒绝：写入边界（create/PATCH 拒 ad 开启
    # grid_storyboard）之外，动作端点再设一道防线，不让 HTTP 直调绕过开关产生计费任务
    project = _load_project_for_grid_write(project_name)
    # 路径穿越、解绑、集号失配均是坏请求，400 而非落入下方 500 兜底；剧本文件损坏
    # （JSONDecodeError）不能被误判为非法 script_file，交由 app 级 catch-all 收口为通用 500。
    script = _load_admitted_grid_script(project_name, project, req.script_file, episode)
    project_path = get_project_manager().get_project_path(project_name)
    queue = get_generation_queue()

    grid_ids: list[str] = []
    task_ids: list[str] = []
    task_ids_by_grid: dict[str, str] = {}
    deduped_flags: list[bool] = []
    async with grid_submission_section(project_name) as section:
        plan = await plan_grid_submission(
            project=project,
            project_path=project_path,
            script=script,
            script_file=req.script_file,
            episode=episode,
            # 空列表与省略同义：缺失即生成
            scene_ids=req.scene_ids or None,
            section=section,
            active_grid_tasks=queue_active_grid_tasks(
                queue, project_name=project_name, script_file=req.script_file, user_id=user.id
            ),
            large_grid_gate=resolve_large_grid_allowed,
        )
        _raise_for_refused_submission(project, plan)
        for submission in commit_grid_submission(plan, project_path):
            task = await queue.enqueue_task(
                project_name=project_name,
                task_type="grid",
                media_type="image",
                resource_id=submission.grid.id,
                payload=submission.payload,
                script_file=req.script_file,
                source="webui",
                user_id=user.id,
            )
            grid_ids.append(submission.grid.id)
            task_ids.append(task["task_id"])
            task_ids_by_grid[submission.grid.id] = task["task_id"]
            deduped_flags.append(bool(task.get("deduped", False)))

    unsplit_grid_ids = [chunk.grid.id for chunk in plan.unsplit if chunk.grid is not None]
    return GenerateGridResponse(
        success=True,
        grid_ids=grid_ids,
        task_ids=task_ids,
        task_ids_by_grid=task_ids_by_grid,
        unsplit_grid_ids=unsplit_grid_ids,
        deduped=bool(task_ids) and all(deduped_flags),
        message=_submission_message(_t, submitted=len(grid_ids), unsplit=len(unsplit_grid_ids)),
    )


def _submission_message(_t: Callable[..., str], *, submitted: int, unsplit: int) -> str:
    if submitted and unsplit:
        return _t("grid_task_submitted_with_unsplit", count=submitted, unsplit=unsplit)
    if submitted:
        return _t("grid_task_submitted", count=submitted)
    if unsplit:
        return _t("grid_unsplit_awaiting_split", unsplit=unsplit)
    return _t("grid_nothing_to_generate")


def _raise_for_refused_submission(project: dict, plan: GridSubmissionPlan) -> None:
    """整批受阻时按类别报出一条错误；同一类别的缺口一次报全。"""

    if not plan.refused:
        return
    by_code: dict[str, list[BlockedScene]] = {}
    for blocked in plan.blocked:
        by_code.setdefault(blocked.problem.code, []).append(blocked)
    if missing := by_code.get(GenerationProblemCode.UNIT_NOT_FOUND):
        raise BadRequestError("segment_not_found", id=", ".join(b.scene_id for b in missing))
    if by_code.get(GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE):
        raise ConflictError("generation_artifact_state_unavailable")
    if conflicts := by_code.get(GenerationProblemCode.ACTIVE_TASK_CONFLICT):
        grid_ids = dict.fromkeys(gid for b in conflicts for gid in b.problem.params.get("grid_ids", []))
        raise ConflictError("grid_generation_in_progress", grid_id=", ".join(grid_ids))
    require_admitted_storyboard_references(project, plan.admission_items)
    _require_grid_prompts_written(list(plan.admission_items), plan.id_field)
    raise RuntimeError(f"grid submission refused without a reportable reason: {sorted(by_code)}")


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


def _require_grid_prompts_written(items: list[dict], id_field: str) -> None:
    """任一分镜的 ``image_prompt`` 待生成即拒绝：联合图的提示词由各格拼成，缺一格整张图都出不了。"""
    pending_ids = pending_grid_prompt_ids(items, id_field)
    if pending_ids:
        raise ConflictError("script_prompt_pending", segment_id=", ".join(pending_ids))


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
    """重置宫格图状态并重新入队联合图生成任务（不隐含落格，切分另行显式触发）。

    读记录、置 pending 与入队同在提交临界区内：另一提交既不会在其间清理掉这条记录，
    也不会看到它停在 pending 却还没有任务。
    """
    project = _load_project_for_grid_write(project_name)
    project_path = get_project_manager().get_project_path(project_name)
    gm = GridManager(project_path)
    queue = get_generation_queue()
    async with grid_submission_section(project_name):
        grid = _load_grid_or_404(project_path, grid_id)
        script = _load_admitted_grid_script(project_name, project, grid.script_file, grid.episode)
        ensure_grid_submittable(project, script)
        items, id_field, _, _, _ = get_storyboard_items(script)
        # 重生成是又一次付费出图：准入与首次生成同一份判定，按记录冻结的分镜集合求值。
        # 剧本在两次生成之间被改过时，缺口以当前剧本为准——worker 也是按当前剧本重建请求的。
        scene_ids = set(grid.scene_ids)
        members = [item for item in items if str(item.get(id_field, "")) in scene_ids]
        require_admitted_storyboard_references(project, members)
        _require_grid_prompts_written(members, id_field)

        # 重生成沿用记录上冻结的 rows/cols 与比例。Worker 在执行时从同一份当前剧本、
        # 风格和冻结布局重建 provider prompt 与 provenance basis，队列里的 prompt 仅作
        # 兼容字段，不能成为脱离当前 basis 的第二真相源。存量记录没有冻结比例时回落
        # 到项目当前比例并就地补齐；想按新比例重排的用户须重跑生成规划。
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
        raise HTTPException(status_code=e.status_code, detail=_t(e.key, **e.params)) from e
    try:
        png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content, max_long_edge=None)
    except ImagePixelLimitError as exc:
        raise BadRequestError("image_pixels_too_large", max_megapixels=MAX_UPLOAD_PIXELS // 1_000_000) from exc
    except ValueError as exc:
        raise BadRequestError("invalid_image_file") from exc

    grid_manager = GridManager(project_path)
    target = grid_manager.image_path(grid_id)
    versions = VersionManager(project_path)

    with project_change_source("webui"):
        staged_file = await asyncio.to_thread(stage_uploaded_bytes, png_bytes, target)
        try:

            def _commit() -> int:
                version_box: list[int] = []

                def _replace_record(current_grid: GridGeneration) -> None:
                    _ensure_grid_idle(current_grid)
                    # 手动补图等价于一次成功的联合图产出：failed 记录就此回到就绪态；
                    # 联合图内容已变更，split_at 清空表示「待显式切分」。
                    current_grid.mark_composite_replaced()
                    # 上传按项目当前比例排布，冻结值随之改写；沿用旧值会在项目比例
                    # 改过之后把新图按旧比例中心裁切。
                    current_grid.video_aspect_ratio = aspect_ratio

                def _activate() -> None:
                    basis = resolve_current_resource_artifact_basis(
                        project_path,
                        resource_type="grids",
                        resource_id=grid_id,
                    )
                    metadata: dict[str, object] = {"source": UPLOAD_VERSION_SOURCE}
                    if file.filename:
                        metadata["original_filename"] = file.filename
                    if basis is not None:
                        metadata[IMAGE_ARTIFACT_BASIS_FIELD] = basis.to_evidence_dict()

                    def _register() -> None:
                        register_current_resource_artifact(
                            project_path,
                            resource_type="grids",
                            resource_id=grid_id,
                            basis=basis,
                        )

                    version_box.append(
                        versions.commit_staged_version(
                            resource_type="grids",
                            resource_id=grid_id,
                            prompt="",
                            staged_file=staged_file,
                            current_file=target,
                            on_commit=_register,
                            **metadata,
                        )
                    )

                committed = grid_manager.update_formal(grid_id, _replace_record, on_commit=_activate)
                if committed is None or len(version_box) != 1:
                    raise RuntimeError("grid upload metadata commit skipped staged activation")
                return version_box[0]

            version = await run_noninterruptible_sync(_commit)
        finally:
            await asyncio.to_thread(staged_file.unlink, missing_ok=True)

        from server.services.tasks.generation_tasks import emit_generation_success_batch

        fingerprints = await asyncio.to_thread(
            emit_generation_success_batch,
            task_type="grid",
            project_name=project_name,
            resource_id=grid_id,
            payload={"script_file": grid.script_file},
        )

    return {
        "success": True,
        "path": f"grids/{grid_id}.png",
        "version": version,
        "asset_fingerprints": fingerprints,
    }
