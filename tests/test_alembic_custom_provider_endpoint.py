"""Alembic 0426endpointrefactor 双向迁移测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    # env.py 通过 DATABASE_URL 环境变量获取 URL，必须用 aiosqlite 协议
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    # alembic env.py 调用 logging.config.fileConfig(...)，默认 disable_existing_loggers=True，
    # 会禁掉测试进程已注册的 logger，导致后续 caplog 测试抓不到日志。
    # patch 为 disable_existing_loggers=False 防止跨测试污染。
    import logging.config

    real_file_config = logging.config.fileConfig
    monkeypatch.setattr(
        logging.config,
        "fileConfig",
        lambda *args, **kwargs: real_file_config(*args, **{**kwargs, "disable_existing_loggers": False}),
    )
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return cfg, db_path


def _sync_engine(db_path: Path) -> sa.Engine:
    """返回同步 engine，用于测试断言（避免 async 复杂度）。"""
    return sa.create_engine(f"sqlite:///{db_path}")


def _seed_pre_endpoint_state(engine: sa.Engine, combos: list[tuple[str, str]]) -> None:
    """注入历史数据：每个 (api_format, media_type) 组合写一个 provider+model。"""
    with engine.begin() as conn:
        for i, (api_fmt, media) in enumerate(combos, start=1):
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider (id, display_name, api_format, base_url, api_key, "
                    "created_at, updated_at) VALUES (:id, :n, :f, :u, :k, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": i, "n": f"P{i}", "f": api_fmt, "u": "https://x", "k": "k"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider_model (provider_id, model_id, display_name, "
                    "media_type, is_default, is_enabled, created_at, updated_at) "
                    "VALUES (:pid, :mid, :dn, :mt, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"pid": i, "mid": f"m-{i}", "dn": f"m-{i}", "mt": media},
            )


def test_upgrade_maps_all_nine_combos(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "a89021f43d52")
    engine = _sync_engine(db_path)

    combos = [
        ("openai", "text"),
        ("openai", "image"),
        ("openai", "video"),
        ("google", "text"),
        ("google", "image"),
        ("google", "video"),
        ("newapi", "text"),
        ("newapi", "image"),
        ("newapi", "video"),
    ]
    _seed_pre_endpoint_state(engine, combos)

    command.upgrade(cfg, "0426endpointrefactor")

    expected_endpoints = [
        "openai-chat",
        "openai-images",
        "openai-video",
        "gemini-generate",
        "gemini-image",
        "openai-video",  # (google, video) 兜底为 openai-video，比 newapi-video 在中转站生态更通用
        "openai-chat",
        "openai-images",
        "newapi-video",
    ]
    expected_discovery = [
        "openai",
        "openai",
        "openai",
        "google",
        "google",
        "google",
        "openai",
        "openai",
        "openai",
    ]

    with engine.connect() as conn:
        for i, ep in enumerate(expected_endpoints, start=1):
            row = conn.execute(
                sa.text("SELECT endpoint FROM custom_provider_model WHERE provider_id=:i"),
                {"i": i},
            ).fetchone()
            assert row.endpoint == ep, f"combo {combos[i - 1]} → expected {ep}, got {row.endpoint}"

        for i, df in enumerate(expected_discovery, start=1):
            row = conn.execute(sa.text("SELECT discovery_format FROM custom_provider WHERE id=:i"), {"i": i}).fetchone()
            assert row.discovery_format == df


def test_downgrade_restores_columns(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "0426endpointrefactor")
    engine = _sync_engine(db_path)

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO custom_provider (id, display_name, discovery_format, base_url, api_key, "
                "created_at, updated_at) VALUES (1, 'P', 'openai', 'https://x', 'k', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO custom_provider_model (provider_id, model_id, display_name, endpoint, "
                "is_default, is_enabled, created_at, updated_at) "
                "VALUES (1, 'sora-2', 'Sora 2', 'openai-video', 0, 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    command.downgrade(cfg, "a89021f43d52")

    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT api_format FROM custom_provider WHERE id=1")).fetchone()
        assert row.api_format == "openai"
        row = conn.execute(sa.text("SELECT media_type FROM custom_provider_model WHERE provider_id=1")).fetchone()
        assert row.media_type == "video"
