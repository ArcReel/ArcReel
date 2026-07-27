"""分集规划全量重置的行为测试（真实文件系统，不触 LLM）。

只断言对外行为：账本状态、文件去向、二段确认出口与错误路径。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lib.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    discover_episode_files,
    discover_sources,
)
from lib.episode_planner import EpisodePlanner
from lib.episode_reset import (
    EpisodeResetError,
    EpisodeResetResult,
    ResetConfirmationRequired,
    reset_episode_planning,
)

SOURCE = "第一章 山村少年。李恒在山村长大。第二章 下山。李恒辞别师父。第三章 风波。少女身份成谜。"


def _write_project(
    tmp_path: Path,
    *,
    episodes: list | None = None,
    planning_cursor: dict | None = None,
    extra: dict | None = None,
    source_text: str = SOURCE,
) -> Path:
    project_dir = tmp_path / "demo-proj"
    (project_dir / "source").mkdir(parents=True)
    project = {
        "schema_version": 3,
        "title": "测试项目",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "style": "国漫",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": episodes or [],
        "planning_cursor": planning_cursor,
    }
    if extra:
        project.update(extra)
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (project_dir / "source" / "novel.txt").write_text(source_text, encoding="utf-8")
    return project_dir


def _load_project(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _entry(num: int, *, source_range: dict | None, status: str = "planned") -> dict:
    return {
        "episode": num,
        "title": f"第 {num} 集",
        "script_file": f"scripts/episode_{num}.json",
        "source_range": source_range,
        "ledger_status": status,
    }


def _write_script(project_dir: Path, num: int) -> Path:
    scripts = project_dir / "scripts"
    scripts.mkdir(exist_ok=True)
    path = scripts / f"episode_{num}.json"
    path.write_text("{}", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 全量重置：账本任意损坏都必须成功
# ---------------------------------------------------------------------------


def test_reset_on_corrupted_ledger_clears_everything(tmp_path: Path) -> None:
    """cursor 越界 + 条目坐标越界 + 账本与磁盘不一致：全量重置仍成功，随后可从头规划。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 99999}),
            _entry(2, source_range={"source_file": "source/gone.txt", "start": 500, "end": 900}),
        ],
        planning_cursor={"source_file": "source/gone.txt", "offset": 99999},
    )
    (project_dir / "source" / "episode_1.txt").write_text("旧内容", encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert result.removed_episodes == [1, 2]
    project = _load_project(project_dir)
    assert project["episodes"] == []
    assert project["planning_cursor"] is None
    # 重置后规划起点回到第一个源文件开头（plan 可正常从头规划）
    assert EpisodePlanner(project_dir)._effective_start(project) == ("source/novel.txt", 0)


def test_reset_clears_source_fingerprints(tmp_path: Path) -> None:
    """源文指纹随账本一并失效：字段存在时被清除，不存在时不报错。"""
    project_dir = _write_project(
        tmp_path,
        extra={SOURCE_FINGERPRINTS_KEY: {"source/novel.txt": "deadbeef"}},
    )

    reset_episode_planning(project_dir, from_episode=1)

    assert SOURCE_FINGERPRINTS_KEY not in _load_project(project_dir)


def test_reset_removes_remaining_file(tmp_path: Path) -> None:
    """余文文件被清理：留着会在下次规划的回填中被换算成游标，让「从头规划」变成续规划。"""
    project_dir = _write_project(tmp_path)
    remaining = project_dir / "source" / "_remaining.txt"
    remaining.write_text("第三章 风波。少女身份成谜。", encoding="utf-8")

    reset_episode_planning(project_dir, from_episode=1)

    assert not remaining.exists()
    project = _load_project(project_dir)
    assert EpisodePlanner(project_dir)._effective_start(project) == ("source/novel.txt", 0)


# ---------------------------------------------------------------------------
# 文件处置：派生的删、非派生的留底
# ---------------------------------------------------------------------------


def test_derived_files_deleted_and_legacy_files_archived(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range=None, status="unanchored"),
        ],
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text(SOURCE[:10], encoding="utf-8")
    legacy = project_dir / "source" / "episode_2.txt"
    legacy.write_text("老项目手工内容", encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert not derived.exists()
    assert result.deleted_files == ["source/episode_1.txt"]
    assert not legacy.exists()
    archived = project_dir / "source" / "_episode_2.txt.bak"
    assert archived.read_text(encoding="utf-8") == "老项目手工内容"
    assert result.archived_files == [("source/episode_2.txt", "source/_episode_2.txt.bak")]


def test_archived_file_left_out_of_discovery(tmp_path: Path) -> None:
    """留底文件既不进源文候选，也不被当成派生集文件重新认领。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(2, source_range=None, status="unanchored")],
    )
    (project_dir / "source" / "episode_2.txt").write_text("老项目手工内容", encoding="utf-8")

    reset_episode_planning(project_dir, from_episode=1)

    assert discover_episode_files(project_dir) == {}
    assert [doc.rel_path for doc in discover_sources(project_dir)] == ["source/novel.txt"]


def test_archive_does_not_overwrite_existing_backup(tmp_path: Path) -> None:
    """重复重置不覆盖上一次的留底：同名时追加序号。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(2, source_range=None, status="unanchored")],
    )
    (project_dir / "source" / "_episode_2.txt.bak").write_text("上一次的留底", encoding="utf-8")
    (project_dir / "source" / "episode_2.txt").write_text("这一次的内容", encoding="utf-8")

    reset_episode_planning(project_dir, from_episode=1)

    assert (project_dir / "source" / "_episode_2.txt.bak").read_text(encoding="utf-8") == "上一次的留底"
    assert (project_dir / "source" / "_episode_2.txt.1.bak").read_text(encoding="utf-8") == "这一次的内容"


