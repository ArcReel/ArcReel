"""Rollback support for multi-file formal artifact commits."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

import portalocker

from lib.infra.json_io import atomic_write_bytes
from lib.infra.project_write_journal import (
    active_journal,
    has_sqlite_history,
    is_history_database_path,
    project_operation,
    track_write,
)
from lib.project.task_project_claim import ensure_task_project_claim


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    path: Path
    content: bytes | None
    symlink_target: str | None = None
    symlink_is_directory: bool = False


def _snapshot_file(path: Path) -> _FileSnapshot:
    if path.is_symlink():
        return _FileSnapshot(
            path=path,
            content=None,
            symlink_target=os.readlink(path),
            symlink_is_directory=path.is_dir(),
        )
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        content = None
    return _FileSnapshot(path=path, content=content)


def _restore_snapshots(snapshots: tuple[_FileSnapshot, ...]) -> None:
    rollback_errors: list[OSError] = []
    for snapshot in reversed(snapshots):
        try:
            if snapshot.symlink_target is not None:
                unchanged = snapshot.path.is_symlink() and os.readlink(snapshot.path) == snapshot.symlink_target
                if not unchanged:
                    if snapshot.path.exists() or snapshot.path.is_symlink():
                        snapshot.path.unlink()  # hard-delete: rollback -- 正式写入回滚：移除事务中换上的符号链接
                    snapshot.path.symlink_to(
                        snapshot.symlink_target,
                        target_is_directory=snapshot.symlink_is_directory,
                    )
            elif snapshot.content is None:
                if snapshot.path.exists() or snapshot.path.is_symlink():
                    snapshot.path.unlink()  # hard-delete: rollback -- 正式写入回滚：移除事务前不存在的文件
            else:
                atomic_write_bytes(snapshot.path, snapshot.content)
        except OSError as exc:
            rollback_errors.append(exc)
    if rollback_errors:
        raise RuntimeError("durable rollback was incomplete") from rollback_errors[0]


@contextmanager
def project_metadata_lock(project_dir: Path, *, journal: bool = True) -> Generator[None]:
    """Serialize project metadata and formal-artifact transactions across processes.

    Inside a task whose project was deleted while it ran, raises
    ``ProjectDeletedDuringTaskError`` before touching the directory.
    """

    ensure_task_project_claim(Path(project_dir).name)
    if active_journal(project_dir) is not None:
        yield
        return
    lock_path = Path(project_dir) / ".project.json.lock"
    lock_path.touch(exist_ok=True)
    with (
        portalocker.Lock(lock_path, flags=portalocker.LOCK_EX),
        project_operation(project_dir) if journal else nullcontext(),
    ):
        yield


@contextmanager
def formal_write_transaction(*paths: Path) -> Generator[None]:
    """Restore exact pre-write bytes when a formal multi-file commit fails.

    Callers must hold the domain locks that serialize writes to ``paths`` for
    the whole context.  The context deliberately knows nothing about Artifact
    Manifest storage: its registration methods compensate their own writes,
    while this seam compensates the formal files surrounding that registration.
    """

    snapshots: list[_FileSnapshot] = []
    seen: set[Path] = set()
    sqlite_projects: list[Path] = []
    seen_sqlite: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if is_history_database_path(path):
            raise ValueError("cannot snapshot live history database")
        if path.parent.name == "versions" and path.name == "versions.json":
            project_dir = path.parent.parent
            if has_sqlite_history(project_dir):
                resolved_project = project_dir.resolve(strict=False)
                if resolved_project not in seen_sqlite:
                    seen_sqlite.add(resolved_project)
                    sqlite_projects.append(project_dir)
                continue
        identity = path.parent.resolve(strict=False) / path.name
        if identity in seen:
            continue
        track_write(path)
        seen.add(identity)
        snapshots.append(_snapshot_file(path))

    with ExitStack() as stack:
        if sqlite_projects:
            from lib.artifacts.sqlite_history import SQLiteHistory

            for project_dir in sqlite_projects:
                stack.enter_context(SQLiteHistory(project_dir).rollback_scope())
        try:
            yield
        except BaseException as failure:
            try:
                _restore_snapshots(tuple(snapshots))
            except RuntimeError as rollback_error:
                rollback_error.__cause__ = failure
                raise RuntimeError("formal write failed and durable rollback was incomplete") from rollback_error
            raise


__all__ = ["formal_write_transaction", "project_metadata_lock"]
