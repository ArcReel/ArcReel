"""「分集」视图：整本源文按集分段的只读投影、``source/`` 里没有登记的文件的处置，以及整本源文文件的类型。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lib.episode.episode_layout import build_episode_layout
from lib.episode.episode_manual_split import (
    ManualSplitError,
    ManualSplitOutcome,
    ManualSplitResult,
    clear_cuts_after,
    cut_unsplit_source,
    merge_with_next_episode,
    move_episode_boundary,
    render_manual_split_impact_text,
    split_episode,
)
from lib.episode.episode_source_commands import (
    EpisodeSourceError,
    adopt_source_file_as_episode,
    adopt_source_file_as_whole_source,
    set_whole_source_file_kind,
)
from lib.episode.source_kinds import SourceKind
from lib.infra.api_errors import ApiError, NotFoundError
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from server.dependencies import require_project_migration_ok
from server.i18n import Translator
from server.routers._episode_source_errors import episode_source_http_error

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/projects/{name}/episodes-view")
async def get_episodes_view(name: str, _t: Translator) -> dict[str, Any]:
    """整本源文按集分段、每集的体量与首尾句，以及 ``source/`` 里没有登记的文本文件。"""

    def _sync() -> dict[str, Any]:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        project = manager.load_project(name)
        return asdict(build_episode_layout(manager.get_project_path(name), project))

    try:
        return await asyncio.to_thread(_sync)
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class AdoptSourceFileRequest(BaseModel):
    #: whole_source 加入整本源文末尾；episode 用作一集的原文。
    target: Literal["whole_source", "episode"]
    #: target=episode 时填给这一集（须是无原文的集）；缺省时在播出顺序末尾新建一集。
    episode: int | None = None


@router.post(
    "/projects/{name}/source-files/{filename}/adopt",
    dependencies=[Depends(require_project_migration_ok)],
)
async def adopt_source_file(name: str, filename: str, req: AdoptSourceFileRequest, _t: Translator) -> dict[str, Any]:
    """处置 ``source/`` 里没有登记的文件：加入整本源文，或用作一集的原文（原文件随即删除）。"""

    def _sync() -> dict[str, Any]:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        with project_change_source("webui"):
            if req.target == "whole_source":
                adopt_source_file_as_whole_source(manager, name, filename)
                return {"success": True, "target": req.target, "path": f"source/{filename}"}
            episode = adopt_source_file_as_episode(manager, name, filename, req.episode)
        return {"success": True, "target": req.target, "episode": episode}

    try:
        return await asyncio.to_thread(_sync)
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t, episode=req.episode, filename=filename) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class _ManualSplitBase(BaseModel):
    #: 创作者在确认清单里看过的有产物的集；锁内复核出清单之外的有产物集时退回确认。
    confirm_episodes: list[int] = Field(default_factory=list)
    #: 只返回波及清单，不写入。
    dry_run: bool = False


class CutRequest(_ManualSplitBase):
    action: Literal["cut"]
    source_file: str
    end: int
    title: str = ""


class SplitRequest(_ManualSplitBase):
    action: Literal["split"]
    episode: int
    at: int


class MoveBoundaryRequest(_ManualSplitBase):
    action: Literal["move_boundary"]
    episode: int
    at: int


class MergeNextRequest(_ManualSplitBase):
    action: Literal["merge_next"]
    episode: int


class ClearAfterRequest(_ManualSplitBase):
    action: Literal["clear_after"]
    episode: int


_ManualSplitBody = CutRequest | SplitRequest | MoveBoundaryRequest | MergeNextRequest | ClearAfterRequest
ManualSplitRequest = Annotated[_ManualSplitBody, Field(discriminator="action")]

_MANUAL_SPLIT_STATUS: dict[str, int] = {
    "episode_not_found": 404,
    "source_file_not_found": 404,
    "position_invalid": 422,
    "empty_range": 422,
}


def _run_manual_split(project_dir: Path, req: _ManualSplitBody) -> ManualSplitOutcome:
    options = {"confirm_episodes": req.confirm_episodes, "dry_run": req.dry_run}
    if isinstance(req, CutRequest):
        return cut_unsplit_source(project_dir, source_file=req.source_file, end=req.end, title=req.title, **options)
    if isinstance(req, SplitRequest):
        return split_episode(project_dir, req.episode, at=req.at, **options)
    if isinstance(req, MoveBoundaryRequest):
        return move_episode_boundary(project_dir, req.episode, at=req.at, **options)
    if isinstance(req, MergeNextRequest):
        return merge_with_next_episode(project_dir, req.episode, **options)
    return clear_cuts_after(project_dir, req.episode, **options)


@router.post(
    "/projects/{name}/episodes-view/manual-split",
    dependencies=[Depends(require_project_migration_ok)],
)
async def manual_split(name: str, req: ManualSplitRequest, _t: Translator) -> dict[str, Any]:
    """手工切分：切分、拆分、移动分界、与下一集合并、清除之后的切分，直接写入分集账本。

    波及有产物的集（或 ``dry_run``）时返回 ``status=confirmation_required`` 与服务端成文的确认清单 ``impact.text``，
    不写入；创作者确认后带上 ``confirm_episodes`` 重新提交。
    """

    def _sync() -> dict[str, Any]:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        with project_change_source("webui"):
            outcome = _run_manual_split(manager.get_project_path(name), req)
        impact = outcome.impact.to_dict()
        if isinstance(outcome, ManualSplitResult):
            return {"status": "applied", "episode": outcome.episode, "impact": impact}
        project = manager.load_project(name)
        text = render_manual_split_impact_text(impact, project, _t)
        return {"status": "confirmation_required", "impact": {**impact, "text": text}}

    try:
        return await asyncio.to_thread(_sync)
    except ManualSplitError as exc:
        raise HTTPException(
            status_code=_MANUAL_SPLIT_STATUS.get(exc.code, 409), detail=_t(f"manual_split_{exc.code}")
        ) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class SetSourceFileKindRequest(BaseModel):
    source_kind: SourceKind
    #: 已确认会让已开始制作的集的脚本规划判 stale。
    confirm: bool = False


@router.put(
    "/projects/{name}/source-files/{filename}/source-kind",
    dependencies=[Depends(require_project_migration_ok)],
)
async def set_source_file_kind(
    name: str, filename: str, req: SetSourceFileKindRequest, _t: Translator
) -> dict[str, Any]:
    """改整本源文文件的源文件类型（只对剧情演绎开放）；不改源文指纹，也不动分集账本。

    会让已开始制作的集的脚本规划判 stale 而 ``confirm`` 为 false 时不写入，返回 ``needs_confirmation``
    与这些集的集 ID（``affected_episodes``）。
    """

    def _sync() -> dict[str, Any]:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        with project_change_source("webui"):
            change = set_whole_source_file_kind(manager, name, filename, req.source_kind, confirm=req.confirm)
        return {
            "success": True,
            "applied": change.applied,
            "needs_confirmation": change.changed and not change.applied,
            "affected_episodes": change.affected_episodes,
        }

    try:
        return await asyncio.to_thread(_sync)
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t, filename=filename) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
