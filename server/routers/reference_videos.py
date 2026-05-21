"""参考生视频 CRUD + 生成路由。

Mount prefix: /api/v1/projects/{project_name}/reference-videos
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from lib.app_data_dir import app_data_dir
from lib.asset_types import BUCKET_KEY
from lib.generation_queue import get_generation_queue
from lib.i18n import Translator
from lib.project_manager import EpisodeScriptReboundError, ProjectManager, effective_mode
from lib.reference_video import parse_prompt
from server.auth import CurrentUser

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


def _episode_script_resolver(episode: int, _t: Translator, refs: list[dict] | None = None) -> Callable[[dict], str]:
    """构造一个解析器：从 project.json 解析并校验指定集，返回其 script_file。

    解析器在 `locked_episode_script` 的项目锁内被调用（候选解析 + 持锁复核各一次），
    把「找 episode + reference_video 模式校验 + 可选 references 存在性校验」收进同一临界区，
    避免锁外快照与并发写者不一致。
    """

    def _resolve(project: dict) -> str:
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
    _project, script, _sf = _load_episode_script(project_name, episode, _t)
    return {"units": script.get("video_units") or []}


@router.post("/episodes/{episode}/units", status_code=status.HTTP_201_CREATED)
async def add_unit(
    project_name: str,
    episode: int,
    req: AddUnitRequest,
    _user: CurrentUser,
    _t: Translator,
) -> dict[str, Any]:
    refs = [r.model_dump() for r in req.references]

    with _locked_episode_script(project_name, _episode_script_resolver(episode, _t, refs), _t) as script:
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

    with _locked_episode_script(project_name, _episode_script_resolver(episode, _t, refs), _t) as script:
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
    with _locked_episode_script(project_name, _episode_script_resolver(episode, _t), _t) as script:
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
    with _locked_episode_script(project_name, _episode_script_resolver(episode, _t), _t) as script:
        units = script.get("video_units") or []
        existing_ids = [u.get("unit_id") for u in units]

        # 校验失败 → 在锁内 raise 400，跳过写回
        if len(req.unit_ids) != len(existing_ids):
            raise HTTPException(status_code=400, detail=_t("ref_unit_ids_length_mismatch"))
        if len(set(req.unit_ids)) != len(req.unit_ids):
            raise HTTPException(status_code=400, detail=_t("ref_duplicate_unit_ids"))
        if set(req.unit_ids) != set(existing_ids):
            raise HTTPException(status_code=400, detail=_t("ref_unit_ids_mismatch"))

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
    _project, script, script_file = _load_episode_script(project_name, episode, _t)
    _find_unit(script, unit_id, _t)  # raises 404 if missing

    queue = get_generation_queue()
    result = await queue.enqueue_task(
        project_name=project_name,
        task_type="reference_video",
        media_type="video",
        resource_id=unit_id,
        payload={"script_file": script_file},
        script_file=script_file,
        source="webui",
        user_id=_user.id,
    )
    return {"task_id": result["task_id"], "deduped": result.get("deduped", False)}
