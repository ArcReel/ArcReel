"""参考生视频 CRUD + 生成路由。

Mount prefix: /api/v1/projects/{project_name}/reference-videos
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from lib.api_errors import ApiError, NotFoundError
from lib.asset_types import asset_name_comparison_key
from lib.generation_queue import get_generation_queue
from lib.generation_queue_client import TaskSpec, TaskSpecValidationError
from lib.i18n import Translator
from lib.path_safety import PathTraversalError, safe_join
from lib.project_change_hints import project_change_source
from lib.project_manager import get_project_manager, is_reference_video_project
from lib.reference_video import (
    assemble_shots_text,
    derive_references_from_text,
    missing_registered_references,
    parse_prompt,
)
from lib.reference_video.script_preview import build_script_preview
from lib.reference_video.units import reference_unit_video_bucket, reference_video_bucket
from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.resource_paths import resource_relative_path
from lib.script_editor import ScriptEditError
from lib.speech_composition import admit_script_unit, refresh_video_unit_replan_state
from lib.version_manager import VersionManager
from server.auth import CurrentUser
from server.error_handlers import script_edit_detail
from server.routers._reorder import full_permutation_error
from server.routers._script_edits import execute_current_episode_edit, require_script_edit_result
from server.routers._validators import require_audio_switch_supported, require_video_bucket_capability
from server.services.generation_tasks import emit_generation_success_batch
from server.services.reference_video_tasks import (
    _finalize_reference_video_unit,
    default_unit_duration,
    precheck_unit,
    resolve_project_duration_context,
)
from server.services.upload_finalize import (
    UploadValidationError,
    record_upload_version,
    save_uploaded_video_stream,
    validate_upload,
)
from server.services.video_caps import project_video_caps

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_name}/reference-videos",
    tags=["reference-videos"],
)

# ============ 请求模型 ============


class ReferenceDto(BaseModel):
    type: str = Field(pattern=r"^(product|character|scene|prop)$")
    name: str


class ScriptPreviewRequest(BaseModel):
    prompt: str = ""


class AddUnitRequest(BaseModel):
    prompt: str
    references: list[ReferenceDto] = Field(default_factory=list)
    duration_seconds: int | None = Field(default=None, ge=1)
    transition_to_next: str = Field(default="cut", pattern=r"^(cut|fade|dissolve)$")
    note: str | None = None


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


def _validate_references_exist(project: dict, refs: list[dict], _t: Translator) -> None:
    """确保 references 都在 project.json 对应 bucket 中。"""
    missing = missing_registered_references(refs, project)
    if missing:
        raise HTTPException(status_code=400, detail=_t("ref_not_registered", missing=", ".join(missing)))


def _next_unit_id(script: dict, episode: int) -> str:
    existing = {str(u.get("unit_id", "")) for u in (script.get("video_units") or [])}
    idx = 1
    while f"E{episode}U{idx}" in existing:
        idx += 1
    return f"E{episode}U{idx}"


def _build_unit_dict(
    *,
    unit_id: str,
    prompt: str,
    references: list[dict],
    duration_seconds: int,
    transition: str,
    note: str | None,
) -> dict:
    shots, _names = parse_prompt(prompt)
    unit = {
        "unit_id": unit_id,
        "shots": [s.model_dump() for s in shots],
        "references": references,
        "duration_seconds": duration_seconds,
        "transition_to_next": transition,
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
    if allow_blank_draft and not assemble_shots_text(unit.get("shots") or []).strip():
        return
    admission = admit_script_unit("video_units", unit, ignore_marker=ignore_marker)
    if not admission.allowed:
        raise HTTPException(status_code=409, detail=admission.to_dict())


# ============ 端点：列出 + 新建 ============


@router.get("/episodes/{episode}/units")
async def list_units(project_name: str, episode: int, _t: Translator) -> dict[str, Any]:
    _project, script, _sf = _load_episode_script(project_name, episode, _t)
    return {"units": script.get("video_units") or []}


def _normalized_refs(references: list[Any]) -> list[dict]:
    """把请求里的 reference 条目转成落盘 dict，资产名统一归一到比对坐标系。

    请求可能携带两端空白或 NFD 形态的资产名，持久化前收敛到 strip + NFC，
    与镜头正文及前端 mergeReferences 的写回口径一致。
    """
    return [{**r.model_dump(), "name": asset_name_comparison_key(r.name)} for r in references]


@router.post("/episodes/{episode}/units", status_code=status.HTTP_201_CREATED)
async def add_unit(
    project_name: str,
    episode: int,
    req: AddUnitRequest,
    _t: Translator,
) -> dict[str, Any]:
    refs = _normalized_refs(req.references)
    references_supplied = "references" in req.model_fields_set

    project, current, _script_file = _load_episode_script(project_name, episode, _t)
    if references_supplied:
        _validate_references_exist(project, refs, _t)
    else:
        derived_refs, _missing = derive_references_from_text(req.prompt, project)
        refs = [reference.model_dump() for reference in derived_refs]

    # 时长是 unit 级单一真相：请求未给出时按项目能力解析默认档位（异步 IO 不进项目锁临界区）
    duration_seconds = req.duration_seconds
    if duration_seconds is None:
        duration_seconds = default_unit_duration(
            await resolve_project_duration_context(
                project, capability=reference_video_bucket(with_references=bool(refs))
            ),
            project,
            with_references=bool(refs),
        )

    units = current.get("video_units") if isinstance(current.get("video_units"), list) else []
    unit = _build_unit_dict(
        unit_id=_next_unit_id(current, episode),
        prompt=req.prompt,
        references=refs,
        duration_seconds=int(duration_seconds),
        transition=req.transition_to_next,
        note=req.note,
    )
    if not references_supplied:
        # Omission means mechanical derivation. Let the shared editor do it from the
        # project snapshot held by the commit lock, rather than persisting this preview.
        unit.pop("references", None)
    result = execute_current_episode_edit(
        get_project_manager(),
        project_name,
        episode,
        current,
        [{"op": "insert_after", "after_id": units[-1].get("unit_id") if units else None, "item": unit}],
    )
    require_script_edit_result(result)
    saved = get_project_manager().load_script(project_name, result.script)
    inserted = _find_unit(saved, unit["unit_id"], _t)
    return {"unit": inserted, "edit_result": result.model_dump(mode="json")}


# ============ 端点：PATCH + DELETE ============


class PatchUnitRequest(BaseModel):
    prompt: str | None = None
    references: list[ReferenceDto] | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    transition_to_next: str | None = Field(default=None, pattern=r"^(cut|fade|dissolve)$")
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
    refs: list[dict] | None = _normalized_refs(req.references) if req.references is not None else None
    project, current, _script_file = _load_episode_script(project_name, episode, _t)
    _find_unit(current, unit_id, _t)
    if refs is not None:
        _validate_references_exist(project, refs, _t)
    fields: dict[str, Any] = {}
    if refs is not None:
        fields["references"] = refs
    if req.prompt is not None:
        shots, _mentions = parse_prompt(req.prompt)
        fields["shots"] = [shot.model_dump() for shot in shots]
    if req.duration_seconds is not None:
        fields["duration_seconds"] = req.duration_seconds
    if req.transition_to_next is not None:
        fields["transition_to_next"] = req.transition_to_next
    if req.note is not None:
        fields["note"] = req.note
    if not fields:
        return {"unit": _find_unit(current, unit_id, _t)}
    result = execute_current_episode_edit(
        get_project_manager(),
        project_name,
        episode,
        current,
        [{"op": "update", "id": unit_id, "fields": fields}],
    )
    require_script_edit_result(result, operation_not_found=True)
    saved = get_project_manager().load_script(project_name, result.script)
    unit = _find_unit(saved, unit_id, _t)
    return {"unit": unit, "edit_result": result.model_dump(mode="json")}


@router.delete("/episodes/{episode}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    _t: Translator,
) -> Response:
    _project, current, _script_file = _load_episode_script(project_name, episode, _t)
    _find_unit(current, unit_id, _t)
    result = execute_current_episode_edit(
        get_project_manager(),
        project_name,
        episode,
        current,
        [{"op": "remove", "id": unit_id}],
    )
    require_script_edit_result(result, operation_not_found=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ReorderRequest(BaseModel):
    unit_ids: list[str]


@router.post("/episodes/{episode}/units/reorder")
async def reorder_units(
    project_name: str,
    episode: int,
    req: ReorderRequest,
    _t: Translator,
) -> dict[str, Any]:
    _project, current, _script_file = _load_episode_script(project_name, episode, _t)
    units = current.get("video_units") or []
    existing_ids = [unit.get("unit_id") for unit in units]
    error_kind = full_permutation_error(existing_ids, req.unit_ids)
    if error_kind is not None:
        detail_key = {
            "length": "ref_unit_ids_length_mismatch",
            "duplicate": "ref_duplicate_unit_ids",
            "mismatch": "ref_unit_ids_mismatch",
        }[error_kind]
        raise HTTPException(status_code=400, detail=_t(detail_key))
    if existing_ids == req.unit_ids:
        return {"units": units}
    operations = [
        {"op": "move_after", "id": unit_id, "after_id": req.unit_ids[index - 1] if index else None}
        for index, unit_id in enumerate(req.unit_ids)
    ]
    result = execute_current_episode_edit(get_project_manager(), project_name, episode, current, operations)
    require_script_edit_result(result)
    reordered = get_project_manager().load_script(project_name, result.script)["video_units"]
    return {"units": reordered, "edit_result": result.model_dump(mode="json")}


@router.get("/episodes/{episode}/units/{unit_id}/duration-precheck")
async def precheck_unit_duration(
    project_name: str,
    episode: int,
    unit_id: str,
    _t: Translator,
) -> dict[str, Any]:
    """入队前的时长取档预检：申请秒数与剧本编排不一致时前端需先向用户确认。

    ``needs_confirmation`` 为 false 时（总时长本身是档位成员、或能力不可解析）直接入队。
    取档按项目当前配置近似解析（provider 在执行时才解析，见 ADR-0001），实际档位以执行
    时的 model 能力为准；执行时的取档结果记入任务 warning。
    """
    project, script, _sf = _load_episode_script(project_name, episode, _t)
    unit = _find_unit(script, unit_id, _t)
    _require_unit_ready(unit)

    # ctx 按 unit 定桶解析（无参考图退化镜头 → i2v），与执行期实际取档的模型同桶。
    slot = precheck_unit(
        await resolve_project_duration_context(project, capability=reference_unit_video_bucket(unit)),
        unit,
    )
    return {
        "needs_confirmation": slot.needs_confirmation,
        "script_duration": slot.total_seconds,
        "request_duration": slot.seconds,
        "adjustment": slot.adjustment,
    }


@router.post("/episodes/{episode}/script-preview")
async def preview_script(
    project_name: str,
    episode: int,
    req: ScriptPreviewRequest,
    _t: Translator,
) -> dict[str, Any]:
    """分镜文稿的读时派生预览：shots / references / utterances + 降级可见性 warning。

    只读、不落盘——文稿是唯一真相，utterances 与 references 都是机械派生物。声音相关的
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
        "shots": [{"index": i, "text": s.text} for i, s in enumerate(preview.shots, start=1)],
        "references": [r.model_dump() for r in preview.references],
        "utterances": [
            {
                "shot_index": u.shot_index,
                "kind": u.utterance.kind,
                "speaker": u.utterance.speaker,
                "text": u.utterance.text,
            }
            for u in preview.utterances
        ],
        "warnings": [{"key": w["key"], "message": _t(w["key"], **w["params"])} for w in preview.warnings],
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
) -> dict[str, Any]:
    project, script, script_file = _load_episode_script(project_name, episode, _t)
    unit = _find_unit(script, unit_id, _t)  # raises 404 if missing
    _require_unit_ready(unit)
    guard_prompt = assemble_shots_text(unit.get("shots") or [])

    # 经统一守卫点构造：空提示词的结构校验在此当场拒绝（400），与 SDK 入队路径一致，
    # 不再漏到执行层失败（见 ADR-0001）。
    try:
        spec = TaskSpec.from_request(
            task_type="reference_video",
            media_type="video",
            resource_id=unit_id,
            prompt=guard_prompt,
            script_file=script_file,
        )
    except TaskSpecValidationError as exc:
        raise HTTPException(status_code=400, detail=_t(exc.code, **exc.params)) from exc

    # 参考生视频按镜头是否携带参考图分流定桶（docs/adr/0054）：有参考图 → r2v，无参考图
    # 退化镜头降级 → i2v。ad 按水合后的成员镜头现算参考集（与执行侧同源），其余按 unit
    # 声明的 references 近似（执行层按解析后的实际图独立判定）；解析闸让能力缺失 / 悬空
    # 引用在提交入口即返回修复指引，而非任务面板里的异步失败。
    _video_bucket = reference_unit_video_bucket(unit)
    await require_video_bucket_capability(project, _video_bucket)
    await require_audio_switch_supported(project, _video_bucket)

    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        payload=spec.payload,
        script_file=spec.script_file,
        source="webui",
        user_id=user.id,
    )
    return {"task_id": result["task_id"], "deduped": result.get("deduped", False)}


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
            except PathTraversalError:
                raise HTTPException(status_code=400, detail=_t("invalid_resource_id", resource_id=unit_id))
            return project_path, VersionManager(project_path), script_file

        project_path, versions, script_file = await asyncio.to_thread(_validate_unit)
        target = project_path / relative_path

        with project_change_source("webui"):
            await asyncio.to_thread(versions.ensure_current_tracked, "reference_videos", unit_id, target, "")
            await save_uploaded_video_stream(file.file, target, max_bytes=max_bytes)

            # 上传流可达数百 MB、耗时数秒，期间 episode→script 绑定可能被并发重绑
            # （PATCH / agent 同步剧本）。落盘后重解析绑定，确保元数据写进当前生效的剧本。
            def _recheck_binding() -> str:
                project2, script2, script_file2 = _load_episode_script(project_name, episode, _t)
                _find_unit_for_project(project2, script2, unit_id, _t)
                return script_file2

            script_file = await asyncio.to_thread(_recheck_binding)

            version = await asyncio.to_thread(
                record_upload_version,
                versions=versions,
                resource_type="reference_videos",
                resource_id=unit_id,
                current_file=target,
                original_filename=file.filename,
            )
            await _finalize_reference_video_unit(
                project_name=project_name,
                script_file=script_file,
                project_path=project_path,
                resource_id=unit_id,
                output_path=target,
                version=version,
                video_uri=None,
                versions=versions,
            )
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
