"""Episode view: read-only whole-source episode segmentation, handling unregistered ``source/`` files, whole-source file
types/replacement/editing/deletion/reordering, and ledger updates after external file changes."""

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
    accept_external_source_change,
    delete_whole_source_file,
    edit_whole_source_file,
    move_whole_source_file,
    replace_whole_source_file,
)
from lib.episode.source_kinds import SourceKind
from lib.infra.api_errors import ApiError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
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
    """Whole-source episode segments, per-episode volume/first-last sentences, unregistered ``source/`` text files, a new
    ``replan`` and externally modified ``external_changes`` files.

    ``replan`` contains unresolved candidate summaries/per-episode changes (:func:`lib.episode.episode_replan.replan_candidate_summary`),
    or null when absent. ``external_changes`` lists ``changed_outside`` files in file order. ``impact`` is the snapshot-aligned
    affected-episode list including server-written ``text``; pass ``revision`` unchanged to ``accept-external`` to confirm.
    If impact cannot be computed, both are null and ``problem`` gives the reason."""

    def _external_change(manager: ProjectManager, project: dict[str, Any], filename: str) -> dict[str, Any]:
        try:
            outcome = accept_external_source_change(manager, name, filename, dry_run=True)
        except SourceFileChangeError as exc:
            return {
                "source_file": f"source/{filename}",
                "impact": None,
                "revision": None,
                "problem": _t(f"source_file_change_{exc.code}"),
            }
        payload = source_file_change_payload(outcome, project, _t)
        return {
            "source_file": f"source/{filename}",
            "impact": payload["impact"],
            "revision": payload["revision"],
            "problem": None,
        }

    def _sync() -> dict[str, Any]:
        manager = get_project_manager()
        if not manager.project_exists(name):
            raise NotFoundError("project_not_found", name=name)
        project = manager.load_project(name)
        project_dir = manager.get_project_path(name)
        layout = build_episode_layout(project_dir, project)
        return {
            **asdict(layout),
            "replan": replan_candidate_summary(project_dir, project),
            "external_changes": [
                _external_change(manager, project, file.name) for file in layout.files if file.changed_outside
            ],
        }

    try:
        return await run_sync_transaction(_sync)
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class AdoptSourceFileRequest(BaseModel):
    #: whole_source appends to whole-source text; episode uses it as episode source.
    target: Literal["whole_source", "episode"]
    #: target=episode assigns to this sourceless episode; omission appends a new episode in broadcast order.
    episode: int | None = None


@router.post(
    "/projects/{name}/source-files/{filename}/adopt",
    dependencies=[Depends(require_project_migration_ok)],
)
async def adopt_source_file(name: str, filename: str, req: AdoptSourceFileRequest, _t: Translator) -> dict[str, Any]:
    """Handle unregistered ``source/`` files by adding to whole source or assigning as episode source, deleting the original file afterward."""

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
        return await run_sync_transaction(_sync)
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t, episode=req.episode, filename=filename) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


class _ManualSplitBase(BaseModel):
    #: Episodes with artifacts reviewed by the creator; extra such episodes found under lock require confirmation again.
    confirm_episodes: list[int] = Field(default_factory=list)
    #: Return impact only, without writing.
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
    #: Whole-source file containing ``at``; default to the file containing this episode’s start.
    source_file: str | None = None


class MoveBoundaryRequest(_ManualSplitBase):
    action: Literal["move_boundary"]
    episode: int
    at: int
    #: Whole-source file containing ``at``; default to the file containing this episode’s end.
    source_file: str | None = None


class MergeNextRequest(_ManualSplitBase):
    action: Literal["merge_next"]
    episode: int
    #: Merged volume reviewed by the creator; different volume on locked recheck requires confirmation again.
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
    """Manual segmentation: cut, split, move boundaries, merge with the next episode or clear later splits, writing directly to the ledger.

    Affected episodes with artifacts, merges incorporating unsplit text between episodes, or ``dry_run`` return
    ``status=confirmation_required`` with server-written ``impact.text``, without writing. After confirmation, resubmit
    ``confirm_episodes`` / ``confirm_merged_units``. Queued/running tasks in removed or newly sourceless episodes return 409 without writing."""

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
        return await run_sync_transaction(_sync)
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
    #: Confirmed that plans for episodes already in production will become stale.
    confirm: bool = False


