"""执行中任务对项目的认领。

任务按项目名定位项目目录。执行中的任务不可取消，删除项目时照常跑完；执行期间项目若被删除或被
覆盖导入替换，同一个名字之后可能指向另一个项目。删除或覆盖项目时作废该项目执行中任务的认领，
任务写产物与写回前经 :func:`ensure_task_project_claim` 复核，认领已作废就放弃落盘。

认领与作废都只在本进程内生效：删除项目的路由、导入项目的路由与生成 worker 运行在同一进程。
"""

from __future__ import annotations

import threading
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class ProjectDeletedDuringTaskError(RuntimeError):
    """任务认领的项目已在任务执行期间删除。"""

    def __init__(self, project_name: str) -> None:
        super().__init__(f"项目 '{project_name}' 已在任务执行期间删除")
        self.project_name = project_name


# 已作废认领的任务。删除项目时任务可能已被认领、但还没进入执行上下文，作废记录因此按任务
# 保存，由任务退出执行上下文时移除。
_revoked_task_ids: set[str] = set()
_revoked_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class TaskProjectClaim:
    task_id: str
    project_name: str | None

    @property
    def revoked(self) -> bool:
        with _revoked_lock:
            return self.task_id in _revoked_task_ids


_current_claim: ContextVar[TaskProjectClaim | None] = ContextVar("task_project_claim", default=None)


@contextmanager
def claim_task_project(task_id: str, project_name: str | None) -> Generator[TaskProjectClaim]:
    """在执行任务的上下文里认领项目。

    认领经 ``contextvars`` 传播，上下文内派生的协程与 ``asyncio.to_thread`` 线程都看得到。
    """
    claim = TaskProjectClaim(task_id, project_name)
    token = _current_claim.set(claim)
    try:
        yield claim
    finally:
        _current_claim.reset(token)
        with _revoked_lock:
            _revoked_task_ids.discard(task_id)


def revoke_task_project_claims(task_ids: Iterable[str]) -> None:
    """作废这些任务对项目的认领。"""
    with _revoked_lock:
        _revoked_task_ids.update(task_ids)


def restore_task_project_claims(task_ids: Iterable[str]) -> None:
    """恢复这些任务对项目的认领：作废它们的那次删除或覆盖没有完成，项目原样保留。

    在恢复之前已复核到作废的任务已经放弃落盘，不随之恢复。
    """
    with _revoked_lock:
        _revoked_task_ids.difference_update(task_ids)


def ensure_task_project_claim(project_name: str) -> None:
    """当前任务认领的正是 ``project_name`` 且认领已作废时，抛 :class:`ProjectDeletedDuringTaskError`。

    不在任务执行上下文里，或操作的是别的项目时不做任何事。
    """
    claim = _current_claim.get()
    if claim is not None and claim.project_name == project_name and claim.revoked:
        raise ProjectDeletedDuringTaskError(project_name)


__all__ = [
    "ProjectDeletedDuringTaskError",
    "TaskProjectClaim",
    "claim_task_project",
    "ensure_task_project_claim",
    "restore_task_project_claims",
    "revoke_task_project_claims",
]
