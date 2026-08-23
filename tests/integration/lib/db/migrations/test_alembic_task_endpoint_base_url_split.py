"""Alembic b3f9c07ae214（tasks 协议标识与请求域名分列）双向迁移测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[5]
REVISION = "b3f9c07ae214"
DOWN_REVISION = "f6a41746c0de"


def _insert_task(conn, task_id: str, endpoint: str | None, base_url: str | None) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, source, "
            "provider_endpoint, submitted_base_url, queued_at, updated_at) "
            "VALUES (:tid, 'demo', 'video', 'video', :tid, 'running', 'webui', :ep, :url, "
            "'2026-08-18 00:00:00', '2026-08-18 00:00:00')"
        ),
        {"tid": task_id, "ep": endpoint, "url": base_url},
    )


def _rows(db_path: Path) -> dict[str, tuple[str | None, str | None]]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT task_id, provider_endpoint, submitted_base_url FROM tasks")).fetchall()
    engine.dispose()
    return {r[0]: (r[1], r[2]) for r in rows}


def _tasks_indexes(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA index_list('tasks')")).fetchall()
    engine.dispose()
    return {r[1] for r in rows}


def test_upgrade_moves_domains_into_submitted_base_url(alembic_cfg):
    """升级后无任何行在协议标识列存放域名；自定义供应商的两列各归其位、不被改写。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _insert_task(conn, "T-builtin", "https://maas-a.example.com/ws-1/api/v1", None)
        _insert_task(conn, "T-builtin-upper", "HTTPS://maas-b.example.com/ws-1/api/v1", None)
        _insert_task(conn, "T-custom", "dashscope-async-video", "https://custom-a.example.com/api/v1")
        _insert_task(conn, "T-custom-nodomain", "openai-video", None)
        _insert_task(conn, "T-empty", None, None)
    engine.dispose()

    command.upgrade(cfg, REVISION)

    rows = _rows(db_path)
    assert rows["T-builtin"] == (None, "https://maas-a.example.com/ws-1/api/v1")
    assert rows["T-builtin-upper"] == (None, "HTTPS://maas-b.example.com/ws-1/api/v1")
    assert rows["T-custom"] == ("dashscope-async-video", "https://custom-a.example.com/api/v1")
    assert rows["T-custom-nodomain"] == ("openai-video", None)
    assert rows["T-empty"] == (None, None)


def test_upgrade_keeps_existing_domain_when_both_columns_hold_one(alembic_cfg):
    """两列都有域名的畸形行以专列为准：只清协议标识位，不覆盖已有域名。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _insert_task(conn, "T-both", "https://stale.example.com/api/v1", "https://kept.example.com/api/v1")
    engine.dispose()

    command.upgrade(cfg, REVISION)

    assert _rows(db_path)["T-both"] == (None, "https://kept.example.com/api/v1")


def test_downgrade_restores_builtin_domains(alembic_cfg):
    """降级把只有域名的行搬回协议标识列；自定义供应商的行两列俱在，原样不动。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _insert_task(conn, "T-builtin", "https://maas-a.example.com/ws-1/api/v1", None)
        _insert_task(conn, "T-custom", "dashscope-async-video", "https://custom-a.example.com/api/v1")
    engine.dispose()

    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, DOWN_REVISION)

    rows = _rows(db_path)
    assert rows["T-builtin"] == ("https://maas-a.example.com/ws-1/api/v1", None)
    assert rows["T-custom"] == ("dashscope-async-video", "https://custom-a.example.com/api/v1")


def test_upgrade_fails_loud_when_a_scheme_survives_the_backfill(alembic_cfg):
    """校验判据独立于回填判据：回填够不着的 scheme 形态会让升级显式失败，而不是静默留下。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _insert_task(conn, "T-odd-scheme", "ftp://legacy.example.com/api/v1", None)
    engine.dispose()

    with pytest.raises(RuntimeError, match="仍存放请求域名"):
        command.upgrade(cfg, REVISION)


def test_migration_keeps_dedupe_index(alembic_cfg):
    """纯数据迁移不重建表，表达式型 partial 唯一索引双向都在。"""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, REVISION)
    assert "idx_tasks_dedupe_active" in _tasks_indexes(db_path)

    command.downgrade(cfg, DOWN_REVISION)
    assert "idx_tasks_dedupe_active" in _tasks_indexes(db_path)
