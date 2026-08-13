"""
生成 API 路由

处理分镜图、视频、角色图、线索图的生成请求。
所有生成请求入队到 GenerationQueue，由 GenerationWorker 异步执行。

错误处理：路由函数体只保留 happy path。领域异常（``lib.api_errors``）与 lib 层异常
（``FileNotFoundError`` / ``ScriptEditError`` / ``TaskSpecValidationError`` / 未预期异常）
由 app 级 exception handler 统一映射为 HTTP 响应并脱敏（见 ``server/error_handlers.py``）。
"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lib.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.artifact_activation import (
    active_artifact_currency_resolver,
    artifact_is_usable,
    resolve_artifact_episode,
    resolve_usable_storyboard_video_inputs,
)
from lib.artifact_manifest import ArtifactKey
from lib.asset_types import ASSET_SPECS, resolve_asset_key, validate_asset_name
from lib.config.resolver import ConfigResolver, video_bucket_for_generation_mode
from lib.generation_queue import get_generation_queue
from lib.generation_queue_client import TaskSpec
from lib.i18n import Translator
from lib.narration_delivery import (
    POST_PRODUCTION,
    USE_TTS,
    NarratedVideoDurationPreparation,
    NarrationDelivery,
    NarrationDeliveryRequestOptions,
    canonical_narration_text,
    video_request_cost_unavailable_problem,
    video_request_requires_exact_quote,
    video_request_reuses_current_visual,
)
from lib.path_safety import safe_exists, safe_join
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import get_project_manager, is_reference_video_project
from lib.reference_video.request_projection import ProjectionResolutionError
from lib.script_editor import resolve_items
from lib.script_models import get_generated_assets
from lib.script_skeleton import resolve_script_kind
from lib.speech_composition import SpeechMode, admit_script_unit
from lib.storyboard_sequence import (
    EndFrameImageUnavailable,
    StoryboardImageUnavailable,
    find_storyboard_item,
    get_storyboard_items,
)
from server.auth import CurrentUser
from server.routers._validators import require_audio_switch_supported, require_video_bucket_capability
from server.services.cost_estimation import quote_video_request
from server.services.generation_context import AudioLaneRequest, resolve_generation_context
from server.services.image_edit_tasks import EDITABLE_RESOURCE_TYPES, resolve_current_image_rel
from server.services.narration_delivery_tasks import (
    active_narrated_video_resource_ids,
    prepare_current_storyboard_narrated_video_duration,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ==================== 请求模型 ====================


class GenerateStoryboardRequest(BaseModel):
    prompt: str | dict
    script_file: str


class GenerateVideoRequest(BaseModel):
    prompt: str | dict
    script_file: str
    duration_seconds: int | None = Field(default=None, gt=0)
    seed: int | None = None
    narration_delivery: NarrationDelivery = POST_PRODUCTION
    confirmed_request_duration_seconds: int | None = Field(default=None, gt=0)


class GenerateTtsRequest(BaseModel):
    script_file: str


class GenerateVoiceSampleRequest(BaseModel):
    text: str
    voice: str


class ConfirmVoiceSampleRequest(BaseModel):
    task_id: str


async def _localized_narrated_video_payload(
    preparation: NarratedVideoDurationPreparation,
    _t: Translator,
) -> dict[str, object]:
    payload = preparation.to_payload()
    problems = preparation.problem_payloads()
    for item, problem in zip(problems, preparation.problems, strict=True):
        item["message"] = _t(problem.code, **problem.parameters())
    payload["problems"] = problems
    if preparation.cost is not None:
        from lib.db import async_session_factory

        quote = await quote_video_request(preparation.cost, async_session_factory)
        if quote is not None:
            if video_request_reuses_current_visual(
                request_duration_seconds=preparation.request_duration_seconds,
                current_reusable_visual_duration_seconds=preparation.current_reusable_visual_duration_seconds,
            ):
                quote = quote.without_new_video_charge()
            payload["request_cost"] = quote.to_payload()
        elif video_request_requires_exact_quote(
            request_duration_seconds=preparation.request_duration_seconds,
            planned_duration_seconds=preparation.planned_duration_seconds,
            current_visual_duration_seconds=preparation.current_visual_duration_seconds,
            current_reusable_visual_duration_seconds=preparation.current_reusable_visual_duration_seconds,
        ):
            cost_problem = video_request_cost_unavailable_problem(preparation.cost)
            cost_payload = cost_problem.to_payload(unit_id=preparation.narration.unit_id)
            cost_payload["message"] = _t(cost_problem.code, **cost_problem.parameters())
            payload["allowed"] = False
            problems.append(cost_payload)
    return payload


class GenerateCharacterRequest(BaseModel):
    prompt: str


class GenerateSceneRequest(BaseModel):
    prompt: str


class GeneratePropRequest(BaseModel):
    prompt: str


class GenerateProductRequest(BaseModel):
    prompt: str


class EditImageRequest(BaseModel):
    resource_type: str
    resource_id: str
    instruction: str
    script_file: str | None = None


# ==================== 分镜图生成 ====================


@router.post("/projects/{project_name}/generate/storyboard/{segment_id}")
async def generate_storyboard(
    project_name: str,
    segment_id: str,
    req: GenerateStoryboardRequest,
    user: CurrentUser,
    _t: Translator,
):
    """
    提交分镜图生成任务到队列，立即返回 task_id。

    生成由 GenerationWorker 异步执行，状态通过 SSE 推送。
    """

    def _sync():
        get_project_manager().load_project(project_name)
        script = get_project_manager().load_script(project_name, req.script_file)
        items, id_field, _, _, _ = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, segment_id)
        if resolved is None:
            raise NotFoundError("segment_not_found", id=segment_id)

    await asyncio.to_thread(_sync)

    # 结构校验 + 构造经单一守卫点（与 SDK 入队同源，规则不分叉）；
    # 校验失败抛 TaskSpecValidationError，由 app 级 handler 映射为 400
    spec = TaskSpec.from_request(
        task_type="storyboard",
        media_type="image",
        resource_id=segment_id,
        prompt=req.prompt,
        script_file=req.script_file,
    )

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
        user_id=user.id,
    )

    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("storyboard_task_submitted", segment_id=segment_id),
    }


# ==================== 视频生成 ====================


@router.post("/projects/{project_name}/generate/video/{segment_id}")
async def generate_video(
    project_name: str,
    segment_id: str,
    req: GenerateVideoRequest,
    user: CurrentUser,
    _t: Translator,
):
    """
    提交视频生成任务到队列，立即返回 task_id。

    需要先有分镜图作为起始帧。生成由 GenerationWorker 异步执行。

    仅服务分镜图生视频路线：参考生视频路线没有分镜图这一步，在此拒绝并指引换入口。
    """

    def _sync() -> tuple[dict, Path, dict, dict]:
        pm_local = get_project_manager()
        project = pm_local.load_project(project_name)
        project_path = pm_local.get_project_path(project_name)

        # 路线闸门前置于分镜图存在性检查：参考路线项目本无分镜图步骤，落到下面会拿到
        # 「请先生成分镜图」的误导指引；换路线前残留分镜图时更糟——请求按 i2v 执行，
        # 与按 r2v 归桶的费用估算不同轴。路线以 project.json 为唯一真相源，磁盘产物不投票。
        if is_reference_video_project(project):
            raise ConflictError("video_route_is_reference_video")

        # 与 worker 一致：优先读取 generated_assets.storyboard_image；Manifest 激活前
        # 保留默认路径兼容，激活后要求显式绑定。旧宫格项目指向 scene_{id}_first.png
        # 时仍可正常解析。
        # 脚本缺失（FileNotFoundError）/ 脏脚本（分镜数组键损坏，ScriptEditError）均
        # fail-fast：不能 silently 降级走 default 路径——default 文件恰好存在时会让请求
        # 「先返回提交成功、worker 解析脚本时再确定失败」，撕裂用户预期。两者均由 app 级
        # handler 统一映射为脱敏响应（404 / 400）。
        script = pm_local.load_script(project_name, req.script_file)
        items, id_field, _, _, _ = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, segment_id)
        if resolved is None:
            raise NotFoundError("segment_not_found", id=segment_id)
        script_kind = resolve_script_kind(script)
        admission = admit_script_unit(script_kind, resolved[0])
        if admission.allowed and script_kind in {"segments", "shots"}:
            # narration / ad 的 worker 会把请求 prompt 里的 dialogue 原样下发；准入必须检查
            # 实际入队的 prompt 与盘上旁白字段，而不能只检查可能已过时的 script prompt。
            admission = admit_script_unit(script_kind, {**resolved[0], "video_prompt": req.prompt})
        if not admission.allowed:
            raise HTTPException(status_code=409, detail=admission.to_dict())
        # 字段值来自磁盘剧本 JSON，不可信任；路径校验和 schema 激活后的显式绑定要求
        # 与 worker / 当前基线重建共用同一解析器。
        try:
            artifact_episode = resolve_artifact_episode(
                project=project,
                script=script,
                script_filename=req.script_file,
            )
            resolve_usable_storyboard_video_inputs(
                project_path=project_path,
                project=project,
                episode=artifact_episode,
                resource_id=segment_id,
                item=resolved[0],
            )
        except EndFrameImageUnavailable:
            raise BadRequestError("invalid_end_frame_image_path", segment_id=segment_id) from None
        except StoryboardImageUnavailable:
            raise BadRequestError("generate_storyboard_first", segment_id=segment_id) from None
        except ValueError:
            raise BadRequestError("invalid_storyboard_image_path", segment_id=segment_id) from None
        return project, project_path, script, resolved[0]

    project, project_path, script, item = await asyncio.to_thread(_sync)

    # 归桶按项目路线求值（docs/adr/0054），与执行层 lane 声明同源、不第二次硬编码 i2v；
    # 上面的路线闸门已挡掉参考路线，此处对能到达的项目恒为 i2v。解析闸预检让能力缺失 /
    # 悬空引用在提交入口即返回修复指引，而非任务面板里的异步失败。
    _video_bucket = video_bucket_for_generation_mode(project.get("generation_mode"))
    await require_video_bucket_capability(project, _video_bucket)
    await require_audio_switch_supported(project, _video_bucket)

    delivery_projection: NarratedVideoDurationPreparation | None = None
    delivery_payload: dict[str, object] | None = None
    if req.narration_delivery == USE_TTS:
        current_planned_duration = item.get("duration_seconds")
        if (
            not isinstance(current_planned_duration, int)
            or isinstance(current_planned_duration, bool)
            or current_planned_duration <= 0
        ):
            current_planned_duration = None
        try:
            delivery_projection = await prepare_current_storyboard_narrated_video_duration(
                project_name=project_name,
                project=project,
                project_path=project_path,
                script=script,
                script_file=req.script_file,
                item=item,
                visual_prompt=req.prompt,
                seed=req.seed,
                capability=_video_bucket,
                # use_tts 不把请求中的 duration 持久化进队列；预检必须和 worker 一样基于
                # 当前盘上单元重投影，否则客户端旧快照可能先通过、执行时才要求另一档确认。
                planned_duration_seconds=current_planned_duration,
                confirmed_request_duration_seconds=req.confirmed_request_duration_seconds,
            )
        except ProjectionResolutionError as exc:
            raise BadRequestError(exc.code, **exc.params) from exc
        delivery_payload = await _localized_narrated_video_payload(delivery_projection, _t)
        if not delivery_payload["allowed"]:
            raise HTTPException(
                status_code=400,
                detail=delivery_payload,
            )

    delivery_options = NarrationDeliveryRequestOptions(
        narration_delivery=req.narration_delivery,
        confirmed_request_duration_seconds=req.confirmed_request_duration_seconds,
    )

    # 结构校验 + 构造经单一守卫点（与 SDK 入队同源，规则不分叉）。
    # duration 是能力维度，留待执行层在 provider 解析后校验（见 ADR-0001）。
    extra_payload: dict[str, object] = {
        "seed": req.seed,
        "narration_delivery_options": delivery_options.to_payload(),
    }
    if req.narration_delivery != USE_TTS:
        extra_payload["duration_seconds"] = req.duration_seconds
    spec = TaskSpec.from_request(
        task_type="video",
        media_type="video",
        resource_id=segment_id,
        prompt=req.prompt,
        script_file=req.script_file,
        extra_payload=extra_payload,
    )

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
        user_id=user.id,
    )

    response = {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("video_task_submitted", segment_id=segment_id),
    }
    if delivery_payload is not None:
        response["narration_delivery"] = delivery_payload
    return response


# ==================== 旁白配音（TTS）生成 ====================


async def _require_audio_provider_configured(project: dict) -> str:
    """未配置任何 audio 供应商时直接 400，让用户在生成入口就看到清晰提示。

    解析失败（无全局默认且 auto-resolve 找不到 ready 的 audio 供应商）即视为未配置；
    解析成功但凭证失效等运行期错误与图片/视频一致，留给 worker 在任务面板暴露。
    返回解析出的 provider_id，入队时直接复用，避免每段重复解析。
    """
    from lib.db import async_session_factory

    try:
        resolved = await ConfigResolver(async_session_factory).resolve_audio_backend(project, None)
    except ValueError:
        raise BadRequestError("audio_provider_not_configured")
    return resolved.provider_id


async def _enqueue_tts_segment(
    *,
    project_name: str,
    segment_id: str,
    script_file: str,
    user_id: str,
    provider_id: str | None,
) -> dict:
    spec = TaskSpec.from_request(
        task_type="tts",
        media_type="audio",
        resource_id=segment_id,
        script_file=script_file,
    )

    queue = get_generation_queue()
    return await queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        script_file=spec.script_file,
        payload=spec.payload,
        source="webui",
        user_id=user_id,
        provider_id=provider_id,
    )


@router.post("/projects/{project_name}/generate/tts/{segment_id}")
async def generate_tts(
    project_name: str,
    segment_id: str,
    req: GenerateTtsRequest,
    user: CurrentUser,
    _t: Translator,
):
    """显式提交一个 narrator-owned unit 的旁白配音任务。"""

    def _sync() -> tuple[dict, dict]:
        pm_local = get_project_manager()
        _project = pm_local.load_project(project_name)
        script = pm_local.load_script(project_name, req.script_file)
        items, id_field, kind = resolve_items(script)
        resolved = find_storyboard_item(items, id_field, segment_id)
        if resolved is None:
            raise NotFoundError("segment_not_found", id=segment_id)
        item = resolved[0]
        admission = admit_script_unit(kind, item)
        narration_text = canonical_narration_text(admission.preparation)
        if not admission.allowed:
            if not narration_text:
                raise BadRequestError("tts_narration_text_missing", segment_id=segment_id)
            raise HTTPException(status_code=409, detail=admission.to_dict())
        if admission.mode is not SpeechMode.NARRATOR_VOICEOVER or not narration_text:
            raise BadRequestError("tts_not_applicable", segment_id=segment_id)
        return _project, item

    project, segment = await asyncio.to_thread(_sync)

    provider_id = await _require_audio_provider_configured(project)

    active_narrated_video = await active_narrated_video_resource_ids(
        project_name=project_name,
        resource_ids=(segment_id,),
        script_file=req.script_file,
    )
    if segment_id in active_narrated_video:
        raise ConflictError("tts_conflicts_with_active_narrated_video", resource_id=segment_id)

    result = await _enqueue_tts_segment(
        project_name=project_name,
        segment_id=segment_id,
        script_file=req.script_file,
        user_id=user.id,
        provider_id=provider_id,
    )

    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("tts_task_submitted", segment_id=segment_id),
    }


@router.post("/projects/{project_name}/generate/tts")
async def generate_tts_batch(
    project_name: str,
    req: GenerateTtsRequest,
    user: CurrentUser,
    _t: Translator,
):
    """批量提交旁白配音任务：只入队缺少 narration_audio 的 narrator-owned 单元。"""

    def _sync() -> tuple[dict, list[str]]:
        pm_local = get_project_manager()
        _project = pm_local.load_project(project_name)
        script = pm_local.load_script(project_name, req.script_file)
        items, id_field, kind = resolve_items(script)
        episode = resolve_artifact_episode(
            project=_project,
            script=script,
            script_filename=req.script_file,
        )
        currency = active_artifact_currency_resolver(pm_local.get_project_path(project_name), _project)
        if currency is not None and episode is None:
            raise ValueError("script episode must be a positive integer")
        missing: list[str] = []
        for item in items:
            admission = admit_script_unit(kind, item)
            if not admission.allowed or admission.mode is not SpeechMode.NARRATOR_VOICEOVER:
                continue
            if not canonical_narration_text(admission.preparation):
                continue
            seg_id = item.get(id_field)
            if seg_id and not artifact_is_usable(
                currency,
                ArtifactKey.episode_audio(episode, str(seg_id))
                if currency is not None and episode is not None
                else None,
                get_generated_assets(item).get("narration_audio"),
            ):
                missing.append(str(seg_id))
        return _project, missing

    project, missing_ids = await asyncio.to_thread(_sync)

    if not missing_ids:
        return {"success": True, "task_ids": [], "deduped": False, "message": _t("tts_batch_none_missing")}

    provider_id = await _require_audio_provider_configured(project)

    task_ids: list[str] = []
    deduped_flags: list[bool] = []
    for seg_id in missing_ids:
        result = await _enqueue_tts_segment(
            project_name=project_name,
            segment_id=seg_id,
            script_file=req.script_file,
            user_id=user.id,
            provider_id=provider_id,
        )
        task_ids.append(result["task_id"])
        deduped_flags.append(bool(result.get("deduped", False)))

    message = _t("tts_batch_submitted", count=len(task_ids)) if task_ids else _t("tts_batch_none_missing")
    # 批量语义：全部入队都命中既有任务（本次一个新任务都没建）才算 deduped
    return {
        "success": True,
        "task_ids": task_ids,
        "deduped": bool(task_ids) and all(deduped_flags),
        "message": message,
    }


# ==================== 角色参考音频：TTS 试听样本 ====================

# 试听文案长度粗护栏（与前端 VOICE_SAMPLE_TEXT_MAX_LENGTH 同值）。参考音频要求 2-10 秒，
# 而「多少字合成出几秒」随语种与音色而变、提交前无法精确判定；此处只挡住整段粘贴导致的
# 必然失败与无谓计费，真正的时长判定仍在执行层落盘后做。
VOICE_SAMPLE_TEXT_MAX_LENGTH = 200


@router.get("/projects/{project_name}/audio-backend/voices")
async def get_audio_backend_voices(project_name: str, _t: Translator):
    """返回当前项目实际生效的 audio backend 音色枚举，供 TTS 试听弹窗选择音色。

    未配置任何 audio 供应商时返回 configured=false + 空列表，不 400——前端据此禁用
    生成入口而非报错，与角色卡「audio_backend 未配置该入口不可用」的口径一致。
    """
    project = await asyncio.to_thread(get_project_manager().load_project, project_name)
    try:
        await _require_audio_provider_configured(project)
    except BadRequestError:
        return {"configured": False, "provider_id": None, "model": None, "voices": []}

    ctx = await resolve_generation_context(
        project_name,
        None,
        project=project,
        audio=AudioLaneRequest(),
    )
    audio = ctx.audio
    # backend 的 VoiceOption.label 对 DashScope 等携带描述性文案的供应商存的是 lib/i18n
    # 翻译 key（不是直出文案），本层按请求 locale 渲染；无描述信息、label 本就等于 id
    # 的供应商（如 OpenAI）_t() 找不到对应 key 时原样返回原字符串，无副作用。
    return {
        "configured": True,
        "provider_id": audio.provider_model.provider_id,
        "model": audio.backend_model,
        "voices": [{"id": v.id, "label": _t(v.label)} for v in audio.voices],
    }


@router.post("/projects/{project_name}/characters/{name}/voice-sample")
async def generate_character_voice_sample(
    project_name: str,
    name: str,
    req: GenerateVoiceSampleRequest,
    user: CurrentUser,
    _t: Translator,
):
    """提交角色 TTS 试听样本生成任务：文本/音色显式传入，不落回全局旁白配置。

    生成产物是预览件，仅在 confirm 端点被显式提升为角色 reference_audio；本端点
    只负责入队，走既有 audio 生成通道（并发/限速/记账与旁白 TTS 完全同一套）。
    """
    text = req.text.strip()
    voice = req.voice.strip()
    if not text:
        raise BadRequestError("prompt_text_empty")
    if len(text) > VOICE_SAMPLE_TEXT_MAX_LENGTH:
        raise BadRequestError("voice_sample_text_too_long", max_length=VOICE_SAMPLE_TEXT_MAX_LENGTH)
    if not voice:
        raise BadRequestError("voice_sample_voice_required")

    def _sync() -> tuple[dict, str]:
        pm_local = get_project_manager()
        project = pm_local.load_project(project_name)
        try:
            char_name = validate_asset_name(name)
        except ValueError:
            raise BadRequestError("asset_invalid_name", name=name)
        # 存量角色 key 可能是 NFD，按坐标系解析存在性
        if resolve_asset_key(project.get("characters"), char_name) is None:
            raise NotFoundError("character_not_found", name=char_name)
        return project, char_name

    project, char_name = await asyncio.to_thread(_sync)
    provider_id = await _require_audio_provider_configured(project)

    spec = TaskSpec.from_request(
        task_type="voice_sample",
        media_type="audio",
        resource_id=char_name,
        prompt=text,
        extra_payload={"voice": voice},
        source="webui",
    )
    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        payload=spec.payload,
        source="webui",
        user_id=user.id,
        provider_id=provider_id,
    )
    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("voice_sample_task_submitted", name=char_name),
    }


@router.post("/projects/{project_name}/characters/{name}/voice-sample/confirm")
async def confirm_character_voice_sample(
    project_name: str,
    name: str,
    req: ConfirmVoiceSampleRequest,
    _t: Translator,
):
    """把已生成、已试听的 TTS 样本提升为角色 reference_audio；不确认不落资产。

    只信一个 task_id 指向的 voice_sample 任务结果，不接受客户端直传文件路径——
    产物已在执行层过一遍与上传同口径的格式/时长/大小校验（见
    ``execute_character_voice_sample_task``），此处只做「任务确属本角色、已成功」
    的归属校验后原样落盘，不重复校验。
    """
    try:
        char_name = validate_asset_name(name)
    except ValueError:
        raise BadRequestError("asset_invalid_name", name=name)
    queue = get_generation_queue()
    task = await queue.get_task(req.task_id)
    if (
        task is None
        or task.get("project_name") != project_name
        or task.get("task_type") != "voice_sample"
        or task.get("resource_id") != char_name
    ):
        raise NotFoundError("task_not_found", id=req.task_id)
    if task.get("status") != "succeeded":
        raise BadRequestError("voice_sample_not_ready")

    result = task.get("result") or {}
    sample_rel = result.get("file_path")
    if not isinstance(sample_rel, str) or not sample_rel:
        raise BadRequestError("voice_sample_not_ready")

    def _sync() -> dict:
        pm_local = get_project_manager()
        project_dir = pm_local.get_project_path(project_name)
        if not safe_exists(project_dir, sample_rel):
            raise NotFoundError("voice_sample_file_missing")
        sample_abs = safe_join(project_dir, sample_rel)
        content = sample_abs.read_bytes()

        filename = f"{char_name}.wav"
        ref_audio_rel = f"characters/refs_audio/{filename}"
        target_path = project_dir / ref_audio_rel
        try:
            with project_change_source("webui"):
                pm_local.install_character_reference_audio(project_name, char_name, ref_audio_rel, content)
        except KeyError:
            raise NotFoundError("character_not_found", name=char_name)

        # 目标文件名固定为 {char_name}.wav：重新生成后再次确认时 reference_audio 字段值
        # 不变（同一路径字符串），project.json 的字段级 diff 因此检测不到变化、不会自动
        # 广播刷新事件——但落盘字节确实已替换。显式带上新 mtime 指纹的 character:updated
        # 事件，让其它已打开该项目的客户端也能对该音频文件 cache-bust，不必等到无关的
        # 下一次项目刷新才碰巧同步。
        try:
            emit_project_change_batch(
                project_name,
                [
                    {
                        "entity_type": "character",
                        "action": "updated",
                        "entity_id": char_name,
                        "label": f"角色「{char_name}」参考音频",
                        "focus": None,
                        "important": False,
                        "asset_fingerprints": {ref_audio_rel: target_path.stat().st_mtime_ns},
                    }
                ],
                source="webui",
            )
        except Exception:
            logger.exception(
                "发送试听样本确认项目事件失败 project=%s character=%s",
                project_name,
                char_name,
            )

        return {"path": ref_audio_rel}

    saved = await asyncio.to_thread(_sync)
    return {
        "success": True,
        "path": saved["path"],
        "url": f"/api/v1/files/{project_name}/{saved['path']}",
    }


# ==================== 资产设计图生成（character / scene / prop / product 共用） ====================


# i18n key 命名差异：scene 用历史前缀 "project_scene_*"
_ASSET_GENERATE_I18N: dict[str, dict[str, str]] = {
    "character": {"not_found": "character_not_found", "submitted": "character_task_submitted"},
    "scene": {"not_found": "project_scene_not_found", "submitted": "scene_task_submitted"},
    "prop": {"not_found": "prop_not_found", "submitted": "prop_task_submitted"},
    "product": {"not_found": "product_not_found", "submitted": "product_task_submitted"},
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
    """项目级资产（character / scene / prop / product）设计图生成共用入队逻辑。"""
    spec = ASSET_SPECS[asset_type]
    keys = _ASSET_GENERATE_I18N[asset_type]

    def _sync() -> str:
        project = get_project_manager().load_project(project_name)
        # 存量 key 可能是 NFD，按坐标系解析存在性并取真实落盘 key；
        # 非 dict / 显式 null 的畸形桶由 resolve_asset_key 按空桶处理
        resolved = resolve_asset_key(project.get(spec.bucket_key), resource_name)
        if resolved is None:
            raise NotFoundError(keys["not_found"], name=resource_name)
        return resolved

    resource_key = await asyncio.to_thread(_sync)

    task_spec = TaskSpec.from_request(
        task_type=asset_type,
        media_type="image",
        resource_id=resource_key,
        prompt=prompt,
    )

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
        "deduped": result.get("deduped", False),
        "message": _t(keys["submitted"], name=resource_name),
    }


@router.post("/projects/{project_name}/generate/character/{char_name}")
async def generate_character(
    project_name: str,
    char_name: str,
    req: GenerateCharacterRequest,
    user: CurrentUser,
    _t: Translator,
):
    """提交角色设计图生成任务到队列，立即返回 task_id。"""
    return await _enqueue_asset_generation(
        asset_type="character",
        project_name=project_name,
        resource_name=char_name,
        prompt=req.prompt,
        user_id=user.id,
        _t=_t,
    )


@router.post("/projects/{project_name}/generate/scene/{scene_name}")
async def generate_scene(
    project_name: str,
    scene_name: str,
    req: GenerateSceneRequest,
    user: CurrentUser,
    _t: Translator,
):
    """提交场景设计图生成任务到队列，立即返回 task_id。"""
    return await _enqueue_asset_generation(
        asset_type="scene",
        project_name=project_name,
        resource_name=scene_name,
        prompt=req.prompt,
        user_id=user.id,
        _t=_t,
    )


@router.post("/projects/{project_name}/generate/prop/{prop_name}")
async def generate_prop(
    project_name: str,
    prop_name: str,
    req: GeneratePropRequest,
    user: CurrentUser,
    _t: Translator,
):
    """提交道具设计图生成任务到队列，立即返回 task_id。"""
    return await _enqueue_asset_generation(
        asset_type="prop",
        project_name=project_name,
        resource_name=prop_name,
        prompt=req.prompt,
        user_id=user.id,
        _t=_t,
    )


@router.post("/projects/{project_name}/generate/product/{product_name}")
async def generate_product(
    project_name: str,
    product_name: str,
    req: GenerateProductRequest,
    user: CurrentUser,
    _t: Translator,
):
    """提交产品标准参考图（product sheet）生成任务到队列，立即返回 task_id。"""
    return await _enqueue_asset_generation(
        asset_type="product",
        project_name=project_name,
        resource_name=product_name,
        prompt=req.prompt,
        user_id=user.id,
        _t=_t,
    )


# ==================== 图片指令式编辑（image edit） ====================


async def _require_i2i_image_provider_configured(project: dict) -> str:
    """项目 i2i 槽解析不出可用供应商时直接 400，不创建任务。

    图片编辑必然 i2i 且入队即知（唯一例外，见 ``docs/adr/0001`` 与 CONTEXT.md「图片编辑」），
    故解析前置到入队；执行层 ``generate_image_async`` 的 capability gating 保留兜底。
    返回解析出的 provider_id，入队时直接复用（限流池路由按 i2i 槽精确记账）。
    """
    from lib.db import async_session_factory

    try:
        resolved = await ConfigResolver(async_session_factory).resolve_image_backend(project, None, capability="i2i")
    except ValueError:
        raise BadRequestError("image_edit_i2i_unavailable")
    return resolved.provider_id


@router.post("/projects/{project_name}/edit/image")
async def edit_image(
    project_name: str,
    req: EditImageRequest,
    user: CurrentUser,
    _t: Translator,
):
    """提交图片指令式编辑任务到队列，立即返回 task_id。

    以当前图为唯一参考图、编辑指令为唯一 prompt 走 i2i；新图覆盖 current、旧图自动进
    版本历史，原 image_prompt 不回写（编辑语义见 ``docs/adr/0050``）。
    """
    if req.resource_type not in EDITABLE_RESOURCE_TYPES:
        raise BadRequestError("image_edit_resource_type_invalid", resource_type=req.resource_type)
    instruction = req.instruction.strip()
    if not instruction:
        raise BadRequestError("image_edit_instruction_required")
    is_storyboard = req.resource_type == "storyboard"
    script_file = req.script_file.strip() if req.script_file else None
    if is_storyboard and not script_file:
        raise BadRequestError("image_edit_script_file_required")

    def _sync() -> dict:
        pm_local = get_project_manager()
        project = pm_local.load_project(project_name)
        project_path = pm_local.get_project_path(project_name)
        script = pm_local.load_script(project_name, str(script_file)) if is_storyboard else None
        try:
            current_rel = resolve_current_image_rel(project, req.resource_type, req.resource_id, script)
        except KeyError:
            if is_storyboard:
                raise NotFoundError("segment_not_found", id=req.resource_id)
            raise NotFoundError(_ASSET_GENERATE_I18N[req.resource_type]["not_found"], name=req.resource_id)
        if not (current_rel and safe_exists(project_path, current_rel)):
            raise BadRequestError("image_edit_no_current_image", id=req.resource_id)
        return project

    project = await asyncio.to_thread(_sync)

    provider_id = await _require_i2i_image_provider_configured(project)

    # 结构校验 + 构造经单一守卫点（与 SDK 入队同源，规则不分叉）
    spec = TaskSpec.from_request(
        task_type="image_edit",
        media_type="image",
        resource_id=req.resource_id,
        prompt=instruction,
        script_file=script_file if is_storyboard else None,
        extra_payload={"resource_type": req.resource_type},
    )

    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        script_file=spec.script_file,
        # image_edit 跨四类资产 + storyboard 共用同一 task_type，resource_id 命名空间
        # 不互斥（角色和道具可能同名）；纳入去重键避免跨类型误判为重复任务。
        resource_type=req.resource_type,
        payload=spec.payload,
        source="webui",
        user_id=user.id,
        provider_id=provider_id,
    )

    return {
        "success": True,
        "task_id": result["task_id"],
        "deduped": result.get("deduped", False),
        "message": _t("image_edit_task_submitted", id=req.resource_id),
    }
