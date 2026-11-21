"""Move legacy Vidu Q3 Mix bindings using the standard official API to the Mix endpoint."""

from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement, TableClause

from alembic import op

revision: str = "2d4a9f6c8b31"
down_revision: str | Sequence[str] | None = "b793a01c4e62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODEL = "viduq3-mix"
_REFERENCE = "vidu-video-q3-reference"
_MIX = "vidu-video-q3-mix"
_DEFAULT_BASE_URL = "https://api.vidu.cn/ent/v2"
_OFFICIAL_AUTHORITIES = frozenset({"api.vidu.cn", "api.vidu.cn:443", "api.vidu.com", "api.vidu.com:443"})


def _official_base_url(value: str, *, custom: bool = False) -> bool:
    """Match the official mount after the custom Vidu builder's host and path expansion."""
    if custom:
        value = value.strip().rstrip("/")
        if "://" not in value:
            value = "https://" + value
    if any(ord(character) <= 32 or ord(character) == 127 for character in value) or "?" in value or "#" in value:
        return False
    try:
        url = urlsplit(value)
    except ValueError:
        return False
    if url.scheme != "https" or url.netloc.lower() not in _OFFICIAL_AUTHORITIES:
        return False
    return url.path in {"/ent/v2", "/ent/v2/"} or (custom and not url.path)


def _legacy_mix(table: TableClause) -> ColumnElement[bool]:
    return sa.and_(
        table.c.model_id == _MODEL,
        table.c.endpoint == _REFERENCE,
        sa.or_(table.c.entry_binding.is_(None), table.c.entry_binding == "shengshu/viduq3-mix"),
    )


def _builtin_base_url(conn: sa.Connection) -> str:
    credential_url = conn.execute(
        sa.text("SELECT base_url FROM provider_credential WHERE provider = 'vidu' AND is_active")
    ).scalar_one_or_none()
    config_url = conn.execute(
        sa.text("SELECT value FROM provider_config WHERE provider = 'vidu' AND key = 'base_url'")
    ).scalar_one_or_none()
    return credential_url or config_url or _DEFAULT_BASE_URL


def _migrate_builtin(conn: sa.Connection) -> None:
    if not _official_base_url(_builtin_base_url(conn)):
        return
    model = sa.table(
        "builtin_provider_model",
        sa.column("id", sa.Integer),
        sa.column("provider_id", sa.String),
        sa.column("model_id", sa.String),
        sa.column("endpoint", sa.String),
        sa.column("entry_binding", sa.String),
        sa.column("user_added", sa.Boolean),
        sa.column("media_type", sa.String),
        sa.column("capability_overrides", sa.JSON),
    )
    rows = conn.execute(
        sa.select(model.c.id, model.c.capability_overrides).where(
            _legacy_mix(model), model.c.provider_id == "vidu", model.c.user_added, model.c.media_type == "video"
        )
    )
    ids = [row.id for row in rows if row.capability_overrides in (None, {})]
    if ids:
        conn.execute(model.update().where(model.c.id.in_(ids)).values(endpoint=_MIX))


def _migrate_custom(conn: sa.Connection) -> None:
    model = sa.table(
        "custom_provider_model",
        sa.column("id", sa.Integer),
        sa.column("provider_id", sa.Integer),
        sa.column("model_id", sa.String),
        sa.column("endpoint", sa.String),
        sa.column("entry_binding", sa.String),
        sa.column("capability_overrides", sa.JSON),
        sa.column("path_overrides", sa.JSON),
    )
    provider = sa.table("custom_provider", sa.column("id", sa.Integer), sa.column("base_url", sa.String))
    rows = conn.execute(
        sa.select(model.c.id, model.c.capability_overrides, model.c.path_overrides, provider.c.base_url)
        .join(provider, model.c.provider_id == provider.c.id)
        .where(_legacy_mix(model))
    )
    ids = [
        row.id
        for row in rows
        if row.capability_overrides in (None, {})
        and row.path_overrides in (None, {})
        and _official_base_url(row.base_url, custom=True)
    ]
    if ids:
        conn.execute(model.update().where(model.c.id.in_(ids)).values(endpoint=_MIX))


def upgrade() -> None:
    conn = op.get_bind()
    _migrate_builtin(conn)
    _migrate_custom(conn)


def downgrade() -> None:
    """Keep the corrected endpoint because it is supported by the parent and indistinguishable from preexisting Mix bindings."""
