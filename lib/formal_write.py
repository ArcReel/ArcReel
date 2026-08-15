"""Rollback support for multi-file formal artifact commits."""

from __future__ import annotations

import os
from collections.abc import Iterator
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


@contextmanager
def project_metadata_lock(project_dir: Path) -> Iterator[None]:
    """Serialize project metadata and formal-artifact transactions across processes."""

    lock_path = Path(project_dir) / ".project.json.lock"
    lock_path.touch(exist_ok=True)
    with portalocker.Lock(lock_path, flags=portalocker.LOCK_EX):
        yield


@contextmanager
def formal_write_transaction(*paths: Path) -> Iterator[None]:
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
        if path.is_symlink():
            snapshots.append(
                _FileSnapshot(
                    path=path,
                    content=None,
                    symlink_target=os.readlink(path),
                    symlink_is_directory=path.is_dir(),
                )
            )
            continue
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            content = None
        snapshots.append(_FileSnapshot(path=path, content=content))

    try:
        yield
    except BaseException as failure:
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
            rollback_errors[0].__cause__ = failure
            raise RuntimeError("formal write failed and durable rollback was incomplete") from rollback_errors[0]
        raise


__all__ = ["formal_write_transaction", "project_metadata_lock"]
