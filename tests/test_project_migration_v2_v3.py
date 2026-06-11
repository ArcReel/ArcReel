"""v2→v3 迁移：分集账本回填 + planning_cursor；版本守卫、幂等、余文保留。"""

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


def test_bumps_schema_version_and_backfills(tmp_path: Path):
    d = _write(
        tmp_path,
        {
            "schema_version": 2,
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    migrate_v2_to_v3(d)
    data = _load(d)
    assert data["schema_version"] == 3
    entry = data["episodes"][0]
    assert entry["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": len(EP1)}
    assert entry["ledger_status"] == "planned"
    assert data["planning_cursor"] == {"source_file": "source/novel.txt", "offset": len(EP1)}


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
    """余文文件保留——旧拆分流程仍以它为下一集源文件，物理废除随流程切换进行。"""
    d = _write(tmp_path, {"schema_version": 2, "episodes": []})
    migrate_v2_to_v3(d)
    assert (d / "source" / "_remaining.txt").read_text(encoding="utf-8") == NOVEL[len(EP1) :]


def test_missing_project_json_is_noop(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    migrate_v2_to_v3(tmp_path / "empty")  # 不抛错
