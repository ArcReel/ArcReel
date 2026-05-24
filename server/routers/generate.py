"""
生成 API 路由

处理分镜图、视频、角色图、线索图的生成请求。
所有生成请求入队到 GenerationQueue，由 GenerationWorker 异步执行。
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib.app_data_dir import app_data_dir
from lib.asset_types import ASSET_SPECS
from lib.generation_queue import get_generation_queue
from lib.generation_queue_client import TaskSpec, TaskSpecValidationError
from lib.i18n import Translator
from lib.project_manager import ProjectManager
from lib.script_editor import ScriptEditError
from lib.storyboard_sequence import (
    find_storyboard_item,
    get_storyboard_items,
)
from server.auth import CurrentUser

router = APIRouter()

# 初始化管理器
pm = ProjectManager(app_data_dir())


def get_project_manager() -> ProjectManager:
    return pm


# ==================== 请求模型 ====================


class GenerateStoryboardRequest(BaseModel):
    prompt: str | dict
    script_file: str


class GenerateVideoRequest(BaseModel):
    prompt: str | dict
    script_file: str
    duration_seconds: int | None = None  # 改为 None，由服务层解析
    seed: int | None = None


class GenerateCharacterRequest(BaseModel):
    prompt: str


class GenerateSceneRequest(BaseModel):
    prompt: str


class GeneratePropRequest(BaseModel):
    prompt: str


# ==================== 分镜图生成 ====================


@router.post("/projects/{project_name}/generate/storyboard/{segment_id}")
async def generate_storyboard(
    project_name: str,
    segment_id: str,
    req: GenerateStoryboardRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """
    提交分镜图生成任务到队列，立即返回 task_id。

    生成由 GenerationWorker 异步执行，状态通过 SSE 推送。
    """
    try:

        def _sync():
            get_project_manager().load_project(project_name)
            script = get_project_manager().load_script(project_name, req.script_file)
            items, id_field, _, _, _ = get_storyboard_items(script)
            resolved = find_storyboard_item(items, id_field, segment_id)
            if resolved is None:
                raise HTTPException(status_code=404, detail=_t("segment_not_found", id=segment_id))

        await asyncio.to_thread(_sync)

        # 结构校验 + 构造经单一守卫点（与 SDK 入队同源，规则不分叉）
        try:
            spec = TaskSpec.from_request(
                task_type="storyboard",
                media_type="image",
                resource_id=segment_id,
                prompt=req.prompt,
                script_file=req.script_file,
            )
        except TaskSpecValidationError as e:
            raise HTTPException(status_code=400, detail=_t(e.code, **e.params))

        # 入队
        queue = get_generation_queue()
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type=spec.task_type,
            media_type=spec.media_type,
            resource_id=spec.resource_id,
            script_file=spec.script_file,
            payload=spec.payload,
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": _t("storyboard_task_submitted", segment_id=segment_id),
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except ScriptEditError as e:
        # 脏脚本(分镜数组键损坏)→ 4xx 客户端错误而非 5xx,detail 走 i18n 不直接暴露 str(e)
        raise HTTPException(status_code=400, detail=_t("script_data_corrupted", reason=str(e)))
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 视频生成 ====================


@router.post("/projects/{project_name}/generate/video/{segment_id}")
async def generate_video(
    project_name: str,
    segment_id: str,
    req: GenerateVideoRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """
    提交视频生成任务到队列，立即返回 task_id。

    需要先有分镜图作为起始帧。生成由 GenerationWorker 异步执行。
    """
    try:

        def _sync():
            pm_local = get_project_manager()
            pm_local.load_project(project_name)
            project_path = pm_local.get_project_path(project_name)

            # 与 worker 一致：优先读取 generated_assets.storyboard_image，回退默认路径。
            # 旧宫格项目 storyboard_image 指向 scene_{id}_first.png，仍可正常解析。
            storyboard_rel: str | None = None
            try:
                script = pm_local.load_script(project_name, req.script_file)
                items, id_field, _, _, _ = get_storyboard_items(script)
                resolved = find_storyboard_item(items, id_field, segment_id)
                if resolved:
                    assets = resolved[0].get("generated_assets") or {}
                    if isinstance(assets, dict):
                        storyboard_rel = assets.get("storyboard_image")
            except FileNotFoundError:
                # 脚本不存在交由后续流程报错；此处只负责存在性检查
                pass
            except ScriptEditError as exc:
                # 脏脚本(分镜数组键损坏)→ fail-fast 4xx,与 storyboard endpoint 对齐。
                # 不再 silently pass 降级走 default 路径:default 文件恰好存在时会让请求
                # 「先返回提交成功、worker 解析脚本时再确定失败」,撕裂用户预期;脚本损坏是
                # 路由层就能识别的客户端错误,提前 4xx 比让 worker 后置失败更准确。
                raise HTTPException(
                    status_code=400,
                    detail=_t("script_data_corrupted", reason=str(exc)),
                )

            storyboard_file = (
                project_path / storyboard_rel
                if storyboard_rel
                else project_path / "storyboards" / f"scene_{segment_id}.png"
            )
            if not storyboard_file.exists():
                raise HTTPException(status_code=400, detail=_t("generate_storyboard_first", segment_id=segment_id))

        await asyncio.to_thread(_sync)

        # 结构校验 + 构造经单一守卫点（与 SDK 入队同源，规则不分叉）。
        # duration 是能力维度，留待执行层在 provider 解析后校验（见 ADR-0001）。
        try:
            spec = TaskSpec.from_request(
                task_type="video",
                media_type="video",
                resource_id=segment_id,
                prompt=req.prompt,
                script_file=req.script_file,
                extra_payload={"duration_seconds": req.duration_seconds, "seed": req.seed},
            )
        except TaskSpecValidationError as e:
            raise HTTPException(status_code=400, detail=_t(e.code, **e.params))

        # 入队（provider 由服务层根据配置自动解析，调用方无需传递）
        queue = get_generation_queue()
        result = await queue.enqueue_task(
            project_name=project_name,
            task_type=spec.task_type,
            media_type=spec.media_type,
            resource_id=spec.resource_id,
            script_file=spec.script_file,
            payload=spec.payload,
            source="webui",
            user_id=_user.id,
        )

        return {
            "success": True,
            "task_id": result["task_id"],
            "message": _t("video_task_submitted", segment_id=segment_id),
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 资产设计图生成（character / scene / prop 共用） ====================


# i18n key 命名差异：scene 用历史前缀 "project_scene_*"
_ASSET_GENERATE_I18N: dict[str, dict[str, str]] = {
    "character": {"not_found": "character_not_found", "submitted": "character_task_submitted"},
    "scene": {"not_found": "project_scene_not_found", "submitted": "scene_task_submitted"},
    "prop": {"not_found": "prop_not_found", "submitted": "prop_task_submitted"},
}


async def _enqueue_asset_generation(
    *,
    asset_type: str,
    project_name: str,
    resource_name: str,
    prompt: str,
    user_id: str,
    _t: Translator,
) -> dict:
    """三类资产（character / scene / prop）设计图生成共用入队逻辑。"""
    spec = ASSET_SPECS[asset_type]
    keys = _ASSET_GENERATE_I18N[asset_type]

    def _sync():
        project = get_project_manager().load_project(project_name)
        if resource_name not in project.get(spec.bucket_key, {}):
            raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=resource_name))

    await asyncio.to_thread(_sync)

    try:
        task_spec = TaskSpec.from_request(
            task_type=asset_type,
            media_type="image",
            resource_id=resource_name,
            prompt=prompt,
        )
    except TaskSpecValidationError as e:
        raise HTTPException(status_code=400, detail=_t(e.code, **e.params))

    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type=task_spec.task_type,
        media_type=task_spec.media_type,
        resource_id=task_spec.resource_id,
        payload=task_spec.payload,
        source="webui",
        user_id=user_id,
    )

    return {
        "success": True,
        "task_id": result["task_id"],
        "message": _t(keys["submitted"], name=resource_name),
    }


@router.post("/projects/{project_name}/generate/character/{char_name}")
async def generate_character(
    project_name: str,
    char_name: str,
    req: GenerateCharacterRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """提交角色设计图生成任务到队列，立即返回 task_id。"""
    try:
        return await _enqueue_asset_generation(
            asset_type="character",
            project_name=project_name,
            resource_name=char_name,
            prompt=req.prompt,
            user_id=_user.id,
            _t=_t,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_name}/generate/scene/{scene_name}")
async def generate_scene(
    project_name: str,
    scene_name: str,
    req: GenerateSceneRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """提交场景设计图生成任务到队列，立即返回 task_id。"""
    try:
        return await _enqueue_asset_generation(
            asset_type="scene",
            project_name=project_name,
            resource_name=scene_name,
            prompt=req.prompt,
            user_id=_user.id,
            _t=_t,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_name}/generate/prop/{prop_name}")
async def generate_prop(
    project_name: str,
    prop_name: str,
    req: GeneratePropRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """提交道具设计图生成任务到队列，立即返回 task_id。"""
    try:
        return await _enqueue_asset_generation(
            asset_type="prop",
            project_name=project_name,
            resource_name=prop_name,
            prompt=req.prompt,
            user_id=_user.id,
            _t=_t,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
