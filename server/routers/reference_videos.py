"""参考生视频 CRUD + 生成路由。

Mount prefix: /api/v1/projects/{project_name}/reference-videos
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from lib.app_data_dir import app_data_dir
from lib.asset_types import BUCKET_KEY
from lib.generation_queue import get_generation_queue
from lib.generation_queue_client import TaskSpec, TaskSpecValidationError
from lib.i18n import Translator
from lib.project_change_hints import project_change_source
from lib.project_manager import EpisodeScriptReboundError, ProjectManager, effective_mode
from lib.reference_video import assemble_shots_text, parse_prompt
from lib.reference_video.ad_units import (
    render_ad_unit_prompt,
    resolve_ad_unit_shots,
    sync_ad_reference_units,
)
from lib.resource_paths import resource_relative_path
from lib.script_editor import ScriptEditError
from lib.version_manager import VersionManager
from server.auth import CurrentUser
from server.routers._reorder import full_permutation_error
from server.services.generation_tasks import emit_generation_success_batch
from server.services.reference_video_tasks import _finalize_reference_video_unit, resolve_max_unit_duration
from server.services.upload_finalize import (
    UploadValidationError,
    record_upload_version,
    save_uploaded_video_stream,
    validate_upload,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_name}/reference-videos",
    tags=["reference-videos"],
)

pm = ProjectManager(app_data_dir())


def get_project_manager() -> ProjectManager:
    return pm


# ============ 请求模型 ============


class ReferenceDto(BaseModel):
    type: str = Field(pattern=r"^(character|scene|prop)$")
    name: str


class AddUnitRequest(BaseModel):
    prompt: str
    references: list[ReferenceDto] = Field(default_factory=list)
    duration_seconds: int | None = None
    transition_to_next: str = Field(default="cut", pattern=r"^(cut|fade|dissolve)$")
    note: str | None = None


# ============ 辅助 ============


def _load_episode_script(project_name: str, episode: int, _t: Translator) -> tuple[dict, dict, str]:
    """加载 project.json + 指定集的剧本。返回 (project, script, script_file)。"""
    try:
        project = get_project_manager().load_project(project_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name)) from exc
    episodes = project.get("episodes") or []
    meta = next((e for e in episodes if e.get("episode") == episode), None)
    if meta is None or not meta.get("script_file"):
        raise HTTPException(status_code=404, detail=_t("ref_episode_not_found", episode=episode))
    script_file = meta["script_file"]
    try:
        script = get_project_manager().load_script(project_name, script_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_t("script_not_found", name=script_file)) from exc
    if effective_mode(project=project, episode=meta) != "reference_video":
        raise HTTPException(status_code=409, detail=_t("ref_not_reference_video_mode"))
    return project, script, script_file


def _episode_script_resolver(
    episode: int,
    _t: Translator,
    refs: list[dict] | None = None,
    *,
    require_ad: bool | None = None,
) -> Callable[[dict], str]:
    """构造一个解析器：从 project.json 解析并校验指定集，返回其 script_file。

    解析器在 `locked_episode_script` 的项目锁内被调用（候选解析 + 持锁复核各一次），
    把「找 episode + reference_video 模式校验 + 可选 references 存在性校验 +
    可选 ad 守卫」收进同一临界区，避免锁外快照与并发写者不一致。

    ``require_ad`` 给定时校验项目是否为 ad：单元增删改重排仅对 narration/drama
    开放（``require_ad=False``，ad 的 unit 是 shots 的派生索引，不能手工编辑），
    派生端点仅对 ad 开放（``require_ad=True``）。
    """

    def _resolve(project: dict) -> str:
        if require_ad is not None:
            _require_ad_project(project, require_ad, _t)
        episodes = project.get("episodes") or []
        meta = next((e for e in episodes if e.get("episode") == episode), None)
        if meta is None or not meta.get("script_file"):
            raise HTTPException(status_code=404, detail=_t("ref_episode_not_found", episode=episode))
        if effective_mode(project=project, episode=meta) != "reference_video":
            raise HTTPException(status_code=409, detail=_t("ref_not_reference_video_mode"))
        if refs is not None:
            _validate_references_exist(project, refs, _t)
        return meta["script_file"]

    return _resolve


@contextmanager
def _locked_episode_script(project_name: str, resolver: Callable[[dict], str], _t: Translator) -> Iterator[dict]:
    """进入 `locked_episode_script`，把缺失文件归一为 404、并发改绑归一为 409。

    project.json 可能残留指向已删除/移动文件的 script_file；此时锁内 load_script 抛
    FileNotFoundError，需转成 404 而非 500。加锁前后 episode→script_file 绑定被并发 PATCH
    改动时抛 EpisodeScriptReboundError，转成 409（前端可重试，不外泄内部绑定细节）。
    """
    try:
        with get_project_manager().locked_episode_script(project_name, resolver) as script:
            yield script
    except FileNotFoundError as exc:
        # 区分「项目缺失」与「project.json 指向的脚本文件缺失（stale 绑定）」
        if not get_project_manager().project_exists(project_name):
            raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name)) from exc
        raise HTTPException(status_code=404, detail=_t("ref_script_missing")) from exc
    except EpisodeScriptReboundError as exc:
        logger.info("episode script rebound during write: %s", exc)
        raise HTTPException(status_code=409, detail=_t("ref_script_rebound")) from exc
    except ValueError as exc:
        # 结构非法（如 shots↔duration 不一致）、集号错配、非法文件名都抛 ValueError
        # （ScriptStructureValidationError 即其子类）：当场转 422，而非生成时才炸。
        # EpisodeScriptReboundError(RuntimeError) 与 FileNotFoundError 不是 ValueError，
        # 已被上面的 409 / 404 分支先行接住，不会落到这里。
        raise HTTPException(
            status_code=422,
            detail=_t("script_validation_failed", details=str(exc)),
        ) from exc


def _require_ad_project(project: dict, required: bool, _t: Translator) -> None:
    """守卫端点适用的项目类型：ad 的 unit 是 shots 的派生索引（手工增删改重排
    走不通），narration/drama 的 unit 内容自包含（派生走不通），互斥拒绝。"""
    is_ad = project.get("content_mode") == "ad"
    if required and not is_ad:
        raise HTTPException(status_code=409, detail=_t("ref_derive_ad_only"))
    if not required and is_ad:
        raise HTTPException(status_code=409, detail=_t("ref_ad_units_derived"))


def _validate_references_exist(project: dict, refs: list[dict], _t: Translator) -> None:
    """确保 references 都在 project.json 对应 bucket 中。"""
    missing: list[str] = []
    for r in refs:
        bucket = project.get(BUCKET_KEY.get(r["type"], "")) or {}
        if r["name"] not in bucket:
            missing.append(f"{r['type']}:{r['name']}")
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
    duration_override: int | None,
    transition: str,
    note: str | None,
) -> dict:
    shots, _names, override = parse_prompt(prompt)
    if override and duration_override is not None:
        shots[0].duration = max(1, int(duration_override))
    duration_total = sum(s.duration for s in shots)
    return {
        "unit_id": unit_id,
        "shots": [s.model_dump() for s in shots],
        "references": references,
        "duration_seconds": duration_total,
        "duration_override": override,
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


# ============ 端点：列出 + 新建 ============


@router.get("/episodes/{episode}/units")
async def list_units(project_name: str, episode: int, _user: CurrentUser, _t: Translator) -> dict[str, Any]:
    project, script, _sf = _load_episode_script(project_name, episode, _t)
    # ad 的 unit 是 shots 的派生索引（reference_units），未派生时为空列表；
    # 前端用 shot_ids 对照本地剧本水合展示，索引不复制镜头内容
    if project.get("content_mode") == "ad":
        return {"units": script.get("reference_units") or []}
    return {"units": script.get("video_units") or []}


@router.post("/episodes/{episode}/derive-units")
async def derive_units(
    project_name: str,
    episode: int,
    _user: CurrentUser,
    _t: Translator,
) -> dict[str, Any]:
    """（重新）派生 ad 项目的 video_unit 分组索引并持久化（仅 ad 开放）。

    分组器是纯函数：shots 与供应商时长上限不变则分组可复现；成员与参考集
    未变的 unit 保留 generated_assets（重生成单个 unit 时分组不漂移）。
    """
    project, _script, _sf = _load_episode_script(project_name, episode, _t)
    _require_ad_project(project, True, _t)
    # 供应商时长上限在锁外解析（异步 I/O 不进项目锁临界区）
    max_unit_duration = await resolve_max_unit_duration(project)

    with _locked_episode_script(project_name, _episode_script_resolver(episode, _t, require_ad=True), _t) as script:
        units = sync_ad_reference_units(script, episode=episode, max_unit_duration=max_unit_duration)
    return {"units": units}


@router.post("/episodes/{episode}/units", status_code=status.HTTP_201_CREATED)
async def add_unit(
    project_name: str,
    episode: int,
    req: AddUnitRequest,
    _user: CurrentUser,
    _t: Translator,
) -> dict[str, Any]:
    refs = [r.model_dump() for r in req.references]

    with _locked_episode_script(
        project_name, _episode_script_resolver(episode, _t, refs, require_ad=False), _t
    ) as script:
        # unit_id 在锁内基于 fresh script 计算，避免并发新增撞 ID
        unit = _build_unit_dict(
            unit_id=_next_unit_id(script, episode),
            prompt=req.prompt,
            references=refs,
            duration_override=req.duration_seconds,
            transition=req.transition_to_next,
            note=req.note,
        )
        script.setdefault("video_units", []).append(unit)
    return {"unit": unit}


# ============ 端点：PATCH + DELETE ============


class PatchUnitRequest(BaseModel):
    prompt: str | None = None
    references: list[ReferenceDto] | None = None
    duration_seconds: int | None = None
    transition_to_next: str | None = Field(default=None, pattern=r"^(cut|fade|dissolve)$")
    note: str | None = None


def _find_unit(script: dict, unit_id: str, _t: Translator) -> dict:
    for u in script.get("video_units") or []:
        if u.get("unit_id") == unit_id:
            return u
    raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id))


def _find_ad_unit(script: dict, unit_id: str, _t: Translator) -> dict:
    for u in script.get("reference_units") or []:
        if isinstance(u, dict) and u.get("unit_id") == unit_id:
            return u
    raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id))


def _find_unit_for_project(project: dict, script: dict, unit_id: str, _t: Translator) -> dict:
    """按项目内容模式选 unit 所在列表：ad 在 reference_units 派生索引，其余在 video_units。"""
    if project.get("content_mode") == "ad":
        return _find_ad_unit(script, unit_id, _t)
    return _find_unit(script, unit_id, _t)


@router.patch("/episodes/{episode}/units/{unit_id}")
async def patch_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    req: PatchUnitRequest,
    _user: CurrentUser,
    _t: Translator,
) -> dict[str, Any]:
    # references 存在性校验在解析器内、项目锁内进行，失败 raise 400
    refs: list[dict] | None = [r.model_dump() for r in req.references] if req.references is not None else None

    with _locked_episode_script(
        project_name, _episode_script_resolver(episode, _t, refs, require_ad=False), _t
    ) as script:
        unit = _find_unit(script, unit_id, _t)  # 未找到 raise 404 → 跳过写回

        if refs is not None:
            unit["references"] = refs

        if req.prompt is not None:
            shots, _mentions, override = parse_prompt(req.prompt)
            if override and req.duration_seconds is not None:
                shots[0].duration = max(1, int(req.duration_seconds))
            unit["shots"] = [s.model_dump() for s in shots]
            unit["duration_seconds"] = sum(s.duration for s in shots)
            unit["duration_override"] = override
        elif req.duration_seconds is not None and unit.get("duration_override"):
            unit["duration_seconds"] = max(1, int(req.duration_seconds))
            if unit.get("shots"):
                unit["shots"][0]["duration"] = unit["duration_seconds"]

        if req.transition_to_next is not None:
            unit["transition_to_next"] = req.transition_to_next
        if req.note is not None:
            unit["note"] = req.note

    return {"unit": unit}


@router.delete("/episodes/{episode}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    _user: CurrentUser,
    _t: Translator,
) -> Response:
    with _locked_episode_script(project_name, _episode_script_resolver(episode, _t, require_ad=False), _t) as script:
        units = script.get("video_units") or []
        new_units = [u for u in units if u.get("unit_id") != unit_id]
        if len(new_units) == len(units):
            # 未找到 → 在锁内 raise，跳过写回
            raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id))
        script["video_units"] = new_units
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ReorderRequest(BaseModel):
    unit_ids: list[str]


@router.post("/episodes/{episode}/units/reorder")
async def reorder_units(
    project_name: str,
    episode: int,
    req: ReorderRequest,
    _user: CurrentUser,
    _t: Translator,
) -> dict[str, Any]:
    with _locked_episode_script(project_name, _episode_script_resolver(episode, _t, require_ad=False), _t) as script:
        units = script.get("video_units") or []
        existing_ids = [u.get("unit_id") for u in units]

        # 校验失败 → 在锁内 raise 400，跳过写回
        error_kind = full_permutation_error(existing_ids, req.unit_ids)
        if error_kind is not None:
            detail_key = {
                "length": "ref_unit_ids_length_mismatch",
                "duplicate": "ref_duplicate_unit_ids",
                "mismatch": "ref_unit_ids_mismatch",
            }[error_kind]
            raise HTTPException(status_code=400, detail=_t(detail_key))

        by_id = {u["unit_id"]: u for u in units}
        reordered = [by_id[uid] for uid in req.unit_ids]
        script["video_units"] = reordered
    return {"units": reordered}


@router.post(
    "/episodes/{episode}/units/{unit_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    _user: CurrentUser,
    _t: Translator,
) -> dict[str, Any]:
    project, script, script_file = _load_episode_script(project_name, episode, _t)
    is_ad = project.get("content_mode") == "ad"
    if is_ad:
        unit = _find_ad_unit(script, unit_id, _t)  # raises 404 if missing
        # 按持久化索引水合成员镜头（重生成单个 unit 不重新派生，分组可复现）；
        # 索引悬空（镜头被删后未重新派生）→ 409 提示重新派生
        try:
            unit_shots = resolve_ad_unit_shots(script, unit)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=_t("ref_ad_stale_index")) from exc
        style = project.get("style")
        guard_prompt = render_ad_unit_prompt(unit_shots, style=style if isinstance(style, str) else None)
    else:
        unit = _find_unit(script, unit_id, _t)  # raises 404 if missing
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

    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type=spec.task_type,
        media_type=spec.media_type,
        resource_id=spec.resource_id,
        payload=spec.payload,
        script_file=spec.script_file,
        source="webui",
        user_id=_user.id,
    )
    return {"task_id": result["task_id"], "deduped": result.get("deduped", False)}


@router.post("/episodes/{episode}/units/{unit_id}/upload-video")
async def upload_unit_video(
    project_name: str,
    episode: int,
    unit_id: str,
    _user: CurrentUser,
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
            target = project_path / relative_path
            try:
                target.resolve().relative_to(project_path.resolve())
            except ValueError:
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
        raise HTTPException(status_code=404, detail=_t("ref_script_missing")) from exc
    except KeyError as exc:
        # finalize 写回时 unit 已被并发删除（落盘后绑定重查到锁内写回之间的窄竞态）
        raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=unit_id)) from exc
    except ScriptEditError as exc:
        raise HTTPException(status_code=400, detail=_t("script_data_corrupted", reason=str(exc))) from exc
    except HTTPException:
        raise
    except Exception as exc:
        # 不回传 str(exc)：未预期异常的消息可能含服务器路径等内部细节，堆栈进日志即可
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
