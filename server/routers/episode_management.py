"""Web entry point for episode creation, reordering and deletion."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from lib.episode.episode_deletion import EpisodeDeletionResult, delete_episode, render_episode_deletion_text
from lib.episode.episode_management import EpisodeManagementError, create_episode, move_episode
from lib.episode.episode_source_commands import EpisodeSourceError
from lib.episode.source_kinds import SourceKind
from lib.generation.generation_queue import get_generation_queue
from lib.infra.api_errors import ApiError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager, get_project_manager
from server.dependencies import require_project_migration_ok
from server.i18n import Translator
from server.routers._episode_source_errors import episode_source_http_error
from server.services.tasks.episode_activity import episode_has_active_tasks as episode_tasks_active

logger = logging.getLogger(__name__)

router = APIRouter()

_STATUS: dict[str, int] = {"episode_not_found": 404}


class CreateEpisodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Episode ID to insert after; omit to append in broadcast order.
    after: int | None = None
    title: Annotated[str, Field(max_length=200)] = ""
    hook: Annotated[str, Field(max_length=2000)] = ""
    #: Episode source text; whitespace counts as absent.
    source_text: str | None = None
    #: Episode source kind, effective only for drama projects; defaults to novel.
    source_kind: SourceKind | None = None


class DeleteEpisodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ``revision`` of the loss list seen by the creator; missing or mismatched revisions return the list without deleting.
    revision: str | None = None


class MoveEpisodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Episode ID to move after; null moves to the beginning.
    after: int | None


async def _run[T](name: str, _t: Translator, action: Callable[[ProjectManager], T], *, episode: int | None = None) -> T:
    def _sync() -> T:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        with project_change_source("webui"):
            return action(manager)

    try:
        return await run_sync_transaction(_sync)
    except EpisodeManagementError as exc:
        raise HTTPException(status_code=_STATUS.get(exc.code, 409), detail=_t(f"episode_manage_{exc.code}")) from exc
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t, episode=episode) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post("/projects/{name}/episodes", dependencies=[Depends(require_project_migration_ok)], status_code=201)
async def create_episode_endpoint(name: str, req: CreateEpisodeRequest, _t: Translator) -> dict[str, Any]:
    """Create an episode: allocate a new episode ID and insert after ``after`` (default: append); supplied source text makes it an own-source episode."""
    episode = await _run(
        name,
        _t,
        lambda manager: create_episode(
            manager,
            name,
            after=req.after,
            title=req.title,
            hook=req.hook,
            source_text=req.source_text,
            source_kind=req.source_kind,
        ),
    )
    return {"success": True, "episode": episode}


@router.post("/projects/{name}/episodes/{episode}/move", dependencies=[Depends(require_project_migration_ok)])
async def move_episode_endpoint(name: str, episode: int, req: MoveEpisodeRequest, _t: Translator) -> dict[str, Any]:
    """Reorder broadcasting: move this episode after ``after``. Preserve source order among extracted episodes."""
    await _run(name, _t, lambda manager: move_episode(manager, name, episode, after=req.after), episode=episode)
    return {"success": True}


async def episode_has_active_tasks(project_name: str, episode: int) -> bool:
    """Whether this episode has queued/running tasks: the task resource, script filename or payload carries this episode’s ID."""
    return await episode_tasks_active(get_generation_queue(), project_name, episode)


@router.post("/projects/{name}/episodes/{episode}/delete", dependencies=[Depends(require_project_migration_ok)])
async def delete_episode_endpoint(name: str, episode: int, req: DeleteEpisodeRequest, _t: Translator) -> dict[str, Any]:
    """Delete an episode permanently.

    First call without ``revision`` for ``status=confirmation_required`` and the server-written loss list ``impact.text``.
    After creator confirmation, resubmit with ``impact.revision``. If the list changed between calls, request confirmation again without deleting."""

    if req.revision is not None and await episode_has_active_tasks(name, episode):
        raise HTTPException(status_code=409, detail=_t("episode_manage_tasks_active"))

    def _delete(manager: ProjectManager) -> dict[str, Any]:
        outcome = delete_episode(manager, name, episode, revision=req.revision)
        impact = outcome.impact.to_dict()
        if isinstance(outcome, EpisodeDeletionResult):
            return {"status": "deleted", "impact": impact}
        text = render_episode_deletion_text(impact, manager.load_project(name), _t)
        return {"status": "confirmation_required", "impact": {**impact, "text": text}}

    return await _run(name, _t, _delete, episode=episode)
