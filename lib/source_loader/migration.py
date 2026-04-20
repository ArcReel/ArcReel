"""历史项目源文件编码迁移。

启动时由 server/app.py lifespan 调用：扫描 projects/<name>/source/*.{txt,md}，
非 UTF-8 文件用 SourceLoader 重编码并备份原文件到 source/raw/。
单文件失败被记录，不影响其它文件 / 项目 / server 启动。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

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
        if file_path.suffix.lower() not in {".txt", ".md"}:
            continue

        raw_bytes = file_path.read_bytes()
        try:
            raw_bytes.decode("utf-8")
            summary.skipped.append(file_path.name)
            continue
        except UnicodeDecodeError:
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
