"""v2→v3 迁移：纯版本盖章；episodes 逐字不变、版本守卫、幂等、余文保留。"""

import json
from pathlib import Path

from lib.project_migrations.v2_to_v3_episode_ledger import migrate_v2_to_v3

NOVEL = "第一集的正文内容。第二集还没拆出来的余文。"
EP1 = "第一集的正文内容。"


def _write(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    source = d / "source"
    source.mkdir()
    (source / "novel.txt").write_text(NOVEL, encoding="utf-8")
    (source / "episode_1.txt").write_text(EP1, encoding="utf-8")
    (source / "_remaining.txt").write_text(NOVEL[len(EP1) :], encoding="utf-8")
    return d


def _load(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


def test_bumps_schema_version_only(tmp_path: Path):
    """只写 schema_version：episodes 逐字不变，不补账本字段、不推导 planning_cursor。"""
    episodes = [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}]
    d = _write(tmp_path, {"schema_version": 2, "episodes": episodes})
    migrate_v2_to_v3(d)
    data = _load(d)
    assert data["schema_version"] == 3
    assert data["episodes"] == episodes
    assert "planning_cursor" not in data


def test_existing_ledger_fields_preserved_verbatim(tmp_path: Path):
    """已带账本字段的 v2 项目（手工或旧版本写入）原样保留，迁移不改写。"""
    episodes = [
        {
            "episode": 1,
            "title": "开端",
            "script_file": "scripts/episode_1.json",
            "source_range": {"source_file": "source/novel.txt", "start": 0, "end": len(EP1)},
            "ledger_status": "consumed",
        }
    ]
    d = _write(tmp_path, {"schema_version": 2, "episodes": episodes, "planning_cursor": None})
    migrate_v2_to_v3(d)
    data = _load(d)
    assert data["episodes"] == episodes
    assert data["planning_cursor"] is None


def test_version_guard_skips_already_v3(tmp_path: Path):
    d = _write(
        tmp_path,
        {
            "schema_version": 3,
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    migrate_v2_to_v3(d)
    entry = _load(d)["episodes"][0]
    # 已是 v3 → 不重复回填，条目不被补账本字段
    assert "ledger_status" not in entry


def test_string_schema_version_is_normalized(tmp_path: Path):
    """历史 project.json 可能存字符串版本号，守卫做 int 归一化而非抛 TypeError。"""
    d = _write(tmp_path, {"schema_version": "2", "episodes": []})
    migrate_v2_to_v3(d)
    assert _load(d)["schema_version"] == 3


def test_string_schema_version_guard_skips_v3(tmp_path: Path):
    d = _write(
        tmp_path,
        {
            "schema_version": "3",
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    migrate_v2_to_v3(d)
    entry = _load(d)["episodes"][0]
    assert "ledger_status" not in entry


def test_double_run_idempotent_at_file_level(tmp_path: Path):
    d = _write(tmp_path, {"schema_version": 2, "episodes": []})
    migrate_v2_to_v3(d)
    first = (d / "project.json").read_bytes()
    migrate_v2_to_v3(d)
    assert (d / "project.json").read_bytes() == first


def test_remaining_file_preserved(tmp_path: Path):
    """余文文件保留：迁移只碰 project.json，不动 source/ 下任何文件。"""
    d = _write(tmp_path, {"schema_version": 2, "episodes": []})
    migrate_v2_to_v3(d)
    assert (d / "source" / "_remaining.txt").read_text(encoding="utf-8") == NOVEL[len(EP1) :]


def test_missing_project_json_is_noop(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    migrate_v2_to_v3(tmp_path / "empty")  # 不抛错
