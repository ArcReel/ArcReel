"""
项目管理路由

处理项目的 CRUD 操作，复用 lib/project/project_manager.py

本模块多数处理器以 ``except Exception`` 兜底为 500。领域异常（``ApiError`` 及其子类）
可以在被兜底覆盖的写盘闭包内抛出（如 backend 字段校验、脚本结构校验），因此各处理器的
透传子句写成 ``except (HTTPException, ApiError)``——只列 ``HTTPException`` 会把这些
4xx 静默降级成 500。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi import Path as FastAPIPath
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from starlette.background import BackgroundTask

logger = logging.getLogger(__name__)

from lib.agent.agent_session_store import default_sdk_config_dir
from lib.agent.profile_manifest import ContentMode
from lib.backends.http_status_errors import TextWaitTimeout
from lib.backends.text_backends.base import TextOutputTruncatedError
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import (
    ConfigResolver,
    NoAvailableProviderError,
    VideoBucketCapabilityError,
    caps_generation_mode,
    video_bucket_for_generation_mode,
)
from lib.db import async_session_factory
from lib.episode.episode_ledger import is_derived_episode_name
from lib.episode.episode_source_commands import (
    EpisodeSourceError,
    register_whole_source_file,
    set_episode_source_text,
)
from lib.episode.episode_target_duration import (
    EPISODE_TARGET_DURATION_FIELD,
    MAX_EPISODE_TARGET_DURATION,
    MIN_EPISODE_TARGET_DURATION,
    is_valid_episode_target_duration,
)
from lib.episode.source_kinds import SourceKind
from lib.generation.task_failure import failure_message_key
from lib.generation.video_request_facts import ResolutionOverride, VideoRequestFactsError
from lib.i18n import render_generation_input_error, render_message
from lib.infra.api_errors import ApiError, BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.infra.async_thread import (
    EventLoopBridge,
    run_noninterruptible_async,
    run_noninterruptible_sync,
    run_sync_transaction,
)
from lib.infra.content_digest import file_identity, file_version
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.json_io import domain_error_on_value_error
from lib.infra.path_safety import safe_join
from lib.material.formal_objects import formal_commit_on, release_holder
from lib.project import deleted_projects
from lib.project.asset_fingerprints import compute_asset_fingerprints
from lib.project.asset_types import asset_name_comparison_key
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import EmptySourceError, EpisodeScriptReboundError, get_project_manager
from lib.prompts.style_templates import is_known_template, resolve_template_prompt
from lib.script.blank_script import BlankScriptError, start_blank_script
from lib.script.script_batch_edit import ScriptBatchEditCommand, ScriptBatchEditor, blank_item_after, script_revision
from lib.script.script_editor import resolve_items
from lib.script.script_references import annotate_derivative_references
from lib.speech.character_voice import PROJECT_FIELD as CHARACTER_VOICE_BINDING_FIELD
from lib.speech.character_voice import VALID_CHARACTER_VOICE_BINDINGS
from lib.speech.narration_config import (
    NARRATION_DELIVERY_FIELD,
    POST_PRODUCTION,
    TTS_BACKEND_FIELD,
    TTS_SPEED_FIELD,
    TTS_VOICE_FIELD,
    NarrationConfigError,
    NarrationDelivery,
)
from lib.speech.speech_rate import MAX_SPEECH_RATE_UPS, MIN_SPEECH_RATE_UPS, SPEECH_RATE_FIELD, is_valid_speech_rate
from lib.workflow.workflow_plan import WorkflowPlan, WorkflowPlanRequest
from lib.workflow.workflow_state import (
    EpisodeNextSteps,
    EpisodeProductionStatus,
    EpisodeScriptStatus,
    EpisodesSummary,
    ProjectSummary,
    WorkflowRequestError,
    WorkflowStateService,
    WorkflowStatus,
)
from server.auth import CurrentUser, create_download_token, reject_embedded_agent, verify_download_token
from server.dependencies import ProjectReads, require_project_migration_ok
from server.i18n import DisplayNamesCatalog, Translator
from server.routers._episode_source_errors import episode_source_http_error
from server.routers._script_edits import (
    execute_current_script_edit,
    require_script_edit_result,
    script_batch_status,
)
from server.routers._validators import split_video_backend_query, validate_backend_value
from server.services.admission.prompt_preview import ScriptItemNotFound, preview_item_prompts
from server.services.project import workflow_planner as workflow_plan_service
from server.services.project.episode_display import present_episode_diagnostics, with_problem_locations
from server.services.project.episode_id_records import recorded_episode_ids_on
from server.services.project.narration_settings import (
    NarrationSettingsInput,
    apply_project_tts_patch,
    new_project_narration_fields,
)
from server.services.project.project_activity import project_last_activity_at
from server.services.project.project_archive import (
    ProjectArchiveService,
    ProjectArchiveValidationError,
    import_recommendations_on,
    provider_asset_link_archive_on,
)
from server.services.project.project_cover import resolve_project_cover
from server.services.project.project_retirement import restore_retired_project, retire_project_on, retiring_project
from server.services.tasks.video_caps import resolve_project_video_capabilities
from server.tool_runtime import truncation_problem

router = APIRouter()

self_auth_router = APIRouter()


def get_workflow_state_service() -> WorkflowStateService:
    return WorkflowStateService(get_project_manager())


WorkflowStateServiceDep = Annotated[WorkflowStateService, Depends(get_workflow_state_service)]


def _project_status_payload(
    summary: ProjectSummary, project: dict[str, Any], translate: Callable[..., str]
) -> dict[str, Any]:
    """项目级状态负载：项目摘要去掉每集明细。

    列表与详情的 ``status`` 都只给项目粒度——修复标记、资产计数、分集汇总。摘要里的
    每集明细留在服务层，不让 N 个项目的列表驮上 N×集数 的对象；剧集粒度的消费方另经
    剧集接口取。
    """

    return present_episode_diagnostics(summary.model_dump(mode="json", exclude={"episodes"}), project, translate)


def _merge_episode_summaries(project: dict[str, Any], summary: ProjectSummary) -> dict[str, Any]:
    """把项目摘要的每集明细并进 ``project["episodes"]``（读时计算，不写盘）。

    每集的脚本进度、产物计数与时长只有项目摘要一个来源，口径是产物清单：可用 = current ∪
    stale，stale 另计。剧集卡、剧集头与画布读到的数字因此与工作台同源。project.json 侧的
    字段（title / script_file / hook / outline……）原样保留。
    """

    per_episode = {item.episode: item for item in summary.episodes}
    episodes = []
    for entry in project.get("episodes", []):
        if not isinstance(entry, dict):
            continue
        merged = dict(entry)
        number = entry.get("episode")
        item = per_episode.get(number) if isinstance(number, int) else None
        if item is not None:
            merged.update(item.model_dump(mode="json", exclude={"episode"}))
        episodes.append(merged)
    project["episodes"] = episodes
    return project


async def get_archive_service() -> ProjectArchiveService:
    bridge = EventLoopBridge.capture()
    return ProjectArchiveService(
        get_project_manager(),
        retire_project=retire_project_on(bridge, async_session_factory),
        formal_commit=formal_commit_on(bridge, async_session_factory),
        provider_asset_links=provider_asset_link_archive_on(bridge, async_session_factory),
        import_recommendations=import_recommendations_on(bridge, async_session_factory),
    )


ArchiveServiceDep = Annotated[ProjectArchiveService, Depends(get_archive_service)]


def get_script_batch_editor(manager: Any | None = None) -> ScriptBatchEditor:
    return ScriptBatchEditor(manager or get_project_manager())


def get_script_batch_editor_factory() -> Callable[[Any], ScriptBatchEditor]:
    """批量编辑器的路由依赖；编辑器要绑处理器内解析出的 manager，故注入工厂而非实例。"""
    return get_script_batch_editor


ScriptBatchEditorFactoryDep = Annotated[Callable[[Any], ScriptBatchEditor], Depends(get_script_batch_editor_factory)]


_PROJECT_BACKEND_FIELDS = (
    "video_backend",
    "video_provider_i2v",
    "video_provider_r2v",
    "image_provider_t2i",
    "image_provider_i2i",
    "default_image_backend",
    "text_backend_simple",
    "text_backend_complex",
    "default_text_backend",
)


def _reject_bool_speech_rate(value: object) -> object:
    """布尔不是语速：Pydantic 非严格模式会把 JSON ``true`` 折成 1.0、``false`` 折成 0.0。

    真相源与数据校验器都把 bool 判为脏值，写入侧若放行，落库后的 1.0 已无从辨认原本是布尔，
    而 0.0 又会被当成「未填」跳过写入——同一类输入两种结局。在进入区间校验前直接拒。
    """
    if isinstance(value, bool):
        raise ValueError("speech rate must be a number, not a boolean")
    return value


SpeechRateOverride = Annotated[float | None, BeforeValidator(_reject_bool_speech_rate)]


def _validated_episode_target_duration(value: int, _t: Translator) -> int:
    """把创建 / PATCH 传入的单集目标时长收进硬区间，越界即 422。

    区间与 ``lib.episode.episode_target_duration`` 的读时守卫、``patch_project`` 的强制转换、
    前端输入校验同一把尺（``is_valid_episode_target_duration``），不在这里另写边界数字。
    """
    if not is_valid_episode_target_duration(value):
        raise HTTPException(
            status_code=422,
            detail=_t(
                "episode_target_duration_out_of_range",
                min=MIN_EPISODE_TARGET_DURATION,
                max=MAX_EPISODE_TARGET_DURATION,
            ),
        )
    return value


def _validated_speech_rate(value: float, _t: Translator) -> float:
    """把创建 / PATCH 传入的口播语速估算收进硬区间，越界即 422。

    区间与 ``lib.speech.speech_rate`` 的读时守卫、前端输入校验同一把尺（``is_valid_speech_rate``），
    不在这里另写边界数字。
    """
    rate = float(value)
    if not is_valid_speech_rate(rate):
        raise HTTPException(
            status_code=422, detail=_t("speech_rate_out_of_range", min=MIN_SPEECH_RATE_UPS, max=MAX_SPEECH_RATE_UPS)
        )
    return rate


class CreateProjectRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    style: str | None = ""
    content_mode: ContentMode | None = "narration"
    aspect_ratio: str | None = "9:16"
    default_duration: int | None = None
    episode_target_duration: int | None = None
    target_duration: int | None = Field(default=None, gt=0)
    brief: str | None = None
    generation_mode: Literal["storyboard", "reference_video"]
    grid_storyboard: bool = False
    speech_rate_units_per_second: SpeechRateOverride = None
    style_template_id: str | None = None
    video_backend: str | None = None
    video_provider_i2v: str | None = None
    video_provider_r2v: str | None = None
    image_provider_t2i: str | None = None
    image_provider_i2i: str | None = None
    default_image_backend: str | None = None
    text_backend_simple: str | None = None
    text_backend_complex: str | None = None
    default_text_backend: str | None = None
    model_settings: dict[str, dict[str, str | None]] | None = None
    narration_delivery: NarrationDelivery = POST_PRODUCTION
    audio_backend: str | None = None
    narration_voice: str | None = None
    narration_speed: float | None = None


class EpisodePatch(BaseModel):
    """单集更新请求体。仅包含可写字段；未声明字段会被忽略。"""

    model_config = ConfigDict(extra="ignore")
    episode: int
    script_file: str | None = None


class UpdateProjectRequest(BaseModel):
    title: str | None = None
    style: str | None = None
    aspect_ratio: str | None = None
    default_duration: int | None = None
    episode_target_duration: int | None = None
    target_duration: int | None = Field(default=None, gt=0)
    brief: str | None = None
    grid_storyboard: bool | None = None
    video_backend: str | None = None
    video_provider_i2v: str | None = None
    video_provider_r2v: str | None = None
    image_provider_t2i: str | None = None
    image_provider_i2i: str | None = None
    default_image_backend: str | None = None
    video_generate_audio: bool | None = None
    character_voice_binding: str | None = None
    narration_delivery: NarrationDelivery | None = None
    audio_backend: str | None = None
    narration_voice: str | None = None
    narration_speed: float | None = None
    speech_rate_units_per_second: SpeechRateOverride = None
    text_backend_simple: str | None = None
    text_backend_complex: str | None = None
    default_text_backend: str | None = None
    style_template_id: str | None = None
    clear_style_image: bool | None = None
    episodes: list[EpisodePatch] | None = None
    model_settings: dict[str, dict[str, str | None]] | None = None


def _apply_narration_patch(project: dict, req: UpdateProjectRequest, bridge: EventLoopBridge) -> None:
    """在项目写锁内应用交付方式与整组配音覆盖。"""

    fields = req.model_fields_set
    try:
        if "narration_delivery" in fields:
            if req.narration_delivery is None:
                raise NarrationConfigError("narration_delivery_required")
            project[NARRATION_DELIVERY_FIELD] = req.narration_delivery
        if req.audio_backend:
            validate_backend_value(req.audio_backend, TTS_BACKEND_FIELD)
        resolver = ConfigResolver(async_session_factory)
        apply_project_tts_patch(
            project,
            req.model_dump(exclude_unset=True),
            lambda provider, model: bridge.run(resolver.audio_model_profile(provider, model)),
        )
    except NarrationConfigError as exc:
        raise UnprocessableError(exc.code) from exc


def _cleanup_temp_file(path: str) -> None:
    try:
        os.unlink(path)  # hard-delete: temp -- 归档导入导出的临时文件
    except FileNotFoundError:
        return


@router.post("/projects/import")
async def import_project_archive(
    _t: Translator,
    archive_service: ArchiveServiceDep,
    file: UploadFile = File(...),
    conflict_policy: str = Form("prompt"),
    confirm_report: bool = Form(False),
):
    """从 ZIP 导入项目。

    导入包升级时有调整或没能带入的内容时，未带 ``confirm_report`` 的请求以 409 返回
    ``confirmation_required`` 与 ``migration_report``，不留下任何项目数据；用户确认后带上它重新提交。
    """
    upload_paths: list[str] = []
    try:
        raw_file = file.file

        def _write_upload() -> str:
            fd, path = tempfile.mkstemp(prefix="arcreel-upload-", suffix=".zip")
            upload_paths.append(path)
            with os.fdopen(fd, "wb") as target:
                while True:
                    chunk = raw_file.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
            return path

        upload_path = await run_sync_transaction(_write_upload)

        def _sync():
            return archive_service.import_project_archive(
                Path(upload_path),
                uploaded_filename=file.filename,
                conflict_policy=conflict_policy,
                confirm_report=confirm_report,
                translate=_t,
            )

        result = await run_sync_transaction(_sync)
        return {
            "success": True,
            "project_name": result.project_name,
            "project": result.project,
            "warnings": [warning.render(_t) for warning in result.warnings],
            "conflict_resolution": result.conflict_resolution,
            "diagnostics": result.diagnostics,
        }
    except ProjectArchiveValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail.render(_t),
                "errors": exc.render_errors(_t),
                "warnings": exc.render_warnings(_t),
                "diagnostics": exc.diagnostics_payload(_t),
                **exc.extra,
            },
        )
    except ApiError:
        raise
    except Exception:
        logger.exception("请求处理失败")
        return JSONResponse(
            status_code=500,
            content={"detail": _t("internal_server_error"), "errors": [], "warnings": []},
        )
    finally:
        await file.close()
        for path in upload_paths:
            await run_noninterruptible_sync(_cleanup_temp_file, path)


@router.post("/projects/{name}/export/token")
async def create_export_token(
    name: str,
    current_user: CurrentUser,
    _t: Translator,
    archive_service: ArchiveServiceDep,
    scope: str = Query("full"),
):
    """签发短时效下载 token，用于浏览器原生下载认证。"""
    try:
        if scope not in ("full", "current"):
            raise HTTPException(status_code=422, detail=_t("scope_invalid"))

        def _sync():
            if not get_project_manager().project_exists(name):
                raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
            return archive_service.get_export_diagnostics(name, scope=scope, translate=_t)

        async with archive_service.export_materials_held(name, scope=scope):
            diagnostics = await asyncio.to_thread(_sync)
        username = current_user.sub
        download_token = create_download_token(username, name)
        return {
            "download_token": download_token,
            "expires_in": 300,
            "diagnostics": diagnostics,
        }
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@self_auth_router.get("/projects/{name}/export")
async def export_project_archive(
    name: str,
    _t: Translator,
    archive_service: ArchiveServiceDep,
    download_token: str = Query(...),
    scope: str = Query("full"),
):
    """将项目导出为 ZIP。需要 download_token 认证（通过 POST /export/token 获取）。"""
    if scope not in ("full", "current"):
        raise HTTPException(status_code=422, detail=_t("scope_invalid"))

    import jwt as pyjwt

    try:
        verify_download_token(download_token, name)
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail=_t("download_expired")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=_t("download_token_mismatch")) from exc
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=_t("download_token_invalid")) from exc

    archive_paths: list[Path] = []

    def _export() -> tuple[Path, str]:
        archive_path, download_name = archive_service.export_project(name, scope=scope)
        archive_paths.append(archive_path)
        return archive_path, download_name

    handed_off = False
    try:
        async with archive_service.export_materials_held(name, scope=scope):
            archive_path, download_name = await run_sync_transaction(_export)
        response = FileResponse(
            archive_path,
            media_type="application/zip",
            filename=download_name,
            background=BackgroundTask(_cleanup_temp_file, str(archive_path)),
        )
        handed_off = True
        return response
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
    finally:
        if not handed_off:
            for path in archive_paths:
                await run_noninterruptible_sync(_cleanup_temp_file, str(path))


_NO_ACTIVITY = datetime.min.replace(tzinfo=UTC)


@router.get("/projects")
async def list_projects(summaries: WorkflowStateServiceDep, _t: Translator):
    """列出所有项目，最近有活动的在前。"""

    def _sync():
        manager = get_project_manager()
        projects = []
        last_activity: dict[str, datetime] = {}
        for name in manager.list_projects():
            try:
                if manager.project_exists(name):
                    project = manager.load_project(name)
                    preloaded_scripts: dict[str, dict] = {}
                    for ep in project.get("episodes") or []:
                        script_file = ep.get("script_file")
                        if not script_file:
                            continue
                        try:
                            preloaded_scripts[script_file] = manager.load_script(name, script_file)
                        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError) as load_err:
                            logger.debug(
                                "list_projects 预加载剧本失败 project=%s script=%s err=%s",
                                name,
                                script_file,
                                load_err,
                            )

                    thumbnail = resolve_project_cover(manager, name, project, preloaded_scripts=preloaded_scripts)

                    status = _project_status_payload(
                        summaries.get_project_summary(
                            name,
                            preloaded_scripts=preloaded_scripts,
                            currency="registered",
                        ),
                        project,
                        _t,
                    )

                    activity = project_last_activity_at(
                        manager.get_project_path(name), project, preloaded_scripts.values()
                    )
                    if activity is not None:
                        last_activity[name] = activity

                    raw_title = project.get("title")
                    projects.append(
                        {
                            "name": name,
                            "title": raw_title if isinstance(raw_title, str) else "",
                            "style": project.get("style", ""),
                            "style_template_id": project.get("style_template_id"),
                            "style_image": project.get("style_image"),
                            "thumbnail": thumbnail,
                            "status": status,
                            "last_activity_at": activity.isoformat() if activity is not None else None,
                        }
                    )
            except Exception as e:
                logger.warning("加载项目 '%s' 元数据失败: %s", name, e)
                projects.append(
                    {"name": name, "title": "", "style": "", "thumbnail": None, "status": {}, "last_activity_at": None}
                )

        projects.sort(key=lambda p: last_activity.get(p["name"], _NO_ACTIVITY), reverse=True)
        return {"projects": projects}

    return await run_sync_transaction(_sync)


@router.post("/projects")
async def create_project(
    req: CreateProjectRequest,
    _t: Translator,
):
    """创建新项目"""
    try:
        try:
            narration_fields = await new_project_narration_fields(
                NarrationSettingsInput(
                    delivery=req.narration_delivery,
                    audio_backend=req.audio_backend,
                    narration_voice=req.narration_voice,
                    narration_speed=req.narration_speed,
                    provided=frozenset(req.model_fields_set & {TTS_BACKEND_FIELD, TTS_VOICE_FIELD, TTS_SPEED_FIELD}),
                ),
                resolver=ConfigResolver(async_session_factory),
            )
        except NarrationConfigError as exc:
            raise UnprocessableError(exc.code) from exc

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

            content_mode = req.content_mode or "narration"
            if content_mode == "ad":
                if req.default_duration is not None:
                    raise HTTPException(status_code=400, detail=_t("ad_no_default_duration"))
                if req.episode_target_duration is not None:
                    raise HTTPException(status_code=400, detail=_t("ad_no_episode_target_duration"))
                if req.grid_storyboard:
                    raise HTTPException(status_code=400, detail=_t("ad_grid_not_supported"))
            else:
                if req.target_duration is not None:
                    raise HTTPException(status_code=400, detail=_t("ad_only_field", field="target_duration"))
                if req.brief is not None:
                    raise HTTPException(status_code=400, detail=_t("ad_only_field", field="brief"))

            for field_name in _PROJECT_BACKEND_FIELDS:
                value = getattr(req, field_name)
                if value:
                    validate_backend_value(value, field_name)

            speech_rate = (
                None
                if req.speech_rate_units_per_second is None
                else _validated_speech_rate(req.speech_rate_units_per_second, _t)
            )
            episode_target_duration = (
                None
                if req.episode_target_duration is None
                else _validated_episode_target_duration(req.episode_target_duration, _t)
            )

            extras = {field: value for field in _PROJECT_BACKEND_FIELDS if (value := getattr(req, field))}
            if req.model_settings is not None:
                extras["model_settings"] = req.model_settings
            extras["generation_mode"] = req.generation_mode
            extras["grid_storyboard"] = req.grid_storyboard
            if speech_rate is not None:
                extras[SPEECH_RATE_FIELD] = speech_rate
            try:
                with (
                    manager.creating_project(project_name, content_mode=req.content_mode or "narration", publish=False),
                    project_change_source("webui"),
                ):
                    project = manager.create_project_metadata(
                        project_name,
                        title or manual_name,
                        style_prompt,
                        req.content_mode,
                        aspect_ratio=req.aspect_ratio,
                        default_duration=req.default_duration,
                        episode_target_duration=episode_target_duration,
                        style_template_id=req.style_template_id,
                        extras=extras or None,
                        target_duration=req.target_duration,
                        brief=req.brief,
                        narration=narration_fields,
                    )
            except FileExistsError as exc:
                raise HTTPException(status_code=400, detail=_t("project_exists", name=project_name)) from exc
            return {"success": True, "name": project_name, "project": project}

        return await run_sync_transaction(_sync)
    except ModelNotInCatalogError:
        raise
    except ValueError as e:
        logger.warning("创建项目参数错误: name=%s (%s)", req.name or req.title, e)
        raise BadRequestError("project_config_invalid") from e
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/projects/{name}/video-capabilities")
async def get_video_capabilities(
    name: str,
    _t: Translator,
    video_backend: Annotated[str | None, Query()] = None,
    resolution: Annotated[str | None, Query()] = None,
    uses_reference_images: Annotated[bool | None, Query()] = None,
):
    """解析当前项目视频模型能力 + 用户项目偏好。

    三级模型选择（项目 > 系统设置 > 系统默认）后，读 model 的 `supported_durations`
    并派生 `max_duration`；同时带回 `project.json.default_duration`（用户偏好）。
    两条生成模式（storyboard/reference_video）都可复用。

    `video_backend`（"provider/model"）用于设置表单里尚未保存的候选模型：不带该参数时按已
    落盘配置解析，带上则按候选模型 × 本项目的生成模式解析，使 voice_consistency 等二维派生值
    对应用户当前选中的模型而非上一次保存的模型；候选身份先过所属桶能力闸。裸 provider（无 "/"）报
    `video_backend_malformed`，不按目录推荐补模型。

    `resolution` / `uses_reference_images` 是表单里编辑中的未保存值：`uses_reference_images` 决定
    按哪个任务类型桶解析（缺省按项目生成模式），`resolution` 作为「覆盖分辨率」交给该桶的视频请求
    事实求值，响应里的 `duration_constraints` 即这次求值的收窄结果与成因。缺省按项目已保存档位求值
    （工作台）；`resolution` 传空串表示表单里选了「自动」：不回退到已保存档位，按项目未存档位解析
    （自定义供应商仍取模型默认档）。

    能力按项目生成模式定轴、全项目同一口径，故无需集号：生成模式创建即定、之后不可更改。
    """
    resolver = ConfigResolver(async_session_factory)
    resolution_override = None if resolution is None else ResolutionOverride(resolution or None)
    try:
        project = await asyncio.to_thread(get_project_manager().load_project, name)
        generation_type = (
            ("r2v" if uses_reference_images else "i2v")
            if uses_reference_images is not None
            else video_bucket_for_generation_mode(caps_generation_mode(project))
        )
        if video_backend:
            provider_id, model_id = split_video_backend_query(video_backend)
            project = {**project, f"video_provider_{generation_type}": f"{provider_id}/{model_id}"}
        return await resolve_project_video_capabilities(
            project,
            generation_type=generation_type,
            config_resolver=resolver,
            resolution_override=resolution_override,
            include_planning=True,
        )
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except VideoBucketCapabilityError as exc:
        raise BadRequestError(exc.code, **exc.params) from exc
    except VideoRequestFactsError as exc:
        raise UnprocessableError(exc.code, **exc.params) from exc
    except ModelNotInCatalogError:
        raise
    except ValueError as exc:
        logger.warning("项目 '%s' 视频模型能力解析失败: %s", name, exc)
        raise HTTPException(
            status_code=422,
            detail=_t("video_capabilities_unresolved", name=name),
        ) from exc


@router.get("/projects/{name}/workflow-status", response_model=WorkflowStatus)
async def get_workflow_status(
    name: str,
    _t: Translator,
    current_user: CurrentUser,
    reads: ProjectReads,
    episode: Annotated[int | None, Query(ge=1)] = None,
):
    """Return the authenticated, server-authoritative project workflow status."""

    try:
        manager = get_project_manager()
        read = await reads.run(
            ("workflow-status-v3", id(manager), name, current_user.id, current_user.role, episode),
            lambda: asyncio.to_thread(WorkflowStateService(manager).read_status, name, episode),
        )
        return present_episode_diagnostics(read.value, read.require_project(), _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except WorkflowRequestError as exc:
        raise BadRequestError("request_invalid") from exc


@router.get("/projects/{name}/workflow-status/episodes", response_model=EpisodeNextSteps)
async def get_episode_next_steps(
    name: str,
    _t: Translator,
    reads: ProjectReads,
    current_user: CurrentUser,
    episodes: Annotated[list[int] | None, Query()] = None,
    cursor: str | None = None,
):
    """按账本顺序返回最多 32 集。可重复传 episodes 指定集号，或传上一页的 next_cursor 续读。已完成集的 next_action 为 null。"""

    try:
        manager = get_project_manager()
        read = await reads.run(
            (
                "workflow-episodes-v3",
                id(manager),
                name,
                current_user.id,
                current_user.role,
                tuple(sorted(episodes)) if episodes is not None else None,
                cursor,
            ),
            lambda: asyncio.to_thread(WorkflowStateService(manager).read_episode_next_steps, name, episodes, cursor),
        )
        return present_episode_diagnostics(read.value, read.require_project(), _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except WorkflowRequestError as exc:
        raise BadRequestError("request_invalid") from exc


@router.post("/projects/{name}/workflow-plan", response_model=WorkflowPlan)
async def get_workflow_plan(
    name: str, request: WorkflowPlanRequest, current_user: CurrentUser, _t: Translator, reads: ProjectReads
):
    """Return the side-effect-free plan for one transient workflow request."""

    try:
        manager = get_project_manager()
        read = await workflow_plan_service.get_workflow_planner(manager, reads).read_plan(
            name,
            request,
            user_id=current_user.id,
            visibility=current_user.role,
        )
        return present_episode_diagnostics(read.value, read.require_project(), _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except WorkflowRequestError as exc:
        raise BadRequestError("request_invalid") from exc


class RegisteredCount(BaseModel):
    total: int
    available: int


class WorkspaceEpisode(BaseModel):
    episode: int
    script_status: EpisodeScriptStatus
    status: EpisodeProductionStatus
    item_count: int
    duration_seconds: int
    needs_replan: int
    videos: RegisteredCount
    storyboards: RegisteredCount


class WorkspaceSummary(BaseModel):
    currency: Literal["registered"] = "registered"
    needs_repair: bool
    repair_reason: str | None
    assets: dict[str, RegisteredCount]
    episodes_summary: EpisodesSummary | None = None
    episodes: list[WorkspaceEpisode]
    source_remaining: bool


class WorkspaceResponse(BaseModel):
    project: dict[str, Any]
    summary: WorkspaceSummary
    asset_fingerprints: dict[str, int]


@router.get("/projects/{name}/workspace", response_model=WorkspaceResponse)
async def get_workspace(
    name: str,
    _t: Translator,
    summaries: WorkflowStateServiceDep,
    current_user: CurrentUser,
    coalescer: ProjectReads,
    episodes: Annotated[list[int] | None, Query()] = None,
):
    manager = get_project_manager()

    def read():
        try:
            project = manager.load_project(name)
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=name) from exc
        start = perf_counter()
        media_paths: set[str] | None = None if episodes is None else set()
        try:
            summary = summaries.get_project_summary(
                name,
                project=project,
                currency="registered",
                workspace=True,
                episode_numbers=episodes,
                media_paths=media_paths,
            )
        except WorkflowRequestError as exc:
            raise BadRequestError("request_invalid") from exc
        summarized = perf_counter()
        fingerprints = compute_asset_fingerprints(manager.get_project_path(name), media_paths)
        scanned = perf_counter()
        return (
            project,
            WorkspaceSummary.model_validate(summary, from_attributes=True),
            fingerprints,
            summarized - start,
            scanned - summarized,
        )

    key = (
        "workspace",
        "registered",
        1,
        id(manager),
        name,
        current_user.id,
        current_user.role,
        None if episodes is None else tuple(sorted(episodes)),
    )
    project, data, fingerprints, summary_time, fingerprint_time = await coalescer.run(
        key, lambda: run_sync_transaction(read)
    )

    def present():
        start = perf_counter()
        summary = data.model_dump()
        if episodes is not None:
            summary["episodes_summary"] = None
        if data.repair_reason:
            summary["repair_reason"] = present_episode_diagnostics(data.repair_reason, project, _t)
        response = JSONResponse({"project": project, "summary": summary, "asset_fingerprints": fingerprints})
        logger.info(
            "workspace %s summary=%.4fs fingerprints=%.4fs serialization=%.4fs",
            name,
            summary_time,
            fingerprint_time,
            perf_counter() - start,
        )
        return response

    return await run_sync_transaction(present)


@router.get("/projects/{name}")
async def get_project(
    name: str,
    _t: Translator,
    summaries: WorkflowStateServiceDep,
    current_user: CurrentUser,
    coalescer: ProjectReads,
):
    """获取项目详情（含实时计算字段）。"""
    try:
        manager = get_project_manager()

        def _sync():
            if not manager.project_exists(name):
                raise NotFoundError("project_not_found", name=name)

            project = manager.load_project(name)

            preloaded: dict[str, dict[str, Any] | None] = {}
            summary = summaries.get_project_summary(name, project=project, detail_scripts=preloaded)
            scripts = {}
            for ep in project.get("episodes", []):
                script_file = ep.get("script_file")
                if not script_file:
                    continue
                if script_file not in preloaded:
                    try:
                        preloaded[script_file] = manager.load_script(name, script_file)
                    except FileNotFoundError:
                        preloaded[script_file] = None
                if (script := preloaded[script_file]) is not None:
                    scripts[script_file.removeprefix("scripts/")] = script
            project = _merge_episode_summaries(project, summary)
            project = annotate_derivative_references(project, scripts.values())

            project_path = manager.get_project_path(name)
            fingerprints = compute_asset_fingerprints(project_path)

            return summary, {
                "project": project,
                "scripts": scripts,
                "asset_fingerprints": fingerprints,
            }

        summary, result = await coalescer.run(
            (
                "project-detail-verified",
                id(manager),
                name,
                current_user.id,
                current_user.role,
                id(summaries.pm if type(summaries) is WorkflowStateService else summaries),
            ),
            lambda: run_sync_transaction(_sync),
        )
        project = result["project"]
        return {**result, "project": {**project, "status": _project_status_payload(summary, project, _t)}}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/projects/{name}/agent-profile")
async def get_agent_profile_status(name: str, _t: Translator):
    """Return project-local Agent Profile customizations."""

    def _sync():
        manager = get_project_manager()
        try:
            project_dir = manager.get_project_path(name)
        except ValueError as exc:
            raise BadRequestError("invalid_project_name", name=name) from exc
        return manager.get_agent_profile_status(project_dir)

    try:
        return await asyncio.to_thread(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("读取项目 Agent profile 状态失败: project=%s", name)
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post("/projects/{name}/agent-profile/reset")
async def reset_agent_profile(name: str, _t: Translator):
    """Destructively restore the project Agent Profile to current built-ins."""

    def _sync():
        manager = get_project_manager()
        try:
            project_dir = manager.get_project_path(name)
        except ValueError as exc:
            raise BadRequestError("invalid_project_name", name=name) from exc
        stats = manager.force_resync_profile(project_dir)
        if stats.get("errors"):
            raise RuntimeError(f"profile reset completed with {stats['errors']} file errors")
        return {"customized": False, "customized_files": []}

    try:
        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("重置项目 Agent profile 失败: project=%s", name)
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.patch("/projects/{name}")
async def update_project(name: str, req: UpdateProjectRequest, _t: Translator):
    """更新项目元数据"""
    bridge = EventLoopBridge.capture()
    try:

        def _sync():
            manager = get_project_manager()

            def _mutate(project: dict) -> None:
                is_ad = project.get("content_mode") == "ad"
                if req.title is not None:
                    project["title"] = req.title
                if req.style is not None:
                    project["style"] = req.style
                for field in _PROJECT_BACKEND_FIELDS:
                    if field in req.model_fields_set:
                        value = getattr(req, field)
                        if value:
                            validate_backend_value(value, field)
                            project[field] = value
                        else:
                            project.pop(field, None)

                if "video_generate_audio" in req.model_fields_set:
                    if req.video_generate_audio is None:
                        project.pop("video_generate_audio", None)
                    else:
                        project["video_generate_audio"] = req.video_generate_audio
                if "character_voice_binding" in req.model_fields_set:
                    binding = (req.character_voice_binding or "").strip()
                    if not binding:
                        project.pop(CHARACTER_VOICE_BINDING_FIELD, None)
                    elif binding in VALID_CHARACTER_VOICE_BINDINGS:
                        project[CHARACTER_VOICE_BINDING_FIELD] = binding
                    else:
                        raise HTTPException(status_code=422, detail=_t("character_voice_binding_invalid"))
                _apply_narration_patch(project, req, bridge)
                if "speech_rate_units_per_second" in req.model_fields_set:
                    if req.speech_rate_units_per_second is None:
                        project.pop(SPEECH_RATE_FIELD, None)
                    else:
                        project[SPEECH_RATE_FIELD] = _validated_speech_rate(req.speech_rate_units_per_second, _t)
                if "aspect_ratio" in req.model_fields_set and req.aspect_ratio is not None:
                    project["aspect_ratio"] = req.aspect_ratio
                if "grid_storyboard" in req.model_fields_set:
                    if is_ad and req.grid_storyboard:
                        raise HTTPException(status_code=400, detail=_t("ad_grid_not_supported"))
                    project["grid_storyboard"] = bool(req.grid_storyboard)
                if "default_duration" in req.model_fields_set:
                    if is_ad:
                        raise HTTPException(status_code=400, detail=_t("ad_no_default_duration"))
                    if req.default_duration is None:
                        project.pop("default_duration", None)
                    else:
                        project["default_duration"] = req.default_duration
                if "episode_target_duration" in req.model_fields_set:
                    if is_ad:
                        raise HTTPException(status_code=400, detail=_t("ad_no_episode_target_duration"))
                    if req.episode_target_duration is None:
                        project.pop(EPISODE_TARGET_DURATION_FIELD, None)
                    else:
                        project[EPISODE_TARGET_DURATION_FIELD] = _validated_episode_target_duration(
                            req.episode_target_duration, _t
                        )
                if "target_duration" in req.model_fields_set:
                    if not is_ad:
                        raise HTTPException(status_code=400, detail=_t("ad_only_field", field="target_duration"))
                    if req.target_duration is None:
                        raise HTTPException(status_code=400, detail=_t("ad_target_duration_required"))
                    project["target_duration"] = req.target_duration
                if "brief" in req.model_fields_set:
                    if not is_ad:
                        raise HTTPException(status_code=400, detail=_t("ad_only_field", field="brief"))
                    project["brief"] = req.brief if req.brief is not None else ""

                if "style_template_id" in req.model_fields_set:
                    if req.style_template_id is None:
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
                        project.pop("style_image", None)
                        project.pop("style_description", None)

                if req.clear_style_image:
                    project.pop("style_image", None)
                    project.pop("style_description", None)

                if "model_settings" in req.model_fields_set:
                    if req.model_settings is None:
                        project.pop("model_settings", None)
                    else:
                        project["model_settings"] = req.model_settings

                if "episodes" in req.model_fields_set and req.episodes is not None:
                    existing_list = project.get("episodes", [])
                    patch_map: dict[int, EpisodePatch] = {}
                    for ep in req.episodes:
                        patch_map[ep.episode] = ep

                    new_episodes: list[dict] = []
                    for existing_ep in existing_list:
                        ep_num = existing_ep.get("episode")
                        patch = patch_map.pop(ep_num, None)
                        if patch is None:
                            new_episodes.append(existing_ep)
                            continue
                        updated = dict(existing_ep)
                        for field_name in patch.model_fields_set - {"episode"}:
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
                project = manager.update_project_reconciling_episode_bindings(name, _mutate)
                return {"success": True, "project": project}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError, ModelNotInCatalogError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.delete("/projects/{name}")
async def delete_project(name: str, _t: Translator):
    """删除项目：整体移入「已删除项目」，保留期内可以恢复"""
    try:
        manager = get_project_manager()
        project_dir = await asyncio.to_thread(manager.get_project_path, name)
        tombstone: str | None = None
        try:
            async with retiring_project(async_session_factory, project_dir) as tombstone:
                await run_sync_transaction(deleted_projects.move_in, manager.layout, project_dir, tombstone)
        except BaseException:
            if tombstone is not None:
                await run_sync_transaction(deleted_projects.move_back, manager.layout, project_dir, tombstone)
            raise
        return {"success": True, "message": _t("project_deleted", name=name)}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/deleted-projects")
async def list_deleted_projects():
    """已删除项目，最近删除的在前"""
    entries = await asyncio.to_thread(deleted_projects.list_entries, get_project_manager().layout)
    return {
        "projects": [entry.to_dict() for entry in entries],
        "retention_days": deleted_projects.RETENTION.days,
    }


@router.post("/deleted-projects/{deleted_id}/restore")
async def restore_deleted_project(deleted_id: str):
    """整体恢复已删除项目：原名被占用时加后缀，记录改回恢复后的名字，再走启动时的迁移链"""
    project_dir = await run_noninterruptible_async(_restore_deleted_project(get_project_manager().layout, deleted_id))
    return {"name": project_dir.name}


async def _restore_deleted_project(layout: DataRootLayout, deleted_id: str) -> Path:
    project_dir = await run_sync_transaction(deleted_projects.move_out, layout, deleted_id)
    try:
        await restore_retired_project(
            async_session_factory, deleted_id, project_dir, sdk_config_dir=default_sdk_config_dir()
        )
    except BaseException:
        await run_sync_transaction(project_dir.rename, layout.deleted_projects_dir / deleted_id)
        raise
    await run_sync_transaction(
        deleted_projects.migrate_restored,
        project_dir,
        recorded_episode_ids=recorded_episode_ids_on(asyncio.get_running_loop()),
    )
    return project_dir


@router.delete("/deleted-projects/{deleted_id}", dependencies=[Depends(reject_embedded_agent)])
async def purge_deleted_project(deleted_id: str):
    """彻底删除已删除项目（只开放给创作者，不给 Agent 工具）"""
    await run_sync_transaction(deleted_projects.purge, get_project_manager().layout, deleted_id)
    try:
        await release_holder(deleted_id)
    except Exception:
        logger.warning("撤下已删除项目的正式对象引用失败 deleted=%s", deleted_id, exc_info=True)
    return {"success": True}


@router.get("/projects/{name}/scripts/{script_file}")
async def get_script(name: str, script_file: str, request: Request, _t: Translator):
    """获取剧本内容"""
    try:

        def read():
            manager = get_project_manager()
            version_mode = request.headers.get("X-ArcReel-Script-Version")
            if version_mode in {"include", "only"}:
                path = safe_join(
                    manager.get_project_path(name) / "scripts", manager.normalize_script_filename(script_file)
                )
                if version_mode == "only":
                    try:
                        version = file_version(file_identity(path.stat()))
                    except FileNotFoundError:
                        version = None
                    return JSONResponse({"file_version": version}, headers={"Cache-Control": "no-store"})

                for _ in range(3):
                    before = file_identity(path.stat())
                    script = manager.load_script(name, script_file)
                    if before == file_identity(path.stat()):
                        return JSONResponse(
                            {"script": script, "revision": script_revision(script)},
                            headers={"X-ArcReel-Script-Version": file_version(before), "Cache-Control": "no-store"},
                        )
                raise HTTPException(status_code=409, detail=_t("script_conversion_conflict"))
            script = manager.load_script(name, script_file)
            return {"script": script, "revision": script_revision(script)}

        return await run_sync_transaction(read)
    except FileNotFoundError as exc:
        if request.headers.get("X-ArcReel-Optional-Read") == "1":
            return JSONResponse(
                content=None, headers={"Cache-Control": "no-store", "X-ArcReel-Script-Version": "missing"}
            )
        raise NotFoundError("script_not_found", name=script_file) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post(
    "/projects/{name}/script-edits",
    response_model=None,
    dependencies=[Depends(require_project_migration_ok)],
)
async def edit_script_batch(
    name: str,
    request: Request,
    command: ScriptBatchEditCommand,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
) -> JSONResponse:
    """Execute the same revisioned script-edit command exposed to the in-process Agent."""

    manager = None
    try:
        manager = get_project_manager()
        try:
            await asyncio.to_thread(manager.get_project_path, name)
        except ValueError as exc:
            raise BadRequestError("invalid_project_name", name=name) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=name) from exc

        with project_change_source("webui"):
            result = await run_sync_transaction(make_script_batch_editor(manager).execute, name, command)
        payload = result.model_dump(mode="json")
        if not result.success:
            payload = with_problem_locations(request, payload)
        return JSONResponse(status_code=script_batch_status(result), content=payload)
    except FileNotFoundError as exc:
        if manager is None or not await asyncio.to_thread(manager.project_exists, name):
            raise NotFoundError("project_not_found", name=name) from exc
        target = command.script or str(command.episode)
        raise NotFoundError("script_not_found", name=target) from exc
    except ApiError:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get(
    "/projects/{name}/script-items/{item_id}/prompt-preview",
    dependencies=[Depends(require_project_migration_ok)],
)
async def preview_script_item_prompts(
    name: str,
    item_id: str,
    _t: Translator,
    names: DisplayNamesCatalog,
    script_file: str = Query(..., description="剧本文件名"),
):
    """渲染该条目当前会送进图像 / 视频模型的最终提示词文本。

    与执行路径共用同一渲染出口，结果逐字等于本次生成实际发出的提示词。只读：不向供应商
    发请求、不产生费用、不写产物清单。某一侧缺提示词或形状不合规时，该侧返回不可用原因、
    另一侧照常渲染，半成品条目仍能看到已写好的那一半。

    挂迁移闸门与生成入口同判据：商品参考的装配读产物清单，且被阻断的项目本就无法生成，
    此时报「先修复」比渲染一份不会被发出的文本诚实。
    """
    try:
        preview = await preview_item_prompts(name, script_file, item_id)
    except ScriptItemNotFound as exc:
        raise NotFoundError("script_item_not_found", id=item_id) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc

    def _side(rendered):
        return {
            **(
                {
                    "reference_count": rendered.reference_count,
                    "max_reference_images": rendered.max_reference_images,
                    "previous_storyboard": rendered.previous_storyboard,
                }
                if rendered.reference_count is not None
                else {}
            ),
            **({"unavailable_code": rendered.unavailable} if rendered.unavailable else {}),
            "text": rendered.text,
            "unavailable": render_generation_input_error(rendered.unavailable, rendered.unavailable_params, _t, names)
            if rendered.unavailable
            else None,
            "is_text_form": rendered.is_text_form,
            "warnings": [render_message(warning["key"], warning["params"], _t, names) for warning in rendered.warnings],
        }

    return {
        "item_id": preview.item_id,
        "content_mode": preview.content_mode,
        "storyboard_image": _side(preview.storyboard_image),
        "video": _side(preview.video),
    }


_STORYBOARD_ITEM_KINDS = frozenset({"segments", "scenes", "shots"})


def _require_storyboard_items(script: dict, item_id: str | None) -> tuple[list, str, str]:
    """返回分镜图生视频剧本的条目数组；形态不支持或给定 id 未命中时抛对应 API 错误。"""
    items, id_field, kind = resolve_items(script)
    if kind not in _STORYBOARD_ITEM_KINDS:
        raise BadRequestError("storyboard_script_required")
    if item_id is not None and not any(isinstance(item, dict) and item.get(id_field) == item_id for item in items):
        raise NotFoundError("script_item_not_found", id=item_id)
    return items, id_field, kind


class InsertScriptItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_file: str
    after_id: str | None = Field(default=None, min_length=1)
    novel_text: str | None = None


@router.post(
    "/projects/{name}/script-items",
    dependencies=[Depends(require_project_migration_ok)],
)
async def insert_script_item(
    name: str,
    req: InsertScriptItemRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """新增一条待编写分镜，按当前剧本 revision 执行 ``insert_after``；新分镜的编号由服务端另发，取本集从没用过的号。"""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            items, id_field, kind = _require_storyboard_items(current, req.after_id)
            last_id = next(
                (item.get(id_field) for item in reversed(items) if isinstance(item, dict)),
                None,
            )
            after_id = req.after_id if req.after_id is not None else last_id
            item = blank_item_after(current, after_id)
            if kind == "segments":
                if req.novel_text is None or not req.novel_text.strip():
                    raise UnprocessableError("narration_segment_text_required")
                item["novel_text"] = req.novel_text
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "insert_after", "after_id": after_id, "item": item}],
                    editor=make_script_batch_editor(manager),
                    current_script=current,
                )
            require_script_edit_result(result, operation_not_found=True)
            saved_items, _id_field, _kind = resolve_items(manager.load_script(name, req.script_file))
            inserted_id = result.affected_ids[0] if result.affected_ids else None
            inserted = next(
                (entry for entry in saved_items if isinstance(entry, dict) and entry.get(id_field) == inserted_id),
                None,
            )
            return {"success": True, "item": inserted, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post(
    "/projects/{name}/episodes/{episode}/blank-script",
    dependencies=[Depends(require_project_migration_ok)],
)
async def start_episode_blank_script(name: str, episode: int):
    """从空白开始：本集没有正式脚本时建出空的正式脚本，弃置未确认的脚本规划。"""

    def _sync():
        with project_change_source("webui"):
            return start_blank_script(get_project_manager(), name, episode)

    try:
        script_file = await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except BlankScriptError as exc:
        if exc.code == "episode_not_found":
            raise NotFoundError("episode_not_found", episode=episode) from exc
        raise ConflictError("blank_script_formal_exists").with_diagnostic(str(exc)) from exc
    return {"success": True, "script_file": script_file}


@router.delete(
    "/projects/{name}/script-items/{item_id}",
    dependencies=[Depends(require_project_migration_ok)],
)
async def remove_script_item(
    name: str,
    item_id: str,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
    script_file: str = Query(..., description="剧本文件名"),
):
    """移除分镜 ``item_id``，按当前剧本 revision 执行 ``remove``；其产物随分镜一并移除。"""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, script_file)
            _require_storyboard_items(current, item_id)
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    script_file,
                    [{"op": "remove", "id": item_id}],
                    editor=make_script_batch_editor(manager),
                    current_script=current,
                )
            require_script_edit_result(result, operation_not_found=True)
            return {"success": True, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class MoveScriptItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_file: str
    after_id: str | None = Field(min_length=1)


@router.post(
    "/projects/{name}/script-items/{item_id}/move",
    dependencies=[Depends(require_project_migration_ok)],
)
async def move_script_item(
    name: str,
    item_id: str,
    req: MoveScriptItemRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """把分镜 ``item_id`` 移到 ``after_id`` 之后，按当前剧本 revision 执行 ``move_after``；分镜连同产物一起移动。"""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            _require_storyboard_items(current, item_id)
            _require_storyboard_items(current, req.after_id)
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "move_after", "id": item_id, "after_id": req.after_id}],
                    editor=make_script_batch_editor(manager),
                    current_script=current,
                )
            require_script_edit_result(result)
            return {"success": True, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class UpdateSceneRequest(BaseModel):
    script_file: str
    updates: dict


@router.patch("/projects/{name}/script-scenes/{scene_id}", dependencies=[Depends(require_project_migration_ok)])
async def update_scene(
    name: str,
    scene_id: str,
    req: UpdateSceneRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """更新剧情演绎剧本中的单个分镜（按 scene_id 定位）。

    路径与项目场景资产 CRUD（``/projects/{name}/scenes/{entry_name}``）做明确区分，
    避免 FastAPI 按注册顺序优先匹配本端点导致 SceneCard 保存请求被截获、Pydantic
    必填字段校验返回双 "Field required"。
    """
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            scenes = current.get("scenes")
            if not isinstance(scenes, list) or not any(
                isinstance(scene, dict) and scene.get("scene_id") == scene_id for scene in scenes
            ):
                raise HTTPException(status_code=404, detail=_t("scene_not_found", id=scene_id))
            allowed = {
                "duration_seconds",
                "image_prompt",
                "video_prompt",
                "characters_in_scene",
                "scenes",
                "props",
                "segment_break",
                "utterances",
                "note",
            }
            fields: dict[str, Any] = {}
            for key, raw_value in req.updates.items():
                if key not in allowed or (raw_value is None and key != "note"):
                    continue
                value = raw_value
                if key in {"characters_in_scene", "scenes", "props"} and isinstance(value, list):
                    value = [asset_name_comparison_key(entry) if isinstance(entry, str) else entry for entry in value]
                fields[key] = value
            if not fields:
                matched = next(scene for scene in scenes if scene.get("scene_id") == scene_id)
                return {"success": True, "scene": matched}
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "update", "id": scene_id, "fields": fields}],
                    editor=make_script_batch_editor(manager),
                )
            require_script_edit_result(
                result,
                operation_not_found=True,
            )
            saved = manager.load_script(name, req.script_file)
            matched = next(scene for scene in saved["scenes"] if scene.get("scene_id") == scene_id)
            return {"success": True, "scene": matched, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class UpdateShotRequest(BaseModel):
    script_file: str
    updates: dict


_SHOT_UPDATABLE_FIELDS = (
    "section",
    "voiceover_text",
    "duration_seconds",
    "image_prompt",
    "video_prompt",
    "characters_in_shot",
    "scenes",
    "props",
    "products_in_shot",
    "note",
)


def _require_ad_script(script: dict, _t: Translator) -> list[dict]:
    """断言剧本是 ad 形状（content_mode=ad 且含 shots 键），返回 shots 列表。

    与 update_segment 的 narration 守卫同模式：其他模式的脚本即使残留 shots 键也拒绝，
    避免被当 ad 改写。
    """
    if script.get("content_mode") != "ad" or "shots" not in script:
        raise HTTPException(status_code=400, detail=_t("ad_mode_required"))
    shots = script.get("shots")
    if not isinstance(shots, list):
        raise ValueError("ad script field 'shots' must be a list")
    if not all(isinstance(s, dict) for s in shots):
        raise ValueError("ad script field 'shots' contains non-object elements")
    if not all(isinstance(s.get("shot_id"), str) and s["shot_id"] for s in shots):
        raise ValueError("ad script field 'shots' contains elements missing valid 'shot_id'")
    shot_ids = [s["shot_id"] for s in shots]
    if len(set(shot_ids)) != len(shot_ids):
        raise ValueError("ad script field 'shots' contains duplicate 'shot_id' values")
    return shots


@router.patch("/projects/{name}/script-shots/{shot_id}", dependencies=[Depends(require_project_migration_ok)])
async def update_shot(
    name: str,
    shot_id: str,
    req: UpdateShotRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """更新广告/短片剧本中的单个分镜（按 shot_id 定位）。

    路径风格与 ``script-scenes`` 对齐；口播文案 / section / 时长 / 引用列表等
    白名单字段可改，结构合法性由写盘统一入口的「不更坏」校验兜底。
    """
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            shots = _require_ad_script(current, _t)
            matched = next((shot for shot in shots if shot.get("shot_id") == shot_id), None)
            if matched is None:
                raise HTTPException(status_code=404, detail=_t("shot_not_found", id=shot_id))
            fields = {
                key: value
                for key, value in req.updates.items()
                if key in _SHOT_UPDATABLE_FIELDS and (value is not None or key == "note")
            }
            if not fields:
                return {"success": True, "shot": matched}
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "update", "id": shot_id, "fields": fields}],
                    editor=make_script_batch_editor(manager),
                )
            require_script_edit_result(
                result,
                operation_not_found=True,
            )
            saved = manager.load_script(name, req.script_file)
            matched = next(shot for shot in saved["shots"] if shot.get("shot_id") == shot_id)
            return {"success": True, "shot": matched, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class UpdateSegmentRequest(BaseModel):
    script_file: str
    duration_seconds: int | None = None
    segment_break: bool | None = None
    novel_text: str | None = None
    image_prompt: dict | str | None = None
    video_prompt: dict | str | None = None
    note: str | None = None
    characters_in_segment: list[str] | None = None
    scenes: list[str] | None = None
    props: list[str] | None = None


class UpdateOverviewRequest(BaseModel):
    synopsis: str | None = None
    genre: str | None = None
    theme: str | None = None
    world_setting: str | None = None


class UpdateEpisodeRequest(BaseModel):
    title: str


class UpdateEpisodeSourceRequest(BaseModel):
    text: str
    source_kind: SourceKind | None = None
    confirm: bool = False


@router.patch("/projects/{name}/segments/{segment_id}", dependencies=[Depends(require_project_migration_ok)])
async def update_segment(
    name: str,
    segment_id: str,
    req: UpdateSegmentRequest,
    _t: Translator,
    make_script_batch_editor: ScriptBatchEditorFactoryDep,
):
    """更新旁白/解说分镜"""
    try:

        def _sync():
            manager = get_project_manager()
            current = manager.load_script(name, req.script_file)
            if current.get("content_mode") != "narration" or "segments" not in current:
                raise HTTPException(status_code=400, detail=_t("narration_mode_required"))
            segments = current.get("segments")
            if not isinstance(segments, list):
                raise ValueError("narration script field 'segments' must be a list")
            matched = next(
                (
                    segment
                    for segment in segments
                    if isinstance(segment, dict) and segment.get("segment_id") == segment_id
                ),
                None,
            )
            if matched is None:
                raise HTTPException(status_code=404, detail=_t("segment_not_found", id=segment_id))
            fields: dict[str, Any] = {}
            for field in (
                "duration_seconds",
                "segment_break",
                "novel_text",
                "image_prompt",
                "video_prompt",
            ):
                value = getattr(req, field)
                if value is not None:
                    fields[field] = value
            if "note" in req.model_fields_set:
                fields["note"] = req.note
            for field in ("characters_in_segment", "scenes", "props"):
                if field in req.model_fields_set:
                    fields[field] = [asset_name_comparison_key(value) for value in (getattr(req, field) or [])]
            if not fields:
                return {"success": True, "segment": matched}
            with project_change_source("webui"):
                result = execute_current_script_edit(
                    manager,
                    name,
                    req.script_file,
                    [{"op": "update", "id": segment_id, "fields": fields}],
                    editor=make_script_batch_editor(manager),
                )
            require_script_edit_result(
                result,
                operation_not_found=True,
            )
            saved = manager.load_script(name, req.script_file)
            matched = next(segment for segment in saved["segments"] if segment.get("segment_id") == segment_id)
            return {"success": True, "segment": matched, "edit_result": result.model_dump(mode="json")}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=req.script_file) from exc
    except ValueError as exc:
        raise UnprocessableError("script_validation_failed").with_diagnostic(str(exc)) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.patch("/projects/{name}/episodes/{episode}", dependencies=[Depends(require_project_migration_ok)])
async def update_episode(name: str, episode: int, req: UpdateEpisodeRequest, _t: Translator):
    """更新分集顶层元数据（当前仅标题）。

    有剧本时以剧本 scripts/*.json 顶层 title 为唯一真相源：走 locked_episode_script 在
    「脚本锁 → 项目锁」临界区内改剧本 title，并内联 _apply_episode_sync 把镜像同步回
    project.json 的 episodes[].title，原子且无 TOCTOU。镜像由 PATCH /projects 改写的入口
    已移除（title 不在 EpisodePatch 上），杜绝第二真相源。

    还没有剧本的集（新建的空集、尚未规划脚本的集）标题只记在账本条目上；之后建出的剧本以它为初值。
    标题可以清空，空标题的集名由呈现层按播出位置派生。
    """
    title = req.title.strip()

    try:

        def _sync():
            manager = get_project_manager()

            def _resolve(project: dict) -> str:
                episodes = project.get("episodes") or []
                meta = next((e for e in episodes if e.get("episode") == episode), None)
                if meta is None or not meta.get("script_file"):
                    raise HTTPException(status_code=404, detail=_t("episode_not_found", episode=episode))
                return meta["script_file"]

            def _retitle_ledger_entry(project: dict) -> None:
                script_file = _resolve(project)
                script_path = (
                    manager.get_project_path(name) / "scripts" / manager.normalize_script_filename(script_file)
                )
                if script_path.is_file():
                    raise HTTPException(status_code=409, detail=_t("ref_script_rebound"))
                meta = next(e for e in project["episodes"] if e.get("episode") == episode)
                meta["title"] = title

            with project_change_source("webui"):
                try:
                    with manager.locked_episode_script(name, _resolve) as script:
                        script["title"] = title
                except FileNotFoundError as exc:
                    if not manager.project_exists(name):
                        raise NotFoundError("project_not_found", name=name) from exc
                    manager.update_project(name, _retitle_ledger_entry)
                except EpisodeScriptReboundError as exc:
                    logger.info("episode script rebound during title update: %s", exc)
                    raise HTTPException(status_code=409, detail=_t("ref_script_rebound")) from exc
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422, detail=_t("script_validation_failed", details=str(exc))
                    ) from exc

            return {"success": True, "episode": {"episode": episode, "title": title}}

        return await run_sync_transaction(_sync)
    except (HTTPException, ApiError):
        raise
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.put("/projects/{name}/episodes/{episode}/source", dependencies=[Depends(require_project_migration_ok)])
async def update_episode_source(name: str, episode: int, req: UpdateEpisodeSourceRequest, _t: Translator):
    """集页填写或改写本集原文：无原文的集填上后转为自带原文的集。切出集的原文由分集规划派生，这里拒绝。

    改类型会让本集已有的脚本规划判 stale 而 ``confirm`` 为 false 时，原文与类型都不写入，返回
    ``needs_confirmation`` 与受影响的集 ID（``affected_episodes``）。
    """

    def _sync() -> dict[str, Any]:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        with project_change_source("webui"):
            written = set_episode_source_text(
                manager, name, episode, req.text, source_kind=req.source_kind, confirm=req.confirm
            )
        return {
            "success": True,
            "episode": episode,
            "source_origin": written.origin.value,
            "applied": written.applied,
            "needs_confirmation": not written.applied,
            "affected_episodes": written.affected_episodes,
        }

    try:
        return await run_sync_transaction(_sync)
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t, episode=episode) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post("/projects/{name}/source", dependencies=[Depends(require_project_migration_ok)])
async def set_project_source(
    name: Annotated[str, FastAPIPath(pattern=r"^[a-zA-Z0-9_-]+$")],
    _t: Translator,
    names: DisplayNamesCatalog,
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

        raw: bytes | None = None
        original_name: str = "novel.txt"
        if file:
            original_name = file.filename or "novel.txt"
            suffix = Path(original_name).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(status_code=400, detail=_t("unsupported_file_type", name=suffix))
            if file.size is not None and file.size > MAX_CHARS * 4:
                raise HTTPException(status_code=400, detail=_t("file_too_large", max_chars=MAX_CHARS))
            raw = await file.read()
        text_content: str = content or ""

        def _sync_write():
            if not manager.project_exists(name):
                raise HTTPException(status_code=404, detail=_t("project_not_found", name=name))
            with manager.locked_source_registration(name) as (source_dir, project, _undo):
                if raw is not None:
                    safe_filename = Path(original_name).name
                    if is_derived_episode_name(safe_filename):
                        raise HTTPException(status_code=400, detail=_t("source_name_reserved"))
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise HTTPException(status_code=400, detail=_t("invalid_encoding")) from exc
                    if len(text) > MAX_CHARS:
                        raise HTTPException(status_code=400, detail=_t("file_too_large", max_chars=MAX_CHARS))
                    (source_dir / safe_filename).write_text(text, encoding="utf-8")
                    register_whole_source_file(project, f"source/{safe_filename}")
                    return safe_filename, len(text)
                if len(text_content) > MAX_CHARS:
                    raise HTTPException(status_code=400, detail=_t("file_too_large", max_chars=MAX_CHARS))
                safe_filename = "novel.txt"
                (source_dir / safe_filename).write_text(text_content, encoding="utf-8")
                register_whole_source_file(project, f"source/{safe_filename}")
                return safe_filename, len(text_content)

        safe_filename, chars = await run_sync_transaction(_sync_write)

        result: dict = {"success": True, "filename": safe_filename, "chars": chars}

        if generate_overview:
            try:
                with project_change_source("webui"):
                    overview = await manager.generate_overview(name)
                result["overview"] = overview
            except TextWaitTimeout as exc:
                result["overview"] = None
                result["overview_error"] = _t(exc.code)
            except ModelNotInCatalogError as ov_err:
                logger.warning("上传后概览生成失败：模型不在清单中 (%s)", ov_err)
                result["overview"] = None
                result["overview_error"] = render_message(
                    failure_message_key(ov_err.code, ov_err.params), ov_err.params, _t, names
                )
            except Exception as ov_err:
                logger.exception("上传后概览生成失败")
                result["overview"] = None
                result["overview_error"] = (
                    _t("overview_ai_response_invalid")
                    if isinstance(ov_err, PydanticValidationError)
                    else _t("overview_generation_failed")
                )

        return result
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
    finally:
        if file:
            await file.close()


@router.post("/projects/{name}/generate-overview", dependencies=[Depends(require_project_migration_ok)])
async def generate_overview(name: str, _t: Translator):
    """使用 AI 生成项目概述"""
    try:
        await asyncio.to_thread(get_project_manager().get_project_path, name)
    except ValueError as e:
        raise BadRequestError("invalid_project_name", name=name) from e
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    def _provider_not_configured(exc: ValueError) -> BadRequestError:
        logger.warning("生成概述配置错误: name=%s (%s)", name, exc)
        return BadRequestError("text_provider_not_configured")

    try:
        with (
            project_change_source("webui"),
            domain_error_on_value_error(
                _provider_not_configured,
                extra_passthrough=(
                    EmptySourceError,
                    PydanticValidationError,
                    ModelNotInCatalogError,
                    NoAvailableProviderError,
                ),
            ),
        ):
            overview = await get_project_manager().generate_overview(name)
        return {"success": True, "overview": overview}
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except PydanticValidationError as exc:
        logger.exception("概述生成响应解析失败")
        raise HTTPException(status_code=400, detail=_t("overview_ai_response_invalid")) from exc
    except TextWaitTimeout as exc:
        raise ApiError(exc.code, status_code=504).with_diagnostic({"code": exc.code}) from exc
    except TextOutputTruncatedError as exc:
        logger.warning("概述生成输出被截断: name=%s (%s)", name, exc)
        raise UnprocessableError(
            "text_output_truncated", provider_id=exc.provider_id or exc.provider, model=exc.model
        ).with_diagnostic(truncation_problem(exc).model_dump()) from exc
    except EmptySourceError as e:
        logger.warning("生成概述参数错误: name=%s (%s)", name, e)
        raise BadRequestError("overview_source_empty") from e
    except NoAvailableProviderError as exc:
        logger.warning("生成概述缺少文本默认模型: name=%s", name)
        raise BadRequestError("text_default_model_not_set") from exc
    except json.JSONDecodeError as exc:
        logger.exception("生成概述失败：项目数据损坏 name=%s", name)
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
    except (HTTPException, ApiError, ModelNotInCatalogError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.patch("/projects/{name}/overview")
async def update_overview(name: str, req: UpdateOverviewRequest, _t: Translator):
    """更新项目概述（手动编辑）"""
    try:

        def _sync():
            manager = get_project_manager()
            captured: dict[str, Any] = {}

            def _mutate(project: dict) -> None:
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
                captured["overview"] = project["overview"]

            with project_change_source("webui"):
                manager.update_project(name, _mutate)
            return {"success": True, "overview": captured["overview"]}

        return await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
