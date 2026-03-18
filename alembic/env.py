"""Alembic environment configuration.

Supports async engines (aiosqlite / asyncpg) by using run_sync().
The database URL is read from the DATABASE_URL environment variable via
lib.db.engine.get_database_url(), falling back to SQLite in projects/.arcreel.db.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Import all models so their tables are included in metadata
import lib.db.models  # noqa: F401
from lib.db.base import Base
from lib.db.engine import get_database_url

# Alembic Config object
config = context.config

# Set up loggers from alembic.ini (仅 CLI 直接运行 alembic 时生效；
# init_db() 使用 Config() 空构造，config_file_name 为 None，自动跳过)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use ORM metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection required)."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        transaction_per_migration=True,
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
