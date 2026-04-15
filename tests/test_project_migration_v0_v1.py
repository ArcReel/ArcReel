"""v0→v1 迁移：clues → scenes/props + 剧本级联 + 文件重命名。"""

import json
from pathlib import Path

from lib.project_migrations.v0_to_v1_clues_to_scenes_props import migrate_v0_to_v1


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
    # importance / type 字段被清理
    assert "importance" not in data["props"]["玉佩"]
    assert "type" not in data["props"]["玉佩"]
    # sheet 字段重命名
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
    migrate_v0_to_v1(p)  # 再跑一次不应抛错
    data = json.loads((p / "project.json").read_text())
    assert data["schema_version"] == 1
