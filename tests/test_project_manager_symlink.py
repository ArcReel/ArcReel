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
        (profile_dir / "CLAUDE.md").write_text("You are a video creation assistant.")

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
        """Create a standard test environment: projects/ and agent_runtime_profile/"""
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
        """Missing symlinks should be created."""
        pm, project_dir = self._make_env(tmp_path)

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_symlink()
        assert (project_dir / "CLAUDE.md").is_symlink()

    def test_repair_fixes_broken_symlink(self, tmp_path):
        """Broken symlinks (is_symlink but not exists) should be deleted and recreated."""
        pm, project_dir = self._make_env(tmp_path)
        # Manually create a broken symlink pointing to a nonexistent path
        broken = project_dir / ".claude"
        broken.symlink_to(Path("../../nonexistent/.claude"))
        assert broken.is_symlink() and not broken.exists()

        pm.repair_claude_symlink(project_dir)

        assert (project_dir / ".claude").is_symlink()
        assert (project_dir / ".claude").exists()

    def test_repair_skips_valid_symlink(self, tmp_path):
        """Valid symlinks should not be modified (readlink value unchanged)."""
        pm, project_dir = self._make_env(tmp_path)
        # First create the correct symlink
        (project_dir / ".claude").symlink_to(Path("../../agent_runtime_profile/.claude"))
        original_target = Path((project_dir / ".claude").readlink())

        pm.repair_claude_symlink(project_dir)

        assert Path((project_dir / ".claude").readlink()) == original_target

    def test_repair_skips_when_profile_missing(self, tmp_path):
        """Should silently skip without error when agent_runtime_profile does not exist."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "test-proj"
        project_dir.mkdir()

        pm.repair_claude_symlink(project_dir)  # should not raise

        assert not (project_dir / ".claude").exists()


class TestRepairAllSymlinks:
    def test_repair_all_returns_stats(self, tmp_path):
        """repair_all_symlinks should return a dict with created/repaired/skipped/errors keys."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_dir = tmp_path / "agent_runtime_profile"
        (profile_dir / ".claude").mkdir(parents=True)
        (profile_dir / "CLAUDE.md").write_text("prompt")
        # An old project with no symlinks
        (projects_root / "old-proj").mkdir()
        pm = ProjectManager(projects_root)

        stats = pm.repair_all_symlinks()

        assert "created" in stats
        assert "repaired" in stats
        assert "skipped" in stats
        assert "errors" in stats
        assert stats["created"] == 2  # one each for .claude and CLAUDE.md

    def test_repair_all_skips_hidden_dirs(self, tmp_path):
        """Directories starting with . should be skipped (e.g. the directory containing .arcreel.db)."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        (tmp_path / "agent_runtime_profile" / ".claude").mkdir(parents=True)
        (tmp_path / "agent_runtime_profile" / "CLAUDE.md").write_text("prompt")
        (projects_root / ".hidden").mkdir()
        pm = ProjectManager(projects_root)

        stats = pm.repair_all_symlinks()

        assert stats["created"] == 0
