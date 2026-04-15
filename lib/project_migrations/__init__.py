"""Project 文件级 schema 迁移框架。

约定：project.json 顶层 schema_version。缺失视为 v0。当前版本 = 1。
迁移器是纯函数，幂等，签名 ``def migrate(project_dir: Path) -> None``。
"""

from lib.project_migrations.runner import (
    CURRENT_SCHEMA_VERSION,
    MIGRATORS,
    cleanup_stale_backups,
    run_project_migrations,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "MIGRATORS",
    "run_project_migrations",
    "cleanup_stale_backups",
]
