"""v2→v3 迁移：纯版本盖章；episodes 逐字不变、版本守卫、幂等、余文保留。"""

import json
from pathlib import Path

import pytest

from lib.data_validator import DataValidator
from lib.project_migrations import v2_to_v3_episode_ledger
from lib.project_migrations.runner import migrate_project_dir
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


def test_upgraded_project_passes_consumer_side_validation(tmp_path: Path):
    """升级后的项目对消费链路照常有效：无位置记录的账本条目不被判损坏。

    盖章迁移刻意留下没有 ``source_range`` / ``ledger_status`` 的条目，这是老项目升级后的
    常态。校验器是所有消费链路（剧本 / 媒体 / 状态 / 导出）的入口守卫，它放行才谈得上
    「升级后照常工作」——规划入口另有门禁拒绝这类条目，见 ``test_episode_planner``。
    """
    d = _write(
        tmp_path,
        {
            "schema_version": 2,
            "title": "Demo",
            "content_mode": "narration",
            "style": "Anime",
            "characters": {"姜月茴": {"description": "女主"}},
            "scenes": {"古宅": {"description": "废弃古宅"}},
            "props": {"玉佩": {"description": "关键道具"}},
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    # 校验器按最新 schema 形态断言（如 generation_mode 必填），消费链路入口前项目
    # 必然已走完整迁移链，这里同口径跑到当前版本再校验
    migrate_project_dir(d)
    result = DataValidator(projects_root=str(tmp_path)).validate_project_payload(_load(d))
    assert result.errors == []


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


@pytest.fixture
def no_write_guard(monkeypatch: pytest.MonkeyPatch):
    """守卫生效的判据是「一次盘都没写」。

    盖章迁移只写 schema_version，对已是 v3 的项目即便守卫失效、迁移照跑一遍，落盘内容
    也与原文件一致——只断言字段/内容不变无法证伪，必须直接盯写入本身。
    """
    calls: list[Path] = []

    def _spy(path: Path, data: dict) -> None:
        calls.append(path)

    monkeypatch.setattr(v2_to_v3_episode_ledger, "atomic_write_json", _spy)
    return calls


def test_version_guard_skips_already_v3(tmp_path: Path, no_write_guard: list[Path]):
    d = _write(
        tmp_path,
        {
            "schema_version": 3,
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    migrate_v2_to_v3(d)
    assert no_write_guard == []


def test_string_schema_version_is_normalized(tmp_path: Path):
    """历史 project.json 可能存字符串版本号，守卫做 int 归一化而非抛 TypeError。"""
    d = _write(tmp_path, {"schema_version": "2", "episodes": []})
    migrate_v2_to_v3(d)
    assert _load(d)["schema_version"] == 3


def test_string_schema_version_guard_skips_v3(tmp_path: Path, no_write_guard: list[Path]):
    d = _write(
        tmp_path,
        {
            "schema_version": "3",
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    migrate_v2_to_v3(d)
    assert no_write_guard == []


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
    """没有 project.json 的目录：迁移既不抛错，也不得凭空造出任何文件。"""
    empty = tmp_path / "empty"
    empty.mkdir()

    assert migrate_v2_to_v3(empty) is None
    assert list(empty.iterdir()) == []
