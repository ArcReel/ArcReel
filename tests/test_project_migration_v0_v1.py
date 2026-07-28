"""v0→v1 迁移：clues → scenes/props + 剧本级联 + 文件重命名。"""

import json
from pathlib import Path

import pytest

from lib.project_migrations import v0_to_v1_clues_to_scenes_props as mod

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


def test_migrate_order_files_before_schema_bump(tmp_path: Path, monkeypatch):
    """若剧本迁移中途崩溃，schema_version 必须仍为 0（防止下次启动因幂等跳过 → 永久丢图）。"""
    p = _make_v0_project(tmp_path)
    original = mod._migrate_scripts

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("boom mid-migration")

    monkeypatch.setattr(mod, "_migrate_scripts", fail)
    with pytest.raises(RuntimeError):
        mod.migrate_v0_to_v1(p)

    data = json.loads((p / "project.json").read_text(encoding="utf-8"))
    assert data.get("schema_version", 0) == 0
    assert "clues" in data  # project.json 未升级，幂等检查会重试


def test_migrate_self_heals_half_migrated(tmp_path: Path):
    """schema_version=1 但 clues/ 仍存在 → 自愈补跑。"""
    p = tmp_path / "demo"
    p.mkdir()
    (p / "characters").mkdir()
    (p / "clues").mkdir()
    (p / "clues" / "玉佩.png").write_bytes(b"prop-image")
    (p / "clues" / "庙宇.png").write_bytes(b"scene-image")
    (p / "scripts").mkdir()

    # 模拟"半迁移"状态：project.json 已 v1，但 clues/ 文件未搬走、剧本 clues[] 未拆
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
