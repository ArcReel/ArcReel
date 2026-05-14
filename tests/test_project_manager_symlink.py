"""Tests for manifest-driven profile sync via ``ProjectManager.repair_claude_symlink``.

历史命名 ``test_project_manager_symlink.py`` 保留（外部测试 selector 仍用此名）。
PR fix/agent-profile-sync-manifest 起改为 manifest + sha256 同步：
- profile 升级内置 skill 自动传播到老项目（行 #4）
- 用户主动删除内置 skill 不复活（行 #2/#11）
- 命名碰撞 / 状态机回流 / 上游删除等 15 行决策表完整覆盖

完整规格见: /Users/pollochen/.claude/plans/temporal-foraging-tulip.md
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lib.profile_manifest import (
    EXPECTED_PROFILE_ID,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    ProfileEmptyError,
    ProfileMissingError,
)
from lib.project_manager import ProjectManager

# ---------- 公共 fixtures ----------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """构造标准测试环境：profile_dir + projects_root + 单个项目目录。

    profile 内置一个 demo skill 和顶层 CLAUDE.md。
    """
    profile_dir = tmp_path / "profile"
    (profile_dir / ".claude" / "skills" / "demo").mkdir(parents=True)
    (profile_dir / ".claude" / "skills" / "demo" / "SKILL.md").write_text("demo v1")
    (profile_dir / "CLAUDE.md").write_text("prompt v1")

    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_dir))

    pm = ProjectManager(projects_root)
    project_dir = projects_root / "proj"
    project_dir.mkdir()
    return pm, profile_dir, project_dir


def _read_manifest(project_dir: Path) -> dict:
    return json.loads((project_dir / MANIFEST_FILENAME).read_text())


def _skill_path(project_dir: Path, name: str = "demo") -> Path:
    return project_dir / ".claude" / "skills" / name / "SKILL.md"


def _profile_skill_path(profile_dir: Path, name: str = "demo") -> Path:
    return profile_dir / ".claude" / "skills" / name / "SKILL.md"


# ---------- 首次迁移分支 ----------


class TestFirstSyncMigration:
    def test_first_sync_full_reset_when_no_manifest(self, env):
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)

        assert _skill_path(project_dir).read_text() == "demo v1"
        assert (project_dir / "CLAUDE.md").read_text() == "prompt v1"
        manifest = _read_manifest(project_dir)
        assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
        assert manifest["profile_id"] == EXPECTED_PROFILE_ID
        assert ".claude/skills/demo/SKILL.md" in manifest["entries"]
        assert "CLAUDE.md" in manifest["entries"]

    def test_first_sync_resets_even_with_existing_user_content(self, env):
        """用户决策'忽略已有'：首次接入时直接覆盖 dest。"""
        pm, _, project_dir = env
        _skill_path(project_dir).parent.mkdir(parents=True, exist_ok=True)
        _skill_path(project_dir).write_text("legacy junk")
        (project_dir / "CLAUDE.md").write_text("legacy prompt")

        pm.repair_claude_symlink(project_dir)

        assert _skill_path(project_dir).read_text() == "demo v1"
        assert (project_dir / "CLAUDE.md").read_text() == "prompt v1"

    def test_create_project_invokes_sync(self, env):
        pm, _, _ = env
        new_dir = pm.create_project("brand-new")

        assert (new_dir / ".claude").is_dir()
        assert not (new_dir / ".claude").is_symlink()
        assert (new_dir / MANIFEST_FILENAME).is_file()
        assert (new_dir / "CLAUDE.md").read_text() == "prompt v1"


# ---------- 决策表 15 行覆盖 ----------


class TestDecisionTable:
    def test_decision_2_user_delete_not_resurrected(self, env):
        """#2：profile 存在 + dest 缺失 + manifest active → 转 tombstone，不补回。"""
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).unlink()
        os.rmdir(_skill_path(project_dir).parent)

        stats = pm.repair_claude_symlink(project_dir)

        assert not _skill_path(project_dir).exists()
        assert stats["deleted_user"] == 1
        entries = _read_manifest(project_dir)["entries"]
        assert entries[".claude/skills/demo/SKILL.md"]["source"] == "tombstone"

    def test_decision_3_no_op_when_three_hashes_match(self, env):
        """#3：三态一致 → no-op，manifest 字节不变（写前比对生效）。"""
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)
        raw1 = (project_dir / MANIFEST_FILENAME).read_bytes()
        mtime1 = _skill_path(project_dir).stat().st_mtime_ns

        stats = pm.repair_claude_symlink(project_dir)

        raw2 = (project_dir / MANIFEST_FILENAME).read_bytes()
        assert raw1 == raw2
        assert _skill_path(project_dir).stat().st_mtime_ns == mtime1
        assert stats["unchanged"] >= 1

    def test_decision_4_profile_upgrade_propagates_when_user_clean(self, env):
        """#4：用户未改 + profile 升级 → 覆盖，manifest 刷 hash。这是方案 C 的核心价值。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _profile_skill_path(profile_dir).write_text("demo v2")

        stats = pm.repair_claude_symlink(project_dir)

        assert _skill_path(project_dir).read_text() == "demo v2"
        assert stats["upgraded"] == 1

    def test_decision_5_user_edit_converging_to_profile_version(self, env):
        """#5：用户改完恰好 = profile 当前版 → 状态机回流刷 manifest，下轮归 #3。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _profile_skill_path(profile_dir).write_text("demo v2")
        _skill_path(project_dir).write_text("demo v2")

        stats = pm.repair_claude_symlink(project_dir)

        assert stats["unchanged"] >= 1
        assert stats["user_modified"] == 0
        stats2 = pm.repair_claude_symlink(project_dir)
        assert stats2["unchanged"] >= 1
        assert stats2["user_modified"] == 0
        assert stats2["upgraded"] == 0

    def test_decision_6_user_edit_preserved_against_profile_upgrade(self, env):
        """#6：用户改 + profile 升级 → 保留用户版。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).write_text("user customized")
        _profile_skill_path(profile_dir).write_text("demo v2")

        stats = pm.repair_claude_symlink(project_dir)

        assert _skill_path(project_dir).read_text() == "user customized"
        assert stats["user_modified"] == 1

    def test_decision_7_profile_deletion_propagates_to_unmodified_dest(self, env):
        """#7：profile 上游删 + 用户未改 → 同步删除 dest + tombstone。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _profile_skill_path(profile_dir).unlink()

        stats = pm.repair_claude_symlink(project_dir)

        assert not _skill_path(project_dir).exists()
        assert stats["pruned"] == 1
        entries = _read_manifest(project_dir)["entries"]
        assert entries[".claude/skills/demo/SKILL.md"]["source"] == "tombstone"

    def test_decision_8_profile_deletion_orphans_user_modified(self, env):
        """#8：profile 上游删 + 用户改过 → 保留 dest + 清 entry，stat=orphaned。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).write_text("user owned now")
        _profile_skill_path(profile_dir).unlink()

        stats = pm.repair_claude_symlink(project_dir)

        assert _skill_path(project_dir).read_text() == "user owned now"
        assert stats["orphaned"] == 1
        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/demo/SKILL.md" not in entries

    def test_decision_9_user_only_file_untouched(self, env):
        """#9：项目独有 skill（profile 没有，manifest 无记录）→ 完全不动。"""
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)
        user_skill = project_dir / ".claude" / "skills" / "user_only" / "SKILL.md"
        user_skill.parent.mkdir(parents=True)
        user_skill.write_text("private workflow")

        for _ in range(3):
            pm.repair_claude_symlink(project_dir)

        assert user_skill.read_text() == "private workflow"
        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/user_only/SKILL.md" not in entries

    def test_decision_10_user_manually_restores_deleted_file(self, env):
        """#10：tombstone 状态下用户手动重写 D → 清 tombstone，下轮按 #9 user_only。"""
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).unlink()
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).write_text("user restored version")

        stats = pm.repair_claude_symlink(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/demo/SKILL.md" not in entries
        assert stats["user_only"] >= 1

    def test_decision_11_tombstone_steady_state(self, env):
        """#11：用户删 + profile 仍在，跑 N 次 repair 都稳态 no-op。"""
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).unlink()
        pm.repair_claude_symlink(project_dir)

        for _ in range(5):
            stats = pm.repair_claude_symlink(project_dir)
            assert not _skill_path(project_dir).exists()
            assert stats["created"] == 0
            assert stats["upgraded"] == 0
            assert stats["tombstoned"] >= 1

    def test_decision_12_orphaned_dest_with_tombstone_clears_entry(self, env):
        """#12：profile 删了 + dest 还在 + manifest tombstone → 清 entry。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).unlink()
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).write_text("user re-added")
        _profile_skill_path(profile_dir).unlink()

        stats = pm.repair_claude_symlink(project_dir)

        assert _skill_path(project_dir).exists()
        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/demo/SKILL.md" not in entries
        assert stats["user_only"] >= 1

    def test_decision_13_tombstone_persists_when_both_missing(self, env):
        """#13：profile + dest 都没 + manifest tombstone → no-op，tombstone 持续。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).unlink()
        pm.repair_claude_symlink(project_dir)
        _profile_skill_path(profile_dir).unlink()

        stats = pm.repair_claude_symlink(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        assert entries.get(".claude/skills/demo/SKILL.md", {}).get("source") == "tombstone"
        assert stats["tombstoned"] >= 1

    def test_decision_14_double_delete_creates_tombstone(self, env):
        """#14：双方同轮删（active entry）→ 转 tombstone。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _profile_skill_path(profile_dir).unlink()
        _skill_path(project_dir).unlink()

        stats = pm.repair_claude_symlink(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        assert entries[".claude/skills/demo/SKILL.md"]["source"] == "tombstone"
        assert stats["pruned"] == 1

    def test_decision_14_tombstone_blocks_future_readd_unless_force_resync(self, env):
        """#14 隐含假设：tombstone 后 profile 重新加回 → 仍走 #11，不自动复活。需 force_resync。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _profile_skill_path(profile_dir).unlink()
        _skill_path(project_dir).unlink()
        pm.repair_claude_symlink(project_dir)
        _profile_skill_path(profile_dir).parent.mkdir(parents=True, exist_ok=True)
        _profile_skill_path(profile_dir).write_text("demo v2 readded")

        stats = pm.repair_claude_symlink(project_dir)

        assert not _skill_path(project_dir).exists()
        assert stats["tombstoned"] >= 1
        pm.force_resync_profile(project_dir, paths=[".claude/skills/demo/SKILL.md"])
        assert _skill_path(project_dir).read_text() == "demo v2 readded"

    def test_decision_15_collision_preserves_user_content(self, env):
        """#15：用户独立创建同名文件（D≠P）→ 保留 D，不写 entry。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)  # 建 baseline
        # 现在手加 user_x 和 profile_x 同名但不同内容
        user_x = project_dir / ".claude" / "skills" / "X" / "SKILL.md"
        user_x.parent.mkdir(parents=True)
        user_x.write_text("user version A")
        profile_x = profile_dir / ".claude" / "skills" / "X" / "SKILL.md"
        profile_x.parent.mkdir(parents=True)
        profile_x.write_text("profile version B")

        stats = pm.repair_claude_symlink(project_dir)

        assert user_x.read_text() == "user version A"
        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/X/SKILL.md" not in entries
        assert stats["collision"] == 1

    def test_decision_15_collision_when_hashes_match_writes_active_entry(self, env):
        """#15：D=P 时视为已下发，写 active entry，下轮归 #3 unchanged。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        user_x = project_dir / ".claude" / "skills" / "X" / "SKILL.md"
        user_x.parent.mkdir(parents=True)
        user_x.write_text("same content")
        profile_x = profile_dir / ".claude" / "skills" / "X" / "SKILL.md"
        profile_x.parent.mkdir(parents=True)
        profile_x.write_text("same content")

        stats = pm.repair_claude_symlink(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/X/SKILL.md" in entries
        assert entries[".claude/skills/X/SKILL.md"]["source"] == "profile"
        assert stats["collision"] == 1
        stats2 = pm.repair_claude_symlink(project_dir)
        assert stats2["collision"] == 0
        assert stats2["unchanged"] >= 1


# ---------- force_resync ----------


class TestForceResync:
    def test_force_resync_overrides_user_edit(self, env):
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).write_text("user customized")

        stats = pm.force_resync_profile(project_dir, paths=[".claude/skills/demo/SKILL.md"])

        assert _skill_path(project_dir).read_text() == "demo v1"
        assert stats["created"] == 1

    def test_force_resync_skips_missing_profile_file(self, env, caplog):
        """paths 含 profile 已删的文件 → skip + warn，不算 error。"""
        pm, profile_dir, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _profile_skill_path(profile_dir).unlink()

        with caplog.at_level("WARNING"):
            stats = pm.force_resync_profile(project_dir, paths=[".claude/skills/demo/SKILL.md"])

        assert stats["errors"] == 0
        assert stats["created"] == 0
        assert any("force_resync skip" in r.message for r in caplog.records)


# ---------- 入口防御 ----------


class TestProfileEntryGuards:
    def test_profile_missing_raises_protective_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """profile 不存在 → ProfileMissingError，绝不静默 mass prune dest。"""
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(tmp_path / "nonexistent"))
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "proj"
        project_dir.mkdir()
        (project_dir / ".claude").mkdir()
        (project_dir / ".claude" / "skill.md").write_text("must not be pruned")

        with pytest.raises(ProfileMissingError):
            pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude" / "skill.md").exists()

    def test_profile_empty_raises_protective_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """profile 目录存在但无可同步文件 → ProfileEmptyError。"""
        empty_profile = tmp_path / "profile"
        empty_profile.mkdir()
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(empty_profile))
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "proj"
        project_dir.mkdir()

        with pytest.raises(ProfileEmptyError):
            pm.repair_claude_symlink(project_dir)


