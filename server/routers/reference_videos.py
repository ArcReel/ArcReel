"""参考生视频 CRUD + 生成路由。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §5.1
Mount prefix: /api/v1/projects/{project_name}/reference-videos
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from lib.app_data_dir import app_data_dir
from lib.asset_types import BUCKET_KEY
from lib.generation_queue import get_generation_queue
from lib.project_manager import ProjectManager, effective_mode
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


def _load_episode_script(project_name: str, episode: int) -> tuple[dict, dict, str]:
    """加载 project.json + 指定集的剧本。返回 (project, script, script_file)。"""
    try:
        project = get_project_manager().load_project(project_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    episodes = project.get("episodes") or []
    meta = next((e for e in episodes if e.get("episode") == episode), None)
    if meta is None or not meta.get("script_file"):
        raise HTTPException(status_code=404, detail=f"episode {episode} not found")
    script_file = meta["script_file"]
    try:
        script = get_project_manager().load_script(project_name, script_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if effective_mode(project=project, episode=meta) != "reference_video":
        raise HTTPException(
            status_code=409,
            detail="episode script is not in reference_video mode",
        )
    return project, script, script_file


def _validate_references_exist(project: dict, refs: list[dict]) -> None:
    """确保 references 都在 project.json 对应 bucket 中。"""
    missing: list[str] = []
    for r in refs:
        bucket = project.get(BUCKET_KEY.get(r["type"], "")) or {}
        if r["name"] not in bucket:
            missing.append(f"{r['type']}:{r['name']}")
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"references not registered: {', '.join(missing)}",
        )


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
async def list_units(project_name: str, episode: int, _user: CurrentUser) -> dict[str, Any]:
    _project, script, _sf = _load_episode_script(project_name, episode)
    return {"units": script.get("video_units") or []}


@router.post("/episodes/{episode}/units", status_code=status.HTTP_201_CREATED)
async def add_unit(
    project_name: str,
    episode: int,
    req: AddUnitRequest,
    _user: CurrentUser,
) -> dict[str, Any]:
    project, script, script_file = _load_episode_script(project_name, episode)

    refs = [r.model_dump() for r in req.references]
    _validate_references_exist(project, refs)

    unit = _build_unit_dict(
        unit_id=_next_unit_id(script, episode),
        prompt=req.prompt,
        references=refs,
        duration_override=req.duration_seconds,
        transition=req.transition_to_next,
        note=req.note,
    )
    script.setdefault("video_units", []).append(unit)
    get_project_manager().save_script(project_name, script, script_file)
    return {"unit": unit}


# ============ 端点：PATCH + DELETE ============


class PatchUnitRequest(BaseModel):
    prompt: str | None = None
    references: list[ReferenceDto] | None = None
    duration_seconds: int | None = None
    transition_to_next: str | None = Field(default=None, pattern=r"^(cut|fade|dissolve)$")
    note: str | None = None


def _find_unit(script: dict, unit_id: str) -> dict:
    for u in script.get("video_units") or []:
        if u.get("unit_id") == unit_id:
            return u
    raise HTTPException(status_code=404, detail=f"unit {unit_id} not found")


@router.patch("/episodes/{episode}/units/{unit_id}")
async def patch_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    req: PatchUnitRequest,
    _user: CurrentUser,
) -> dict[str, Any]:
    project, script, script_file = _load_episode_script(project_name, episode)
    unit = _find_unit(script, unit_id)

    if req.references is not None:
        refs = [r.model_dump() for r in req.references]
        _validate_references_exist(project, refs)
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

    get_project_manager().save_script(project_name, script, script_file)
    return {"unit": unit}


@router.delete("/episodes/{episode}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    _user: CurrentUser,
) -> Response:
    _project, script, script_file = _load_episode_script(project_name, episode)
    units = script.get("video_units") or []
    new_units = [u for u in units if u.get("unit_id") != unit_id]
    if len(new_units) == len(units):
        raise HTTPException(status_code=404, detail=f"unit {unit_id} not found")
    script["video_units"] = new_units
    get_project_manager().save_script(project_name, script, script_file)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ReorderRequest(BaseModel):
    unit_ids: list[str]


@router.post("/episodes/{episode}/units/reorder")
async def reorder_units(
    project_name: str,
    episode: int,
    req: ReorderRequest,
    _user: CurrentUser,
) -> dict[str, Any]:
    _project, script, script_file = _load_episode_script(project_name, episode)
    units = script.get("video_units") or []
    existing_ids = [u.get("unit_id") for u in units]

    if len(req.unit_ids) != len(existing_ids):
        raise HTTPException(status_code=400, detail="unit_ids length mismatch")
    if len(set(req.unit_ids)) != len(req.unit_ids):
        raise HTTPException(status_code=400, detail="duplicate unit_ids")
    if set(req.unit_ids) != set(existing_ids):
        raise HTTPException(status_code=400, detail="unit_ids do not match existing units")

    by_id = {u["unit_id"]: u for u in units}
    script["video_units"] = [by_id[uid] for uid in req.unit_ids]
    get_project_manager().save_script(project_name, script, script_file)
    return {"units": script["video_units"]}


@router.post(
    "/episodes/{episode}/units/{unit_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_unit(
    project_name: str,
    episode: int,
    unit_id: str,
    _user: CurrentUser,
) -> dict[str, Any]:
    _project, script, script_file = _load_episode_script(project_name, episode)
    _find_unit(script, unit_id)  # raises 404 if missing

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
