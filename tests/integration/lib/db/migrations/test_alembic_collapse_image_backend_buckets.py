"""Alembic collapse_image_backend_buckets_to_default: exhaustive existing-key combinations.

Once image t2i / i2i mandatory slots become optional override buckets, default_image_backend becomes user-visible.
Enumerate existing default × t2i × i2i value shapes to pin postmigration keys and resolution.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command
from lib.config.resolver import _IMAGE_LAYERED_KEYS, ConfigResolver

PROJECT_ROOT = Path(__file__).resolve().parents[5]

_PREV_REVISION = "d10f1df40f96"
_KEYS = ("default_image_backend", "default_image_backend_t2i", "default_image_backend_i2i")


def _sync_engine(db_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{db_path}")


def _seed(engine: sa.Engine, settings: dict[str, str]) -> None:
    if not settings:
        return
    with engine.begin() as conn:
        for key, value in settings.items():
            conn.execute(
                sa.text(
                    "INSERT INTO system_setting (key, value, updated_at) VALUES (:k, :v, CURRENT_TIMESTAMP)"
                ).bindparams(k=key, v=value)
            )


def _read_image_settings(engine: sa.Engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT key, value FROM system_setting WHERE key IN :keys").bindparams(
                sa.bindparam("keys", value=_KEYS, expanding=True)
            )
        ).fetchall()
    return {row.key: row.value for row in rows}


# (case name, premigration keys, postmigration keys).
#
# Semantics change only in the two rows with invalid/empty buckets plus configured defaults: empty_buckets_with_default and
# mixed_empty_bucket i2i. Empty buckets previously selected automatic inference; afterward they fall back to defaults.
# Every other row resolves identically before/after; migration only normalizes key shapes.
_CASES = [
    ("all_absent", {}, {}),
    (
        "default_only",
        {"default_image_backend": "grok/grok-2-image"},
        {"default_image_backend": "grok/grok-2-image"},
    ),
    (
        "equal_buckets_without_default",
        {"default_image_backend_t2i": "openai/gpt-image-2", "default_image_backend_i2i": "openai/gpt-image-2"},
        {"default_image_backend": "openai/gpt-image-2"},
    ),
    (
        "equal_buckets_matching_default",
        {
            "default_image_backend": "openai/gpt-image-2",
            "default_image_backend_t2i": "openai/gpt-image-2",
            "default_image_backend_i2i": "openai/gpt-image-2",
        },
        {"default_image_backend": "openai/gpt-image-2"},
    ),
    (
        "equal_buckets_diverging_from_default",
        {
            "default_image_backend": "grok/grok-2-image",
            "default_image_backend_t2i": "openai/gpt-image-2",
            "default_image_backend_i2i": "openai/gpt-image-2",
        },
        {
            "default_image_backend": "grok/grok-2-image",
            "default_image_backend_t2i": "openai/gpt-image-2",
            "default_image_backend_i2i": "openai/gpt-image-2",
        },
    ),
    (
        "distinct_buckets_without_default",
        {"default_image_backend_t2i": "openai/gpt-image-2", "default_image_backend_i2i": "ark/kolors-img2img"},
        {"default_image_backend_t2i": "openai/gpt-image-2", "default_image_backend_i2i": "ark/kolors-img2img"},
    ),
    (
        "distinct_buckets_with_default",
        {
            "default_image_backend": "grok/grok-2-image",
            "default_image_backend_t2i": "openai/gpt-image-2",
            "default_image_backend_i2i": "ark/kolors-img2img",
        },
        {
            "default_image_backend": "grok/grok-2-image",
            "default_image_backend_t2i": "openai/gpt-image-2",
            "default_image_backend_i2i": "ark/kolors-img2img",
        },
    ),
    (
        "empty_buckets_with_default",
        {
            "default_image_backend": "grok/grok-2-image",
            "default_image_backend_t2i": "",
            "default_image_backend_i2i": "",
        },
        {"default_image_backend": "grok/grok-2-image"},
    ),
    (
        "empty_buckets_without_default",
        {"default_image_backend_t2i": "", "default_image_backend_i2i": ""},
        {},
    ),
    (
        "mixed_empty_bucket",
        {
            "default_image_backend": "grok/grok-2-image",
            "default_image_backend_t2i": "openai/gpt-image-2",
            "default_image_backend_i2i": "",
        },
        {"default_image_backend": "grok/grok-2-image", "default_image_backend_t2i": "openai/gpt-image-2"},
    ),
    (
        "empty_default_with_equal_buckets",
        {
            "default_image_backend": "",
            "default_image_backend_t2i": "openai/gpt-image-2",
            "default_image_backend_i2i": "openai/gpt-image-2",
        },
        {"default_image_backend": "openai/gpt-image-2"},
    ),
    (
        "bucket_without_slash_dropped",
        {"default_image_backend": "grok/grok-2-image", "default_image_backend_t2i": "openai"},
        {"default_image_backend": "grok/grok-2-image"},
    ),
]


@pytest.mark.parametrize(("name", "before", "after"), _CASES, ids=[case[0] for case in _CASES])
def test_upgrade_exhausts_legacy_key_combinations(alembic_cfg, name, before, after):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, _PREV_REVISION)
    engine = _sync_engine(db_path)
    _seed(engine, before)

    command.upgrade(cfg, "head")

    assert _read_image_settings(engine) == after


# Effective postmigration models (t2i, i2i); None leaves automatic selection because no configuration applies.
_RESOLVED_AFTER: dict[str, tuple[str | None, str | None]] = {
    "all_absent": (None, None),
    "default_only": ("grok/grok-2-image", "grok/grok-2-image"),
    "equal_buckets_without_default": ("openai/gpt-image-2", "openai/gpt-image-2"),
    "equal_buckets_matching_default": ("openai/gpt-image-2", "openai/gpt-image-2"),
    "equal_buckets_diverging_from_default": ("openai/gpt-image-2", "openai/gpt-image-2"),
    "distinct_buckets_without_default": ("openai/gpt-image-2", "ark/kolors-img2img"),
    "distinct_buckets_with_default": ("openai/gpt-image-2", "ark/kolors-img2img"),
    # Changed semantics: previously automatic empty buckets now use defaults.
    "empty_buckets_with_default": ("grok/grok-2-image", "grok/grok-2-image"),
    "empty_buckets_without_default": (None, None),
    # Changed semantics: same for i2i.
    "mixed_empty_bucket": ("openai/gpt-image-2", "grok/grok-2-image"),
    "empty_default_with_equal_buckets": ("openai/gpt-image-2", "openai/gpt-image-2"),
    "bucket_without_slash_dropped": ("grok/grok-2-image", "grok/grok-2-image"),
}


@pytest.mark.parametrize(("name", "before", "after"), _CASES, ids=[case[0] for case in _CASES])
async def test_resolution_after_upgrade(name, before, after):
    """Evaluate migrated output through the real resolution skeleton to pin effective upgraded models for each combination."""
    resolver = ConfigResolver.__new__(ConfigResolver)

    class _Settings:
        async def get_all_settings(self) -> dict[str, str]:
            return dict(after)

    for generation_type, expected in zip(("t2i", "i2i"), _RESOLVED_AFTER[name], strict=True):
        if expected is None:
            # No applicable configuration must reach automatic selection, requiring real provider tables not evaluated here;
            # assert its prerequisite: no valid bucket/default value after migration.
            assert not any("/" in after.get(key, "") for key in (f"default_image_backend_{generation_type}", _KEYS[0]))
            continue
        resolved = await resolver._resolve_layered_backend(
            _Settings(),
            None,
            None,
            _IMAGE_LAYERED_KEYS[generation_type],
        )
        assert resolved == tuple(expected.split("/", 1))


def test_downgrade_restores_buckets_from_default(alembic_cfg):
    """Downgrade copies defaults into both buckets, restoring bucket-authoritative shapes."""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    engine = _sync_engine(db_path)
    _seed(engine, {"default_image_backend": "grok/grok-2-image"})

    command.downgrade(cfg, _PREV_REVISION)

    assert _read_image_settings(engine) == {
        "default_image_backend": "grok/grok-2-image",
        "default_image_backend_t2i": "grok/grok-2-image",
        "default_image_backend_i2i": "grok/grok-2-image",
    }


def test_downgrade_restores_default_over_emptied_bucket(alembic_cfg):
    """Buckets cleared after upgrade, leaving empty-string rows, are not valid downgrade overrides; restore defaults.

    Otherwise old semantics treat empty buckets as authoritative, skip defaults, and silently select automatically.
    """
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    engine = _sync_engine(db_path)
    _seed(
        engine,
        {
            "default_image_backend": "grok/grok-2-image",
            "default_image_backend_t2i": "",
        },
    )

    command.downgrade(cfg, _PREV_REVISION)

    assert _read_image_settings(engine) == {
        "default_image_backend": "grok/grok-2-image",
        "default_image_backend_t2i": "grok/grok-2-image",
        "default_image_backend_i2i": "grok/grok-2-image",
    }


def test_downgrade_keeps_existing_buckets(alembic_cfg):
    """Downgrade must not overwrite configured buckets."""
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    engine = _sync_engine(db_path)
    _seed(
        engine,
        {
            "default_image_backend": "grok/grok-2-image",
            "default_image_backend_t2i": "openai/gpt-image-2",
            "default_image_backend_i2i": "ark/kolors-img2img",
        },
    )

    command.downgrade(cfg, _PREV_REVISION)

    assert _read_image_settings(engine) == {
        "default_image_backend": "grok/grok-2-image",
        "default_image_backend_t2i": "openai/gpt-image-2",
        "default_image_backend_i2i": "ark/kolors-img2img",
    }
