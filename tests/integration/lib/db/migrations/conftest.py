"""Shared Alembic configuration for migration tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from alembic.config import Config

_PROJECT_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture
def migration_revisions() -> Callable[[str], tuple[str, str]]:
    """Read migration ``(revision, down_revision)`` through filename globbing.

    Cases chain parent upgrade, data creation, then migration upgrade without hardcoded revision strings; reordered or
    inserted migration scripts could otherwise make fixed IDs silently target another migration.
    """

    def _revisions(filename_glob: str) -> tuple[str, str]:
        matches = list((_PROJECT_ROOT / "alembic" / "versions").glob(filename_glob))
        assert len(matches) == 1, f"{filename_glob} 匹配到 {len(matches)} 个迁移文件，期望 1"
        revision: str | None = None
        down_revision: str | None = None
        for line in matches[0].read_text(encoding="utf-8").splitlines():
            if line.startswith("revision: str ="):
                revision = line.split("=")[1].strip().strip('"').strip("'")
            elif line.startswith("down_revision:"):
                down_revision = line.split("=")[1].strip().strip('"').strip("'")
        if not revision or not down_revision:
            raise RuntimeError(f"未在 {matches[0].name} 中找到 revision / down_revision")
        return revision, down_revision

    return _revisions


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, Path]:
    """Config targeting repository Alembic scripts and this case's exclusive temporary SQLite path.

    Intentionally construct without ``alembic.ini``: ``env.py`` skips ``fileConfig()`` when
    ``config_file_name`` is None; otherwise alembic.ini logging sections reset the root logger
    and remove pytest caplog handlers. ``lib/db/__init__.py`` ``init_db()`` follows the same approach.
    Pass database locations to ``env.py`` through ``DATABASE_URL``.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config()
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    return cfg, db_path
