"""Tests for .claude symlink creation on project creation."""

from pathlib import Path
from lib.project_manager import ProjectManager


class TestProjectSymlink:
    def test_create_project_creates_claude_symlink(self, tmp_path):
        """New project should have .claude symlink pointing to agent_runtime_profile."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        # Create agent_runtime_profile structure
        profile_claude = tmp_path / "agent_runtime_profile" / ".claude" / "skills"
        profile_claude.mkdir(parents=True)

        pm = ProjectManager(projects_root)
        pm.create_project("test-proj")

        symlink = projects_root / "test-proj" / ".claude"
        assert symlink.is_symlink()
        target = symlink.resolve()
        expected = (tmp_path / "agent_runtime_profile" / ".claude").resolve()
        assert target == expected

    def test_create_project_symlink_is_relative(self, tmp_path):
        """Symlink should use relative path for portability."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        profile_claude = tmp_path / "agent_runtime_profile" / ".claude"
        profile_claude.mkdir(parents=True)

        pm = ProjectManager(projects_root)
        pm.create_project("test-proj")

        symlink = projects_root / "test-proj" / ".claude"
        link_target = Path(symlink.readlink())
        # Should be relative, not absolute
        assert not link_target.is_absolute()

    def test_create_project_skips_symlink_when_profile_missing(self, tmp_path):
        """If agent_runtime_profile doesn't exist, skip symlink (no error)."""
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        # No agent_runtime_profile created

        pm = ProjectManager(projects_root)
        project_dir = pm.create_project("test-proj")

        symlink = project_dir / ".claude"
        assert not symlink.exists()