@router.put(
    "/projects/{name}/source-files/{filename}/source-kind",
    dependencies=[Depends(require_project_migration_ok)],
)
async def set_source_file_kind(
    name: str, filename: str, req: SetSourceFileKindRequest, _t: Translator
) -> dict[str, Any]:
    """Change whole-source file kind for drama only, without changing source fingerprints or the episode ledger.

    If plans for episodes already in production would become stale and ``confirm`` is false, write nothing and return
    ``needs_confirmation`` plus their IDs in ``affected_episodes``."""

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
        return await run_sync_transaction(_sync)
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t, filename=filename) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


# ---------------------------------------------------------------------------
# Whole-source file replacement, editing, deletion and reordering.
# ---------------------------------------------------------------------------


class _SourceFileChangeBase(BaseModel):
    #: Creator-confirmed affected-list revision; absent/mismatched locked recalculation returns the list without writing.
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
    """``command(manager, dry_run)`` runs a whole-source file-change command with ``revision`` already supplied."""

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
        return await run_sync_transaction(_sync)
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
    """Edit a whole-source file’s full text and remap affected extracted episodes using before/after alignment.

    Without affected episodes, execute directly and return ``status=applied``. Otherwise, missing/stale ``revision``
    writes nothing and returns ``status=confirmation_required``, categorized affected episodes with server-written
    ``impact.text``, and ``revision``. Queued/running tasks in removed or newly sourceless episodes return 409 without writing."""
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
    """Replace a whole-source file with an upload, preserving position/name and retaining kind if omitted.

    Without affected episodes, execute directly and return ``status=applied``. Otherwise, missing/stale ``revision``
    writes nothing and returns ``status=confirmation_required``, categorized affected episodes with server-written
    ``impact.text``, and ``revision``. Queued/running tasks in removed or newly sourceless episodes return 409 without writing."""
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
    """Move a whole-source file one position up/down, moving its extracted episodes together.

    Without affected episodes, execute directly and return ``status=applied``. Otherwise, missing/stale ``revision``
    writes nothing and returns ``status=confirmation_required``, categorized affected episodes with server-written
    ``impact.text``, and ``revision``. Queued/running tasks in removed or newly sourceless episodes return 409 without writing."""
    return await _run_source_file_change(
        name,
        _t,
        req.revision,
        lambda pm, dry_run: move_whole_source_file(
            pm, name, filename, direction=req.direction, revision=req.revision, dry_run=dry_run
        ),
    )


class AcceptExternalChangeRequest(_SourceFileChangeBase):
    pass


@router.post(
    "/projects/{name}/source-files/{filename}/accept-external",
    dependencies=[Depends(require_project_migration_ok)],
)
async def accept_external_change(
    name: str, filename: str, req: AcceptExternalChangeRequest, _t: Translator
) -> dict[str, Any]:
    """Update the ledger after external file changes, aligning snapshot old text against current new text and remapping affected extracted episodes.

    Without affected episodes, execute directly and return ``status=applied``. Otherwise missing/stale ``revision`` writes nothing
    and returns ``status=confirmation_required``, categorized affected episodes with server-written ``impact.text``, and
    ``revision``. Unchanged files or extracted episodes without snapshots return 409. Queued/running tasks in removed
    or newly sourceless episodes return 409 without writing."""
    return await _run_source_file_change(
        name,
        _t,
        req.revision,
        lambda pm, dry_run: accept_external_source_change(pm, name, filename, revision=req.revision, dry_run=dry_run),
    )


@router.post(
    "/projects/{name}/source-files/{filename}/delete",
    dependencies=[Depends(require_project_migration_ok)],
)
async def delete_whole_source(name: str, filename: str, req: DeleteSourceFileRequest, _t: Translator) -> dict[str, Any]:
    """Delete a whole-source file, equivalent to deleting all its text.

    Without affected episodes, execute directly and return ``status=applied``. Otherwise, missing/stale ``revision``
    writes nothing and returns ``status=confirmation_required``, categorized affected episodes with server-written
    ``impact.text``, and ``revision``. Queued/running tasks in removed or newly sourceless episodes return 409 without writing."""
    return await _run_source_file_change(
        name,
        _t,
        req.revision,
        lambda pm, dry_run: delete_whole_source_file(pm, name, filename, revision=req.revision, dry_run=dry_run),
    )
