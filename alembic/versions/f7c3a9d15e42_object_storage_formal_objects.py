"""对象存储正式素材对象与引用表

Revision ID: f7c3a9d15e42
Revises: e3b9c4d27a61
Create Date: 2026-11-03 20:00:00.000000

正式素材对象记录远端内容版本所在的对象，引用表记录哪些项目的内容版本、哪些进行中的正式提交在使用它，
失败补偿与连接变更保护都按这两张表判断。两张都是新表，存量实例为空。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7c3a9d15e42"
down_revision: str | Sequence[str] | None = "e3b9c4d27a61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "object_storage_formal_object",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_attempt", sa.String(length=32), nullable=True),
        sa.Column("delete_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=128), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "uq_object_storage_formal_object_key",
        "object_storage_formal_object",
        ["connection_id", "bucket", "object_key"],
        unique=True,
    )
    op.create_index(
        "ix_object_storage_formal_object_state_next", "object_storage_formal_object", ["state", "next_attempt_at"]
    )
    op.create_table(
        "object_storage_formal_object_ref",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column("content_version_id", sa.String(length=64), nullable=True),
        sa.Column("attempt_id", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_object_storage_formal_object_ref_object", "object_storage_formal_object_ref", ["object_id"])
    op.create_index(
        "ix_object_storage_formal_object_ref_version",
        "object_storage_formal_object_ref",
        ["project_name", "content_version_id"],
    )
    op.create_index("ix_object_storage_formal_object_ref_attempt", "object_storage_formal_object_ref", ["attempt_id"])


def downgrade() -> None:
    op.drop_index("ix_object_storage_formal_object_ref_attempt", table_name="object_storage_formal_object_ref")
    op.drop_index("ix_object_storage_formal_object_ref_version", table_name="object_storage_formal_object_ref")
    op.drop_index("ix_object_storage_formal_object_ref_object", table_name="object_storage_formal_object_ref")
    op.drop_table("object_storage_formal_object_ref")
    op.drop_index("ix_object_storage_formal_object_state_next", table_name="object_storage_formal_object")
    op.drop_index("uq_object_storage_formal_object_key", table_name="object_storage_formal_object")
    op.drop_table("object_storage_formal_object")
