"""参考生视频 CRUD + 生成路由。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §5.1
Mount prefix: /api/v1/projects/{project_name}/reference-videos
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from lib import PROJECT_ROOT
from lib.asset_types import BUCKET_KEY
from lib.project_manager import ProjectManager
from lib.reference_video import parse_prompt
from server.auth import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_name}/reference-videos",
    tags=["reference-videos"],
)

pm = ProjectManager(PROJECT_ROOT / "projects")


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
    if script.get("content_mode") != "reference_video":
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
