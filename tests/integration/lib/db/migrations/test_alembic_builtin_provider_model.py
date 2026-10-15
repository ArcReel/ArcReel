"""Alembic 迁移：新建内置供应商模型表，迁移后非隐藏的官方收录模型为启用，原先隐藏的 3 个 Agnes 旧模型为停用。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from lib.catalog import query as catalog
from lib.catalog.builtin_models import disabled_builtin_models
from lib.catalog.query import factory_snapshot
from tests.catalog_support import install_catalog, with_entry_changes

_HIDDEN = {"agnes-2.0-flash", "agnes-image-2.1-flash", "agnes-video-v2.0"}


async def _disabled(db_path: Path) -> dict[str, frozenset[str]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with async_sessionmaker(engine)() as session:
            return await disabled_builtin_models(session)
    finally:
        await engine.dispose()


def test_hidden_agnes_models_disabled_and_others_enabled(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision, _parent = migration_revisions("*_builtin_provider_model.py")
    command.upgrade(cfg, revision)

    disabled = asyncio.run(_disabled(db_path))

    assert disabled["agnes"] == _HIDDEN
    assert {pid for pid, models in disabled.items() if models} == {"agnes"}
    # 其余官方收录模型都是启用：agnes 只停用这 3 个
    agnes_models = {view.model_id for view in catalog.provider_models("agnes")}
    assert agnes_models - _HIDDEN
    assert disabled["agnes"] < agnes_models


def test_hidden_models_stay_disabled_when_entry_no_longer_discontinued(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
):
    # 停用是迁移写下的用户状态，不随条目是否下架变化
    cfg, db_path = alembic_cfg
    revision, _parent = migration_revisions("*_builtin_provider_model.py")
    command.upgrade(cfg, revision)
    data = factory_snapshot()
    for model_id in _HIDDEN:
        data = with_entry_changes(data, "agnes", model_id, discontinued=False)
    install_catalog(monkeypatch, data)

    assert asyncio.run(_disabled(db_path))["agnes"] == _HIDDEN


def test_downgrade_drops_table(alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]):
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_builtin_provider_model.py")
    command.upgrade(cfg, revision)
    command.downgrade(cfg, parent)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert "builtin_provider_model" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
