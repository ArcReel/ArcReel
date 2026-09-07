"""add usage record columns to api_calls

Revision ID: 7c1e5b93a204
Revises: 9f3c7a52d1b4
Create Date: 2026-09-07 16:10:00.000000

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c1e5b93a204"
down_revision: str | Sequence[str] | None = "c9fcbd98e62c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS = ("task_id", "purpose", "session_id", "inputs", "error_code", "error_params")
_TASK_ID_INDEX = "idx_api_calls_task_id"


def _backfill_task_id_from_task_payload() -> None:
    """把历史 video 任务的 ``payload_json.api_call_id`` 反向写成 ``api_calls.task_id``。

    ``payload_json`` 是 Text 列（两种方言上都不是原生 JSON 类型），故解析放在 Python 侧，
    不用任一方言的 JSON 函数。回填只做能精确得到的：反查得到的行同时标记
    ``purpose = generation_task``，其余新列留空。
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT task_id, payload_json FROM tasks WHERE media_type = 'video' AND payload_json IS NOT NULL")
    ).fetchall()

    updates: list[dict[str, object]] = []
    for task_id, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        call_id = payload.get("api_call_id")
        # bool 是 int 的子类，单独排除，避免把 payload 里的布尔值当成行号。
        if isinstance(call_id, bool) or not isinstance(call_id, int):
            continue
        updates.append({"call_id": call_id, "task_id": task_id})

    if updates:
        bind.execute(
            sa.text("UPDATE api_calls SET task_id = :task_id, purpose = 'generation_task' WHERE id = :call_id"),
            updates,
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("api_calls", sa.Column("task_id", sa.String(), nullable=True))
    op.add_column("api_calls", sa.Column("purpose", sa.String(), nullable=True))
    op.add_column("api_calls", sa.Column("session_id", sa.String(), nullable=True))
    op.add_column("api_calls", sa.Column("inputs", sa.JSON(), nullable=True))
    op.add_column("api_calls", sa.Column("error_code", sa.String(), nullable=True))
    op.add_column("api_calls", sa.Column("error_params", sa.JSON(), nullable=True))
    op.create_index(_TASK_ID_INDEX, "api_calls", ["task_id"], unique=False)
    _backfill_task_id_from_task_payload()


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(_TASK_ID_INDEX, table_name="api_calls")
    for column in reversed(_NEW_COLUMNS):
        op.drop_column("api_calls", column)
