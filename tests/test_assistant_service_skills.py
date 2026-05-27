"""Tests for AssistantService.list_available_skills with agent_runtime_profile."""

from unittest.mock import patch

from server.agent_runtime.service import AssistantService


class TestListAvailableSkills:
    def test_lists_skills_from_agent_runtime_profile(self, tmp_path):
        """Should scan agent_runtime_profile/.claude/skills/ instead of .claude/skills/."""
        skill_dir = tmp_path / "agent_runtime_profile" / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test skill\n---\n",
            encoding="utf-8",
        )

        # Create a dev-only skill in .claude/skills/ (should NOT appear)
        dev_skill = tmp_path / ".claude" / "skills" / "dev-tool"
        dev_skill.mkdir(parents=True)
        (dev_skill / "SKILL.md").write_text(
            "---\nname: dev-tool\ndescription: Dev only\n---\n",
            encoding="utf-8",
        )

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

    def test_lists_skill_with_only_content_mode_variants(self, tmp_path, monkeypatch):
        """Variant-only skills (SKILL.<mode>.md without a plain SKILL.md) must appear."""
        profile_root = tmp_path / "agent_runtime_profile"
        skill_dir = profile_root / ".claude" / "skills" / "manga-workflow"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.narration.md").write_text(
            "---\nname: manga-workflow\ndescription: Narration variant\n---\n",
            encoding="utf-8",
        )
        (skill_dir / "SKILL.drama.md").write_text(
            "---\nname: manga-workflow\ndescription: Drama variant\n---\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_root))

        with patch.object(AssistantService, "__init__", lambda self, *a, **kw: None):
            service = AssistantService.__new__(AssistantService)
            service.project_root = tmp_path
            from lib.project_manager import ProjectManager

            service.pm = ProjectManager(tmp_path / "projects")

        skills = service.list_available_skills()
        names = [s["name"] for s in skills]
        assert "manga-workflow" in names

    def test_skips_variant_skill_when_user_invocable_disagrees(self, tmp_path, monkeypatch, caplog):
        """Variants with conflicting user-invocable frontmatter should be skipped with a warning."""
        import logging

        profile_root = tmp_path / "agent_runtime_profile"
        skill_dir = profile_root / ".claude" / "skills" / "drifted-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.narration.md").write_text(
            "---\nname: drifted-skill\ndescription: Narration\nuser-invocable: true\n---\n",
            encoding="utf-8",
        )
        (skill_dir / "SKILL.drama.md").write_text(
            "---\nname: drifted-skill\ndescription: Drama\nuser-invocable: false\n---\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_root))

        with patch.object(AssistantService, "__init__", lambda self, *a, **kw: None):
            service = AssistantService.__new__(AssistantService)
            service.project_root = tmp_path
            from lib.project_manager import ProjectManager

            service.pm = ProjectManager(tmp_path / "projects")

        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.service"):
            skills = service.list_available_skills()

        assert all(s["name"] != "drifted-skill" for s in skills)
        assert any("user-invocable" in record.message for record in caplog.records)
