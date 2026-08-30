"""Rollback support for multi-file formal artifact commits."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import portalocker

from lib.json_io import atomic_write_bytes


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    path: Path
    content: bytes | None
    symlink_target: str | None = None
    symlink_is_directory: bool = False


@dataclass(frozen=True, slots=True)
class FormalWriteReceipt:
    """Restore a committed file set only while it still matches this write."""

    before: tuple[_FileSnapshot, ...]
    committed: tuple[_FileSnapshot, ...]

    def matches_current(self) -> bool:
        return tuple(_snapshot_file(item.path) for item in self.committed) == self.committed

    def compensate_cancelled(self) -> bool:
        if not self.matches_current():
            return False
        _restore_snapshots(self.before)
        return True


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
                        snapshot.path.unlink()
                    snapshot.path.symlink_to(
                        snapshot.symlink_target,
                        target_is_directory=snapshot.symlink_is_directory,
                    )
            elif snapshot.content is None:
                if snapshot.path.exists() or snapshot.path.is_symlink():
                    snapshot.path.unlink()
            else:
                atomic_write_bytes(snapshot.path, snapshot.content)
        except OSError as exc:
            rollback_errors.append(exc)
    if rollback_errors:
        raise RuntimeError("durable rollback was incomplete") from rollback_errors[0]


@contextmanager
def project_metadata_lock(project_dir: Path) -> Generator[None]:
    """Serialize project metadata and formal-artifact transactions across processes."""

    lock_path = Path(project_dir) / ".project.json.lock"
    lock_path.touch(exist_ok=True)
    with portalocker.Lock(lock_path, flags=portalocker.LOCK_EX):
        yield


@contextmanager
def formal_write_transaction(
    *paths: Path,
    cancellation_receipts: list[FormalWriteReceipt] | None = None,
) -> Generator[None]:
    """Restore exact pre-write bytes when a formal multi-file commit fails.

    Callers must hold the domain locks that serialize writes to ``paths`` for
    the whole context.  The context deliberately knows nothing about Artifact
    Manifest storage: its registration methods compensate their own writes,
    while this seam compensates the formal files surrounding that registration.
    """

    snapshots: list[_FileSnapshot] = []
    seen: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        # Resolve only the parent: resolving the final component would collapse
        # two distinct symlink entries onto one external target and leave one of
        # them outside rollback coverage.
        identity = path.parent.resolve(strict=False) / path.name
        if identity in seen:
            continue
        seen.add(identity)
        snapshots.append(_snapshot_file(path))

    try:
        yield
    except BaseException as failure:
        try:
            _restore_snapshots(tuple(snapshots))
        except RuntimeError as rollback_error:
            rollback_error.__cause__ = failure
            raise RuntimeError("formal write failed and durable rollback was incomplete") from rollback_error
        raise
    else:
        if cancellation_receipts is not None:
            cancellation_receipts.append(
                FormalWriteReceipt(
                    before=tuple(snapshots),
                    committed=tuple(_snapshot_file(snapshot.path) for snapshot in snapshots),
                )
            )


__all__ = ["FormalWriteReceipt", "formal_write_transaction", "project_metadata_lock"]
