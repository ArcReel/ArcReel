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
