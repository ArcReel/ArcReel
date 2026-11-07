"""Host-neutral project-trash tools: move an unreferenced file or a non-current history version into the project
trash, list deletion records, restore a record.

There is deliberately no purge or empty tool: those are irreversible and belong to the creator alone
(``docs/adr/0107``). Deletion reuses the file-view check, so the reference index is rebuilt under the
project lock before anything moves; the record's origin is the Agent because the change source is not ``webui``.
A remote history version whose local cache was cleared is fetched first, exactly as in the file view.
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
from lib.project.file_view import local_file, move_file_to_trash
from lib.project.project_change_hints import project_change_source
from server.media_tools.context import tool_error, tool_problem
from server.tool_runtime import CallerContext, NoArguments, ProjectScope, Services, ToolOutcome, ToolRequest


class DeleteProjectFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(
        min_length=1, description="Project-relative path (forward slashes), for example storyboards/scene_E1S02.png"
    )


class RestoreTrashRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(
        min_length=1, description="Deletion record ID from list_trash_records or delete_project_file results"
    )


async def _run(operation: str, write: bool, func: Callable[[], Any]) -> ToolOutcome[Any]:
    """Run trash operations in a thread; relay domain exceptions in the default language, retaining their i18n keys as problem codes."""

    def guarded() -> Any:
        # Use the generation-tool origin: Agent changes are not shown as external file changes; deletion records identify the Agent as initiator.
        with project_change_source("worker"):
            return func()

    try:
        value = await (run_sync_transaction(guarded) if write else asyncio.to_thread(guarded))
    except ApiError as exc:
        return _problem(exc)
    except Exception as exc:
        return tool_error(operation, exc)
    return ToolOutcome(value=value)


def _problem(exc: ApiError) -> ToolOutcome[Any]:
    return tool_problem(translate(exc.key, DEFAULT_LOCALE, **exc.params), code=exc.key, params=exc.params or None)


async def delete_project_file(
    request: ToolRequest[DeleteProjectFileRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    path = request.value.path
    try:
        project_dir = await asyncio.to_thread(services.projects.get_project_path, scope.project_name)
    except (FileNotFoundError, NotADirectoryError):
        return _problem(NotFoundError("file_not_found", path=path))
    except Exception as exc:
        return tool_error("delete_project_file", exc)

    def delete() -> dict[str, Any]:
        try:
            record = move_file_to_trash(project_dir, path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise NotFoundError("file_not_found", path=path) from exc
        return {"record": record.to_dict()}

    try:
        # Retrieve remote historical versions after cache cleanup so deletion records retain content, using the same entry point as file views.
        async with local_file(project_dir, path, deleting=True):
            return await _run("delete_project_file", True, delete)
    except ApiError as exc:
        return _problem(exc)


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
        project_dir = services.projects.get_project_path(scope.project_name)
        # Compare object descriptions at deletion and restoration to explain changed names or IDs.
        original = dict(project_trash.require_record(project_dir, record_id).object)
        return {**project_trash.restore(project_dir, record_id).to_dict(), "original_object": original}

    return await _run("restore_trash_record", True, restore)


def delete_project_file_summary(value: dict[str, Any]) -> str:
    record = value["record"]
    return f"已移入项目回收站：{'、'.join(record['files'])}（删除记录 {record['id']}，保留 {project_trash.RETENTION.days} 天）"


def _identity_notes(value: dict[str, Any]) -> list[str]:
    """Explain changed names or IDs after object restoration; omit this for degraded restorations that do not preserve original structure."""
    original, landed = value.get("original_object", {}), value["object"]
    notes = [
        f"{label}已换：{original[key]} → {landed[key]}"
        for key, label in (("name", "名称"), ("id", "编号"))
        if key in original and landed.get(key) != original[key]
    ]
    if landed.get("kind") == "episode":
        notes.append("这一集排在播出顺序末尾")
    return notes


def restore_trash_record_summary(value: dict[str, Any]) -> str:
    lines = [
        f"{item['original']} → {item['path']}" + ("（作为历史版本加回）" if item["as_history"] else "")
        for item in value["restored"]
    ]
    if not value.get("degraded"):
        return "已恢复：\n" + "\n".join([*lines, *_identity_notes(value)])
    kept = "、".join(value["download_only"])
    return (
        "无法按原结构恢复，只加回了其中的文件"
        + ("：\n" + "\n".join(lines) if lines else "（没有可加回的文件）")
        + (f"\n结构文件 {kept} 留在这条删除记录里，只能由创作者在回收站下载" if kept else "")
    )


__all__ = [
    "DeleteProjectFileRequest",
    "RestoreTrashRecordRequest",
    "delete_project_file",
    "delete_project_file_summary",
    "list_trash_records",
    "restore_trash_record",
    "restore_trash_record_summary",
]
