"""Project file-level schema migration framework.

Top-level project.json schema_version defaults to v0 if absent. Current version comes from
``lib.project.project_schema.CURRENT_PROJECT_SCHEMA_VERSION``.
Migrators are pure, idempotent functions with signature ``def migrate(project_dir: Path) -> None``."""

from lib.project.project_migrations.runner import (
    CURRENT_SCHEMA_VERSION,
    MIGRATORS,
    cleanup_stale_backups,
    migrate_project_with_verdict,
    run_project_migrations,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "MIGRATORS",
    "cleanup_stale_backups",
    "migrate_project_with_verdict",
    "run_project_migrations",
]
