"""分集规划全量重置的行为测试（真实文件系统，不触 LLM）。

只断言对外行为：账本状态、文件去向、二段确认出口与错误路径。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from lib.artifact_manifest import MANIFEST_FILENAME, ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.episode_ledger import (
    SOURCE_FINGERPRINTS_KEY,
    discover_episode_file_aliases,
    discover_episode_files,
    discover_sources,
)
from lib.episode_planner import EpisodePlanner
from lib.episode_reset import (
    EpisodeResetConflictError,
    EpisodeResetError,
    EpisodeResetResult,
    ResetConfirmationRequired,
    reset_episode_planning,
)

# 全部用例跨 EpisodeReset / ProjectManager / EpisodePlanner 协作，用真实 tmp_path 文件系统，
# 不 mock 被测模块的公共入口——按 CONTRIBUTING.md 的 marker 纪律归类为 integration。
pytestmark = pytest.mark.integration

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


def _write_claimed_artifact(
    project_dir: Path,
    adapter: ProjectArtifactManifestAdapter,
    key: ArtifactKey,
    relative_path: str,
    *,
    digest_byte: str,
) -> None:
    path = project_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(relative_path.encode("utf-8"))
    adapter.put_entry(
        key,
        ArtifactManifestEntry(
            artifact_path=relative_path,
            basis_digest=f"sha256-v1:{digest_byte * 64}",
        ),
    )


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
    """余文文件被清理：账本游标已取代它，留着只会在 source/ 下留一份与账本无关的陈旧剩余正文。"""
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
            _entry(2, source_range=None),
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
        episodes=[_entry(2, source_range=None)],
    )
    (project_dir / "source" / "episode_2.txt").write_text("老项目手工内容", encoding="utf-8")

    reset_episode_planning(project_dir, from_episode=1)

    assert discover_episode_files(project_dir) == {}
    assert [doc.rel_path for doc in discover_sources(project_dir)] == ["source/novel.txt"]


def test_archive_does_not_overwrite_existing_backup(tmp_path: Path) -> None:
    """重复重置不覆盖上一次的留底：同名时追加序号。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(2, source_range=None)],
    )
    (project_dir / "source" / "_episode_2.txt.bak").write_text("上一次的留底", encoding="utf-8")
    (project_dir / "source" / "episode_2.txt").write_text("这一次的内容", encoding="utf-8")

    reset_episode_planning(project_dir, from_episode=1)

    assert (project_dir / "source" / "_episode_2.txt.bak").read_text(encoding="utf-8") == "上一次的留底"
    assert (project_dir / "source" / "_episode_2.txt.1.bak").read_text(encoding="utf-8") == "这一次的内容"


def test_orphan_episode_file_archived(tmp_path: Path) -> None:
    """账本无对应条目的孤儿集文件按无坐标处理：留底而非删除，避免孤儿条目登记重新补建条目。"""
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


def test_conflict_when_new_consumed_episode_appears_during_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁内复扫发现确认清单之外的新消费集时拒绝提交、账本不被改动：这是防止「确认时看到的
    清单」与「实际提交时的账本状态」不一致的关键安全网，用 monkeypatch 模拟该竞态窗口。"""
    # 通过 sys.modules 取模块对象供 monkeypatch 使用，不再新增一种 import 风格
    # （lib.episode_reset 已在文件顶部用 from-import 引入）
    episode_reset_module = sys.modules["lib.episode_reset"]
    project_dir = _write_project(tmp_path)
    before = _load_project(project_dir)
    original_scan = episode_reset_module._scan
    calls = {"n": 0}

    def _fake_scan(pd: Path, project: dict, *, from_episode: int = 1):
        calls["n"] += 1
        result = original_scan(pd, project, from_episode=from_episode)
        if calls["n"] == 2:  # 第二次调用发生在锁内（_commit 的复扫）
            result.consumed.append(1)
        return result

    monkeypatch.setattr(episode_reset_module, "_scan", _fake_scan)

    with pytest.raises(EpisodeResetConflictError):
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


def test_reset_deletes_symlinked_episode_file_without_touching_target(tmp_path: Path) -> None:
    """单个派生集文件是符号链接时按计划正常处置：unlink/rename 只作用于链接条目本身、
    不跟随最终一段的链接目标，外部文件不受影响（与 source/ 本身是符号链接的风险不同）。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
    )
    outside_target = tmp_path / "outside_episode_1.txt"
    outside_target.write_text("外部文件", encoding="utf-8")
    link = project_dir / "source" / "episode_1.txt"
    link.symlink_to(outside_target)

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert not link.exists()
    assert outside_target.exists()
    assert outside_target.read_text(encoding="utf-8") == "外部文件"


