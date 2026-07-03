"""Alembic 迁移：custom_provider 三个并发上限定型列的 upgrade / downgrade。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """指向项目 alembic 脚本，DB 用临时 sqlite（env.py 经 DATABASE_URL 读取）。

    刻意不传 alembic.ini 路径：env.py 在 config_file_name 为 None 时跳过 fileConfig()，
    避免 alembic.ini 的 logging section 在测试中重置 root logger。
    """
    repo_root = Path(__file__).resolve().parent.parent
    cfg = Config()
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg.attributes["_test_db_path"] = str(db_path)
    return cfg


@pytest.fixture
def revisions() -> tuple[str, str]:
    """读出加列迁移的 (revision, down_revision)。"""
    repo_root = Path(__file__).resolve().parent.parent
    versions_dir = repo_root / "alembic" / "versions"
    matches = list(versions_dir.glob("*_add_max_workers_columns_to_custom_.py"))
    assert len(matches) == 1, f"找到 {len(matches)} 个加列迁移文件，期望 1"
    text = matches[0].read_text()
    revision: str | None = None
    down_revision: str | None = None
    for line in text.splitlines():
        if line.startswith("revision: str ="):
            revision = line.split("=")[1].strip().strip('"').strip("'")
        elif line.startswith("down_revision:"):
            down_revision = line.split("=")[1].strip().strip('"').strip("'")
    if not revision or not down_revision:
        raise RuntimeError("未在迁移文件中找到 revision / down_revision")
    return revision, down_revision


_COLS = ("image_max_workers", "video_max_workers", "audio_max_workers")


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(custom_provider)")).fetchall()
    return {r[1] for r in rows}


def test_upgrade_adds_columns_existing_row_null(alembic_cfg: Config, revisions: tuple[str, str]):
    """升到加列前插一行，升级后三列存在且该行为 NULL（零回归）。"""
    revision_id, parent_id = revisions
    command.upgrade(alembic_cfg, parent_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert not (set(_COLS) & _columns(engine)), "加列前不应存在并发列"
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider "
                    "(id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
                    "VALUES (1, 'P', 'openai', 'https://x', 'k', "
                    "'2026-06-29 00:00:00', '2026-06-29 00:00:00')"
                )
            )

        command.upgrade(alembic_cfg, revision_id)

        assert set(_COLS) <= _columns(engine)
        with engine.begin() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT image_max_workers, video_max_workers, audio_max_workers FROM custom_provider WHERE id = 1"
                )
            ).fetchone()
        assert row == (None, None, None)
    finally:
        engine.dispose()


@pytest.mark.parametrize("bad_value", [-1, 0])
@pytest.mark.parametrize("col", _COLS)
def test_upgrade_rejects_non_positive_workers(
    alembic_cfg: Config, revisions: tuple[str, str], col: str, bad_value: int
):
    """升级后三列各带正整数 CHECK 约束，写入 0 与负值被 DB 拒绝；NULL 与 ≥1 仍合法。"""
    revision_id, _ = revisions
    command.upgrade(alembic_cfg, revision_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        # 失败写入放在非自动提交连接里，约束触发即回滚，不污染后续断言
        with engine.connect() as conn, pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider "
                    f"(id, display_name, discovery_format, base_url, api_key, {col}, created_at, updated_at) "
                    f"VALUES (1, 'P', 'openai', 'https://x', 'k', {bad_value}, "
                    "'2026-06-29 00:00:00', '2026-06-29 00:00:00')"
                )
            )
        # ≥1 合法
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider "
                    f"(id, display_name, discovery_format, base_url, api_key, {col}, created_at, updated_at) "
                    "VALUES (2, 'P', 'openai', 'https://x', 'k', 1, "
                    "'2026-06-29 00:00:00', '2026-06-29 00:00:00')"
                )
            )
            value = conn.execute(sa.text(f"SELECT {col} FROM custom_provider WHERE id = 2")).scalar_one()
        assert value == 1
    finally:
        engine.dispose()


def test_downgrade_drops_columns(alembic_cfg: Config, revisions: tuple[str, str]):
    """downgrade 回退后三列消失，其余数据保留。"""
    revision_id, parent_id = revisions
    command.upgrade(alembic_cfg, revision_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider "
                    "(id, display_name, discovery_format, base_url, api_key, "
                    "image_max_workers, created_at, updated_at) "
                    "VALUES (1, 'P', 'openai', 'https://x', 'k', 3, "
                    "'2026-06-29 00:00:00', '2026-06-29 00:00:00')"
                )
            )

        command.downgrade(alembic_cfg, parent_id)

        assert not (set(_COLS) & _columns(engine))
        with engine.begin() as conn:
            name = conn.execute(sa.text("SELECT display_name FROM custom_provider WHERE id = 1")).scalar_one()
        assert name == "P"
    finally:
        engine.dispose()
