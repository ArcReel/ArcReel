"""Versioned backups a migration step writes before touching one of its inputs.

Backup names are ``<file>.bak.v<from_version>-<timestamp>``; the runner's
cleanup recognises exactly this shape. Kept copies (``<file>.kept.v<N>-<ts>``)
hold the side a legacy-shape normalization set aside and are never cleaned up. A backup whose content already exists
for the same starting version is not written again, so a migration that keeps
failing on an unchanged project leaves one backup, not one per attempt.
"""

from __future__ import annotations

import time
from pathlib import Path

from lib.infra.json_io import atomic_write_bytes


def versioned_backup_name(base_name: str, from_version: int, ts: int) -> str:
    """例如 project.json → project.json.bak.v0-1712345678。"""

    return f"{base_name}.bak.v{from_version}-{ts}"


def versioned_backup_candidates(source: Path, versions: tuple[int, ...]) -> list[Path]:
    """Enumerate only backup names emitted for one migration-owned source."""

    candidates: list[Path] = []
    for version in versions:
        prefix = f"{source.name}.bak.v{version}-"
        candidates.extend(
            candidate for candidate in source.parent.glob(f"{prefix}*") if candidate.name.removeprefix(prefix).isdigit()
        )
    return candidates


def ensure_versioned_backup(source: Path, from_version: int) -> Path | None:
    """Back up ``source`` for a step starting at ``from_version`` unless an identical backup exists.

    Returns the backup that now holds the content (existing or new); ``None``
    when ``source`` does not exist.
    """

    if not source.is_file():
        return None
    content = source.read_bytes()
    for candidate in versioned_backup_candidates(source, (from_version,)):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            if candidate.read_bytes() == content:
                return candidate
        except OSError:
            continue
    ts = int(time.time())
    backup = source.with_name(versioned_backup_name(source.name, from_version, ts))
    while backup.exists():
        # 同一秒内的第二份不同内容不能覆盖上一份：名字往后挪一秒，直到落到空位。
        ts += 1
        backup = source.with_name(versioned_backup_name(source.name, from_version, ts))
    atomic_write_bytes(backup, content)
    return backup


def kept_copy_name(base_name: str, from_version: int, ts: int) -> str:
    """例如 script_plan_segments.json → script_plan_segments.json.kept.v9-1712345678。"""

    return f"{base_name}.kept.v{from_version}-{ts}"


def set_aside_as_kept_copy(source: Path, from_version: int, *, copy: bool = False) -> Path:
    """Move (or copy) ``source`` to a kept copy beside it and return the copy's path.

    A kept copy holds the side a legacy-shape normalization did not keep. Unlike
    versioned backups it is never cleaned up: it is the user's content, and the
    migration report names it. No reader picks it up under its new name.
    """

    ts = int(time.time())
    target = source.with_name(kept_copy_name(source.name, from_version, ts))
    while target.exists():
        ts += 1
        target = source.with_name(kept_copy_name(source.name, from_version, ts))
    if copy:
        atomic_write_bytes(target, source.read_bytes())
    else:
        source.replace(target)
    return target


__all__ = [
    "ensure_versioned_backup",
    "kept_copy_name",
    "set_aside_as_kept_copy",
    "versioned_backup_candidates",
    "versioned_backup_name",
]