def test_reset_clears_dangling_symlinked_episode_file(tmp_path: Path) -> None:
    """目标不存在的悬空符号链接同样被发现并清理，否则残留链接会在下次 plan_episodes
    写派生文件时被 EpisodePlanner 的符号链接校验硬拦截，重置的"可继续规划"承诺落空。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(2, source_range=None)],
    )
    link = project_dir / "source" / "episode_2.txt"
    link.symlink_to(project_dir / "source" / "does_not_exist.txt")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert not link.exists()
    assert result.archived_files
    archived_target = project_dir / result.archived_files[0][1]
    assert archived_target.is_symlink()


def test_reset_aggregates_consumed_status_across_duplicate_episode_entries(tmp_path: Path) -> None:
    """损坏账本同一集号出现多条条目（首条 planned、后条 consumed）时，已消费判定按
    集号聚合全部条目，不能只看被去重逻辑留下的首条而漏判。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="planned"),
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="consumed"),
        ],
    )

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_reset_aggregates_downstream_products_across_duplicate_entries(tmp_path: Path) -> None:
    """损坏账本同一集号出现多条条目，首条指向缺失的规范剧本路径、后条的 script_file
    指向实际存在的非规范路径（如 scripts/custom_name.json）时，已消费判定仍要命中——
    不能只把 setdefault 留下的首条传给 has_downstream_products。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            {"episode": 1, "title": "首条", "script_file": "scripts/episode_1.json"},
            {"episode": 1, "title": "后条", "script_file": "scripts/custom_name.json"},
        ],
    )
    scripts = project_dir / "scripts"
    scripts.mkdir()
    (scripts / "custom_name.json").write_text("{}", encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


def test_reset_archives_when_source_range_is_structurally_incomplete(tmp_path: Path) -> None:
    """source_range 是空字典或缺字段的损坏映射时，虽满足 isinstance(Mapping) 但没有
    可用坐标，仍按无法从账本重造处理（留底而非删除）。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={})],
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text(SOURCE[:10], encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert result.deleted_files == []
    assert result.archived_files
    assert not derived.exists()


def test_reset_archives_when_any_duplicate_entry_lacks_source_range(tmp_path: Path) -> None:
    """损坏账本同一集号出现多条条目，首条带 source_range、后条不带时，按无法证明可
    重造处理（留底而非删除）——删除不可逆，证据冲突时偏保守。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(1, source_range=None),
        ],
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text(SOURCE[:10], encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert result.deleted_files == []
    assert result.archived_files
    assert not derived.exists()


def test_archive_path_increments_past_dangling_symlink_collision(tmp_path: Path) -> None:
    """留底目标名恰好被上一次遗留的悬空符号链接占用时仍按占用处理并追加序号：
    exists() 对悬空链接返回 False，只查 exists() 会让 rename() 静默覆盖旧留底。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(2, source_range=None)],
    )
    stale_backup = project_dir / "source" / "_episode_2.txt.bak"
    stale_backup.symlink_to(project_dir / "source" / "does_not_exist_either.txt")
    legacy = project_dir / "source" / "episode_2.txt"
    legacy.write_text("这一次的内容", encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert stale_backup.is_symlink()  # 旧留底（悬空链接）未被覆盖
    new_backup = project_dir / "source" / "_episode_2.txt.1.bak"
    assert new_backup.read_text(encoding="utf-8") == "这一次的内容"
    assert result.archived_files == [("source/episode_2.txt", "source/_episode_2.txt.1.bak")]


def test_discover_episode_files_prefers_readable_over_dangling_alias(tmp_path: Path) -> None:
    """代表路径在悬空别名恰好排序在前时仍优先选可读文件，避免按内容读派生文件的调用方
    读到悬空链接而误判该集无内容。"""
    project_dir = _write_project(tmp_path)
    valid = project_dir / "source" / "episode_1.txt"
    valid.write_text(SOURCE[:10], encoding="utf-8")
    dangling = project_dir / "source" / "episode_01.txt"  # 排序早于 episode_1.txt
    dangling.symlink_to(project_dir / "source" / "does_not_exist.txt")

    assert discover_episode_files(project_dir)[1] == valid


def test_discover_episode_files_skips_episode_with_only_dangling_alias(tmp_path: Path) -> None:
    """某集号全部别名都是悬空符号链接（无真实内容）时，代表路径映射里不出现该集号，
    而不是返回一个读不到内容的路径——否则孤儿条目登记会为纯悬空、无真实内容的集号
    凭空补建一个幽灵条目。"""
    project_dir = _write_project(tmp_path)
    dangling = project_dir / "source" / "episode_9.txt"
    dangling.symlink_to(project_dir / "source" / "does_not_exist.txt")

    assert 9 not in discover_episode_files(project_dir)
    assert discover_episode_file_aliases(project_dir)[9] == [dangling]


def test_empty_drafts_dir_does_not_require_confirmation(tmp_path: Path) -> None:
    """drafts/episode_N/ 目录存在但为空（如一次被拒绝的草稿保存留下的空目录）时不计入
    已消费产物，不能因为目录存在就误报需要确认。"""
    project_dir = _write_project(tmp_path)
    (project_dir / "drafts" / "episode_1").mkdir(parents=True)

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)