# ---------- repair_all_symlinks ----------


class TestRepairAllSymlinks:
    def test_repair_all_returns_stats_with_aggregated_keys(self, env):
        pm, _, _ = env

        stats = pm.repair_all_symlinks()

        assert "created" in stats
        assert "repaired" in stats
        assert "skipped" in stats
        assert "errors" in stats
        assert "failed_projects" in stats
        assert "aborted" in stats
        assert stats["created"] >= 2

    def test_repair_all_skips_hidden_dirs(self, env):
        pm, _, _ = env
        (pm.projects_root / ".hidden").mkdir()
        stats = pm.repair_all_symlinks()
        assert not (pm.projects_root / ".hidden" / ".claude").exists()
        assert stats["aborted"] is False

    def test_repair_all_continues_on_single_project_failure(self, env, monkeypatch: pytest.MonkeyPatch):
        """单项目异常 → 其他项目继续；failed_projects 计数。"""
        pm, _, _ = env
        (pm.projects_root / "proj2").mkdir()

        original = pm.repair_claude_symlink

        def patched(project_dir: Path):
            if project_dir.name == "proj":
                raise RuntimeError("simulated failure on proj")
            return original(project_dir)

        monkeypatch.setattr(pm, "repair_claude_symlink", patched)

        stats = pm.repair_all_symlinks()

        assert stats["failed_projects"] == 1
        assert (pm.projects_root / "proj2" / ".claude").is_dir()
        assert stats["aborted"] is False

    def test_repair_all_aborts_on_profile_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """ProfileMissingError → totals.aborted=True，所有项目跳过。"""
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(tmp_path / "nonexistent"))
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        (projects_root / "proj1").mkdir()
        (projects_root / "proj2").mkdir()
        pm = ProjectManager(projects_root)

        stats = pm.repair_all_symlinks()

        assert stats["aborted"] is True
        assert not (projects_root / "proj1" / MANIFEST_FILENAME).exists()
        assert not (projects_root / "proj2" / MANIFEST_FILENAME).exists()


