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
    """SQLite declares AUTOINCREMENT only at table creation, so rebuild the table.

    SQLite rowid primary keys default to max(id)+1. Deleting the highest-ID provider lets a new provider
    reuse its ``custom-<id>``, misattributing old failures and usage. AUTOINCREMENT keeps the highest
    used value in sqlite_sequence. Rebuilding copies existing IDs, starts from the current maximum,
    then raises the sequence for historical references (see :func:`_reserve_referenced_ids`).

    Rebuilding requires ``foreign_keys`` pragma disabled on the migration connection (env.py does not set it);
    otherwise dropping the old table cascades deletion of custom_provider_model. Foreign keys refer by
    table name, so need no changes after rebuilding.

    Other dialects already use sequences or IDENTITY without recycling deleted integer IDs; no-op there.
    """
    if op.get_bind().dialect.name != "sqlite":
        return
    with op.batch_alter_table(
        "custom_provider",
        recreate="always",
        table_kwargs={"sqlite_autoincrement": autoincrement},
    ):
        pass


#: Tables containing failures and usage.
_REFERENCING_TABLES = ("tasks", "api_calls")
_CUSTOM_PROVIDER_ID = re.compile(r"\bcustom-(\d{1,9})\b")


def _reserve_referenced_ids() -> None:
    """Raise the sequence at least to the largest custom-provider ID referenced by failures and usage.

    The largest ID deleted before upgrade is absent, so the rebuilt sequence forgets it. Existing references
    would then be misattributed if a new provider reused it. References occur in provider columns and
    failure envelopes, parameters, and payloads, so scan every text column of both tables. References
    outside the database (project files) are outside this scan.
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