def test_reset_tolerates_unconvertible_digit_episode_num(tmp_path: Path) -> None:
    """episode 字段是 str.isdigit() 认可但 int() 无法转换的字符（如上标 ²）时不崩溃，
    该条目按无法识别集号处理（原样跳过），不阻断重置这个逃生口本身。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[{"episode": "²", "title": "损坏集号"}],
    )

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, EpisodeResetResult)
    assert result.removed_episodes == []


def test_orphan_disk_product_with_padded_filename_requires_confirmation(tmp_path: Path) -> None:
    """账本丢失条目、产物文件名 padding 与规范路径不一致（episode_01.json 而非
    episode_1.json）时，仍要求确认——不能因为 has_downstream_products 只认规范路径
    就漏判已消费。"""
    project_dir = _write_project(tmp_path)
    scripts = project_dir / "scripts"
    scripts.mkdir()
    (scripts / "episode_01.json").write_text("{}", encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=1)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [1]


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
    否则未处理的别名会被孤儿条目登记重新补建账本条目。"""
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


def test_reset_rejects_non_positive_from_episode(tmp_path: Path) -> None:
    project_dir = _write_project(tmp_path)
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="正整数"):
        reset_episode_planning(project_dir, from_episode=0)

    assert _load_project(project_dir) == before


# ---------------------------------------------------------------------------
# 部分重置：成功路径
# ---------------------------------------------------------------------------


def test_partial_reset_keeps_retained_episodes_and_rewinds_cursor(tmp_path: Path) -> None:
    """规划 3 集后部分重置到第 2 集：账本只保留第 1 集，游标退到第 1 集原文范围末尾，
    再次规划的起点与集号自然从第 2 集续上。"""
    end1 = 10
    end2 = 20
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": end1}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": end1, "end": end2}),
            _entry(3, source_range={"source_file": "source/novel.txt", "start": end2, "end": end2 + 10}),
        ],
        planning_cursor={"source_file": "source/novel.txt", "offset": end2 + 10},
    )
    (project_dir / "source" / "episode_2.txt").write_text(SOURCE[end1:end2], encoding="utf-8")
    (project_dir / "source" / "episode_3.txt").write_text(SOURCE[end2 : end2 + 10], encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=2)

    assert isinstance(result, EpisodeResetResult)
    assert result.removed_episodes == [2, 3]
    project = _load_project(project_dir)
    assert [e["episode"] for e in project["episodes"]] == [1]
    assert project["planning_cursor"] == {"source_file": "source/novel.txt", "offset": end1}
    # 再次规划的起点（供 EpisodePlanner._effective_start 消费）与新集号自然从第 2 集续上
    assert EpisodePlanner(project_dir)._effective_start(project) == ("source/novel.txt", end1)


