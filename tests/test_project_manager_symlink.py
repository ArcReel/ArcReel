"""Tests for .claude and CLAUDE.md symlink creation on project creation."""

from pathlib import Path
from lib.project_manager import ProjectManager


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
