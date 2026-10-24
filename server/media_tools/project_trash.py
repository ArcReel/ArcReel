"""Host-neutral project-trash tools: move an unreferenced file or a non-current history version into the project
trash, list deletion records, restore a record.

There is deliberately no purge or empty tool: those are irreversible and belong to the creator alone
(``docs/adr/0107``). Deletion reuses the file-view check, so the reference index is rebuilt under the
project lock before anything moves; the record's origin is the Agent because the change source is not ``webui``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as translate
from lib.infra.api_errors import ApiError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.project import project_trash
from lib.project.file_view import move_file_to_trash
from lib.project.project_change_hints import project_change_source
from server.media_tools.context import tool_error, tool_problem
from server.tool_runtime import CallerContext, NoArguments, ProjectScope, Services, ToolOutcome, ToolRequest


class DeleteProjectFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, description="项目内相对路径（正斜杠），如 storyboards/scene_E1S02.png")


class RestoreTrashRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(
        min_length=1, description="删除记录 ID，取自 list_trash_records 或 delete_project_file 的结果"
    )


async def _run(operation: str, write: bool, func: Callable[[], Any]) -> ToolOutcome[Any]:
    """在线程里执行回收站操作；领域异常按默认语言转述，问题码沿用异常的 i18n key。"""

    def guarded() -> Any:
        # 与生成工具同一来源标签：Agent 发起的变更不显示成外部文件改动，删除记录的发起方记为 Agent。
        with project_change_source("worker"):
            return func()

    try:
        value = await (run_sync_transaction(guarded) if write else asyncio.to_thread(guarded))
    except ApiError as exc:
        return tool_problem(translate(exc.key, DEFAULT_LOCALE, **exc.params), code=exc.key, params=exc.params or None)
    except Exception as exc:
        return tool_error(operation, exc)
    return ToolOutcome(value=value)


async def delete_project_file(
    request: ToolRequest[DeleteProjectFileRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    path = request.value.path

    def delete() -> dict[str, Any]:
        try:
            record = move_file_to_trash(services.projects.get_project_path(scope.project_name), path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise NotFoundError("file_not_found", path=path) from exc
        return {"record": record.to_dict()}

    return await _run("delete_project_file", True, delete)


async def list_trash_records(
    _request: ToolRequest[NoArguments],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    def list_all() -> dict[str, Any]:
        records = project_trash.list_records(services.projects.get_project_path(scope.project_name))
        return {
            "records": [record.to_dict() for record in records],
            "total_size": sum(record.size for record in records),
            "retention_days": project_trash.RETENTION.days,
        }

    return await _run("list_trash_records", False, list_all)


async def restore_trash_record(
    request: ToolRequest[RestoreTrashRecordRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    record_id = request.value.record_id

    def restore() -> dict[str, Any]:
        restored = project_trash.restore(services.projects.get_project_path(scope.project_name), record_id)
        return {"restored": [item.to_dict() for item in restored]}

    return await _run("restore_trash_record", True, restore)


def delete_project_file_summary(value: dict[str, Any]) -> str:
    record = value["record"]
    return f"已移入项目回收站：{'、'.join(record['files'])}（删除记录 {record['id']}，保留 {project_trash.RETENTION.days} 天）"


def restore_trash_record_summary(value: dict[str, Any]) -> str:
    lines = [
        f"{item['original']} → {item['path']}" + ("（作为历史版本加回）" if item["as_history"] else "")
        for item in value["restored"]
    ]
    return "已恢复：\n" + "\n".join(lines)


__all__ = [
    "DeleteProjectFileRequest",
    "RestoreTrashRecordRequest",
    "delete_project_file",
    "delete_project_file_summary",
    "list_trash_records",
    "restore_trash_record",
    "restore_trash_record_summary",
]
