"""「分集」视图：整本源文按集分段的只读投影，以及 ``source/`` 里没有登记的文件的处置。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lib.episode.episode_layout import build_episode_layout
from lib.episode.episode_source_commands import (
    EpisodeSourceError,
    adopt_source_file_as_episode,
    adopt_source_file_as_whole_source,
)
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
