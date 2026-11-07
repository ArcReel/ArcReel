"""v0→v1 migration: clues → scenes/props, script cascading, and file renaming."""

import json
from pathlib import Path

import pytest

from lib.project.project_migrations import v0_to_v1_clues_to_scenes_props as mod

migrate_v0_to_v1 = mod.migrate_v0_to_v1


def _make_v0_project(root: Path) -> Path:
    p = root / "demo"
    (p / "characters").mkdir(parents=True)
    (p / "clues").mkdir(parents=True)
    (p / "clues" / "玉佩.png").write_bytes(b"prop-image")
    (p / "clues" / "庙宇.png").write_bytes(b"scene-image")
    (p / "scripts").mkdir(parents=True)

    (p / "project.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "characters": {"王小明": {"description": "", "voice_style": ""}},
                "clues": {
                    "玉佩": {
                        "type": "prop",
                        "importance": "major",
                        "description": "白玉",
                        "clue_sheet": "clues/玉佩.png",
                    },
                    "庙宇": {"type": "location", "importance": "minor", "description": "阴森"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (p / "scripts" / "ep1.json").write_text(
        json.dumps(
            {
                "content_mode": "drama",
                "scenes": [
                    {"scene_id": "s1", "characters": ["王小明"], "clues": ["玉佩", "庙宇"]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return p


def test_migrate_v0_to_v1_project_json(tmp_path: Path):
    p = _make_v0_project(tmp_path)
    migrate_v0_to_v1(p)

    data = json.loads((p / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert "clues" not in data
    assert set(data["scenes"].keys()) == {"庙宇"}
    assert set(data["props"].keys()) == {"玉佩"}
    # Remove importance / type fields.
    assert "importance" not in data["props"]["玉佩"]
    assert "type" not in data["props"]["玉佩"]
    # Rename sheet fields.
    assert data["props"]["玉佩"]["prop_sheet"] == "props/玉佩.png"
    assert "clue_sheet" not in data["props"]["玉佩"]


def test_migrate_v0_to_v1_moves_files(tmp_path: Path):
    p = _make_v0_project(tmp_path)
    migrate_v0_to_v1(p)

    assert not (p / "clues").exists()
    assert (p / "scenes" / "庙宇.png").read_bytes() == b"scene-image"
    assert (p / "props" / "玉佩.png").read_bytes() == b"prop-image"


def test_migrate_v0_to_v1_script_clues_split(tmp_path: Path):
    p = _make_v0_project(tmp_path)
    migrate_v0_to_v1(p)

    script = json.loads((p / "scripts" / "ep1.json").read_text(encoding="utf-8"))
    assert script["schema_version"] == 1
    scene = script["scenes"][0]
    assert "clues" not in scene
    assert scene["scenes"] == ["庙宇"]
    assert scene["props"] == ["玉佩"]


def test_migrate_idempotent(tmp_path: Path):
    p = _make_v0_project(tmp_path)
    migrate_v0_to_v1(p)
    migrate_v0_to_v1(p)  # Repeating must not raise.
    data = json.loads((p / "project.json").read_text())
    assert data["schema_version"] == 1


def test_migrate_order_files_before_schema_bump(tmp_path: Path):
    """If script migration crashes midway, schema_version must remain 0 to prevent an idempotent skip on next startup and permanent image loss.

    A malformed script triggers a real crash: manually corrupted v0 ``scenes`` stores a string instead of an object, and cascading
    raises AttributeError at ``pop``. File relocation precedes script cascading, producing precisely the state where images moved, scripts
    remain partly rewritten, and the version is not upgraded.
    """
    p = _make_v0_project(tmp_path)
    (p / "scripts" / "ep1.json").write_text(
        json.dumps({"content_mode": "drama", "scenes": ["clues"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(AttributeError):
        mod.migrate_v0_to_v1(p)

    # Step one (image relocation) completed first; the crash occurs in step two.
    assert (p / "props" / "玉佩.png").exists()
    assert (p / "scenes" / "庙宇.png").exists()

    data = json.loads((p / "project.json").read_text(encoding="utf-8"))
    assert data.get("schema_version", 0) == 0
    assert "clues" in data  # project.json is not upgraded, so idempotency checks permit retry.


def test_migrate_self_heals_half_migrated(tmp_path: Path):
    """schema_version=1 with clues/ still present triggers self-healing rerun."""
    p = tmp_path / "demo"
    p.mkdir()
    (p / "characters").mkdir()
    (p / "clues").mkdir()
    (p / "clues" / "玉佩.png").write_bytes(b"prop-image")
    (p / "clues" / "庙宇.png").write_bytes(b"scene-image")
    (p / "scripts").mkdir()

    # Simulate partial migration: project.json is v1, but clues/ files remain and script clues[] is unsplit.
    (p / "project.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "schema_version": 1,
                "scenes": {"庙宇": {"description": "阴森"}},
                "props": {"玉佩": {"description": "白玉", "prop_sheet": "props/玉佩.png"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (p / "scripts" / "ep1.json").write_text(
        json.dumps(
            {
                "content_mode": "drama",
                "scenes": [{"scene_id": "s1", "characters": ["王小明"], "clues": ["玉佩", "庙宇"]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    migrate_v0_to_v1(p)

    assert not (p / "clues").exists()
    assert (p / "scenes" / "庙宇.png").read_bytes() == b"scene-image"
    assert (p / "props" / "玉佩.png").read_bytes() == b"prop-image"
    script = json.loads((p / "scripts" / "ep1.json").read_text(encoding="utf-8"))
    assert script["scenes"][0]["scenes"] == ["庙宇"]
    assert script["scenes"][0]["props"] == ["玉佩"]