def test_partial_reset_deletes_derived_files_in_range_keeps_retained(tmp_path: Path) -> None:
    """范围内（>= from_episode）有 source_range 的派生文件删除，保留段（< from_episode）
    派生文件不受影响。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    retained_file = project_dir / "source" / "episode_1.txt"
    retained_file.write_text(SOURCE[:10], encoding="utf-8")
    reset_file = project_dir / "source" / "episode_2.txt"
    reset_file.write_text(SOURCE[10:20], encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=2)

    assert isinstance(result, EpisodeResetResult)
    assert result.deleted_files == ["source/episode_2.txt"]
    assert not reset_file.exists()
    assert retained_file.exists()  # 保留段派生文件不动


def test_partial_reset_archives_file_without_source_range_in_range(tmp_path: Path) -> None:
    """范围内无 source_range 的集文件按无法重造处理，留底而非删除。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range=None),
        ],
    )
    (project_dir / "source" / "episode_1.txt").write_text(SOURCE[:10], encoding="utf-8")
    legacy = project_dir / "source" / "episode_2.txt"
    legacy.write_text("老项目手工内容", encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=2)

    assert isinstance(result, EpisodeResetResult)
    assert result.archived_files == [("source/episode_2.txt", "source/_episode_2.txt.bak")]
    assert not legacy.exists()


# ---------------------------------------------------------------------------
# 部分重置：前置校验拒绝（账本与文件均不被改动）
# ---------------------------------------------------------------------------


