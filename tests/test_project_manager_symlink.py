"""Tests for .claude and CLAUDE.md symlink creation on project creation."""

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager


@pytest.fixture(autouse=True)
def _force_symlink_platform(monkeypatch):
    """Pin sys.platform=darwin so the upstream symlink-shaped suites always
    exercise the symlink branch.

    ``ProjectManager.repair_claude_symlink`` materializes the profile into a
    real directory/file on Linux because the Claude Agent SDK bwrap sandbox
    refuses to bind a symlink at ``<cwd>/.claude``. ``TestLinuxMaterialize``
    overrides this fixture to cover the Linux branch.
    """
    monkeypatch.setattr("lib.project_manager.sys.platform", "darwin")


class TestProjectSymlink:
    def test_create_project_creates_claude_dir_symlink(self, tmp_path):
        """New project should have .claude symlink pointing to agent_runtime_profile."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_claude = tmp_path / "agent_runtime_profile" / ".claude" / "skills"
        profile_claude.mkdir(parents=True)

        pm = ProjectManager(projects_root)
        pm.create_project("test-proj")

        symlink = projects_root / "test-proj" / ".claude"
        assert symlink.is_symlink()
        target = symlink.resolve()
        expected = (tmp_path / "agent_runtime_profile" / ".claude").resolve()
        assert target == expected

    def test_create_project_creates_claude_md_symlink(self, tmp_path):
        """New project should have CLAUDE.md symlink pointing to agent_runtime_profile."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_dir = tmp_path / "agent_runtime_profile"
        profile_dir.mkdir(parents=True)
        (profile_dir / "CLAUDE.md").write_text("你是视频创作助手。")

        pm = ProjectManager(projects_root)
        pm.create_project("test-proj")

        symlink = projects_root / "test-proj" / "CLAUDE.md"
        assert symlink.is_symlink()
        target = symlink.resolve()
        expected = (profile_dir / "CLAUDE.md").resolve()
        assert target == expected

    def test_create_project_symlinks_are_relative(self, tmp_path):
        """Symlinks should use relative paths for portability."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_dir = tmp_path / "agent_runtime_profile"
        (profile_dir / ".claude").mkdir(parents=True)
        (profile_dir / "CLAUDE.md").write_text("prompt")

        pm = ProjectManager(projects_root)
        pm.create_project("test-proj")

        for name in (".claude", "CLAUDE.md"):
            symlink = projects_root / "test-proj" / name
            link_target = Path(symlink.readlink())
            assert not link_target.is_absolute(), f"{name} symlink should be relative"

    def test_create_project_skips_symlinks_when_profile_missing(self, tmp_path):
        """If agent_runtime_profile doesn't exist, skip symlinks (no error)."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        pm = ProjectManager(projects_root)
        project_dir = pm.create_project("test-proj")

        assert not (project_dir / ".claude").exists()
        assert not (project_dir / "CLAUDE.md").exists()


class TestRepairClaudeSymlink:
    def _make_env(self, tmp_path):
        """创建标准测试环境：projects/ 和 agent_runtime_profile/"""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_dir = tmp_path / "agent_runtime_profile"
        (profile_dir / ".claude").mkdir(parents=True)
        (profile_dir / "CLAUDE.md").write_text("prompt")
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "test-proj"
        project_dir.mkdir()
        return pm, project_dir

    def test_repair_creates_missing_symlinks(self, tmp_path):
        """缺失软连接时应新建。"""
        pm, project_dir = self._make_env(tmp_path)

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_symlink()
        assert (project_dir / "CLAUDE.md").is_symlink()

    def test_repair_fixes_broken_symlink(self, tmp_path):
        """损坏的软连接（is_symlink but not exists）应被删除并重建。"""
        pm, project_dir = self._make_env(tmp_path)
        # 手动创建一个指向不存在路径的损坏软连接
        broken = project_dir / ".claude"
        broken.symlink_to(Path("../../nonexistent/.claude"))
        assert broken.is_symlink() and not broken.exists()

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_symlink()
        assert (project_dir / ".claude").exists()

    def test_repair_skips_valid_symlink(self, tmp_path):
        """已正确的软连接不应被修改（readlink 值不变）。"""
        pm, project_dir = self._make_env(tmp_path)
        # 先建好正确软连接
        (project_dir / ".claude").symlink_to(Path("../../agent_runtime_profile/.claude"))
        original_target = Path((project_dir / ".claude").readlink())

        pm.repair_claude_symlink(project_dir)

        assert Path((project_dir / ".claude").readlink()) == original_target

    def test_repair_skips_when_profile_missing(self, tmp_path):
        """agent_runtime_profile 不存在时静默跳过，不报错。"""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "test-proj"
        project_dir.mkdir()

        pm.repair_claude_symlink(project_dir)  # 不应抛异常

        assert not (project_dir / ".claude").exists()

    def test_repair_refreshes_stale_symlink_after_profile_dir_change(self, tmp_path, monkeypatch):
        """ARCREEL_PROFILE_DIR 变更后，存量项目里指向旧 profile 的 symlink 应被刷到新 profile。"""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        project_dir = projects_root / "test-proj"
        project_dir.mkdir()

        # 旧 profile：autouse fixture 已 pin 在 tmp_path/agent_runtime_profile
        old_profile = tmp_path / "agent_runtime_profile"
        (old_profile / ".claude").mkdir(parents=True)
        (old_profile / "CLAUDE.md").write_text("old")

        pm = ProjectManager(projects_root)
        pm.repair_claude_symlink(project_dir)

        # 切到新 profile 路径，content 完全不同
        new_profile = tmp_path / "alt_profile"
        (new_profile / ".claude").mkdir(parents=True)
        (new_profile / "CLAUDE.md").write_text("new")
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(new_profile))

        stats = pm.repair_claude_symlink(project_dir)

        assert stats["repaired"] == 2  # .claude + CLAUDE.md 都被刷新
        assert (project_dir / ".claude").resolve() == (new_profile / ".claude").resolve()
        assert (project_dir / "CLAUDE.md").resolve() == (new_profile / "CLAUDE.md").resolve()


