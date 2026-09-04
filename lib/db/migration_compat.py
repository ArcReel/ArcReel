"""Cross-dialect compatibility helpers for Alembic migrations.

MySQL lacks partial/filtered indexes (``sqlite_where`` / ``postgresql_where``)
and does not accept function expressions (e.g. ``COALESCE(col, '')``) as
index columns without a generated column wrapper. Legacy migrations that
create such indexes must skip on MySQL — a later unification migration
installs the generated-column equivalent for all dialects.

MySQL also treats ``key`` as a reserved word (used in ``CREATE TABLE`` index
clauses); raw SQL referencing a column literally named ``key`` must be quoted
with backticks on MySQL. Use :func:`qident` to quote identifiers portably.
"""

from __future__ import annotations

from alembic import op


def is_mysql() -> bool:
    """True if the current migration is running against MySQL."""
    bind = op.get_bind()
    return bind.dialect.name == "mysql"


def is_sqlite() -> bool:
    """True if the current migration is running against SQLite."""
    bind = op.get_bind()
    return bind.dialect.name == "sqlite"


def qident(name: str) -> str:
    """Quote a SQL identifier for the current dialect.

    Delegates to the dialect's ``identifier_preparer``, which only quotes when
    necessary (reserved word, special chars, etc.). On MySQL ``key`` becomes
    `` `key` ``; on SQLite/PostgreSQL it stays bare ``key``.
    """
    bind = op.get_bind()
    return bind.dialect.identifier_preparer.quote(name)
