"""Alembic 回填迁移：验证 video endpoint 模型的 NULL supported_durations 被启发式填充。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """指向项目 alembic 脚本，但 DB 用临时 sqlite（通过 DATABASE_URL，env.py 会读取）。

    刻意不传 alembic.ini 路径：env.py 在 config.config_file_name 为 None 时跳过
    fileConfig() 调用，避免 alembic.ini 的 logging section 在测试中重置 root
    logger 把 pytest caplog 的 handler 清掉（其它 test_text_backends 测试会断言失败）。
    """
    repo_root = Path(__file__).resolve().parent.parent
    cfg = Config()  # 不传 ini 路径 → config_file_name=None → env.py 跳过 fileConfig
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    db_path = tmp_path / "test.db"
    # env.py 通过 lib.db.engine.get_database_url() 读环境变量，不会读 cfg sqlalchemy.url
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg.attributes["_test_db_path"] = str(db_path)
    return cfg


@pytest.fixture
def backfill_revisions() -> tuple[str, str]:
    """读出新增迁移的 (revision, down_revision)，便于按名锁定。"""
    repo_root = Path(__file__).resolve().parent.parent
    versions_dir = repo_root / "alembic" / "versions"
    matches = list(versions_dir.glob("*_backfill_custom_model_durations.py"))
    assert len(matches) == 1, f"找到 {len(matches)} 个回填迁移文件，期望 1"
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


def test_backfill_video_endpoints_with_null_durations(alembic_cfg: Config, backfill_revisions: tuple[str, str]):
    """先回退到 backfill 之前一格，插入若干 NULL 行，再升级到 backfill，断言被填充。"""
    backfill_revision_id, parent_revision_id = backfill_revisions
    # 1. 把 schema 升到 backfill 的 down_revision
    command.upgrade(alembic_cfg, parent_revision_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO custom_provider "
                "(id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
                "VALUES (1, 'P', 'openai', 'https://x', 'k', '2026-05-04 00:00:00', '2026-05-04 00:00:00')"
            )
        )
        # 四条：video endpoint 且 NULL → 应被回填；text endpoint 不动；非 NULL 也不动
        conn.execute(
            sa.text(
                "INSERT INTO custom_provider_model "
                "(id, provider_id, model_id, display_name, endpoint, is_default, is_enabled, "
                "supported_durations, created_at, updated_at) VALUES "
                "(1, 1, 'sora-2-pro', 'X', 'openai-video', 0, 1, NULL, '2026-05-04 00:00:00', '2026-05-04 00:00:00'),"
                "(2, 1, 'unknown-foo', 'Y', 'openai-video', 0, 1, NULL, '2026-05-04 00:00:00', '2026-05-04 00:00:00'),"
                "(3, 1, 'gpt-4o', 'Z', 'openai-chat', 0, 1, NULL, '2026-05-04 00:00:00', '2026-05-04 00:00:00'),"
                "(4, 1, 'sora-2', 'W', 'openai-video', 0, 1, '[1,2,3]', '2026-05-04 00:00:00', '2026-05-04 00:00:00')"
            )
        )

    # 2. 升级到 backfill
    command.upgrade(alembic_cfg, backfill_revision_id)

    # 3. 断言
    with engine.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT model_id, supported_durations FROM custom_provider_model ORDER BY id")
        ).fetchall()
    by_id = {r[0]: r[1] for r in rows}

    # sora-2-pro 命中第一条预设：[4, 8, 12]
    assert by_id["sora-2-pro"] == "[4, 8, 12]"
    # 未知 → DEFAULT_FALLBACK [4, 8]
    assert by_id["unknown-foo"] == "[4, 8]"
    # text endpoint 不动
    assert by_id["gpt-4o"] is None
    # 已有非 NULL 不动
    assert by_id["sora-2"] == "[1,2,3]"