def test_partial_reset_rejects_when_from_episode_not_in_ledger(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[_entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="不在账本中"):
        reset_episode_planning(project_dir, from_episode=5)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_gap_before_from_episode(tmp_path: Path) -> None:
    """保留段（1..from_episode-1）存在缺口时拒绝：无法确定「保留到哪」。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(3, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="不连续"):
        reset_episode_planning(project_dir, from_episode=3)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_huge_from_episode_without_hanging(tmp_path: Path) -> None:
    """账本只有少量条目、``from_episode`` 却是天文数字集号（损坏写出）时快速拒绝：
    缺口扫描按已有条目而非 ``range(1, from_episode)`` 走，不物化整段区间。"""
    huge = 100_000_000
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(huge, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="不连续"):
        reset_episode_planning(project_dir, from_episode=huge)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_when_retain_boundary_has_no_source_range(tmp_path: Path) -> None:
    """第 from_episode-1 集（游标退回点）本身没有位置记录时无法确定重置后的规划起点。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range=None),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="缺少可信的原文范围记录"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_structurally_incomplete_boundary_same_as_missing(tmp_path: Path) -> None:
    """退回点 source_range 结构不完整（非 None 但字段非法）与缺失同口径处理：
    都归为「无可信坐标」，报同一条「缺少可信的原文范围记录」，不是单独的「非法」硬错误。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": "0", "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="缺少可信的原文范围记录"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_on_fingerprint_mismatch(tmp_path: Path) -> None:
    """已记录的源文指纹与当前源文不一致时拒绝，账本与文件均不被改动。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
        extra={SOURCE_FINGERPRINTS_KEY: {"source/novel.txt": "0" * 64}},  # 与实际内容不一致的假指纹
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="已被修改或移除"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_when_retained_range_out_of_bounds(tmp_path: Path) -> None:
    """保留段坐标越出当前源文长度（源文已被替换为更短内容，指纹未记录故绕过指纹门禁）时拒绝。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 99999}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 99999, "end": 100010}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="原文范围无效"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_non_contiguous_retained_ranges(tmp_path: Path) -> None:
    """保留段相邻两集各自坐标均未越界，但首尾不相接（此处为倒序）时拒绝：放行会让退回
    后的游标与真实已消费范围脱节，下次 plan 与已保留内容重叠。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 30, "end": 40}),
            _entry(3, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="不连续"):
        reset_episode_planning(project_dir, from_episode=3)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_when_first_episode_not_at_source_start(tmp_path: Path) -> None:
    """第 1 集范围本身未越界，但没有从首个源文件偏移 0 起：放行会让 [0, 该起点) 这段
    源文既不在保留账本里、退回后的游标也不会再规划到它，永久遗漏。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 20, "end": 30}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 30, "end": 40}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="未从源文件起点开始"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_out_of_order_source_file_switch(tmp_path: Path) -> None:
    """保留段跨源文件时必须切到排序中紧邻的下一个文件：这里第 1 集用完 a.txt 后跳过
    b.txt 直接切到 c.txt，拒绝。"""
    project_dir = _write_project(tmp_path)
    (project_dir / "source" / "a.txt").write_text("A" * 10, encoding="utf-8")
    (project_dir / "source" / "b.txt").write_text("B" * 10, encoding="utf-8")
    (project_dir / "source" / "c.txt").write_text("C" * 10, encoding="utf-8")
    project = _load_project(project_dir)
    project["episodes"] = [
        _entry(1, source_range={"source_file": "source/a.txt", "start": 0, "end": 10}),
        _entry(2, source_range={"source_file": "source/c.txt", "start": 0, "end": 10}),
        _entry(3, source_range={"source_file": "source/c.txt", "start": 10, "end": 10}),
    ]
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="切换源文件不合法"):
        reset_episode_planning(project_dir, from_episode=3)

    assert _load_project(project_dir) == before


def test_partial_reset_allows_first_episode_after_blank_leading_source(tmp_path: Path) -> None:
    """排序中排在第 1 集源文件之前的文件若只剩空白（EpisodePlanner 会自动跳过），
    第 1 集合法落在非首个文件，不应被误判为账本损坏。"""
    project_dir = _write_project(tmp_path)
    (project_dir / "source" / "a.txt").write_text("   \n  ", encoding="utf-8")
    (project_dir / "source" / "b.txt").write_text("B" * 10, encoding="utf-8")
    project = _load_project(project_dir)
    project["episodes"] = [
        _entry(1, source_range={"source_file": "source/b.txt", "start": 0, "end": 10}),
        _entry(2, source_range={"source_file": "source/b.txt", "start": 10, "end": 10}),
    ]
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=2)

    assert isinstance(result, EpisodeResetResult)
    assert _load_project(project_dir)["planning_cursor"] == {"source_file": "source/b.txt", "offset": 10}


def test_partial_reset_allows_skip_over_blank_middle_source(tmp_path: Path) -> None:
    """相邻保留集跨源文件时，中间被跳过的文件若只剩空白同样合法，不要求恰为紧邻下一个
    文件。"""
    project_dir = _write_project(tmp_path)
    (project_dir / "source" / "a.txt").write_text("A" * 10, encoding="utf-8")
    (project_dir / "source" / "b.txt").write_text("   \n  ", encoding="utf-8")
    (project_dir / "source" / "c.txt").write_text("C" * 10, encoding="utf-8")
    project = _load_project(project_dir)
    project["episodes"] = [
        _entry(1, source_range={"source_file": "source/a.txt", "start": 0, "end": 10}),
        _entry(2, source_range={"source_file": "source/c.txt", "start": 0, "end": 10}),
        _entry(3, source_range={"source_file": "source/c.txt", "start": 10, "end": 10}),
    ]
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=3)

    assert isinstance(result, EpisodeResetResult)
    assert _load_project(project_dir)["planning_cursor"] == {"source_file": "source/c.txt", "offset": 10}


def test_partial_reset_rejects_mid_sequence_entry_without_source_range(tmp_path: Path) -> None:
    """保留段中间（非边界）出现无位置记录的条目，其后又有坐标记录：该记录无法证明与
    已验证内容衔接，中间那段源文可能被永久遗漏，拒绝而非凭空重新起算。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range=None),
            _entry(3, source_range={"source_file": "source/novel.txt", "start": 20, "end": 30}),
            _entry(4, source_range={"source_file": "source/novel.txt", "start": 30, "end": 40}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="存在没有原文范围记录的条目"):
        reset_episode_planning(project_dir, from_episode=4)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_zero_length_retained_range(tmp_path: Path) -> None:
    """保留段坐标 start == end（零长度）时拒绝：零长度区间不构成可信的保留段坐标，
    否则会保留一个空集并让游标退到起点，造成编号错位。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 0}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="原文范围无效"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before


def test_partial_reset_accepts_retain_boundary_without_ledger_status(tmp_path: Path) -> None:
    """游标退回点没有 ledger_status 但有结构完整、界内连续的 source_range：照常接受——
    位置真相在 source_range，状态是咨询性的。"""
    project_dir = _write_project(tmp_path)
    project = _load_project(project_dir)
    entry = _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10})
    del entry["ledger_status"]
    project["episodes"] = [
        entry,
        _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
    ]
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    result = reset_episode_planning(project_dir, from_episode=2)

    assert isinstance(result, EpisodeResetResult)
    after = _load_project(project_dir)
    assert [e["episode"] for e in after["episodes"]] == [1]
    assert after["planning_cursor"] == {"source_file": "source/novel.txt", "offset": 10}


def test_partial_reset_trusts_source_range_under_legacy_status(tmp_path: Path) -> None:
    """存量遗留的已废弃状态值不再让坐标失效：结构完整、界内连续的 source_range 照常采信。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(
                1,
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 10},
                status="已废弃的状态",
            ),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )

    result = reset_episode_planning(project_dir, from_episode=2)

    assert isinstance(result, EpisodeResetResult)
    assert _load_project(project_dir)["planning_cursor"] == {"source_file": "source/novel.txt", "offset": 10}


