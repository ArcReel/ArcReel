"""「分集」视图：整本源文按集分段的只读投影、``source/`` 里没有登记的文件的处置，以及整本源文文件的类型、
替换、编辑、删除与调序。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
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
from lib.episode.episode_replan import replan_candidate_summary
from lib.episode.episode_source_commands import (
    EpisodeSourceError,
    adopt_source_file_as_episode,
    adopt_source_file_as_whole_source,
    set_whole_source_file_kind,
)
from lib.episode.source_file_changes import (
    MoveDirection,
    SourceFileChangeError,
    SourceFileChangeOutcome,
    delete_whole_source_file,
    edit_whole_source_file,
    move_whole_source_file,
    replace_whole_source_file,
)
from lib.episode.source_kinds import SourceKind
from lib.infra.api_errors import ApiError, NotFoundError
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager, get_project_manager
from server.dependencies import require_project_migration_ok
from server.i18n import Translator
from server.routers._episode_source_errors import episode_source_http_error
from server.routers._source_file_changes import (
    ensure_no_displaced_tasks,
    source_file_change_http_error,
    source_file_change_payload,
)
from server.routers._source_uploads import extract_uploaded_source_text
from server.routers.episode_management import episode_has_active_tasks

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/projects/{name}/episodes-view")
async def get_episodes_view(name: str, _t: Translator) -> dict[str, Any]:
    """整本源文按集分段、每集的体量与首尾句、``source/`` 里没有登记的文本文件，以及新的分集方案 ``replan``。

    ``replan`` 是悬而未决的重新规划候选的摘要与逐集变化（见 :func:`lib.episode.episode_replan.replan_candidate_summary`），
    没有候选时为 null。
    """

    def _sync() -> dict[str, Any]:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        project = manager.load_project(name)
        project_dir = manager.get_project_path(name)
        return {
            **asdict(build_episode_layout(project_dir, project)),
            "replan": replan_candidate_summary(project_dir, project),
        }

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
    #: ``at`` 所在的整本源文文件；缺省时按这一集起点所在的文件算。
    source_file: str | None = None


class MoveBoundaryRequest(_ManualSplitBase):
    action: Literal["move_boundary"]
    episode: int
    at: int
    #: ``at`` 所在的整本源文文件；缺省时按这一集终点所在的文件算。
    source_file: str | None = None


class MergeNextRequest(_ManualSplitBase):
    action: Literal["merge_next"]
    episode: int
    #: 创作者在确认清单里看过的并入体量；与锁内复核出的体量不一致时退回确认。
    confirm_merged_units: int = 0


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
        return split_episode(project_dir, req.episode, at=req.at, source_file=req.source_file, **options)
    if isinstance(req, MoveBoundaryRequest):
        return move_episode_boundary(project_dir, req.episode, at=req.at, source_file=req.source_file, **options)
    if isinstance(req, MergeNextRequest):
        return merge_with_next_episode(
            project_dir, req.episode, confirm_merged_units=req.confirm_merged_units, **options
        )
    return clear_cuts_after(project_dir, req.episode, **options)


@router.post(
    "/projects/{name}/episodes-view/manual-split",
    dependencies=[Depends(require_project_migration_ok)],
)
async def manual_split(name: str, req: ManualSplitRequest, _t: Translator) -> dict[str, Any]:
    """手工切分：切分、拆分、移动分界、与下一集合并、清除之后的切分，直接写入分集账本。

    波及有产物的集、合并会并入两集之间未切分的原文（或 ``dry_run``）时返回 ``status=confirmation_required`` 与
    服务端成文的确认清单 ``impact.text``，不写入；创作者确认后带上 ``confirm_episodes`` 与 ``confirm_merged_units``
    重新提交。要移除或转为无原文的集有排队或执行中的任务时返回 409，不写入。
    """

    def _displaced_episodes() -> list[int]:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        preview = _run_manual_split(manager.get_project_path(name), req.model_copy(update={"dry_run": True}))
        return [*preview.impact.retired, *preview.impact.removed]

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
        if not req.dry_run:
            for episode in await asyncio.to_thread(_displaced_episodes):
                if await episode_has_active_tasks(name, episode):
                    raise HTTPException(status_code=409, detail=_t("manual_split_tasks_active"))
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


# ---------------------------------------------------------------------------
# 整本源文文件的替换、编辑、删除与调序
# ---------------------------------------------------------------------------


class _SourceFileChangeBase(BaseModel):
    #: 创作者确认过的受影响集清单的版本；缺省或与锁内重算的不一致时只返回清单，不写入。
    revision: str | None = None


class EditSourceFileRequest(_SourceFileChangeBase):
    text: str


class MoveSourceFileRequest(_SourceFileChangeBase):
    direction: MoveDirection


class DeleteSourceFileRequest(_SourceFileChangeBase):
    pass


async def _run_source_file_change(
    name: str,
    _t: Translator,
    revision: str | None,
    command: Callable[[ProjectManager, bool], SourceFileChangeOutcome],
) -> dict[str, Any]:
    """``command(manager, dry_run)`` 跑一个整本源文文件改动命令，``revision`` 已经传给了它。"""

    def _manager() -> ProjectManager:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        return manager

    def _sync() -> dict[str, Any]:
        manager = _manager()
        project = manager.load_project(name)
        with project_change_source("webui"):
            outcome = command(manager, False)
        return source_file_change_payload(outcome, project, _t)

    try:
        await ensure_no_displaced_tasks(name, revision, lambda: command(_manager(), True), _t)
        return await asyncio.to_thread(_sync)
    except SourceFileChangeError as exc:
        raise source_file_change_http_error(exc, _t) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.put(
    "/projects/{name}/source-files/{filename}/text",
    dependencies=[Depends(require_project_migration_ok)],
)
async def edit_source_file(name: str, filename: str, req: EditSourceFileRequest, _t: Translator) -> dict[str, Any]:
    """编辑整本源文文件的全文，按改动前后的对齐重映射触及它的切出集。

    没有受影响的集时直接执行，返回 ``status=applied``；有受影响的集而 ``revision`` 缺省或已过时时不写入，
    返回 ``status=confirmation_required``、按类分组的受影响集清单（含服务端成文的 ``impact.text``）与 ``revision``。
    要移除或转为无原文的集有排队或执行中的任务时返回 409，不写入。
    """
    return await _run_source_file_change(
        name,
        _t,
        req.revision,
        lambda pm, dry_run: edit_whole_source_file(
            pm, name, filename, req.text, revision=req.revision, dry_run=dry_run
        ),
    )


@router.post(
    "/projects/{name}/source-files/{filename}/replace",
    dependencies=[Depends(require_project_migration_ok)],
)
async def replace_source_file(
    name: str,
    filename: str,
    _t: Translator,
    file: UploadFile = File(...),
    source_kind: Annotated[SourceKind | None, Form()] = None,
    revision: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """用上传的文件替换整本源文文件：保留位置与文件名，类型缺省时不变。

    没有受影响的集时直接执行，返回 ``status=applied``；有受影响的集而 ``revision`` 缺省或已过时时不写入，
    返回 ``status=confirmation_required``、按类分组的受影响集清单（含服务端成文的 ``impact.text``）与 ``revision``。
    要移除或转为无原文的集有排队或执行中的任务时返回 409，不写入。
    """
    text = await asyncio.to_thread(extract_uploaded_source_text, file, _t)
    return await _run_source_file_change(
        name,
        _t,
        revision,
        lambda pm, dry_run: replace_whole_source_file(
            pm, name, filename, text, source_kind=source_kind, revision=revision, dry_run=dry_run
        ),
    )


@router.post(
    "/projects/{name}/source-files/{filename}/move",
    dependencies=[Depends(require_project_migration_ok)],
)
async def move_source_file(name: str, filename: str, req: MoveSourceFileRequest, _t: Translator) -> dict[str, Any]:
    """把整本源文文件上移或下移一位，其中的切出集整块跟着走。

    没有受影响的集时直接执行，返回 ``status=applied``；有受影响的集而 ``revision`` 缺省或已过时时不写入，
    返回 ``status=confirmation_required``、按类分组的受影响集清单（含服务端成文的 ``impact.text``）与 ``revision``。
    要移除或转为无原文的集有排队或执行中的任务时返回 409，不写入。
    """
    return await _run_source_file_change(
        name,
        _t,
        req.revision,
        lambda pm, dry_run: move_whole_source_file(
            pm, name, filename, direction=req.direction, revision=req.revision, dry_run=dry_run
        ),
    )


@router.post(
    "/projects/{name}/source-files/{filename}/delete",
    dependencies=[Depends(require_project_migration_ok)],
)
async def delete_whole_source(name: str, filename: str, req: DeleteSourceFileRequest, _t: Translator) -> dict[str, Any]:
    """删除整本源文文件，等同于删掉它的全部文字。

    没有受影响的集时直接执行，返回 ``status=applied``；有受影响的集而 ``revision`` 缺省或已过时时不写入，
    返回 ``status=confirmation_required``、按类分组的受影响集清单（含服务端成文的 ``impact.text``）与 ``revision``。
    要移除或转为无原文的集有排队或执行中的任务时返回 409，不写入。
    """
    return await _run_source_file_change(
        name,
        _t,
        req.revision,
        lambda pm, dry_run: delete_whole_source_file(pm, name, filename, revision=req.revision, dry_run=dry_run),
    )
