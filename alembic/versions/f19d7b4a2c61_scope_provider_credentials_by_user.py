"""scope provider credentials by local user

Revision ID: f19d7b4a2c61
Revises: f08c1d2e3a44
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f19d7b4a2c61"
down_revision: str | Sequence[str] | None = "f08c1d2e3a44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Attach each provider credential to exactly one ArcReel user."""
    op.drop_index("uq_provider_credential_one_active", table_name="provider_credential")
    with op.batch_alter_table("provider_credential", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.String(), server_default="default", nullable=False))
        batch_op.create_foreign_key(
            "fk_provider_credential_user_id",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_provider_credential_user_provider", ["user_id", "provider"], unique=False)
        batch_op.create_index(
            "uq_provider_credential_one_active",
            ["user_id", "provider"],
            unique=True,
            sqlite_where=sa.text("is_active = 1"),
            postgresql_where=sa.text("is_active"),
        )


def downgrade() -> None:
    # The legacy schema permits only one active row per provider globally.
    # Multi-user data can legitimately contain one active row per user, so
    # deterministically keep the default user's row (then lowest id) active and
    # retain every other credential as inactive before recreating the old index.
    conn = op.get_bind()
    credentials = sa.table(
        "provider_credential",
        sa.column("id", sa.Integer),
        sa.column("user_id", sa.String),
        sa.column("provider", sa.String),
        sa.column("is_active", sa.Boolean),
    )
    active_rows = conn.execute(
        sa.select(credentials.c.id, credentials.c.provider, credentials.c.user_id)
        .where(credentials.c.is_active.is_(True))
        .order_by(
            credentials.c.provider,
            sa.case((credentials.c.user_id == "default", 0), else_=1),
            credentials.c.id,
        )
    ).all()
    keep_by_provider: dict[str, int] = {}
    deactivate: list[int] = []
    for row in active_rows:
        provider = str(row.provider)
        if provider not in keep_by_provider:
            keep_by_provider[provider] = int(row.id)
        else:
            deactivate.append(int(row.id))
    if deactivate:
        conn.execute(
            credentials.update().where(credentials.c.id.in_(deactivate)).values(is_active=False)
        )

    with op.batch_alter_table("provider_credential", schema=None) as batch_op:
        batch_op.drop_index("uq_provider_credential_one_active")
        batch_op.drop_index("ix_provider_credential_user_provider")
        batch_op.drop_constraint("fk_provider_credential_user_id", type_="foreignkey")
        batch_op.drop_column("user_id")
    op.create_index(
        "uq_provider_credential_one_active",
        "provider_credential",
        ["provider"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active"),
    )
