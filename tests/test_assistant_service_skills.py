"""Tests for AssistantService.list_available_skills with agent_runtime_profile."""

from unittest.mock import patch

import pytest

from server.agent_runtime.service import AssistantService

pytestmark = pytest.mark.unit


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
        (skill_dir / "SKILL.ad.md").write_text(
            "---\nname: manga-workflow\ndescription: Ad variant\n---\n",
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

    @pytest.mark.parametrize("include_common", [False, True])
    def test_skips_incomplete_or_conflicting_variants(self, tmp_path, monkeypatch, caplog, include_common):
        import logging

        profile_root = tmp_path / "agent_runtime_profile"
        skill_dir = profile_root / ".claude" / "skills" / "broken-variants"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.narration.md").write_text(
            "---\nname: broken-variants\ndescription: Narration\n---\n",
            encoding="utf-8",
        )
        if include_common:
            (skill_dir / "SKILL.md").write_text(
                "---\nname: broken-variants\ndescription: Common\n---\n",
                encoding="utf-8",
            )
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_root))
        with patch.object(AssistantService, "__init__", lambda self, *a, **kw: None):
            service = AssistantService.__new__(AssistantService)
            from lib.project_manager import ProjectManager

            service.pm = ProjectManager(tmp_path / "projects")

        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.service"):
            assert service.list_available_skills() == []
        assert "跳过" in caplog.text

    def test_accepts_crlf_frontmatter_and_rejects_malformed_closing_delimiter(self, tmp_path, monkeypatch, caplog):
        import logging

        profile_root = tmp_path / "agent_runtime_profile"
        skills_root = profile_root / ".claude" / "skills"
        valid = skills_root / "valid"
        invalid = skills_root / "invalid"
        valid.mkdir(parents=True)
        invalid.mkdir(parents=True)
        (valid / "SKILL.md").write_bytes(b"---\r\nname: valid\r\ndescription: Valid on Windows\r\n---\r\nBody\r\n")
        (invalid / "SKILL.md").write_text(
            "---\nname: invalid\ndescription: Invalid delimiter\n---oops\nBody\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_root))
        with patch.object(AssistantService, "__init__", lambda self, *a, **kw: None):
            service = AssistantService.__new__(AssistantService)
            from lib.project_manager import ProjectManager

            service.pm = ProjectManager(tmp_path / "projects")

        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.service"):
            skills = service.list_available_skills()

        assert [skill["name"] for skill in skills] == ["valid"]
        assert "invalid skill frontmatter" in caplog.text

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
        (skill_dir / "SKILL.ad.md").write_text(
            "---\nname: drifted-skill\ndescription: Ad\nuser-invocable: true\n---\n",
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

    def test_parses_quoted_colon_and_multiline_yaml_metadata(self, tmp_path, monkeypatch):
        profile_root = tmp_path / "agent_runtime_profile"
        skill_dir = profile_root / ".claude" / "skills" / "yaml-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: "yaml: skill"
description: >-
  Read quoted values: safely
  across multiple lines.
user-invocable: true
---
# Body
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_root))
        with patch.object(AssistantService, "__init__", lambda self, *a, **kw: None):
            service = AssistantService.__new__(AssistantService)
            from lib.project_manager import ProjectManager

            service.pm = ProjectManager(tmp_path / "projects")

        assert service.list_available_skills() == [
            {
                "name": "yaml: skill",
                "description": "Read quoted values: safely across multiple lines.",
                "scope": "agent",
                "path": str(skill_dir / "SKILL.md"),
            }
        ]

    @pytest.mark.parametrize(
        "frontmatter",
        [
            "- not\n- an object",
            "name: ''\ndescription: valid",
            "name: valid\ndescription: ''",
            "name: valid\ndescription: valid\nuser-invocable: nope",
            "name: [broken\ndescription: invalid yaml",
        ],
    )
    def test_invalid_yaml_metadata_is_warned_and_hidden(self, tmp_path, monkeypatch, caplog, frontmatter):
        import logging

        profile_root = tmp_path / "agent_runtime_profile"
        skill_dir = profile_root / ".claude" / "skills" / "broken"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\n{frontmatter}\n---\nBody must not become a fallback description.\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_root))
        with patch.object(AssistantService, "__init__", lambda self, *a, **kw: None):
            service = AssistantService.__new__(AssistantService)
            from lib.project_manager import ProjectManager

            service.pm = ProjectManager(tmp_path / "projects")

        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.service"):
            assert service.list_available_skills() == []
        assert "frontmatter" in caplog.text