# ---------- 老 symlink 迁移 ----------


class TestLegacySymlinkMigration:
    def test_legacy_symlink_replaced_with_materialized_dir(self, env):
        """老版本部署遗留的 symlink → 首次 repair 拆除升级为物化。"""
        pm, profile_dir, project_dir = env
        (project_dir / ".claude").symlink_to(profile_dir / ".claude")
        (project_dir / "CLAUDE.md").symlink_to(profile_dir / "CLAUDE.md")

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_dir()
        assert not (project_dir / ".claude").is_symlink()
        assert (project_dir / "CLAUDE.md").is_file()
        assert not (project_dir / "CLAUDE.md").is_symlink()
        assert (project_dir / MANIFEST_FILENAME).exists()


# ---------- manifest 字段不变量 ----------


class TestManifestInvariants:
    def test_manifest_uses_posix_path_keys(self, env):
        """跨平台路径 key 必须用 POSIX 分隔符。"""
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        for key in entries.keys():
            assert "\\" not in key, f"manifest key has backslash: {key!r}"

    def test_manifest_skipped_when_unchanged_across_repair(self, env):
        """repeat repair 时 manifest 字节级稳态（写前比对生效）。"""
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)
        raw1 = (project_dir / MANIFEST_FILENAME).read_bytes()
        mtime1 = (project_dir / MANIFEST_FILENAME).stat().st_mtime_ns

        pm.repair_claude_symlink(project_dir)

        raw2 = (project_dir / MANIFEST_FILENAME).read_bytes()
        mtime2 = (project_dir / MANIFEST_FILENAME).stat().st_mtime_ns
        assert raw1 == raw2
        assert mtime1 == mtime2

    def test_manifest_schema_version_mismatch_triggers_full_reset(self, env):
        """旧 schema 的 manifest → 触发 _full_reset_from_profile。"""
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)
        _skill_path(project_dir).write_text("user customized")
        manifest_path = project_dir / MANIFEST_FILENAME
        data = json.loads(manifest_path.read_text())
        data["schema_version"] = 999
        manifest_path.write_text(json.dumps(data))

        pm.repair_claude_symlink(project_dir)

        assert _skill_path(project_dir).read_text() == "demo v1"
        assert _read_manifest(project_dir)["schema_version"] == MANIFEST_SCHEMA_VERSION

    def test_manifest_profile_id_mismatch_triggers_full_reset(self, env):
        """profile_id 不匹配 → 等价 reset。"""
        pm, _, project_dir = env
        pm.repair_claude_symlink(project_dir)
        manifest_path = project_dir / MANIFEST_FILENAME
        data = json.loads(manifest_path.read_text())
        data["profile_id"] = "other/foo"
        manifest_path.write_text(json.dumps(data))

        pm.repair_claude_symlink(project_dir)

        assert _read_manifest(project_dir)["profile_id"] == EXPECTED_PROFILE_ID
