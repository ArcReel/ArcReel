"""enable autoincrement on custom_provider

Revision ID: 015359a572fa
Revises: e3b81f6c4a27
Create Date: 2026-10-08 22:17:47.470974

"""

from collections.abc import Sequence

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
    用过的最大值；重建时带 id 复制旧行，序列从现存最大 ID 起步。

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


def upgrade() -> None:
    """Upgrade schema."""
    _rebuild_custom_provider(autoincrement=True)


def downgrade() -> None:
    """Downgrade schema."""
    _rebuild_custom_provider(autoincrement=False)
