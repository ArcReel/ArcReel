"""已删除项目留在数据库里的记录。

任务、批次、调用记录与助手会话只按项目名关联项目。项目删除后这些行改挂到墓碑名上：费用统计仍按
已删除项目保留，同名新建或导入的项目查不到它们，活动任务的去重也不会把新请求并到旧任务上。
墓碑名带 ``#``，项目名只允许字母、数字与中划线，墓碑名不会与任何项目同名。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.base import utc_now
from lib.db.models.api_call import ApiCall
from lib.db.models.session import AgentSession
from lib.db.models.task import GenerationBatch, Task

_DELETED_MARKER = "#deleted-"


def _deleted_project_name(project_name: str, deleted_at: datetime) -> str:
    """项目在 ``deleted_at``（UTC）删除后，其记录改挂的墓碑名，如 ``demo#deleted-20261002T034309Z``。

    前端用量页按这个形式解析出原名与删除日期，改格式时同步 ``frontend/src/components/usage/usage-record-format.ts``。
    """
    return f"{project_name}{_DELETED_MARKER}{deleted_at:%Y%m%dT%H%M%SZ}"


def is_deleted_project_name(project_name: str) -> bool:
    """``project_name`` 是否为已删除项目的墓碑名。墓碑名不是合法项目名，按它解析不出项目目录。"""
    return _DELETED_MARKER in project_name


async def retire_project_records(session: AsyncSession, project_name: str) -> list[str]:
    """在 ``session`` 的事务里取消项目排队中的任务，并把项目的全部记录改挂到墓碑名，返回执行中任务的 id。

    不提交：调用方提交事务，取消与改名一起生效，读侧看不到只做了一半的记录。执行中的任务不可取消，
    照常跑完，结算落在墓碑名下；调用方据返回的 id 作废它们对项目的认领，让它们放弃落盘。不按用户
    区分：项目目录本身不分用户。
    """
    now = utc_now()
    tombstone = _deleted_project_name(project_name, now)
    await session.execute(
        update(Task)
        .where(Task.project_name == project_name, Task.status == "queued")
        .values(status="cancelled", cancelled_by="user", finished_at=now, updated_at=now)
    )
    # 在取消排队任务之后读：与之并发的认领要么已把任务翻成 running，要么只能看到它已取消。
    running = await session.execute(
        select(Task.task_id).where(Task.project_name == project_name, Task.status == "running")
    )
    running_task_ids = list(running.scalars())
    for model in (Task, GenerationBatch, ApiCall, AgentSession):
        await session.execute(update(model).where(model.project_name == project_name).values(project_name=tombstone))
    return running_task_ids


__all__ = ["is_deleted_project_name", "retire_project_records"]
