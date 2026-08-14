"""Alembic c8e1d4a91b70：接口身份 / 请求地址拆分，并补产物集合列。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVISION = "c8e1d4a91b70"
DOWN_REVISION = "f6a41746c0de"


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
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


def test_upgrade_moves_request_url_and_fills_artifact_collection(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, DOWN_REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                "source, provider_endpoint, submitted_base_url, queued_at, updated_at) VALUES "
                "('T-url', 'demo', 'video', 'video', 'r1', 'running', 'webui', "
                "'https://maas.example/api/v1', NULL, '2026-08-14 00:00:00', '2026-08-14 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                "source, provider_endpoint, submitted_base_url, queued_at, updated_at) VALUES "
                "('T-proto', 'demo', 'video', 'video', 'r2', 'running', 'webui', "
                "'openai-video', 'https://custom.example/v1', '2026-08-14 00:00:00', '2026-08-14 00:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, REVISION)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        url_row = conn.execute(
            sa.text(
                "SELECT provider_endpoint, submitted_base_url, artifact_collection FROM tasks WHERE task_id = 'T-url'"
            )
        ).one()
        proto_row = conn.execute(
            sa.text(
                "SELECT provider_endpoint, submitted_base_url, artifact_collection FROM tasks WHERE task_id = 'T-proto'"
            )
        ).one()
    engine.dispose()

    assert url_row.provider_endpoint is None
    assert url_row.submitted_base_url == "https://maas.example/api/v1"
    assert url_row.artifact_collection == "videos"
    assert proto_row.provider_endpoint == "openai-video"
    assert proto_row.submitted_base_url == "https://custom.example/v1"
    assert proto_row.artifact_collection == "videos"
