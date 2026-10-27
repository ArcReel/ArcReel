"""项目删除与覆盖导入共用的收尾，以及已删除项目恢复时的记录改回。

删除项目与覆盖导入都让项目名下的现有项目成为已删除项目：排队中的任务取消，任务、批次、调用记录与助手
会话改挂墓碑名，执行中的任务作废对项目的认领、放弃落盘。之后用这个名字的项目，无论是覆盖导入装进
来的，还是删除后新建或导入的，都不沿用这些记录。已删除项目恢复时，记录从墓碑名改回恢复后的项目名；
原名已被占用而换名恢复时，助手会话的 Agent 上下文记录随项目目录改到新目录派生的存储键下。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.agent.agent_session_store import make_project_key
from lib.agent.agent_session_store.rekey import SessionRekey
from lib.db.repositories.project_records import (
    parse_deleted_project_name,
    restore_project_records,
    retire_project_records,
    retired_agent_session_ids,
)
from lib.infra.async_thread import EventLoopBridge, run_noninterruptible_async, run_sync_transaction
from lib.project.task_project_claim import restore_task_project_claims, revoke_task_project_claims


@asynccontextmanager
async def retiring_project(session_factory: async_sessionmaker[AsyncSession], project_name: str) -> AsyncGenerator[str]:
    """收尾项目名下的现有项目，``async with`` 块拿到墓碑名，在块里把项目目录移成同名的已删除项目。

    进入时在一个事务里取消排队中的任务、把记录改挂墓碑名，并作废执行中任务的认领；块正常结束才
    提交，提交开始后不被取消打断。块或提交抛错时事务回滚、认领恢复，现有项目的记录保持原样，目录
    由块的调用方还原。
    """
    async with session_factory() as session:
        tombstone, running_task_ids = await retire_project_records(session, project_name)
        revoke_task_project_claims(running_task_ids)
        try:
            yield tombstone
            await run_noninterruptible_async(session.commit())
        except BaseException:
            await session.rollback()
            restore_task_project_claims(running_task_ids)
            raise


async def restore_retired_project(
    session_factory: async_sessionmaker[AsyncSession],
    tombstone: str,
    project_dir: Path,
    *,
    sdk_config_dir: Path,
) -> None:
    """已删除项目恢复到 ``project_dir`` 后，把墓碑名 ``tombstone`` 下的记录改回恢复后的项目名并提交；提交开始后不被取消打断。

    恢复后的名字与删除前不同时，Agent 会话存储键（由项目目录的绝对路径派生）也须跟着换：同一事务里把这个项目
    的会话在数据库中的 transcript 镜像改到新目录的键下，提交后再移动 ``sdk_config_dir`` 下的 SDK 本地记录。原键
    此时归占用原名的项目，只按会话搬。
    """
    async with session_factory() as session:
        rekey = await _session_rekey(session, tombstone, project_dir)
        if rekey is not None:
            await rekey.rewrite_records(session)
        await restore_project_records(session, tombstone, project_dir.name)
        await run_noninterruptible_async(session.commit())
    if rekey is not None:
        await run_sync_transaction(rekey.move_local_transcripts, sdk_config_dir)


async def _session_rekey(session: AsyncSession, tombstone: str, project_dir: Path) -> SessionRekey | None:
    parsed = parse_deleted_project_name(tombstone)
    if parsed is None or parsed[0] == project_dir.name:
        return None
    return SessionRekey.of(
        await retired_agent_session_ids(session, tombstone),
        make_project_key(project_dir),
        source_key=make_project_key(project_dir.parent / parsed[0]),
    )


type RetireProject = Callable[[str], AbstractContextManager[str]]
"""在工作线程里收尾 ``project_name`` 名下的现有项目，``with`` 块里替换项目目录，语义同 :func:`retiring_project`。"""


def retire_project_on(bridge: EventLoopBridge, session_factory: async_sessionmaker[AsyncSession]) -> RetireProject:
    """给同步的导入流程用的收尾：进出 ``with`` 块时投回 ``bridge`` 的事件循环执行，块本身留在调用线程。

    只能在工作线程里使用。
    """

    @contextmanager
    def _retire(project_name: str) -> Generator[str]:
        retirement = retiring_project(session_factory, project_name)
        tombstone = bridge.run(retirement.__aenter__())
        try:
            yield tombstone
        except BaseException as exc:
            bridge.run(retirement.__aexit__(type(exc), exc, exc.__traceback__))
            raise
        bridge.run(retirement.__aexit__(None, None, None))

    return _retire


__all__ = ["RetireProject", "restore_retired_project", "retire_project_on", "retiring_project"]
