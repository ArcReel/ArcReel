"""项目回收站：列出删除记录、恢复、彻底删除与清空。彻底删除与清空只开放给创作者：不给 Agent 工具，服务端也拒绝内嵌 Agent 的令牌。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends

from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.project import project_trash
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from server.auth import reject_embedded_agent

router = APIRouter(prefix="/projects/{project_name}/trash")


def _project_dir(project_name: str) -> Path:
    try:
        return get_project_manager().get_project_path(project_name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


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
            restored = project_trash.restore(_project_dir(project_name), record_id)
        return {"restored": [item.to_dict() for item in restored]}

    return await run_sync_transaction(_sync)


@router.delete("/{record_id}", dependencies=[Depends(reject_embedded_agent)])
async def purge_trash_record(project_name: str, record_id: str):
    def _sync() -> None:
        with project_change_source("webui"):
            project_trash.purge(_project_dir(project_name), record_id)

    await run_sync_transaction(_sync)
    return {"success": True}


@router.delete("", dependencies=[Depends(reject_embedded_agent)])
async def empty_trash(project_name: str):
    def _sync() -> int:
        with project_change_source("webui"):
            return project_trash.empty(_project_dir(project_name))

    return {"purged": await run_sync_transaction(_sync)}
