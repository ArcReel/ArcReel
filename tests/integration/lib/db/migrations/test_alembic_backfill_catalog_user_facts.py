"""Alembic: backfill user-entry copies with preupgrade reference-image limits, video inputs/reference audio, and image-token estimation bases."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from arcreel_catalog_core import ImageFacts, VideoFacts, parse_entry

_SEEDANCE_2 = {
    "maker": "bytedance",
    "name": "doubao-seedance-2.0",
    "media_type": "video",
    "display_name": "我的 Seedance",
    "aliases": ["doubao-seedance-2-0-260128"],
    "facts": {"durations": [5, 10], "resolutions": ["720p"]},
}
_GPT_IMAGE_2 = {
    "maker": "openai",
    "name": "gpt-image-2",
    "media_type": "image",
    "display_name": "GPT Image 2",
    "facts": {"text_to_image": True, "image_to_image": True, "resolutions": ["1K", "2K"]},
}


def _upgrade_with(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
    rows: list[tuple[dict[str, object], bool]],
) -> list[tuple[dict[str, object], int, str]]:
    """Insert ``(条目, 是否用户新建)`` at the parent revision, upgrade, and read ``(条目, user_created, updated_at)`` in insertion order."""
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_backfill_catalog_user_facts.py")
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            for entry, user_created in rows:
                conn.execute(
                    sa.text(
                        "INSERT INTO catalog_user_entry "
                        "(maker, name, media_type, entry, user_created, created_at, updated_at) "
                        "VALUES (:maker, :name, :media_type, :entry, :user_created, '2026-10-01', '2026-10-02')"
                    ),
                    {
                        "maker": entry["maker"],
                        "name": entry["name"],
                        "media_type": entry["media_type"],
                        "entry": json.dumps(entry),
                        "user_created": user_created,
                    },
                )
        command.upgrade(cfg, revision)
        with engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT entry, user_created, updated_at FROM catalog_user_entry ORDER BY id")
            ).all()
    finally:
        engine.dispose()
    return [(json.loads(raw), int(created), str(updated)) for raw, created, updated in result]


def test_seedance_2_copy_keeps_last_frame_and_reference_audio(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    [(entry, user_created, updated_at)] = _upgrade_with(alembic_cfg, migration_revisions, [(_SEEDANCE_2, False)])

    facts = parse_entry(entry).facts
    assert isinstance(facts, VideoFacts)
    # Unlisted first/last frames enabled only first frames; preupgrade Seedance 2 accepted last frames, so record that explicitly in copies.
    assert (facts.text_to_video, facts.first_frame, facts.last_frame) == (True, True, True)
    assert facts.max_reference_images == 9
    assert (facts.reference_audio, facts.max_reference_audio_count, facts.max_reference_audio_total_seconds) == (
        True,
        3,
        15.0,
    )
    assert entry["display_name"] == "我的 Seedance"
    assert (user_created, updated_at) == (0, "2026-10-02")


def test_gpt_image_2_copy_keeps_output_token_estimate(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    [(entry, _, _)] = _upgrade_with(alembic_cfg, migration_revisions, [(_GPT_IMAGE_2, False)])

    facts = parse_entry(entry).facts
    assert isinstance(facts, ImageFacts)
    # gpt-image-2 quality bases from the official Calculating costs calculator.
    assert dict(facts.output_token_bases) == {"low": 16, "medium": 48, "high": 96}


def test_fields_the_user_filled_are_kept(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    filled = {**_SEEDANCE_2, "facts": {**_SEEDANCE_2["facts"], "last_frame": False, "max_reference_audio_count": 1}}
    unknown = {**_SEEDANCE_2, "name": "my-own-video", "aliases": [], "display_name": "自建"}
    [(kept, _, _), (created, user_created, _)] = _upgrade_with(
        alembic_cfg, migration_revisions, [(filled, False), (unknown, True)]
    )

    facts = parse_entry(kept).facts
    assert isinstance(facts, VideoFacts)
    assert facts.last_frame is False
    # Treat three reference-audio fields as a group: if users set one, do not backfill others into an unconfirmed combination.
    assert (facts.reference_audio, facts.max_reference_audio_count, facts.max_reference_audio_total_seconds) == (
        None,
        1,
        None,
    )
    assert (facts.first_frame, facts.max_reference_images) == (True, 9)
    # Names unknown before upgrade have only channel fallback, no model facts to backfill; leave copies unlisted.
    assert created == unknown
    assert user_created == 1
