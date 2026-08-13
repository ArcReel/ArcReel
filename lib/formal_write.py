"""Rollback support for multi-file formal artifact commits."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from lib.json_io import atomic_write_bytes


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    path: Path
    content: bytes | None


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
        identity = path.resolve(strict=False)
        if identity in seen:
            continue
        seen.add(identity)
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
                if snapshot.content is None:
                    snapshot.path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(snapshot.path, snapshot.content)
            except OSError as exc:
                rollback_errors.append(exc)
        if rollback_errors:
            rollback_errors[0].__cause__ = failure
            raise RuntimeError("formal write failed and durable rollback was incomplete") from rollback_errors[0]
        raise


__all__ = ["formal_write_transaction"]