class TestRepairAllSymlinks:
    def test_repair_all_returns_stats(self, tmp_path):
        """repair_all_symlinks 应返回含 created/repaired/skipped/errors 的字典。"""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_dir = tmp_path / "agent_runtime_profile"
        (profile_dir / ".claude").mkdir(parents=True)
        (profile_dir / "CLAUDE.md").write_text("prompt")
        # 一个无软连接的老项目
        (projects_root / "old-proj").mkdir()
        pm = ProjectManager(projects_root)

        stats = pm.repair_all_symlinks()

        assert "created" in stats
        assert "repaired" in stats
        assert "skipped" in stats
        assert "errors" in stats
        assert stats["created"] == 2  # .claude 和 CLAUDE.md 各一条

    def test_repair_all_skips_hidden_dirs(self, tmp_path):
        """以 . 开头的目录应跳过（如 .arcreel.db 所在目录）。"""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        (tmp_path / "agent_runtime_profile" / ".claude").mkdir(parents=True)
        (tmp_path / "agent_runtime_profile" / "CLAUDE.md").write_text("prompt")
        (projects_root / ".hidden").mkdir()
        pm = ProjectManager(projects_root)

        stats = pm.repair_all_symlinks()

        assert stats["created"] == 0


# ---------------------------------------------------------------------------
# Linux branch: ``repair_claude_symlink`` materializes ``.claude`` / ``CLAUDE.md``
# into real directories/files so the Claude Agent SDK bwrap sandbox can bind
# the path (it lstats ``<cwd>/.claude`` and refuses to bind a symlink that
# resolves to a directory, raising ``Can't create file ... Is a directory``).
# ---------------------------------------------------------------------------


class TestLinuxMaterialize:
    @pytest.fixture(autouse=True)
    def _force_linux(self, monkeypatch):
        # Overrides the module-level fixture (pinned to darwin) for this class.
        monkeypatch.setattr("lib.project_manager.sys.platform", "linux")

    def _make_env(self, tmp_path: Path):
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_dir = tmp_path / "agent_runtime_profile"
        (profile_dir / ".claude").mkdir(parents=True)
        (profile_dir / "CLAUDE.md").write_text("prompt")
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "test-proj"
        project_dir.mkdir()
        return pm, project_dir, profile_dir

    def test_creates_real_directory_and_file(self, tmp_path):
        pm, project_dir, _ = self._make_env(tmp_path)

        pm.repair_claude_symlink(project_dir)

        claude = project_dir / ".claude"
        assert claude.is_dir() and not claude.is_symlink()
        md = project_dir / "CLAUDE.md"
        assert md.is_file() and not md.is_symlink()

    def test_upgrades_existing_symlink_to_real_dir(self, tmp_path):
        """A symlink left over from a previous release should be upgraded
        to a materialized copy on startup."""
        pm, project_dir, _ = self._make_env(tmp_path)
        (project_dir / ".claude").symlink_to(Path("../../agent_runtime_profile/.claude"))

        stats = pm.repair_claude_symlink(project_dir)

        claude = project_dir / ".claude"
        assert claude.is_dir() and not claude.is_symlink()
        assert stats["repaired"] >= 1

    def test_sync_propagates_profile_updates(self, tmp_path):
        """A second ``repair`` after profile updates must propagate new files,
        preserving the upstream symlink-mode semantics where editing the
        profile is reflected immediately in every project."""
        pm, project_dir, profile_dir = self._make_env(tmp_path)

        pm.repair_claude_symlink(project_dir)
        assert (project_dir / ".claude").is_dir()

        (profile_dir / ".claude" / "skills").mkdir()
        (profile_dir / ".claude" / "skills" / "new.md").write_text("v2")
        (profile_dir / "CLAUDE.md").write_text("v2 prompt")

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude" / "skills" / "new.md").read_text() == "v2"
        assert (project_dir / "CLAUDE.md").read_text() == "v2 prompt"

    def test_sync_removes_deleted_profile_files(self, tmp_path):
        pm, project_dir, profile_dir = self._make_env(tmp_path)
        stale_source = profile_dir / ".claude" / "stale.md"
        stale_source.write_text("remove me")

        pm.repair_claude_symlink(project_dir)
        assert (project_dir / ".claude" / "stale.md").exists()

        stale_source.unlink()
        pm.repair_claude_symlink(project_dir)

        assert not (project_dir / ".claude" / "stale.md").exists()

    def test_sync_replaces_mismatched_destination_types(self, tmp_path):
        pm, project_dir, _ = self._make_env(tmp_path)
        (project_dir / ".claude").write_text("wrong type")
        (project_dir / "CLAUDE.md").mkdir()

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_dir()
        assert (project_dir / "CLAUDE.md").is_file()
        assert (project_dir / "CLAUDE.md").read_text() == "prompt"

    def test_repair_idempotent(self, tmp_path):
        pm, project_dir, _ = self._make_env(tmp_path)

        pm.repair_claude_symlink(project_dir)
        stats = pm.repair_claude_symlink(project_dir)

        assert stats["errors"] == 0
        assert (project_dir / ".claude").is_dir()
