"""升级前的库 revision：在 Alembic 改写它之前记进全局设置（docs/adr/0099）。

升级固化按这个 revision 推断升级前的应用版本（``lib.config.pre_upgrade_recommendations``）。应用启动
（``lib.db.init_db``）、Alembic 命令行和迁移脚本都经过 ``alembic/env.py``，记录挂在那里（:class:`SourceRecorder`）：

- 库里已有全局设置表：执行任何迁移步之前，单独提交一条记录；
- 还没有这张表：PostgreSQL 在建表那一步的事务里记录；SQLite 先提交建表与 revision，再记录，失败后可重试；
- 已有记录时不改：记录反映的是带这段逻辑的版本第一次迁移这个库之前的 revision。

离线模式（``--sql``）生成的脚本由别处执行，这里记不了来源，``env.py`` 直接拒绝。
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from lib.db.models.config import SystemSetting

if TYPE_CHECKING:
    from alembic.runtime.migration import MigrationContext, MigrationInfo

#: 全局设置键：升级前库里的 alembic revision（JSON ``{"revision": ...}``，全新的库为 ``null``）。
SOURCE_SETTING_KEY = "pre_upgrade_db_revision"


@dataclass(frozen=True)
class Source:
    """记下的升级前 revision：``known`` 为假时没有可信的记录；``revision`` 为 ``None`` 表示全新的库。"""

    known: bool
    revision: str | None = None


def parse(raw: str) -> Source:
    """解析 :data:`SOURCE_SETTING_KEY` 的值；空值或格式不对时为没有可信的记录。"""
    if not raw:
        return Source(known=False)
    try:
        revision = json.loads(raw)["revision"]
    except (ValueError, TypeError, KeyError):
        return Source(known=False)
    if revision is not None and not isinstance(revision, str):
        return Source(known=False)
    return Source(known=True, revision=revision)


def current_revision(connection: Connection) -> str | None:
    """库里此刻的 revision。全新的库为 ``None``；有表、没有 revision 记录的旧库为空串；多个 head 用逗号连接。"""
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" in tables:
        heads = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if heads:
            return ",".join(sorted(heads))
    return "" if tables - {"alembic_version"} else None


def record(connection: Connection, revision: str | None) -> None:
    """记下升级前的 revision。已有记录时不改，不提交事务。"""
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(SystemSetting).values(
        key=SOURCE_SETTING_KEY, value=json.dumps({"revision": revision}), updated_at=datetime.now(UTC)
    )
    connection.execute(stmt.on_conflict_do_nothing(index_elements=[SystemSetting.key]))


def _has_settings_table(connection: Connection) -> bool:
    return inspect(connection).has_table(SystemSetting.__tablename__)


class SourceRecorder:
    """一次 Alembic 运行里的来源记录。在 ``context.configure`` 之前构造，:meth:`after_step` 传给 ``on_version_apply``。

    构造时读出迁移前的 revision；已有全局设置表就立即写入并提交，之后的迁移步改写 revision 时记录已经在库里。
    没有这张表时，:meth:`after_step` 在建表那一步里记录。PostgreSQL 写入失败时整步回滚；SQLite 的 DDL 不随
    事务回滚，先提交建表与 revision 再记录，避免写入失败后留下表却回滚 revision。此时失败重试按未知来源处理。
    """

    def __init__(self, connection: Connection) -> None:
        self._revision = current_revision(connection)
        self._recorded = _has_settings_table(connection)
        if self._recorded:
            record(connection, self._revision)
        connection.commit()

    def after_step(
        self, ctx: MigrationContext, step: MigrationInfo, heads: Collection[Any], run_args: Mapping[str, Any]
    ) -> None:
        connection = ctx.connection
        if self._recorded or not step.is_upgrade or connection is None:
            return
        if _has_settings_table(connection):
            if connection.dialect.name == "sqlite":
                # SQLite DDL 已生效，不能让来源写入失败单独回滚这一步的 revision。
                with ctx.autocommit_block():
                    assert ctx.connection is not None
                    record(ctx.connection, self._revision)
            else:
                record(connection, self._revision)
            self._recorded = True


__all__ = ["SOURCE_SETTING_KEY", "Source", "SourceRecorder", "current_revision", "parse", "record"]
