"""Alembic 数据迁移：存量 Agent 凭证的 base_url 归一到「存储值即调用值」形态。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_NOW = "2026-09-08 00:00:00"


def _insert_credential(conn: sa.Connection, cred_id: int, base_url: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO agent_anthropic_credentials "
            "(id, user_id, preset_id, display_name, base_url, api_key, is_active, created_at, updated_at) "
            "VALUES (:id, 'default', '__custom__', :name, :base_url, 'sk-x', 0, :now, :now)"
        ),
        {"id": cred_id, "name": f"cred-{cred_id}", "base_url": base_url, "now": _NOW},
    )


def test_upgrade_normalizes_legacy_base_urls(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
) -> None:
    """升到父版本 → 写入旧格式 base_url → 升到本版本：带端点后缀 / 尾斜杠的行被归一，规范值原样。"""
    revision, parent = migration_revisions("*_normalize_agent_credential_base_url.py")
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, parent)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _insert_credential(conn, 1, "https://proxy.internal/anthropic/v1/messages")
            _insert_credential(conn, 2, "https://api.minimaxi.com/anthropic/")
            _insert_credential(conn, 3, "  https://x//v1/messages/  ")
            _insert_credential(conn, 4, "https://api.deepseek.com/anthropic")
            _insert_credential(conn, 5, "https://relay.example.com/a?api_key=sk-x")

        command.upgrade(cfg, revision)

        with engine.begin() as conn:
            rows = conn.execute(sa.text("SELECT id, base_url FROM agent_anthropic_credentials ORDER BY id")).fetchall()
    finally:
        engine.dispose()

    assert {row.id: row.base_url for row in rows} == {
        1: "https://proxy.internal/anthropic",
        2: "https://api.minimaxi.com/anthropic",
        3: "https://x",
        4: "https://api.deepseek.com/anthropic",
        # 带 query 的值只做同样三步、不在迁移里拒绝：它由测试连接的入口校验拦下，用户须自行改地址。
        5: "https://relay.example.com/a?api_key=sk-x",
    }
