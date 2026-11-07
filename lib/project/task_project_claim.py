"""Project claims held by executing tasks.

Tasks locate directories by project name. Executing tasks cannot cancel and finish after project
deletion; deletion/overwrite during execution may make that name identify another project later.
Revoke executing claims on deletion/overwrite, then recheck via :func:`ensure_task_project_claim`
before artifact writes/writeback; revoked tasks discard persistence.

Claims/revocation apply only in-process: deletion/import routes and generation workers share one process."""

from __future__ import annotations

import threading
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class ProjectDeletedDuringTaskError(RuntimeError):
    """The task-claimed project was deleted during execution."""

    def __init__(self, project_name: str) -> None:
        super().__init__(f"项目 '{project_name}' 已在任务执行期间删除")
        self.project_name = project_name


# Tasks with revoked claims: deletion may happen after task claiming but before execution context entry, so retain per-task
# revocations until execution-context exit.
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
    """Claim a project in task-execution context.

    ``contextvars`` propagates claims to derived coroutines and ``asyncio.to_thread`` workers."""
    claim = TaskProjectClaim(task_id, project_name)
    token = _current_claim.set(claim)
    try:
        yield claim
    finally:
        _current_claim.reset(token)
        with _revoked_lock:
            _revoked_task_ids.discard(task_id)


def revoke_task_project_claims(task_ids: Iterable[str]) -> None:
    """Revoke these tasks' project claims."""
    with _revoked_lock:
        _revoked_task_ids.update(task_ids)


def restore_task_project_claims(task_ids: Iterable[str]) -> None:
    """Restore claims when their revoking deletion/overwrite failed and left the original project intact.

    Tasks already observing revocation and discarding persistence are not revived."""
    with _revoked_lock:
        _revoked_task_ids.difference_update(task_ids)


def ensure_task_project_claim(project_name: str) -> None:
    """Raise :class:`ProjectDeletedDuringTaskError` if this task claims ``project_name`` and its claim is revoked.

    Do nothing outside task-execution context or for operations on other projects."""
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