def test_orphan_episode_file_archived(tmp_path: Path) -> None:
    """账本无对应条目的孤儿集文件按无坐标处理：留底而非删除，避免回填重新补建条目。"""
    project_dir = _write_project(tmp_path)
    (project_dir / "source" / "episode_7.txt").write_text("孤儿内容", encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert result.archived_files == [("source/episode_7.txt", "source/_episode_7.txt.bak")]
    assert discover_episode_files(project_dir) == {}


def test_file_failure_aborts_and_leaves_ledger_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """文件处置失败硬失败：账本写回随之回滚，避免留下「账本已空但残留文件会被重新认领」的中间态。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
        planning_cursor={"source_file": "source/novel.txt", "offset": 10},
    )
    (project_dir / "source" / "episode_1.txt").write_text(SOURCE[:10], encoding="utf-8")
    before = _load_project(project_dir)

    def _boom(self: Path, missing_ok: bool = False) -> None:
        raise OSError("device busy")

    monkeypatch.setattr(Path, "unlink", _boom)

    with pytest.raises(EpisodeResetError, match="派生集文件删除失败"):
        reset_episode_planning(project_dir, from_episode=1)

    assert _load_project(project_dir) == before


# ---------------------------------------------------------------------------
# 已消费集的二段确认
# ---------------------------------------------------------------------------


def test_consumed_requires_confirmation_and_writes_nothing(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="consumed"),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
        planning_cursor={"source_file": "source/novel.txt", "offset": 20},
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text("已消费集", encoding="utf-8")
    before = _load_project(project_dir)

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]
    assert _load_project(project_dir) == before
    assert derived.exists()


def test_consumed_detected_from_disk_when_ledger_status_missing(tmp_path: Path) -> None:
    """账本状态不可信时以磁盘产物为准：条目无 ledger_status 但剧本已存在，仍要确认。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[{"episode": 1, "title": "第 1 集", "script_file": "scripts/episode_1.json"}],
    )
    _write_script(project_dir, 1)

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_confirmed_reset_keeps_downstream_products(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="consumed"),
        ],
    )
    script = _write_script(project_dir, 1)
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    step1 = drafts / "step1_segments.json"
    step1.write_text("{}", encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1, confirm_consumed=True)

    assert isinstance(result, EpisodeResetResult)
    assert result.consumed_episodes == [1]
    assert script.is_file()
    assert step1.is_file()
    project = _load_project(project_dir)
    assert project["episodes"] == []
    assert project["planning_cursor"] is None


# ---------------------------------------------------------------------------
# 部分重置尚未支持
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 损坏边界：非列表 episodes / 符号链接 / 磁盘孤儿产物 / 同集号别名文件
# ---------------------------------------------------------------------------


def test_reset_tolerates_non_list_episodes(tmp_path: Path) -> None:
    """episodes 被写坏成 truthy 非列表值（如手工误编辑成整数）时按空账本处理，不崩溃。"""
    project_dir = _write_project(tmp_path, extra={"episodes": 1})

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert result.removed_episodes == []
    assert _load_project(project_dir)["episodes"] == []


def test_reset_rejects_symlinked_source_dir(tmp_path: Path) -> None:
    """source/ 是指向项目外目录的符号链接时拒绝处置，避免删除/改名外部目录中的文件。"""
    project_dir = _write_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "episode_1.txt").write_text("外部文件", encoding="utf-8")
    source_dir = project_dir / "source"
    shutil.rmtree(source_dir)
    source_dir.symlink_to(outside, target_is_directory=True)
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="符号链接"):
        reset_episode_planning(project_dir, from_episode=1)

    assert (outside / "episode_1.txt").exists()
    assert _load_project(project_dir) == before


def test_reset_rejects_symlinked_episode_file(tmp_path: Path) -> None:
    """单个派生集文件是符号链接时拒绝处置，避免跟随链接删除/改名外部文件。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
    )
    outside_target = tmp_path / "outside_episode_1.txt"
    outside_target.write_text("外部文件", encoding="utf-8")
    (project_dir / "source" / "episode_1.txt").symlink_to(outside_target)
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="符号链接"):
        reset_episode_planning(project_dir, from_episode=1)

    assert outside_target.exists()
    assert _load_project(project_dir) == before


def test_orphan_disk_product_requires_confirmation(tmp_path: Path) -> None:
    """账本丢失条目、无对应 source/episode_N.txt，但 scripts/ 下仍有产物时仍要求确认。"""
    project_dir = _write_project(tmp_path)
    _write_script(project_dir, 1)

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_orphan_draft_dir_requires_confirmation(tmp_path: Path) -> None:
    """账本丢失条目、无对应 source/episode_N.txt，但 drafts/ 下仍有 step1 产物时仍要求确认。"""
    project_dir = _write_project(tmp_path)
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_segments.json").write_text("{}", encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_reset_processes_all_padding_aliases_of_same_episode(tmp_path: Path) -> None:
    """同一集号的多个 padding 别名（episode_1.txt / episode_01.txt）全部被处置，
    否则未处理的别名会在下次回填中重新补建账本条目。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
    )
    alias_a = project_dir / "source" / "episode_1.txt"
    alias_a.write_text(SOURCE[:10], encoding="utf-8")
    alias_b = project_dir / "source" / "episode_01.txt"
    alias_b.write_text(SOURCE[:10], encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert not alias_a.exists()
    assert not alias_b.exists()
    assert discover_episode_files(project_dir) == {}


def test_partial_reset_rejected_without_touching_ledger(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
        planning_cursor={"source_file": "source/novel.txt", "offset": 10},
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="部分重置"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before
