"""collapse image backend buckets into the default layer

Revision ID: b7f2c41d9a30
Revises: d10f1df40f96
Create Date: 2026-08-02 20:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeGuard

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f2c41d9a30"
down_revision: str | Sequence[str] | None = "d10f1df40f96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_KEY = "default_image_backend"
_BUCKET_KEYS = ("default_image_backend_t2i", "default_image_backend_i2i")


def _read(bind: sa.Connection, key: str) -> str | None:
    row = bind.execute(sa.text("SELECT value FROM system_setting WHERE key = :k").bindparams(k=key)).fetchone()
    return None if row is None else row[0]


def _write(bind: sa.Connection, key: str, value: str) -> None:
    updated = bind.execute(
        sa.text("UPDATE system_setting SET value = :v, updated_at = CURRENT_TIMESTAMP WHERE key = :k").bindparams(
            k=key, v=value
        )
    ).rowcount
    if not updated:
        bind.execute(
            sa.text(
                "INSERT INTO system_setting (key, value, updated_at) VALUES (:k, :v, CURRENT_TIMESTAMP)"
            ).bindparams(k=key, v=value)
        )


def _delete(bind: sa.Connection, key: str) -> None:
    bind.execute(sa.text("DELETE FROM system_setting WHERE key = :k").bindparams(k=key))


def _configured(value: str | None) -> TypeGuard[str]:
    """Valid configuration has the form ``provider/model``; empty or slashless residual values are not configured."""
    return value is not None and "/" in value


def upgrade() -> None:
    """Make image t2i / i2i optional override buckets, promoting default_image_backend as the default layer.

    Handle existing key combinations individually (``docs/adr/0054``):

    - Invalid bucket (empty / no slash): delete. Previously this meant explicit clearing followed by
      automatic inference; now an absent bucket falls back to the default layer. Deletion normalizes
      both forms and removes empty buckets that could be mistaken for configured overrides.
    - Equal buckets with no configured default: promote to default and delete both buckets. Users
      who configured one image model before splitting return to the canonical default-only form.
    - Equal buckets matching the default: delete both redundant copies from the splitting migration.
    - Otherwise (different buckets, or equal buckets differing from a configured default): preserve
      unchanged, continuing to apply bucket overrides.
    """
    bind = op.get_bind()
    default = _read(bind, _DEFAULT_KEY)
    buckets = {key: _read(bind, key) for key in _BUCKET_KEYS}

    for key, value in buckets.items():
        if value is not None and not _configured(value):
            _delete(bind, key)
            buckets[key] = None

    t2i, i2i = (buckets[key] for key in _BUCKET_KEYS)
    if not _configured(t2i) or t2i != i2i:
        return
    if not _configured(default):
        _write(bind, _DEFAULT_KEY, t2i)
    elif default != t2i:
        return
    for key in _BUCKET_KEYS:
        _delete(bind, key)


def downgrade() -> None:
    """Restore authoritative buckets: copy a configured default into both buckets if neither has a valid value.

    Normalize invalid bucket rows using upgrade semantics first: clearing a bucket writes an empty row
    rather than deleting it (backend-key writes in ``server/routers/system_config.py``). Treating row
    existence as a valid override would make old semantics prefer that empty bucket after downgrade,
    skipping the configured default and silently falling back to automatic inference.

    Deleted invalid values cannot be reconstructed individually: old explicit-clear and never-configured
    states become identical after downgrade; these configurations are treated as unconfigured.
    """
    bind = op.get_bind()
    default = _read(bind, _DEFAULT_KEY)
    if not _configured(default):
        return
    for key in _BUCKET_KEYS:
        if not _configured(_read(bind, key)):
            _delete(bind, key)
    if any(_read(bind, key) is not None for key in _BUCKET_KEYS):
        return
    for key in _BUCKET_KEYS:
        _write(bind, key, default)
