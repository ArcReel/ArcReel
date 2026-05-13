"""Tests for AssistantService.list_available_skills with agent_runtime_profile."""

from unittest.mock import patch

from server.agent_runtime.service import AssistantService


class TestListAvailableSkills:
    def test_lists_skills_from_agent_runtime_profile(self, tmp_path):
        """Should scan agent_runtime_profile/.claude/skills/ instead of .claude/skills/."""
        skill_dir = tmp_path / "agent_runtime_profile" / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test-skill\ndescription: A test skill\n---\n")

        # Create a dev-only skill in .claude/skills/ (should NOT appear)
        dev_skill = tmp_path / ".claude" / "skills" / "dev-tool"
        dev_skill.mkdir(parents=True)
        (dev_skill / "SKILL.md").write_text("---\nname: dev-tool\ndescription: Dev only\n---\n")

        with patch.object(AssistantService, "__init__", lambda self, *a, **kw: None):
            service = AssistantService.__new__(AssistantService)
            service.project_root = tmp_path
            from lib.project_manager import ProjectManager

            service.pm = ProjectManager(tmp_path / "projects")

        skills = service.list_available_skills()
        names = [s["name"] for s in skills]
        assert "test-skill" in names
        assert "dev-tool" not in names

    def test_returns_empty_when_no_profile(self, tmp_path):
        """Should return empty list when agent_runtime_profile doesn't exist."""
        with patch.object(AssistantService, "__init__", lambda self, *a, **kw: None):
            service = AssistantService.__new__(AssistantService)
            service.project_root = tmp_path
            from lib.project_manager import ProjectManager

            service.pm = ProjectManager(tmp_path / "projects")

        skills = service.list_available_skills()
        assert skills == []
