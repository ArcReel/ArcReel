"""Legacy project source-encoding migration.

Called at startup by server/app.py lifespan: scan projects/<name>/source/*.{txt,md},
re-encode non-UTF-8 files with SourceLoader and back up originals under source/raw/.
Log individual failures without affecting other files/projects or server startup.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lib.episode.episode_ledger import SOURCE_TEXT_SUFFIXES

from .errors import SourceDecodeError
from .txt import decode_txt

logger = logging.getLogger(__name__)


@dataclass
class MigrationSummary:
    migrated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def migrate_project_source_encoding(project_dir: Path) -> MigrationSummary:
    summary = MigrationSummary()
    source_dir = project_dir / "source"
    if not source_dir.exists():
        return summary

    for file_path in sorted(source_dir.iterdir()):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SOURCE_TEXT_SUFFIXES:
            continue

        raw_bytes = file_path.read_bytes()
        try:
            raw_bytes.decode("utf-8")
            summary.skipped.append(file_path.name)
            continue
        except UnicodeDecodeError:
            # Expected branch: not plain UTF-8; use decode_txt multi-encoding fallback below
            pass

        try:
            text, used_enc = decode_txt(raw_bytes)
        except SourceDecodeError as exc:
            logger.warning(
                "迁移失败：无法解码 %s（尝试 %s）",
                file_path,
                ", ".join(exc.tried_encodings),
            )
            summary.failed.append(file_path.name)
            continue

        backup_dir = source_dir / "raw"
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / file_path.name
        if not backup_path.exists():
            shutil.copyfile(file_path, backup_path)
        file_path.write_text(text, encoding="utf-8")
        logger.info(
            "迁移成功：%s（%s → utf-8），原文件备份到 %s",
            file_path,
            used_enc,
            backup_path,
        )
        summary.migrated.append(file_path.name)

    return summary


def migrate_project_source_encoding_once(
    project_dir: Path, *, migrate: Callable[[Path], MigrationSummary] = migrate_project_source_encoding
) -> dict:
    """Migrate one project once, marked by ``.arcreel/source_encoding_migrated``.

    Called per project at startup and when restoring deleted projects. Catch failures, recording logs and
    ``.arcreel/migration_errors.log`` without raising.
    """
    marker_dir = project_dir / ".arcreel"
    marker = marker_dir / "source_encoding_migrated"
    if marker.exists():
        return {"skipped": True}
    try:
        result = migrate(project_dir)
        marker_dir.mkdir(exist_ok=True)
        marker.touch()
        if result.failed:
            err_log = marker_dir / "migration_errors.log"
            err_log.write_text(
                "\n".join(f"FAILED: {name}" for name in result.failed) + "\n",
                encoding="utf-8",
            )
        return {
            "migrated": result.migrated,
            "skipped": result.skipped,
            "failed": result.failed,
        }
    except Exception as exc:
        logger.exception("源文件编码迁移失败 project=%s，已跳过", project_dir.name)
        try:
            marker_dir.mkdir(exist_ok=True)
            (marker_dir / "migration_errors.log").write_text(f"FATAL: {exc}\n", encoding="utf-8")
            marker.touch()
        except Exception:
            pass
        return {"error": str(exc)}
