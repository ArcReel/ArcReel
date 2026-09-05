"""Versioned backups a migration step writes before touching one of its inputs.

Backup names are ``<file>.bak.v<from_version>-<timestamp>``; the runner's
cleanup recognises exactly this shape. A backup whose content already exists
for the same starting version is not written again, so a migration that keeps
failing on an unchanged project leaves one backup, not one per attempt.
"""

from __future__ import annotations

import time
from pathlib import Path

from lib.json_io import atomic_write_bytes


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


__all__ = ["ensure_versioned_backup", "versioned_backup_candidates", "versioned_backup_name"]
