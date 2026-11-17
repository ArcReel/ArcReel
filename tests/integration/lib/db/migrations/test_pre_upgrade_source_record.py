"""升级前的 revision 在 Alembic 改写它之前记下（docs/adr/0099），迁移从哪里发起都一样。

部署时可以先在命令行执行 ``alembic upgrade head`` 再启动应用，也可以让应用启动时迁移。迁移后库里只剩新 revision，
来源若只在应用启动时记，先跑命令行的实例就会被当成上一版，固化出它从未用过的模型。期望值是 0.35.0 的解析结果：
只有 Ark 有凭证、没有同步数据时，图片用 0.35.0 出厂快照里 Ark 的默认模型。

用例在真实数据库上跑真实的迁移入口：本地是 SQLite 文件库；``DATABASE_URL`` 指向 PostgreSQL 时（postgres-compat
job）每个用例另建一个空库。故障用数据库触发器在写入处注入。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, event, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import lib.db
from alembic import command
from lib.config import pre_upgrade_recommendations
from lib.config.recommended_models import freeze_effective_defaults
from lib.config.repository import SystemSettingRepository
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.db import init_db, upgrade_source
from lib.db.repositories.credential_repository import CredentialRepository

pytestmark = pytest.mark.uses_db

_ROOT = Path(__file__).resolve().parents[5]

#: 0.34.0 与 0.35.0 库里的 alembic revision。
REVISION_0_34_0_35 = "b5d1e8f3a627"
#: 0.34.0 与 0.35.0 出厂快照里 Ark 的图片默认模型。
ARK_IMAGE_0_35 = "doubao-seedream-5-0-lite-260128"
_FAILING = "test_source_write_fails"


@dataclass(frozen=True)
class _Database:
    sessions: async_sessionmaker[AsyncSession]
    postgres: bool


@pytest.fixture
async def database(tmp_path, monkeypatch):
    """本用例独占的空库，迁移入口（命令行、``init_db``）与用例读写的都是它。"""
    configured = os.environ.get("DATABASE_URL", "")
    admin = None
    name = f"upgrade_source_{uuid.uuid4().hex[:12]}"
    if configured.startswith("postgresql"):
        admin = create_async_engine(configured, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
        url = make_url(configured).set(database=name).render_as_string(hide_password=False)
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_async_engine(url, poolclass=NullPool)
    monkeypatch.setattr(lib.db, "async_engine", engine)
    yield _Database(async_sessionmaker(engine, expire_on_commit=False), postgres=admin is not None)
    await engine.dispose()
    if admin is not None:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    """部署文档里的命令行迁移：``alembic`` 读仓库根目录的 alembic.ini，库由 ``DATABASE_URL`` 指定。"""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=_ROOT, env=os.environ.copy(), capture_output=True, text=True
    )


async def _cli_upgrade() -> None:
    result = await asyncio.to_thread(_cli, "upgrade", "head")
    assert result.returncode == 0, result.stderr


async def _old_instance(db: _Database, revision: str) -> None:
    """停在 ``revision`` 的旧实例：库结构由迁移建出，旧版本不记来源；有全局设置表时配上 Ark 的凭证。"""
    cfg = Config()
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    await asyncio.to_thread(command.upgrade, cfg, revision)  # env.py 自己起事件循环
    async with db.sessions() as session:
        if await _has_settings_table(session):
            await session.execute(
                text("DELETE FROM system_setting WHERE key = :key"), {"key": upgrade_source.SOURCE_SETTING_KEY}
            )
            await CredentialRepository(session).create(provider="ark", name="主 Key", api_key="TEST-ark")
        await session.commit()


async def _has_settings_table(session: AsyncSession) -> bool:
    return await session.run_sync(lambda sync: sync.connection().dialect.has_table(sync.connection(), "system_setting"))


async def _revision(db: _Database) -> str:
    async with db.sessions() as session:
        return (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()


async def _source(db: _Database) -> upgrade_source.Source:
    async with db.sessions() as session:
        return upgrade_source.parse(await SystemSettingRepository(session).get(upgrade_source.SOURCE_SETTING_KEY, ""))


async def _start(db: _Database) -> ProviderModel:
    """应用启动：``init_db``、算定升级前的选择、固化，再按生成时的规则解析图片模型。"""
    await init_db()
    async with db.sessions() as session:
        await pre_upgrade_recommendations.capture(session)
    async with db.sessions() as session:
        await freeze_effective_defaults(session)
    return await ConfigResolver(db.sessions).resolve_image_backend(None, None, generation_type="t2i")


async def _fail_writes(db: _Database, table: str, event: str, condition: str) -> None:
    """``table`` 上满足 ``condition`` 的写入一律失败，模拟一次写库故障。"""
    async with db.sessions() as session:
        if db.postgres:
            await session.execute(
                text(
                    f"CREATE FUNCTION {_FAILING}() RETURNS trigger LANGUAGE plpgsql AS "
                    "'BEGIN RAISE EXCEPTION ''write failed''; END'"
                )
            )
            await session.execute(
                text(
                    f"CREATE TRIGGER {_FAILING} BEFORE {event} ON {table} FOR EACH ROW "
                    f"WHEN ({condition}) EXECUTE FUNCTION {_FAILING}()"
                )
            )
        else:
            await session.execute(
                text(
                    f"CREATE TRIGGER {_FAILING} BEFORE {event} ON {table} WHEN {condition} "
                    "BEGIN SELECT RAISE(ABORT, 'write failed'); END"
                )
            )
        await session.commit()


async def _restore_writes(db: _Database, table: str) -> None:
    async with db.sessions() as session:
        await session.execute(text(f"DROP TRIGGER {_FAILING}" + (f" ON {table}" if db.postgres else "")))
        await session.commit()


_SOURCE_WRITE = f"NEW.key = '{upgrade_source.SOURCE_SETTING_KEY}'"


@pytest.mark.parametrize("entry", ["command-line", "init_db"])
async def test_migrating_before_the_first_start_keeps_the_old_release(entry, database):
    """先在命令行（或直接调用 ``init_db`` 的脚本）迁移再启动：记下的仍是 0.35.0，图片用 0.35.0 的 Ark 默认模型。"""
    await _old_instance(database, REVISION_0_34_0_35)

    if entry == "command-line":
        await _cli_upgrade()
    else:
        await init_db()

    assert await _source(database) == upgrade_source.Source(known=True, revision=REVISION_0_34_0_35)
    assert await _start(database) == ProviderModel("ark", ARK_IMAGE_0_35)
    # 之后的启动与迁移不改记录
    await _cli_upgrade()
    assert await _source(database) == upgrade_source.Source(known=True, revision=REVISION_0_34_0_35)


async def test_a_failed_source_record_stops_before_the_revision_changes(database):
    """记不下来源就不迁移：库里仍是旧 revision，修复后重试照常记下 0.35.0。"""
    await _old_instance(database, REVISION_0_34_0_35)
    await _fail_writes(database, "system_setting", "INSERT", _SOURCE_WRITE)

    result = await asyncio.to_thread(_cli, "upgrade", "head")

    assert result.returncode != 0
    assert "write failed" in result.stderr
    assert await _revision(database) == REVISION_0_34_0_35
    await _restore_writes(database, "system_setting")
    await _cli_upgrade()
    assert await _start(database) == ProviderModel("ark", ARK_IMAGE_0_35)


@pytest.mark.parametrize("before_the_settings_table", [False, True], ids=["fresh-database", "older-schema"])
async def test_a_database_without_the_settings_table_records_the_revision_it_started_from(
    before_the_settings_table, database, migration_revisions
):
    """库里还没有全局设置表：建表那一步写入迁移开始时的 revision。全新的库记为全新安装；早于这张表的旧库对不上
    任何发布版，升级前的选择按未知处理，不会被当成从上一版升级。"""
    _, older = migration_revisions("e13e987e2170_*.py")
    if before_the_settings_table:
        await _old_instance(database, older)

    await _cli_upgrade()

    expected = older if before_the_settings_table else None
    assert await _source(database) == upgrade_source.Source(known=True, revision=expected)
    await init_db()
    async with database.sessions() as session:
        await CredentialRepository(session).create(provider="ark", name="主 Key", api_key="TEST-ark")
        await session.commit()
        table = await pre_upgrade_recommendations.capture(session)
    if before_the_settings_table:
        assert table.auto_pick("image") == pre_upgrade_recommendations.AutoPick("unknown")
    else:
        assert table.auto_pick("image").kind == "builtin"


async def test_a_failure_after_the_settings_table_is_created_keeps_the_revision_it_started_from(
    database, migration_revisions
):
    """建表那一步已提交、下一步失败：库停在建表那一版，记下的仍是迁移开始时的旧 revision，重试不会改成它。"""
    settings_table_revision, older = migration_revisions("e13e987e2170_*.py")
    next_revision, _ = migration_revisions("802fa55d8aff_*.py")
    await _old_instance(database, older)
    await _fail_writes(database, "alembic_version", "UPDATE", f"NEW.version_num = '{next_revision}'")

    result = await asyncio.to_thread(_cli, "upgrade", "head")

    assert result.returncode != 0
    assert await _revision(database) == settings_table_revision
    assert await _source(database) == upgrade_source.Source(known=True, revision=older)


def test_offline_migrations_are_refused():
    """离线模式生成的 SQL 在别处执行，改写 revision 前记不了来源：直接拒绝，不输出改写 revision 的语句。"""
    result = _cli("upgrade", "head", "--sql")

    assert result.returncode != 0
    assert "alembic_version" not in result.stdout


@pytest.mark.parametrize("older_database", [False, True], ids=["fresh-database", "older-schema"])
async def test_a_failed_source_insert_in_the_settings_step_can_be_retried(
    older_database, database, migration_revisions
):
    """建表步里的来源写失败后，修复数据库即可重启；不能留下表却回滚 revision，导致重试重复建表。"""
    settings_revision, older = migration_revisions("e13e987e2170_*.py")
    if older_database:
        await _old_instance(database, older)

    def fail_source_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO system_setting") and upgrade_source.SOURCE_SETTING_KEY in str(parameters):
            raise OSError("source insert temporarily unavailable")

    event.listen(Engine, "before_cursor_execute", fail_source_insert)
    try:
        with pytest.raises(OSError, match="source insert temporarily unavailable"):
            await init_db()
    finally:
        event.remove(Engine, "before_cursor_execute", fail_source_insert)

    # PG 回滚整个建表步；SQLite 的表已经落盘，revision 必须一起保留。
    stopped = older if database.postgres else settings_revision
    assert await _revision(database) == stopped
    await init_db()
    assert await _source(database) == upgrade_source.Source(known=True, revision=stopped)
    async with database.sessions() as session:
        table = await pre_upgrade_recommendations.capture(session)
    assert table.auto_pick("image") == pre_upgrade_recommendations.AutoPick("unknown")
