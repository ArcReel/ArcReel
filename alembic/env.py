"""Alembic environment configuration.

Supports async engines (aiosqlite / asyncpg) by using run_sync().
The database URL is read from the DATABASE_URL environment variable via
lib.db.engine.get_database_url(), falling back to SQLite in projects/arcreel.db.
"""

import asyncio
from logging.config import fileConfig

from alembic.util import CommandError
from sqlalchemy import DateTime, String, pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from lib.agent.agent_session_store.models import register_models as register_agent_session_models
from lib.db.base import Base
from lib.db.engine import get_database_url
from lib.db.models import register_models as register_db_models
from lib.db.upgrade_source import SourceRecorder

# 把全部 ORM 模型登记到 Base.metadata，否则 autogenerate 的对比范围取决于 import 链
register_agent_session_models()
register_db_models()

# Alembic Config object
config = context.config

# Set up loggers from alembic.ini (仅 CLI 直接运行 alembic 时生效；
# init_db() 使用 Config() 空构造，config_file_name 为 None，自动跳过)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use ORM metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection required).

    不支持：生成的脚本在别处执行，改写 revision 之前没法记下升级前的版本（``lib.db.upgrade_source``），
    升级固化会把旧库当成新版本。
    """
    raise CommandError(
        "离线模式（--sql）不受支持：生成的 SQL 改写 revision 前无法记录升级前的版本。请直接对数据库执行 alembic upgrade。"
    )


def _compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    """Suppress VARCHAR ↔ DateTime drift on SQLite.

    We intentionally keep datetime columns as VARCHAR in SQLite DDL to avoid
    Alembic batch_alter_table's CAST truncation bug (see b942e8c5d545).
    SQLAlchemy handles str↔datetime conversion at the Python level.
    """
    if context.dialect.name == "sqlite":
        if isinstance(inspected_type, String) and isinstance(metadata_type, DateTime):
            return False
        if isinstance(inspected_type, DateTime) and isinstance(metadata_type, String):
            return False
    # Return None to let Alembic use its default comparison for all other cases
    return None


def do_run_migrations(connection) -> None:
    # 无论迁移从应用启动、命令行还是脚本发起，改写 revision 之前都先记下升级前的版本
    source = SourceRecorder(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        transaction_per_migration=True,
        compare_type=_compare_type,
        on_version_apply=source.after_step,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine."""
    url = get_database_url()
    connectable = create_async_engine(url, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
