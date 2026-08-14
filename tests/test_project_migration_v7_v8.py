"""v7→v8 领域数据契约迁移：字段更名、统计去持久化、预检原子性。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lib.project_migrations import v7_to_v8_domain_contract as v7_to_v8
from lib.project_migrations.runner import CURRENT_SCHEMA_VERSION, migrate_project_dir
from lib.project_migrations.v7_to_v8_domain_contract import (
    _ensure_backup,
    migrate_script_payload,
    migrate_v7_to_v8,
)

pytestmark = pytest.mark.integration


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _v7_project(
    tmp_path: Path,
    *,
    content_mode: str = "drama",
    source_kind: str = "screenplay",
    generation_mode: str = "storyboard",
    episodes: int = 1,
    extra_episode_fields: dict | None = None,
) -> Path:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    episode_entries = []
    for number in range(1, episodes + 1):
        entry = {
            "episode": number,
            "title": f"第 {number} 集",
            "script_file": f"scripts/episode_{number}.json",
            "scenes_count": 9,
        }
        if extra_episode_fields:
            entry.update(extra_episode_fields)
        episode_entries.append(entry)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 7,
            "content_mode": content_mode,
            "source_kind": source_kind,
            "generation_mode": generation_mode,
            "episodes": episode_entries,
        },
    )
    return project_dir


def _storyboard_script(*, content_mode: str = "drama") -> dict:
    items_key = {"narration": "segments", "drama": "scenes", "ad": "shots"}[content_mode]
    id_field = {"narration": "segment_id", "drama": "scene_id", "ad": "shot_id"}[content_mode]
    return {
        "episode": 1,
        "title": "开端",
        "content_mode": content_mode,
        "metadata": {"total_scenes": 2, "estimated_duration_seconds": 16},
        items_key: [
            {id_field: "E1S1", "duration_seconds": 8, "generated_assets": {}},
            {id_field: "E1S2", "duration_seconds": 8, "generated_assets": {}},
        ],
    }


def _reference_script(*, content_mode: str = "narration") -> dict:
    return {
        "episode": 1,
        "title": "参考集",
        "content_mode": content_mode,
        "metadata": {"total_scenes": 1},
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"text": "镜头1：@[英雄] 走进大厅"}],
                "references": [{"type": "character", "name": "英雄"}],
                "duration_seconds": 8,
                "generated_assets": {},
            }
        ],
    }


def test_renames_project_and_script_contract_fields(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())

    migrate_v7_to_v8(project_dir)

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == 8
    assert project["creation_type"] == "drama"
    assert project["source_file_type"] == "screenplay"
    assert "content_mode" not in project
    assert "source_kind" not in project
    assert "scenes_count" not in project["episodes"][0]

    script = _read_json(project_dir / "scripts/episode_1.json")
    assert script["creation_type"] == "drama"
    assert "content_mode" not in script
    assert "total_scenes" not in script.get("metadata", {})
    assert [item["scene_id"] for item in script["scenes"]] == ["E1S1", "E1S2"]
    assert list((project_dir / "scripts").glob("episode_1.json.bak.v7-*"))
    assert list(project_dir.glob("project.json.bak.v7-*"))


@pytest.mark.parametrize("mode", ["narration", "drama", "ad"])
def test_migrates_all_creation_types(tmp_path: Path, mode: str) -> None:
    project_dir = _v7_project(tmp_path, content_mode=mode, source_kind="novel")
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script(content_mode=mode))

    migrate_v7_to_v8(project_dir)

    project = _read_json(project_dir / "project.json")
    script = _read_json(project_dir / "scripts/episode_1.json")
    assert project["creation_type"] == mode
    assert script["creation_type"] == mode


def test_materializes_missing_creation_and_source_fields(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 7,
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json"}],
        },
    )
    _write_json(
        project_dir / "scripts/episode_1.json",
        {"episode": 1, "title": "开端", "segments": [{"segment_id": "E1S1"}]},
    )

    migrate_v7_to_v8(project_dir)

    project = _read_json(project_dir / "project.json")
    script = _read_json(project_dir / "scripts/episode_1.json")
    assert project["creation_type"] == "narration"
    assert project["source_file_type"] == "novel"
    assert script["creation_type"] == "narration"


def test_reference_video_keeps_shots_and_references(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path, content_mode="narration", generation_mode="reference_video")
    payload = _reference_script()
    _write_json(project_dir / "scripts/episode_1.json", payload)

    migrate_v7_to_v8(project_dir)

    script = _read_json(project_dir / "scripts/episode_1.json")
    assert script["creation_type"] == "narration"
    assert script["video_units"][0]["shots"] == payload["video_units"][0]["shots"]
    assert script["video_units"][0]["references"] == payload["video_units"][0]["references"]
    assert "text" not in script["video_units"][0]
    assert "reference_assets" not in script["video_units"][0]


def test_invalid_script_blocks_all_writes(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path, episodes=2)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())
    (project_dir / "scripts").mkdir(exist_ok=True)
    (project_dir / "scripts" / "episode_2.json").write_text("{not-json", encoding="utf-8")
    original_project = (project_dir / "project.json").read_text(encoding="utf-8")
    original_script = (project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")

    with pytest.raises((json.JSONDecodeError, ValueError)):
        migrate_v7_to_v8(project_dir)

    assert (project_dir / "project.json").read_text(encoding="utf-8") == original_project
    assert (project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8") == original_script
    assert not list(project_dir.glob("project.json.bak.v7-*"))
    assert not list((project_dir / "scripts").glob("episode_1.json.bak.v7-*"))


def test_invalid_creation_type_blocks_rewrite(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path, content_mode="documentary")
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())
    original_project = (project_dir / "project.json").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="creation_type|content_mode"):
        migrate_v7_to_v8(project_dir)

    assert (project_dir / "project.json").read_text(encoding="utf-8") == original_project
    assert _read_json(project_dir / "project.json")["schema_version"] == 7


def test_resumes_after_crash_between_script_and_project_commit(tmp_path: Path) -> None:
    """崩在附属文件已落盘、schema_version 未提交之间：重跑收敛，原版备份不被覆盖。"""
    project_dir = _v7_project(tmp_path)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())
    script_path = project_dir / "scripts/episode_1.json"
    pristine_script = script_path.read_text(encoding="utf-8")

    # 模拟崩溃点，按首轮真实顺序：先备份原版，再改写剧本，project.json 尚未提交
    _ensure_backup(script_path)
    _write_json(script_path, migrate_script_payload(_storyboard_script(), fallback_creation_type="drama"))
    assert _read_json(project_dir / "project.json")["schema_version"] == 7

    migrate_v7_to_v8(project_dir)

    project = _read_json(project_dir / "project.json")
    script = _read_json(script_path)
    assert project["schema_version"] == 8
    assert project["creation_type"] == "drama"
    assert script["creation_type"] == "drama"
    assert "content_mode" not in script
    # 首轮备份是原版 v7 剧本，重跑不得用半迁移态覆盖它
    backups = list((project_dir / "scripts").glob("episode_1.json.bak.v7-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == pristine_script


def test_write_failure_rolls_back_already_written_scripts(tmp_path: Path, monkeypatch) -> None:
    """写入期失败（磁盘满 / 权限收紧）不得留下部分写入：先落盘的剧本还原回 v7 原文。"""
    project_dir = _v7_project(tmp_path, episodes=2)
    for episode in (1, 2):
        _write_json(project_dir / f"scripts/episode_{episode}.json", _storyboard_script())
    first_script = project_dir / "scripts/episode_1.json"
    second_script = project_dir / "scripts/episode_2.json"
    pristine_first = first_script.read_text(encoding="utf-8")
    pristine_project = (project_dir / "project.json").read_text(encoding="utf-8")

    real_write = v7_to_v8.atomic_write_json

    def _fail_on_second(path: Path, payload: object, **kwargs) -> None:
        if path == second_script:
            raise OSError(28, "No space left on device")
        real_write(path, payload, **kwargs)

    monkeypatch.setattr(v7_to_v8, "atomic_write_json", _fail_on_second)

    with pytest.raises(OSError):
        migrate_v7_to_v8(project_dir)

    assert first_script.read_text(encoding="utf-8") == pristine_first
    assert (project_dir / "project.json").read_text(encoding="utf-8") == pristine_project

    # 回滚后重跑（写入恢复正常）仍收敛到 v8
    monkeypatch.setattr(v7_to_v8, "atomic_write_json", real_write)
    migrate_v7_to_v8(project_dir)
    assert _read_json(project_dir / "project.json")["schema_version"] == 8
    assert _read_json(first_script)["creation_type"] == "drama"
    assert _read_json(second_script)["creation_type"] == "drama"


def test_rollback_keeps_valid_json_when_restore_itself_fails(tmp_path: Path, monkeypatch) -> None:
    """回滚写入失败（磁盘继续满）不得把剧本截断成半截：文件保持完整 v8 形态，重跑可收敛。"""
    project_dir = _v7_project(tmp_path, episodes=2)
    for episode in (1, 2):
        _write_json(project_dir / f"scripts/episode_{episode}.json", _storyboard_script())
    first_script = project_dir / "scripts/episode_1.json"
    second_script = project_dir / "scripts/episode_2.json"

    real_write = v7_to_v8.atomic_write_json

    def _fail_on_second(path: Path, payload: object, **kwargs) -> None:
        if path == second_script:
            raise OSError(28, "No space left on device")
        real_write(path, payload, **kwargs)

    real_write_bytes = v7_to_v8.atomic_write_bytes

    def _restore_also_fails(path: Path, data: bytes) -> None:
        if ".bak.v7-" in path.name:
            real_write_bytes(path, data)  # 备份照常落盘，失败的只是回滚写回
            return
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(v7_to_v8, "atomic_write_json", _fail_on_second)
    monkeypatch.setattr(v7_to_v8, "atomic_write_bytes", _restore_also_fails)

    with pytest.raises(OSError):
        migrate_v7_to_v8(project_dir)

    restored = _read_json(first_script)  # 解析得出来就说明没被截断
    assert restored["creation_type"] == "drama"
    assert _read_json(project_dir / "project.json")["schema_version"] == 7

    monkeypatch.setattr(v7_to_v8, "atomic_write_json", real_write)
    migrate_v7_to_v8(project_dir)
    assert _read_json(project_dir / "project.json")["schema_version"] == 8
    assert _read_json(second_script)["creation_type"] == "drama"


@pytest.mark.parametrize(
    ("content_mode", "count_key"),
    [("narration", "total_segments"), ("ad", "total_shots"), ("drama", "total_units")],
)
def test_removes_every_legacy_metadata_count_key(tmp_path: Path, content_mode: str, count_key: str) -> None:
    """v7 按骨架落盘的计数键不止 total_scenes：残留任何一个都会与读时计算的新计数打架。"""
    project_dir = _v7_project(tmp_path, content_mode=content_mode)
    script = _storyboard_script(content_mode=content_mode)
    script["metadata"][count_key] = 99
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v7_to_v8(project_dir)

    metadata = _read_json(project_dir / "scripts/episode_1.json")["metadata"]
    assert count_key not in metadata
    assert "total_scenes" not in metadata
    assert metadata["estimated_duration_seconds"] == 16


@pytest.mark.parametrize("count_key", ["scenes_count", "units_count"])
def test_removes_every_legacy_episode_count_key(tmp_path: Path, count_key: str) -> None:
    """episodes[] 上落盘过的读模型计数不止 scenes_count：残留的会与读时注入的新计数打架。"""
    project_dir = _v7_project(tmp_path, extra_episode_fields={count_key: 9})
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())

    migrate_v7_to_v8(project_dir)

    assert count_key not in _read_json(project_dir / "project.json")["episodes"][0]


@pytest.mark.parametrize("binding", ["episode_1.json", "scripts\\episode_1.json"])
def test_migrates_script_bound_by_alias_path(tmp_path: Path, binding: str) -> None:
    """裸文件名与 Windows 分隔符是同一剧本的合法别名：按字面找不到就跳过，会留下版本号已升、
    剧本仍是旧契约的项目，而且往后再也不进迁移链。"""
    project_dir = _v7_project(tmp_path)
    project = _read_json(project_dir / "project.json")
    project["episodes"][0]["script_file"] = binding
    _write_json(project_dir / "project.json", project)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())

    migrate_v7_to_v8(project_dir)

    script = _read_json(project_dir / "scripts/episode_1.json")
    assert script["creation_type"] == "drama"
    assert "content_mode" not in script


def test_migrates_script_bound_by_nested_unprefixed_path(tmp_path: Path) -> None:
    """省略 scripts/ 前缀的写法不限于裸文件名：读取方对任何绑定都相对 scripts/ 解析，
    迁移按字面找不到嵌套剧本就会漏迁，只留下版本号已升的混合契约项目。"""
    project_dir = _v7_project(tmp_path)
    project = _read_json(project_dir / "project.json")
    project["episodes"][0]["script_file"] = "season_1/episode_1.json"
    _write_json(project_dir / "project.json", project)
    script_path = project_dir / "scripts/season_1/episode_1.json"
    _write_json(script_path, _storyboard_script())

    migrate_v7_to_v8(project_dir)

    script = _read_json(script_path)
    assert script["creation_type"] == "drama"
    assert "content_mode" not in script


@pytest.mark.parametrize("stamp_key", ["content_mode", "creation_type"])
def test_explicit_null_script_stamp_falls_back_to_project(tmp_path: Path, stamp_key: str) -> None:
    """剧本显式 null 戳等同未打戳（与运行时解析同口径），回退项目声明而不是兜底 narration。"""
    project_dir = _v7_project(tmp_path, content_mode="drama")
    script = _storyboard_script(content_mode="drama")
    script.pop("content_mode")
    script[stamp_key] = None
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v7_to_v8(project_dir)

    assert _read_json(project_dir / "scripts/episode_1.json")["creation_type"] == "drama"


def test_backup_creation_failure_leaves_no_partial_backup(tmp_path: Path, monkeypatch) -> None:
    """备份写入中途失败不得留下截断的 .bak：下一轮会把它当原版复用，回滚时反倒盖掉现场。"""
    project_dir = _v7_project(tmp_path)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())

    def _fail(path: Path, data: bytes) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(v7_to_v8, "atomic_write_bytes", _fail)

    with pytest.raises(OSError):
        migrate_v7_to_v8(project_dir)

    assert not list(project_dir.glob("*.bak.v7-*"))
    assert not list((project_dir / "scripts").glob("*.bak.v7-*"))
    assert _read_json(project_dir / "project.json")["schema_version"] == 7


def test_backup_mtime_is_creation_time_not_source_mtime(tmp_path: Path) -> None:
    """备份的 mtime 按做出来的时刻算：沿用源文件旧 mtime 会被启动时的 7 天过期清理立刻删掉。"""
    import os

    project_dir = _v7_project(tmp_path)
    script_path = project_dir / "scripts/episode_1.json"
    _write_json(script_path, _storyboard_script())
    long_ago = time.time() - 60 * 86400
    for path in (project_dir / "project.json", script_path):
        os.utime(path, (long_ago, long_ago))

    migrate_v7_to_v8(project_dir)

    backups = [*project_dir.glob("project.json.bak.v7-*"), *(project_dir / "scripts").glob("*.bak.v7-*")]
    assert len(backups) == 2
    for backup in backups:
        assert backup.stat().st_mtime > long_ago + 86400


def test_idempotent_when_already_v8(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())
    migrate_v7_to_v8(project_dir)
    first = _read_json(project_dir / "project.json")
    migrate_v7_to_v8(project_dir)
    assert _read_json(project_dir / "project.json") == first


def test_runner_entry_upgrades_v7_to_current(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path)
    _write_json(project_dir / "scripts/episode_1.json", _storyboard_script())

    assert migrate_project_dir(project_dir) is True
    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_SCHEMA_VERSION
    assert project["creation_type"] == "drama"
    assert migrate_project_dir(project_dir) is False


def test_profile_manifest_field_renamed(tmp_path: Path) -> None:
    project_dir = _v7_project(tmp_path)
    _write_json(
        project_dir / ".arcreel_profile_manifest.json",
        {"schema_version": 1, "profile_id": "arcreel/builtin", "content_mode": "drama", "files": {}},
    )

    migrate_v7_to_v8(project_dir)

    manifest = _read_json(project_dir / ".arcreel_profile_manifest.json")
    assert manifest["creation_type"] == "drama"
    assert "content_mode" not in manifest


def test_profile_manifest_null_stamp_stays_null(tmp_path: Path) -> None:
    """manifest 的 null 戳被 load_manifest 读成「未迁移」并整体重置；编个兜底值反而会写错模式。"""
    project_dir = _v7_project(tmp_path)
    _write_json(
        project_dir / ".arcreel_profile_manifest.json",
        {"schema_version": 1, "profile_id": "arcreel/builtin", "content_mode": None, "files": {}},
    )

    migrate_v7_to_v8(project_dir)

    manifest = _read_json(project_dir / ".arcreel_profile_manifest.json")
    assert manifest["creation_type"] is None
    assert "content_mode" not in manifest
