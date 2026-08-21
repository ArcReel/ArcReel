"""unify partial indexes to generated columns and add string lengths

Revision ID: 7a8b9c0d1e2f
Revises: f6a41746c0de
Create Date: 2026-08-15 00:00:00

三方言（SQLite/PG/MySQL）统一改造：
1. 所有 String 无长度字段指定长度（修复 MySQL VARCHAR(1) 默认）
2. tasks 表三个大 Text 字段在 MySQL 升级为 MEDIUMTEXT（16MB）
3. partial unique index 统一改为「生成列 + 普通 unique index」方案
   - SQLite/PG: drop 旧 partial index, add 生成列, add 普通 unique index
   - MySQL: add 生成列, add 普通 unique index（历史迁移已跳过 partial index 创建）
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from alembic import op
from lib.db.migration_compat import is_mysql

# revision identifiers
revision: str = "7a8b9c0d1e2f"
down_revision: str | Sequence[str] | None = "f6a41746c0de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 字段长度变更清单：(table, column, length)
_STRING_LENGTH_CHANGES = [
    # users
    ("users", "id", 64),
    ("users", "username", 128),
    ("users", "role", 32),
    # tasks
    ("tasks", "task_id", 36),
    ("tasks", "project_name", 255),
    ("tasks", "task_type", 64),
    ("tasks", "media_type", 32),
    ("tasks", "resource_id", 255),
    ("tasks", "resource_type", 32),
    ("tasks", "script_file", 512),
    ("tasks", "status", 32),
    ("tasks", "source", 32),
    ("tasks", "dependency_task_id", 36),
    ("tasks", "dependency_group", 64),
    ("tasks", "cancelled_by", 64),
    ("tasks", "provider_id", 128),
    ("tasks", "provider_job_id", 255),
    ("tasks", "provider_endpoint", 128),
    ("tasks", "submitted_base_url", 512),
    ("tasks", "user_id", 64),
    # api_calls
    ("api_calls", "project_name", 255),
    ("api_calls", "call_type", 32),
    ("api_calls", "model", 128),
    ("api_calls", "resolution", 32),
    ("api_calls", "aspect_ratio", 16),
    ("api_calls", "status", 32),
    ("api_calls", "currency", 8),
    ("api_calls", "provider", 32),
    ("api_calls", "user_id", 64),
    # api_keys
    ("api_keys", "name", 128),
    ("api_keys", "key_hash", 64),
    ("api_keys", "key_prefix", 16),
    ("api_keys", "user_id", 64),
    # agent_sessions
    ("agent_sessions", "id", 64),
    ("agent_sessions", "sdk_session_id", 128),
    ("agent_sessions", "project_name", 255),
    ("agent_sessions", "title", 255),
    ("agent_sessions", "status", 32),
    ("agent_sessions", "superseded_by", 64),
    ("agent_sessions", "fork_parent_session_id", 64),
    ("agent_sessions", "fork_anchor_uuid", 128),
    ("agent_sessions", "user_id", 64),
    # agent_session_user_message_links
    ("agent_session_user_message_links", "session_id", 64),
    ("agent_session_user_message_links", "user_entry_uuid", 128),
    ("agent_session_user_message_links", "sdk_entry_uuid", 128),
    ("agent_session_user_message_links", "user_id", 64),
    # agent_session_event_log
    ("agent_session_event_log", "session_id", 64),
    ("agent_session_event_log", "entry_type", 32),
    ("agent_session_event_log", "client_key", 128),
    ("agent_session_event_log", "user_id", 64),
    # agent_session_entries
    ("agent_session_entries", "project_key", 255),
    ("agent_session_entries", "session_id", 128),
    ("agent_session_entries", "subpath", 128),
    ("agent_session_entries", "uuid", 128),
    ("agent_session_entries", "entry_type", 32),
    ("agent_session_entries", "user_id", 64),
    # agent_session_summaries
    ("agent_session_summaries", "project_key", 255),
    ("agent_session_summaries", "session_id", 128),
    ("agent_session_summaries", "user_id", 64),
    # worker_lease
    ("worker_lease", "name", 128),
    ("worker_lease", "owner_id", 64),
]


def _apply_string_lengths() -> None:
    """按表分组，用 batch_alter_table 改字段长度。

    收窄前先审计存量数据：任何列的 max(length) 超过目标长度时直接抛
    RuntimeError 点名表/列/观测长度，而不是让 ALTER 在方言各异的隐晦
    报错（PG "value too long"、MySQL 1406）里中断。

    MySQL 的 MODIFY COLUMN 是完全重定义列，必须显式保留 nullable/server_default，
    否则 NOT NULL 主键列会被改成 NULL（报 1171 错误），server_default 也会丢失。

    反射回来的 ``info["default"]`` 是 SQL 字面量文本（如 ``'USD'``，含引号）。
    传 string 给 ``existing_server_default`` 会被 DDL compiler 再包一层引号，每轮
    upgrade 累加一层；必须用 ``sa.text()`` 包装成 TextClause，compiler 才会原样输出。
    """
    from collections import defaultdict

    from lib.db.migration_compat import is_sqlite

    bind = op.get_bind()
    insp = sa.inspect(bind)

    # SQLite 无硬性 VARCHAR 长度上限，收窄是纯 DDL 标注，无需审计。
    if not is_sqlite():
        violations: list[str] = []
        for table, column, length in _STRING_LENGTH_CHANGES:
            existing_cols = {c["name"] for c in insp.get_columns(table)}
            if column not in existing_cols:
                continue
            col = sa.column(column)
            t = sa.table(table, col)
            observed = bind.execute(sa.select(sa.func.max(sa.func.length(col))).select_from(t)).scalar()
            if observed is not None and observed > length:
                violations.append(
                    f"  - {table}.{column}: max observed length {observed} > target {length}."
                    "  Increase target length or truncate source values before retrying."
                )
        if violations:
            raise RuntimeError(
                "String length audit failed — narrowing these columns would abort the"
                " ALTER mid-way or silently truncate.  Fix the source data, then re-run"
                " the migration.\n" + "\n".join(violations)
            )

    by_table: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for table, column, length in _STRING_LENGTH_CHANGES:
        by_table[table].append((column, length))

    for table, columns in by_table.items():
        existing = {c["name"]: c for c in insp.get_columns(table)}
        with op.batch_alter_table(table) as batch_op:
            for column, length in columns:
                info = existing[column]
                raw_default = info.get("default")
                batch_op.alter_column(
                    column,
                    type_=sa.String(length),
                    existing_nullable=info["nullable"],
                    existing_server_default=sa.text(raw_default) if raw_default is not None else None,
                )


def _upgrade_mediumtext() -> None:
    """MySQL 专用：tasks 表大 Text 字段升级为 MEDIUMTEXT。"""
    if not is_mysql():
        return
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {c["name"]: c for c in insp.get_columns("tasks")}
    for col in ("payload_json", "result_json", "execution_checkpoint_json"):
        info = existing[col]
        raw_default = info.get("default")
        op.alter_column(
            "tasks",
            col,
            type_=MEDIUMTEXT,
            existing_nullable=info["nullable"],
            existing_server_default=sa.text(raw_default) if raw_default is not None else None,
        )


def _tasks_dedupe_index() -> None:
    """tasks 表 partial index → 生成列 + 普通 unique index。

    生成列在 MySQL/PG 存去重键的 SHA-256 hex（64 字符）而非拼接原文（理论上界
    1122 字符，超 MySQL InnoDB 3072 字节 / PG btree 约 2704 字节的索引键长上限）；
    SQLite 无长度/键长限制，直接存拼接原文。哈希函数：MySQL sha2()、
    PG encode(sha256(convert_to(...)))。列声明宽度按值域分化：hash 方言 64、
    SQLite 1122——MySQL 的索引键长按声明宽度 × utf8mb4 4B 计，统一声明 1122
    即使存 64 字符 hash 也会在 create_index 时超 3072 上限。
    """
    from lib.db.migration_compat import is_sqlite

    concat_sqlite = (
        "project_name || '|' || task_type || '|' || resource_id || '|' || "
        "COALESCE(script_file, '') || '|' || COALESCE(resource_type, '')"
    )
    if is_mysql():
        expr = (
            "CASE WHEN status IN ('queued', 'running', 'cancelling') "
            "THEN sha2(CONCAT(project_name, '|', task_type, '|', resource_id, '|', "
            "COALESCE(script_file, ''), '|', COALESCE(resource_type, '')), 256) "
            "ELSE NULL END"
        )
    elif is_sqlite():
        expr = f"CASE WHEN status IN ('queued', 'running', 'cancelling') THEN {concat_sqlite} ELSE NULL END"
    else:
        expr = (
            "CASE WHEN status IN ('queued', 'running', 'cancelling') "
            "THEN encode(sha256(convert_to("
            f"{concat_sqlite}, 'UTF8')), 'hex') ELSE NULL END"
        )
    column_width = sa.String(1122) if is_sqlite() else sa.String(64)

    # SQLite 的 ALTER TABLE ADD COLUMN 不支持 STORED 生成列（只支持 VIRTUAL），
    # 必须走 batch_alter_table 重建表；PG/MySQL 上 batch 模式 recreate='auto'
    # 会直接走 ALTER TABLE ADD COLUMN，不重建表。
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "active_dedupe_key",
                column_width,
                sa.Computed(expr, persisted=True),
                nullable=True,
            )
        )
    op.create_index("idx_tasks_dedupe_active", "tasks", ["active_dedupe_key"], unique=True)


def _provider_credential_dedupe_index() -> None:
    """provider_credential 表 partial index → 生成列。"""
    # 三方言兼容：直接用 boolean 列作为条件（PG boolean、SQLite 0/1、MySQL TINYINT(1) 均支持）
    expr = "CASE WHEN is_active THEN provider ELSE NULL END"

    with op.batch_alter_table("provider_credential") as batch_op:
        batch_op.add_column(
            sa.Column(
                "active_provider",
                sa.String(32),
                sa.Computed(expr, persisted=True),
                nullable=True,
            )
        )
    op.create_index(
        "uq_provider_credential_one_active",
        "provider_credential",
        ["active_provider"],
        unique=True,
    )


def _agent_credential_dedupe_index() -> None:
    """agent_anthropic_credentials 表 partial index → 生成列。"""
    expr = "CASE WHEN is_active THEN user_id ELSE NULL END"

    with op.batch_alter_table("agent_anthropic_credentials") as batch_op:
        batch_op.add_column(
            sa.Column(
                "active_user",
                sa.String(64),
                sa.Computed(expr, persisted=True),
                nullable=True,
            )
        )
    op.create_index(
        "uq_agent_credential_one_active_per_user",
        "agent_anthropic_credentials",
        ["active_user"],
        unique=True,
    )


def _agent_entries_uuid_dedupe_index() -> None:
    """agent_session_entries 表 partial index → 生成列。"""
    expr = "CASE WHEN uuid IS NOT NULL THEN uuid ELSE NULL END"

    with op.batch_alter_table("agent_session_entries") as batch_op:
        batch_op.add_column(
            sa.Column(
                "dedupe_uuid",
                sa.String(128),
                sa.Computed(expr, persisted=True),
                nullable=True,
            )
        )
    op.create_index(
        "uq_agent_entries_uuid",
        "agent_session_entries",
        ["project_key", "session_id", "subpath", "dedupe_uuid"],
        unique=True,
    )


def _agent_event_log_client_key_indexes() -> None:
    """agent_session_event_log 表两个 partial index → 生成列 + 普通 index。"""
    expr = "CASE WHEN client_key IS NOT NULL THEN client_key ELSE NULL END"

    with op.batch_alter_table("agent_session_event_log") as batch_op:
        batch_op.add_column(
            sa.Column(
                "dedupe_client_key",
                sa.String(128),
                sa.Computed(expr, persisted=True),
                nullable=True,
            )
        )
    op.create_index(
        "uq_agent_event_log_client_key",
        "agent_session_event_log",
        ["session_id", "dedupe_client_key"],
        unique=True,
    )
    op.create_index(
        "ix_agent_event_log_client_key",
        "agent_session_event_log",
        ["client_key"],
        unique=False,
    )


def _drop_partial_indexes() -> None:
    """Drop all partial indexes before batch_alter_table rebuilds tables.

    SQLite's batch mode (table rebuild) cannot reliably reflect partial indexes
    (sqlite_where). Some historical migrations already lost them during batch
    rebuilds, so use DROP INDEX IF EXISTS to avoid errors on already-missing
    indexes. MySQL never had partial indexes, so this is a no-op there.
    """
    if is_mysql():
        return
    # SQLite + PostgreSQL: both support DROP INDEX IF EXISTS
    op.execute("DROP INDEX IF EXISTS idx_tasks_dedupe_active")
    op.execute("DROP INDEX IF EXISTS uq_provider_credential_one_active")
    op.execute("DROP INDEX IF EXISTS uq_agent_credential_one_active_per_user")
    op.execute("DROP INDEX IF EXISTS uq_agent_entries_uuid")
    op.execute("DROP INDEX IF EXISTS uq_agent_event_log_client_key")
    op.execute("DROP INDEX IF EXISTS ix_agent_event_log_client_key")


def upgrade() -> None:
    """三方言统一改造：字段长度 + MEDIUMTEXT + partial index → 生成列。"""
    _drop_partial_indexes()
    _apply_string_lengths()
    _upgrade_mediumtext()
    _tasks_dedupe_index()
    _provider_credential_dedupe_index()
    _agent_credential_dedupe_index()
    _agent_entries_uuid_dedupe_index()
    _agent_event_log_client_key_indexes()


def downgrade() -> None:
    """回滚到 partial index 方案（字段长度与 MEDIUMTEXT 变更不回滚）。

    MySQL 不支持 partial index，本降级在 MySQL 上会因后续迁移的 drop 失败而
    不可达——生成列方案是 MySQL 上的目标态，无法降级。
    """
    if is_mysql():
        raise NotImplementedError(
            "MySQL 不支持 partial index，本迁移无法降级。生成列方案是 MySQL 上的目标态。"
            " 请从备份恢复，而不是执行 downgrade。"
        )
    # drop 生成列 + 普通索引（SQLite 上 drop 生成列同样需 batch mode）
    op.drop_index("ix_agent_event_log_client_key", table_name="agent_session_event_log")
    op.drop_index("uq_agent_event_log_client_key", table_name="agent_session_event_log")
    with op.batch_alter_table("agent_session_event_log") as batch_op:
        batch_op.drop_column("dedupe_client_key")
    # 恢复被本迁移替换的 partial index（f3d21ac90b17 / bd25b66f82e8 建），
    # 否则继续下行到那两个迁移的 downgrade 时会按名 drop 不存在的索引。
    op.create_index(
        "uq_agent_event_log_client_key",
        "agent_session_event_log",
        ["session_id", "client_key"],
        unique=True,
        postgresql_where=sa.text("client_key IS NOT NULL"),
        sqlite_where=sa.text("client_key IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_event_log_client_key",
        "agent_session_event_log",
        ["client_key"],
        unique=False,
        postgresql_where=sa.text("client_key IS NOT NULL"),
        sqlite_where=sa.text("client_key IS NOT NULL"),
    )

    op.drop_index("uq_agent_entries_uuid", table_name="agent_session_entries")
    with op.batch_alter_table("agent_session_entries") as batch_op:
        batch_op.drop_column("dedupe_uuid")
    op.create_index(
        "uq_agent_entries_uuid",
        "agent_session_entries",
        ["project_key", "session_id", "subpath", "uuid"],
        unique=True,
        postgresql_where=sa.text("uuid IS NOT NULL"),
        sqlite_where=sa.text("uuid IS NOT NULL"),
    )

    op.drop_index("uq_agent_credential_one_active_per_user", table_name="agent_anthropic_credentials")
    with op.batch_alter_table("agent_anthropic_credentials") as batch_op:
        batch_op.drop_column("active_user")
    op.create_index(
        "uq_agent_credential_one_active_per_user",
        "agent_anthropic_credentials",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.drop_index("uq_provider_credential_one_active", table_name="provider_credential")
    with op.batch_alter_table("provider_credential") as batch_op:
        batch_op.drop_column("active_provider")
    op.create_index(
        "uq_provider_credential_one_active",
        "provider_credential",
        ["provider"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.drop_index("idx_tasks_dedupe_active", table_name="tasks")
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("active_dedupe_key")
    op.create_index(
        "idx_tasks_dedupe_active",
        "tasks",
        ["project_name", "task_type", "resource_id", sa.text("COALESCE(script_file, '')")],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
        sqlite_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
    )
