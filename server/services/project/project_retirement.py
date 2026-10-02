"""项目删除与覆盖导入共用的收尾。

删除项目与覆盖导入都让项目名下的现有项目不复存在：排队中的任务取消，任务、批次、调用记录与助手
会话改挂墓碑名，执行中的任务作废对项目的认领、放弃落盘。之后用这个名字的项目，无论是覆盖导入装进
来的，还是删除后新建或导入的，都不沿用这些记录。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.repositories.project_records import retire_project_records
from lib.infra.async_thread import EventLoopBridge, run_noninterruptible_async
from lib.project.task_project_claim import restore_task_project_claims, revoke_task_project_claims


@asynccontextmanager
async def retiring_project(
    session_factory: async_sessionmaker[AsyncSession], project_name: str
) -> AsyncGenerator[None]:
    """收尾项目名下的现有项目，``async with`` 块里删除或替换项目目录。

    进入时在一个事务里取消排队中的任务、把记录改挂墓碑名，并作废执行中任务的认领；块正常结束才
    提交，提交开始后不被取消打断。块或提交抛错时事务回滚、认领恢复，现有项目的记录保持原样，目录
    由块的调用方还原。
    """
    async with session_factory() as session:
        running_task_ids = await retire_project_records(session, project_name)
        revoke_task_project_claims(running_task_ids)
        try:
            yield
            await run_noninterruptible_async(session.commit())
        except BaseException:
            await session.rollback()
            restore_task_project_claims(running_task_ids)
            raise


async def retire_project(session_factory: async_sessionmaker[AsyncSession], project_name: str) -> None:
    """只收尾记录、不动目录。删除项目先收尾记录再删目录：删除中途失败时旧记录也不会留给之后同名的项目。"""
    async with retiring_project(session_factory, project_name):
        pass


type RetireProject = Callable[[str], AbstractContextManager[None]]
"""在工作线程里收尾 ``project_name`` 名下的现有项目，``with`` 块里替换项目目录，语义同 :func:`retiring_project`。"""


def retire_project_on(bridge: EventLoopBridge, session_factory: async_sessionmaker[AsyncSession]) -> RetireProject:
    """给同步的导入流程用的收尾：进出 ``with`` 块时投回 ``bridge`` 的事件循环执行，块本身留在调用线程。

    只能在工作线程里使用。
    """

    @contextmanager
    def _retire(project_name: str) -> Generator[None]:
        retirement = retiring_project(session_factory, project_name)
        bridge.run(retirement.__aenter__())
        try:
            yield
        except BaseException as exc:
            bridge.run(retirement.__aexit__(type(exc), exc, exc.__traceback__))
            raise
        bridge.run(retirement.__aexit__(None, None, None))

    return _retire


__all__ = ["RetireProject", "retire_project", "retire_project_on", "retiring_project"]