def test_partial_reset_rejects_non_positive_episode_numbers_in_ledger(tmp_path: Path) -> None:
    """账本存在非正数集号（如损坏写出的 0）时拒绝：不能证明它属于保留段还是应被清除。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(0, source_range={"source_file": "source/novel.txt", "start": 0, "end": 0}),
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="非法集号"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_duplicate_episode_numbers(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="重复集号"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before


def test_partial_reset_rejects_non_list_episodes(tmp_path: Path) -> None:
    project_dir = _write_project(tmp_path, extra={"episodes": 1})
    before = _load_project(project_dir)

    with pytest.raises(EpisodeResetError, match="形状异常"):
        reset_episode_planning(project_dir, from_episode=2)

    assert _load_project(project_dir) == before


# ---------------------------------------------------------------------------
# 部分重置：已消费集的二段确认
# ---------------------------------------------------------------------------


def test_partial_reset_consumed_within_range_requires_confirmation(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(
                2,
                source_range={"source_file": "source/novel.txt", "start": 10, "end": 20},
                status="consumed",
            ),
        ],
    )
    script = _write_script(project_dir, 2)
    before = _load_project(project_dir)

    result = reset_episode_planning(project_dir, from_episode=2)

    assert isinstance(result, ResetConfirmationRequired)
    assert result.consumed_episodes == [2]
    assert _load_project(project_dir) == before
    assert script.is_file()


def test_partial_reset_consumed_before_range_not_flagged(tmp_path: Path) -> None:
    """保留段（< from_episode）已消费不影响二段确认：确认只针对本次重置范围内的集。"""
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}, status="consumed"),
            _entry(2, source_range={"source_file": "source/novel.txt", "start": 10, "end": 20}),
        ],
    )
    _write_script(project_dir, 1)

    result = reset_episode_planning(project_dir, from_episode=2)

    assert isinstance(result, EpisodeResetResult)


def test_partial_reset_confirmed_keeps_downstream_products(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(
                2,
                source_range={"source_file": "source/novel.txt", "start": 10, "end": 20},
                status="consumed",
            ),
        ],
    )
    script = _write_script(project_dir, 2)

    result = reset_episode_planning(project_dir, from_episode=2, confirm_consumed=True)

    assert isinstance(result, EpisodeResetResult)
    assert result.consumed_episodes == [2]
    assert script.is_file()
    project = _load_project(project_dir)
    assert [e["episode"] for e in project["episodes"]] == [1]


def test_partial_reset_removes_all_unbound_episode_claims_and_preserves_retained_claims(tmp_path: Path) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, source_range={"source_file": "source/novel.txt", "start": 0, "end": 10}),
            _entry(
                2,
                source_range={"source_file": "source/novel.txt", "start": 10, "end": 20},
                status="consumed",
            ),
        ],
        planning_cursor={"source_file": "source/novel.txt", "offset": 20},
        extra={"schema_version": 8},
    )
    (project_dir / "source" / "episode_1.txt").write_text(SOURCE[:10], encoding="utf-8")
    (project_dir / "source" / "episode_2.txt").write_text(SOURCE[10:20], encoding="utf-8")
    _write_script(project_dir, 1)
    _write_script(project_dir, 2)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    retained_key = ArtifactKey.episode_step1(1)
    _write_claimed_artifact(
        project_dir,
        adapter,
        retained_key,
        "drafts/episode_1/step1_segments.json",
        digest_byte="1",
    )
    removed: dict[ArtifactKey, str] = {
        ArtifactKey.episode_step1(2): "drafts/episode_2/step1_segments.json",
        ArtifactKey.episode_script(2): "scripts/episode_2.json",
        ArtifactKey.episode_grid(2, "grid_000000000002"): "grids/grid_000000000002.png",
        ArtifactKey.episode_step1(3): "drafts/episode_3/step1_segments.json",
    }
    for index, key in enumerate(ArtifactKey.episode_resource_artifacts(2, "E2S01"), start=3):
        removed[key] = f"retained-media/episode-2-{index}.bin"
    for index, (key, relative_path) in enumerate(removed.items(), start=2):
        if not (project_dir / relative_path).exists():
            _write_claimed_artifact(
                project_dir,
                adapter,
                key,
                relative_path,
                digest_byte=hex(index)[-1],
            )
        else:
            adapter.put_entry(
                key,
                ArtifactManifestEntry(
                    artifact_path=relative_path,
                    basis_digest=f"sha256-v1:{hex(index)[-1] * 64}",
                ),
            )

    result = reset_episode_planning(project_dir, from_episode=2, confirm_consumed=True)

    assert isinstance(result, EpisodeResetResult)
    entries = adapter.snapshot_entries()
    assert retained_key in entries
    assert not set(removed) & set(entries)
    assert all((project_dir / relative_path).is_file() for relative_path in removed.values())


def test_manifest_failure_restores_reset_project_and_claims_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(
                1,
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 10},
                status="consumed",
            )
        ],
        planning_cursor={"source_file": "source/novel.txt", "offset": 10},
        extra={"schema_version": 8},
    )
    derived = project_dir / "source" / "episode_1.txt"
    derived.write_text(SOURCE[:10], encoding="utf-8")
    remaining = project_dir / "source" / "_remaining.txt"
    remaining.write_text("未规划余文", encoding="utf-8")
    _write_script(project_dir, 1)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    _write_claimed_artifact(
        project_dir,
        adapter,
        ArtifactKey.episode_step1(1),
        "drafts/episode_1/step1_segments.json",
        digest_byte="a",
    )
    project_before = (project_dir / "project.json").read_bytes()
    manifest_before = (project_dir / MANIFEST_FILENAME).read_bytes()
    derived_before = derived.read_bytes()
    remaining_before = remaining.read_bytes()

    def _fail_registration(*_args, **_kwargs):
        raise RuntimeError("manifest unavailable")

    monkeypatch.setattr("lib.artifact_activation.register_artifact_entries_atomically", _fail_registration)

    with pytest.raises(RuntimeError, match="manifest unavailable"):
        reset_episode_planning(project_dir, from_episode=1, confirm_consumed=True)

    assert (project_dir / "project.json").read_bytes() == project_before
    assert (project_dir / MANIFEST_FILENAME).read_bytes() == manifest_before
    assert derived.read_bytes() == derived_before
    assert remaining.read_bytes() == remaining_before


def test_manifest_failure_restores_symlinked_episode_entry_without_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(
                1,
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 10},
                status="consumed",
            )
        ],
        extra={"schema_version": 8},
    )
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    derived = project_dir / "source" / "episode_1.txt"
    derived.symlink_to(outside)
    _write_script(project_dir, 1)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    _write_claimed_artifact(
        project_dir,
        adapter,
        ArtifactKey.episode_step1(1),
        "drafts/episode_1/step1_segments.json",
        digest_byte="b",
    )

    def _fail_registration(*_args, **_kwargs):
        raise RuntimeError("manifest unavailable")

    monkeypatch.setattr("lib.artifact_activation.register_artifact_entries_atomically", _fail_registration)

    with pytest.raises(RuntimeError, match="manifest unavailable"):
        reset_episode_planning(project_dir, from_episode=1, confirm_consumed=True)

    assert derived.is_symlink()
    assert derived.readlink() == outside
    assert outside.read_bytes() == b"outside"
