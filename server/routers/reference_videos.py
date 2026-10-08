"""参考生视频 CRUD + 生成路由。

Mount prefix: /api/v1/projects/{project_name}/reference-videos
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from lib.artifacts.artifact_activation import resolve_artifact_episode
from lib.artifacts.version_manager import VersionManager
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation.batch_admission import BatchAdmissionDecision, refused_ticket
from lib.generation.generation_queue import get_generation_queue
from lib.generation.generation_queue_client import (
    TaskSpec,
    TaskSpecValidationError,
    batch_enqueue_only,
)
from lib.generation.generation_result import (
    GenerationAction,
    GenerationProblemCode,
    GenerationSelectionMode,
    normalize_requested_ids,
)
from lib.i18n import render_message
from lib.i18n.display_names import DisplayNames
from lib.infra.api_errors import ApiError, BadRequestError, NotFoundError
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager, is_reference_video_project
from lib.project.resource_paths import resource_relative_path
from lib.script.reference_video.request_projection import (
    ReferenceRequestFactsLookup,
    ReferenceRequestOptions,
    ReferenceUnitRequestProjection,
    configured_reference_request_facts,
    project_reference_unit_request,
)
from lib.script.reference_video.script_preview import build_script_preview
from lib.script.reference_video.unit_capabilities import hydrate_reference_units
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_editor import ScriptEditError, new_item_id
from lib.speech.speech_composition import admit_script_unit, refresh_video_unit_replan_state
from server.auth import CurrentUser
from server.error_handlers import script_edit_detail
from server.i18n import DisplayNamesCatalog, Translator
from server.routers._batch_admission import enqueue_failure_payload, localized_admission_payload
from server.routers._script_edits import execute_current_episode_edit, require_script_edit_result
from server.routers._validators import reject_retired_query_params
from server.services.admission.reference_prompt_preview import render_reference_prompt_preview
from server.services.admission.video_batch_admission import (
    admit_reference_video_batch,
    artifact_state_tickets,
    reference_unit_task_spec,
    request_options_for_unit,
    resolve_reference_batch_targets,
    screen_script_entries,
)
from server.services.currency.upload_finalize import (
    UploadValidationError,
    commit_manual_video_upload,
    stage_uploaded_video_stream,
    validate_upload,
)
from server.services.tasks.generation_tasks import emit_generation_success_batch
from server.services.tasks.reference_video_tasks import (
    apply_unit_video_assets,
    default_unit_duration,
)
from server.services.tasks.video_caps import (
    project_video_caps,
    reference_request_facts_lookup,
    reference_unit_capabilities,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_name}/reference-videos",
    tags=["reference-videos"],
)

# ============ 请求模型 ============


class ScriptPreviewRequest(BaseModel):
    prompt: str = ""


class AddUnitRequest(BaseModel):
    # extra="forbid"：正文是单元的唯一真相，参考图执行期才派生。旧客户端仍带着
    # ``references`` 调用时要拿到 422 而不是被静默丢弃——被丢弃的话它会以为自己
    # 指定的参考图生效了。
    model_config = ConfigDict(extra="forbid")

    prompt: str
    duration_seconds: int | None = Field(default=None, ge=1)
    note: str | None = None
    #: 新单元插在这个单元之后；缺省时追加到末尾。
    after_unit_id: str | None = Field(default=None, min_length=1)


class GenerateUnitRequest(BaseModel):
    # 旁白交付方式是项目配置，不影响视频请求；已删除的按请求交付字段按未知字段拒收。
    model_config = ConfigDict(extra="forbid")

    confirmed_request_duration_seconds: int | None = Field(default=None, gt=0)

    def projection_options(self) -> ReferenceRequestOptions:
        return ReferenceRequestOptions(confirmed_request_duration_seconds=self.confirmed_request_duration_seconds)


class GenerateUnitsBatchRequest(BaseModel):
    """批量视频生成请求。

    ``unit_ids`` 省略时只补齐缺失项；指定 id 则重新生成对应单元。
    ``confirmed_request_durations`` 为用户在聚合确认中接受的时长档位。
    """

    model_config = ConfigDict(extra="forbid")

    unit_ids: list[str] | None = None
    confirmed_request_durations: dict[str, PositiveInt] = Field(default_factory=dict)


# ============ 辅助 ============


def _load_episode_script(project_name: str, episode: int, _t: Translator) -> tuple[dict, dict, str]:
    """加载 project.json + 指定集的剧本。返回 (project, script, script_file)。"""
    try:
        project = get_project_manager().load_project(project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    episodes = project.get("episodes") or []
    meta = next((e for e in episodes if e.get("episode") == episode), None)
    if meta is None or not meta.get("script_file"):
        raise HTTPException(status_code=404, detail=_t("ref_episode_not_found", episode=episode))
    script_file = meta["script_file"]
    try:
        script = get_project_manager().load_script(project_name, script_file)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=script_file) from exc
    if not is_reference_video_project(project):
        raise HTTPException(status_code=409, detail=_t("ref_not_reference_video_mode"))
    return project, script, script_file


def _problem_payload(
    projection: ReferenceUnitRequestProjection, _t: Translator, names: DisplayNames
) -> list[dict[str, Any]]:
    payloads = projection.problem_payloads()
    for payload, problem in zip(payloads, projection.problems, strict=True):
        payload["message"] = render_message(problem.code, problem.parameters(), _t, names)
    return payloads


def _raise_projection_blocker(
    projection: ReferenceUnitRequestProjection,
    _t: Translator,
    names: DisplayNames,
    *,
    allow_duration_confirmation: bool,
) -> None:
    blockers = [
        problem
        for problem in projection.blocking_problems
        if not (allow_duration_confirmation and problem.code == "reference_duration_confirmation_required")
    ]
    if not blockers:
        return
    detail = projection.to_advisory_payload()
    detail["allowed"] = False
    detail["problems"] = _problem_payload(projection, _t, names)
    raise HTTPException(
        status_code=400,
        detail=detail,
    )


def _build_unit_dict(
    *,
    unit_id: str,
    prompt: str,
    duration_seconds: int,
    note: str | None,
) -> dict:
    unit = {
        "unit_id": unit_id,
        "text": prompt,
        "duration_seconds": duration_seconds,
        "note": note,
        "generated_assets": {
            "storyboard_image": None,
            "storyboard_last_image": None,
            "grid_id": None,
            "grid_cell_index": None,
            "video_clip": None,
            "video_uri": None,
            "status": "pending",
        },
    }
    refresh_video_unit_replan_state(unit)
    return unit


def _require_unit_ready(unit: dict, *, ignore_marker: bool = False, allow_blank_draft: bool = False) -> None:
    if allow_blank_draft and not str(unit.get("text") or "").strip():
        return
    admission = admit_script_unit("video_units", unit, ignore_marker=ignore_marker)
    if not admission.allowed:
        raise HTTPException(status_code=409, detail=admission.to_dict())


# ============ 端点：列出 + 新建 ============


async def _unit_capabilities(
    project_name: str,
    project: dict,
    units: list[dict],
    request_facts: ReferenceRequestFactsLookup | None = None,
) -> dict[str, dict[str, object]]:
    """逐单元按可用参考图定桶的服务端结论，随单元一起回给画布。"""
    return await reference_unit_capabilities(
        project,
        get_project_manager().get_project_path(project_name),
        units,
        request_facts=request_facts or reference_request_facts_lookup(project),
    )


async def _unit_capability(
    project_name: str, project: dict, unit: dict, request_facts: ReferenceRequestFactsLookup | None = None
) -> dict[str, object]:
    return (await _unit_capabilities(project_name, project, [unit], request_facts))[str(unit.get("unit_id") or "")]


@router.get("/episodes/{episode}/units")
async def list_units(project_name: str, episode: int, _t: Translator) -> dict[str, Any]:
    project, script, _sf = _load_episode_script(project_name, episode, _t)
    units = script.get("video_units") or []
    return {"units": units, "unit_capabilities": await _unit_capabilities(project_name, project, units)}


@router.post("/episodes/{episode}/units", status_code=status.HTTP_201_CREATED)
async def add_unit(
    project_name: str,
    episode: int,
    req: AddUnitRequest,
    _t: Translator,
) -> dict[str, Any]:
    project, current, script_file = _load_episode_script(project_name, episode, _t)
    request_facts = reference_request_facts_lookup(project)

    # 时长是 unit 级单一真相：请求未给出时取这条 unit 所落桶的默认档位（异步 IO 不进项目锁临界区）。
    # 桶按可用参考图判定，与响应里的逐单元结论及执行期投影同一判据。
    duration_seconds = req.duration_seconds
    if duration_seconds is None:
        (hydration,) = hydrate_reference_units(
            project, get_project_manager().get_project_path(project_name), [{"text": req.prompt}]
        )
        duration_seconds = default_unit_duration(await request_facts(hydration.hydrated_generation_type), project)

    units = current.get("video_units") if isinstance(current.get("video_units"), list) else []
    if req.after_unit_id is not None:
        _find_unit(current, req.after_unit_id, _t)
    unit = _build_unit_dict(
        unit_id=new_item_id(current),
        prompt=req.prompt,
        duration_seconds=int(duration_seconds),
        note=req.note,
    )
    result = execute_current_episode_edit(
        get_project_manager(),
        project_name,
        episode,
        script_file,
        current,
        [
            {
                "op": "insert_after",
                "after_id": req.after_unit_id or (units[-1].get("unit_id") if units else None),
                "item": unit,
            }
        ],
    )
    require_script_edit_result(result)
    saved = get_project_manager().load_script(project_name, result.script)
    inserted = _find_unit(saved, unit["unit_id"], _t)
    return {
        "unit": inserted,
        "unit_capability": await _unit_capability(project_name, project, inserted, request_facts),
        "edit_result": result.model_dump(mode="json"),
    }


# ============ 端点：PATCH + DELETE ============


class PatchUnitRequest(BaseModel):
    # extra="forbid" 同 ``AddUnitRequest``。
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    note: str | None = None


def _find_unit(script: dict, unit_id: str, _t: Translator) -> dict:
    for u in script.get("video_units") or []:
        if u.get("unit_id") == unit_id:
            return u
    raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id))


def _find_unit_for_project(_project: dict, script: dict, unit_id: str, _t: Translator) -> dict:
    return _find_unit(script, unit_id, _t)


@router.patch("/episodes/{episode}/units/{unit_id}")
async def patch_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    req: PatchUnitRequest,
    _t: Translator,
) -> dict[str, Any]:
    project, current, script_file = _load_episode_script(project_name, episode, _t)
    _find_unit(current, unit_id, _t)
    fields: dict[str, Any] = {}
    if req.prompt is not None:
        fields["text"] = req.prompt
    if req.duration_seconds is not None:
        fields["duration_seconds"] = req.duration_seconds
    if req.note is not None:
        fields["note"] = req.note
    if not fields:
        unit = _find_unit(current, unit_id, _t)
        return {"unit": unit, "unit_capability": await _unit_capability(project_name, project, unit)}
    result = execute_current_episode_edit(
        get_project_manager(),
        project_name,
        episode,
        script_file,
        current,
        [{"op": "update", "id": unit_id, "fields": fields}],
    )
    require_script_edit_result(result, operation_not_found=True)
    saved = get_project_manager().load_script(project_name, result.script)
    unit = _find_unit(saved, unit_id, _t)
    return {
        "unit": unit,
        "unit_capability": await _unit_capability(project_name, project, unit),
        "edit_result": result.model_dump(mode="json"),
    }


@router.delete("/episodes/{episode}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    _t: Translator,
) -> Response:
    _project, current, script_file = _load_episode_script(project_name, episode, _t)
    _find_unit(current, unit_id, _t)
    result = execute_current_episode_edit(
        get_project_manager(),
        project_name,
        episode,
        script_file,
        current,
        [{"op": "remove", "id": unit_id}],
    )
    require_script_edit_result(result, operation_not_found=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class MoveUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 移到这个单元之后；为 null 时移到最前。
    after_unit_id: str | None = Field(min_length=1)


@router.post("/episodes/{episode}/units/{unit_id}/move")
async def move_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    req: MoveUnitRequest,
    _t: Translator,
) -> dict[str, Any]:
    """把单元移到 ``after_unit_id`` 之后，按当前剧本 revision 执行 ``move_after``；单元连同产物一起移动。"""
    _project, current, script_file = _load_episode_script(project_name, episode, _t)
    _find_unit(current, unit_id, _t)
    if req.after_unit_id is not None:
        _find_unit(current, req.after_unit_id, _t)
    result = execute_current_episode_edit(
        get_project_manager(),
        project_name,
        episode,
        script_file,
        current,
        [{"op": "move_after", "id": unit_id, "after_id": req.after_unit_id}],
    )
    require_script_edit_result(result)
    moved = get_project_manager().load_script(project_name, result.script)["video_units"]
    return {"units": moved, "edit_result": result.model_dump(mode="json")}


@router.get("/episodes/{episode}/units/{unit_id}/duration-precheck")
async def precheck_unit_duration(
    project_name: str,
    episode: int,
    unit_id: str,
    request: Request,
    _t: Translator,
    names: DisplayNamesCatalog,
) -> dict[str, Any]:
    """入队前的时长取档预检：申请秒数与剧本计划时长不一致时前端需先向用户确认。

    ``needs_confirmation`` 为 false 时仅表示计划时长本身是当前档位成员。能力或档位元数据
    无法解析时返回结构化 blocker，不制造无约束申请。
    """
    reject_retired_query_params(request, "narration_delivery")
    project, script, _script_file = _load_episode_script(project_name, episode, _t)
    unit = _find_unit(script, unit_id, _t)
    _require_unit_ready(unit)
    projection = await project_reference_unit_request(
        request_facts_lookup=configured_reference_request_facts(project, ConfigResolver(async_session_factory)),
        project=project,
        script=script,
        unit=unit,
        project_path=get_project_manager().get_project_path(project_name),
    )
    _raise_projection_blocker(projection, _t, names, allow_duration_confirmation=True)
    slot = projection.request_duration
    if slot is None:
        raise BadRequestError("reference_supported_durations_missing")
    return {
        **projection.to_advisory_payload(),
        "needs_confirmation": any(
            problem.blocking and problem.code == "reference_duration_confirmation_required"
            for problem in projection.problems
        ),
        "script_duration": projection.planned_duration,
        "duration_input": projection.duration_input,
        "request_duration": slot.seconds,
        "adjustment": slot.adjustment,
        "declared_capability": projection.declared_generation_type,
        "hydrated_capability": projection.hydrated_generation_type,
        "provider_id": projection.provider_id,
        "model_id": projection.model_id,
        "problems": _problem_payload(projection, _t, names),
    }


class UnitPromptPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str


@router.post("/episodes/{episode}/units/{unit_id}/prompt-preview")
async def preview_unit_prompt(
    project_name: str,
    episode: int,
    unit_id: str,
    req: UnitPromptPreviewRequest,
    _t: Translator,
    names: DisplayNamesCatalog,
) -> dict[str, Any]:
    """按草稿正文投影并渲染，不保存、不入队。"""
    project, script, _sf = _load_episode_script(project_name, episode, _t)
    unit = {**_find_unit(script, unit_id, _t), "text": req.prompt}
    project_path = get_project_manager().get_project_path(project_name)
    projection = await project_reference_unit_request(
        project=project,
        script=script,
        unit=unit,
        project_path=project_path,
    )
    return await asyncio.to_thread(
        render_reference_prompt_preview,
        project=project,
        unit=unit,
        project_path=project_path,
        projection=projection,
        translate=_t,
        names=names,
    )


@router.post("/episodes/{episode}/script-preview")
async def preview_script(
    project_name: str,
    episode: int,
    req: ScriptPreviewRequest,
    _t: Translator,
    names: DisplayNamesCatalog,
) -> dict[str, Any]:
    """视频单元正文的读时派生预览：utterances + 降级可见性 warning。

    只读、不落盘——正文是唯一真相，utterances 与参考图都是机械派生物。声音相关的
    warning 依赖该集视频后端的能力（``voice_consistency`` 与参考音频段数上限）与本集的无声
    开关，与执行层同一份解析出口；能力解析失败时按 ``soft`` 降级，只是少发这几条提示。
    """
    project, _script, _sf = _load_episode_script(project_name, episode, _t)
    caps = await project_video_caps(project, degraded_to="解析预览不发声音相关提示")
    preview = build_script_preview(
        req.prompt,
        project,
        VoiceRenderSettings.from_caps(caps),
        max_reference_images=caps.get("max_reference_images"),
    )
    return {
        "utterances": [
            {"index": index, "kind": u.kind, "speaker": u.speaker, "text": u.text}
            for index, u in enumerate(preview.utterances, start=1)
        ],
        "warnings": [
            {"key": w["key"], "message": render_message(w["key"], w["params"], _t, names)} for w in preview.warnings
        ],
    }


@router.post(
    "/episodes/{episode}/units/{unit_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    user: CurrentUser,
    _t: Translator,
    names: DisplayNamesCatalog,
    req: GenerateUnitRequest | None = None,
) -> dict[str, Any]:
    project, script, script_file = _load_episode_script(project_name, episode, _t)
    unit = _find_unit(script, unit_id, _t)  # raises 404 if missing
    _require_unit_ready(unit)
    guard_prompt = str(unit.get("text") or "")
    request_options = (req or GenerateUnitRequest()).projection_options()
    projection = await project_reference_unit_request(
        request_facts_lookup=configured_reference_request_facts(project, ConfigResolver(async_session_factory)),
        project=project,
        script=script,
        unit=unit,
        project_path=get_project_manager().get_project_path(project_name),
        options=request_options,
    )
    _raise_projection_blocker(projection, _t, names, allow_duration_confirmation=False)

    # 经统一守卫点构造：空提示词的结构校验在此当场拒绝（400），与 SDK 入队路径一致，
    # 不再漏到执行层失败（见 ADR-0001）。
    try:
        spec = TaskSpec.from_request(
            task_type="reference_video",
            media_type="video",
            resource_id=unit_id,
            prompt=guard_prompt,
            script_file=script_file,
            extra_payload={"reference_request_options": request_options.to_payload()},
        )
    except TaskSpecValidationError as exc:
        raise HTTPException(status_code=400, detail=_t(exc.code, **exc.params)) from exc

    result = await get_generation_queue().enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        payload=spec.payload,
        script_file=spec.script_file,
        source="webui",
        user_id=user.id,
    )
    return {
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "projection": {**projection.to_advisory_payload(), "problems": _problem_payload(projection, _t, names)},
    }


@router.post("/episodes/{episode}/units/generate-batch")
async def generate_units_batch(
    project_name: str,
    episode: int,
    user: CurrentUser,
    _t: Translator,
    names: DisplayNamesCatalog,
    req: GenerateUnitsBatchRequest,
) -> dict[str, Any]:
    """Admit a whole batch of reference units, then create their tasks.

    The verdict is returned with HTTP 200 in all three outcomes: an evaluation
    that refuses the request is a successful evaluation, and collapsing it into a
    generic 4xx would hide every gap after the first one. Callers branch on
    ``decision``. An admitted batch whose enqueue is interrupted is likewise a
    200: the tasks already created run on, and ``enqueue_failures`` names the
    targets that never reached the queue.
    """

    project, script, script_file = _load_episode_script(project_name, episode, _t)
    body = req
    try:
        requested_ids = normalize_requested_ids(body.unit_ids, field="unit_ids")
    except ValueError as exc:
        raise BadRequestError("ref_batch_empty_selection") from exc

    # 容器原样交给筛查：`or []` 会把假值（false / 0 / ""）变成合法的空数组，那次请求就会
    # 报成「通过且零任务」，而不是如实说剧本的 video_units 坏了。成不了目标的条目（非对象、
    # 缺 unit_id、id 不是标量、id 重复，来自外部编辑或 Agent 裸写）在「缺失即生成」的目标
    # 集合里属于这次请求：悄悄略过就等于让同批健康的 unit 独自入队计费。
    units, malformed = screen_script_entries(script.get("video_units", []), requested_ids=requested_ids)

    project_path = get_project_manager().get_project_path(project_name)
    artifact_episode = resolve_artifact_episode(project=project, script=script, script_filename=script_file) or episode
    targets, selection, _states = resolve_reference_batch_targets(
        units=units,
        requested_ids=requested_ids,
        project=project,
        project_path=project_path,
        episode=artifact_episode,
    )
    unmatched = [
        refused_ticket(
            unit_id,
            code=GenerationProblemCode.UNIT_NOT_FOUND,
            detail=f"unit {unit_id} 不在 video_units 中",
            action=GenerationAction.FIX_INPUT,
        )
        for unit_id in selection.unmatched_ids
    ]
    queue = get_generation_queue()
    admission = await admit_reference_video_batch(
        project_name=project_name,
        project=project,
        project_path=project_path,
        script=script,
        script_file=script_file,
        units=targets,
        request_options=ReferenceRequestOptions(),
        operation="generate_reference_videos_batch",
        selection=(
            GenerationSelectionMode.EXPLICIT if requested_ids is not None else GenerationSelectionMode.MISSING_ONLY
        ),
        confirmed_request_durations=body.confirmed_request_durations,
        spec_check=lambda unit: reference_unit_task_spec(unit, script_file),
        # 产物状态不可读的 unit 被选目标环节排除在外，但它属于这次请求：不带进准入，
        # 同批健康的 unit 会照常入队，剩下这一个被无声略过。
        extra_tickets=[*unmatched, *malformed, *artifact_state_tickets(selection.unavailable)],
        user_id=user.id,
        queue=queue,
    )
    payload = localized_admission_payload(admission, _t, names)
    payload["skipped_unit_ids"] = sorted(state.unit_id for state in selection.skipped)
    if admission.decision is not BatchAdmissionDecision.ADMITTED:
        payload["task_ids"] = []
        payload["task_ids_by_unit"] = {}
        payload["enqueue_failures"] = []
        payload["deduped"] = False
        return payload

    specs = [reference_unit_task_spec(unit, script_file) for unit in targets]
    for spec in specs:
        spec.source = "webui"
        # 确认过的档位按 unit 记进请求事实：它是本次请求的一部分，而不是全批共用的一个值。
        # 复用准入用的那份推导，两处各算一遍才是口径分叉的来源。
        options = request_options_for_unit(
            ReferenceRequestOptions(),
            spec.resource_id,
            body.confirmed_request_durations,
        )
        spec.payload = {
            **(spec.payload or {}),
            "reference_request_options": options.to_payload(),
        }
    enqueued, enqueue_failures = await batch_enqueue_only(
        project_name=project_name,
        specs=specs,
        user_id=user.id,
        queue=queue,
    )
    # 入队中断不撤销已创建的任务：它们是准入通过的完整付费单元，照常执行。没轮到的目标
    # 逐 ID 报出来，界面据此释放乐观占用标记，下次「缺失即生成」只补这些。
    payload["enqueue_failures"] = [enqueue_failure_payload(failure, _t) for failure in enqueue_failures]
    payload["task_ids"] = [item.task_id for item in enqueued]
    # 逐 unit 给出它自己的任务行：调用方的乐观占用标记要各等各的，拿整批清单会让每个 unit
    # 都等到全批落库为止。
    payload["task_ids_by_unit"] = {item.resource_id: item.task_id for item in enqueued}
    payload["deduped"] = bool(enqueued) and all(item.deduped for item in enqueued)
    return payload


@router.post("/episodes/{episode}/units/{unit_id}/upload-video")
async def upload_unit_video(
    project_name: str,
    episode: int,
    unit_id: str,
    _t: Translator,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """上传单元成片视频，替换该 unit 的 AI 生成视频。

    复用生成链路的 finalize（抽缩略图、清旧 video_uri、status=completed），
    并纳入版本管理。参考图上传走既有的项目资产上传通路，不在此处。
    """
    try:
        max_bytes = validate_upload(file.filename, file.size, kind="video")

        relative_path = resource_relative_path("reference_videos", unit_id)

        def _validate_unit() -> tuple[Path, VersionManager, str]:
            project, script, script_file = _load_episode_script(project_name, episode, _t)
            _find_unit_for_project(project, script, unit_id, _t)  # raises 404 if missing
            project_path = get_project_manager().get_project_path(project_name)
            # 路径遍历防护：unit_id 拼出的绝对路径不得逃出项目目录（与 versions.py 对齐）
            try:
                safe_join(project_path, relative_path)
            except PathTraversalError as exc:
                raise HTTPException(status_code=400, detail=_t("invalid_resource_id", resource_id=unit_id)) from exc
            return project_path, VersionManager(project_path), script_file

        project_path, versions, script_file = await asyncio.to_thread(_validate_unit)
        target = project_path / relative_path

        with project_change_source("webui"):
            staged_video = await stage_uploaded_video_stream(file.file, target, max_bytes=max_bytes)

            # 上传流可达数百 MB、耗时数秒，期间 episode→script 绑定可能被并发重绑
            # （PATCH / agent 同步剧本）。staging 后重解析绑定，确保元数据写进当前生效的剧本。
            def _recheck_binding() -> str:
                project2, script2, script_file2 = _load_episode_script(project_name, episode, _t)
                _find_unit_for_project(project2, script2, unit_id, _t)
                return script_file2

            try:
                script_file = await asyncio.to_thread(_recheck_binding)

                def _commit_metadata(thumb_rel: str | None, on_commit: Callable[[Path], None]) -> None:
                    pm = get_project_manager()
                    with pm.locked_script(
                        project_name,
                        script_file,
                        validate=False,
                        on_commit=on_commit,
                    ) as script:
                        apply_unit_video_assets(script, unit_id, video_uri=None, thumb_rel=thumb_rel)

                version = await commit_manual_video_upload(
                    project_path=project_path,
                    versions=versions,
                    resource_type="reference_videos",
                    resource_id=unit_id,
                    script_file=script_file,
                    staged_video=staged_video,
                    current_video=target,
                    thumbnail_file=project_path / "reference_videos" / "thumbnails" / f"{unit_id}.jpg",
                    thumbnail_rel=f"reference_videos/thumbnails/{unit_id}.jpg",
                    original_filename=file.filename,
                    commit_metadata=_commit_metadata,
                )
            finally:
                await asyncio.to_thread(staged_video.unlink, missing_ok=True)
            # emit 内部会读剧本解析 episode 并计算指纹，放线程池避免阻塞事件循环；
            # 返回的指纹直接复用进响应体，免二次计算
            fingerprints = await asyncio.to_thread(
                emit_generation_success_batch,
                task_type="reference_video",
                project_name=project_name,
                resource_id=unit_id,
                payload={"script_file": script_file},
            )

        return {
            "success": True,
            "path": relative_path,
            "version": version,
            "asset_fingerprints": fingerprints,
        }
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=_t(exc.key, **exc.params)) from exc
    except FileNotFoundError as exc:
        # 不回传 str(exc)：load_script 的异常信息含服务器绝对路径
        raise NotFoundError("ref_script_missing") from exc
    except KeyError as exc:
        # finalize 写回时 unit 已被并发删除（落盘后绑定重查到锁内写回之间的窄竞态）
        raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id)) from exc
    except ScriptEditError as exc:
        raise HTTPException(status_code=400, detail=script_edit_detail(exc, _t)) from exc
    except (HTTPException, ApiError):
        # ApiError 与 HTTPException 并列：_load_episode_script 抛出的 NotFoundError
        # 不是 HTTPException 子类，不并入这里会被下面的 except Exception 吞成 500
        raise
    except Exception as exc:
        # 不回传 str(exc)：未预期异常的消息可能含服务器路径等内部细节，堆栈进日志即可
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
