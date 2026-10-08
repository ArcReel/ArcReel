"""enable autoincrement on custom_provider

Revision ID: 015359a572fa
Revises: e3b81f6c4a27
Create Date: 2026-10-08 22:17:47.470974

"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015359a572fa"
down_revision: str | Sequence[str] | None = "e3b81f6c4a27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rebuild_custom_provider(*, autoincrement: bool) -> None:
    """SQLite 只能在建表时声明 AUTOINCREMENT，故整表重建。

    SQLite 的 rowid 主键默认取 max(id)+1，删掉 ID 最大的供应商后新供应商会拿到同一个
    ``custom-<id>``，旧失败记录与用量随之错归。AUTOINCREMENT 让 sqlite_sequence 记住
    用过的最大值；重建时带 id 复制旧行，序列从现存最大 ID 起步，升级时再按历史引用抬高
    （见 :func:`_reserve_referenced_ids`）。

    重建依赖迁移连接未开启 ``foreign_keys`` pragma（env.py 不设）：否则 DROP 旧表会级联
    删光 custom_provider_model。引用方外键按表名指向本表，重建后无需改动。

    其他方言的整数主键本就由序列或 IDENTITY 生成、不回收已删 ID，这里为空操作。
    """
    if op.get_bind().dialect.name != "sqlite":
        return
    with op.batch_alter_table(
        "custom_provider",
        recreate="always",
        table_kwargs={"sqlite_autoincrement": autoincrement},
    ):
        pass


#: 失败记录与用量所在的表。
_REFERENCING_TABLES = ("tasks", "api_calls")
_CUSTOM_PROVIDER_ID = re.compile(r"\bcustom-(\d{1,9})\b")


def _reserve_referenced_ids() -> None:
    """序列至少抬到失败记录与用量引用过的最大自定义供应商 ID。

    升级前删掉的最大 ID 已不在表里，重建后的序列记不住它；这些记录仍引用它时，新供应商拿到
    同一个 ID 会让它们错归。引用不只在供应商列，也嵌在失败信封、失败参数与载荷里，故扫描两张表
    的全部文本列。库外的引用（项目文件）不在扫描范围内。
    """
    bind = op.get_bind()
    referenced = 0
    for table in _REFERENCING_TABLES:
        for column in sa.inspect(bind).get_columns(table):
            if not isinstance(column["type"], (sa.String, sa.JSON)):
                continue
            name = column["name"]
            rows = bind.execute(sa.text(f'SELECT "{name}" FROM {table} WHERE "{name}" LIKE \'%custom-%\''))
            for (value,) in rows:
                referenced = max([referenced, *(int(n) for n in _CUSTOM_PROVIDER_ID.findall(str(value)))])
    if not referenced:
        return
    bind.execute(
        sa.text("UPDATE sqlite_sequence SET seq = :seq WHERE name = 'custom_provider' AND seq < :seq"),
        {"seq": referenced},
    )
    bind.execute(
        sa.text(
            "INSERT INTO sqlite_sequence (name, seq) SELECT 'custom_provider', :seq "
            "WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'custom_provider')"
        ),
        {"seq": referenced},
    )


def upgrade() -> None:
    """Upgrade schema."""
    _rebuild_custom_provider(autoincrement=True)
    if op.get_bind().dialect.name == "sqlite":
        _reserve_referenced_ids()


def downgrade() -> None:
    """Downgrade schema."""
    _rebuild_custom_provider(autoincrement=False)
