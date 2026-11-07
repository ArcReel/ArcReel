"""Project trash: list deletion records, restore, download recorded files, permanently delete, and empty. Permanent deletion/emptying are creator-only: no Agent tools, and the server rejects embedded-Agent tokens."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.material.formal_objects import reconcile_and_collect
from lib.project import project_trash
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from server.auth import reject_embedded_agent
from server.routers.file_view import content_media_type, stream_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_name}/trash")


def _project_dir(project_name: str) -> Path:
    try:
        return get_project_manager().get_project_path(project_name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


async def _collect_released_objects(project_name: str) -> None:
    """Permanently deleted records no longer retain formal objects; recompute project references and reclaim objects with no remaining references.

    Log failures only: records are already deleted, excess references merely delay reclamation, and maintenance recomputes them again.
    """
    try:
        await reconcile_and_collect(project_name, await asyncio.to_thread(_project_dir, project_name))
    except Exception:
        logger.warning("回收站清除后回收正式对象失败 project=%s", project_name, exc_info=True)


@router.get("")
async def list_trash(project_name: str):
    def _sync() -> dict[str, object]:
        records = project_trash.list_records(_project_dir(project_name))
        return {
            "records": [record.to_dict() for record in records],
            "total_size": sum(record.size for record in records),
            "retention_days": project_trash.RETENTION.days,
        }

    return await asyncio.to_thread(_sync)


@router.post("/{record_id}/restore")
async def restore_trash_record(project_name: str, record_id: str):
    def _sync() -> dict[str, object]:
        with project_change_source("webui"):
            return project_trash.restore(_project_dir(project_name), record_id).to_dict()

    return await run_sync_transaction(_sync)


@router.get("/{record_id}/files")
async def download_trash_record_file(project_name: str, record_id: str, path: str = Query(...)):
    """Download a deleted file from a record: retrieve structure files available only for download after degraded recovery here."""
    name = Path(path).name

    def _sync():
        handle, info = project_trash.open_record_file(_project_dir(project_name), record_id, path)
        return handle, info.st_size, content_media_type(name)

    handle, size, media_type = await asyncio.to_thread(_sync)
    return stream_file(handle, size, name, media_type, download=True)


@router.delete("/{record_id}", dependencies=[Depends(reject_embedded_agent)])
async def purge_trash_record(project_name: str, record_id: str):
    def _sync() -> None:
        with project_change_source("webui"):
            project_trash.purge(_project_dir(project_name), record_id)

    await run_sync_transaction(_sync)
    await _collect_released_objects(project_name)
    return {"success": True}


@router.delete("", dependencies=[Depends(reject_embedded_agent)])
async def empty_trash(project_name: str):
    def _sync() -> int:
        with project_change_source("webui"):
            return project_trash.empty(_project_dir(project_name))

    purged = await run_sync_transaction(_sync)
    await _collect_released_objects(project_name)
    return {"purged": purged}
