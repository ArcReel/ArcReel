"""rebuild session event log for typed skill and subagent entries

Revision ID: 3b841838ac16
Revises: f3d21ac90b17
Create Date: 2026-07-06 16:55:43.613251

事件日志是 SDK transcript 的物化视图，删除不丢真相：写入点定型规则演进
（skill 调用条目只记名与入参、懒生成合并 subagent subpath）后，按旧规则
生成的存量条目整表清空，各会话首次访问时按新规则从 transcript 懒生成重建。

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b841838ac16"
down_revision: str | Sequence[str] | None = "f3d21ac90b17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.text("DELETE FROM agent_session_event_log"))


def downgrade() -> None:
    """Downgrade schema."""
    # 数据迁移不可逆；旧代码同样按需懒生成，无需回填
    pass
