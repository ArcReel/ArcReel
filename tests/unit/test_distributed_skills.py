import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"
PUBLIC_SKILL_SELECTORS = ("setup-arcreel-skills", "video-workflow")
EMBEDDED_PUBLIC_SKILL = "adapt-custom-endpoint"


def _frontmatter(skill_file: Path) -> dict[str, object]:
    _, frontmatter, _ = skill_file.read_text(encoding="utf-8").split("---", 2)
    return yaml.safe_load(frontmatter)


def test_distributed_skills_have_flat_matching_names() -> None:
    skill_files = sorted(SKILLS_ROOT.rglob("SKILL.md"))

    assert skill_files
    for skill_file in skill_files:
        assert skill_file.relative_to(REPO_ROOT).parts == ("skills", skill_file.parent.name, "SKILL.md")
        assert _frontmatter(skill_file)["name"] == skill_file.parent.name


def test_public_skill_selectors_are_independently_installable() -> None:
    for selector in PUBLIC_SKILL_SELECTORS:
        skill_file = SKILLS_ROOT / selector / "SKILL.md"
        assert skill_file.is_file()
        assert _frontmatter(skill_file)["name"] == selector


def test_custom_endpoint_skill_is_mirrored_from_the_runtime_profile() -> None:
    skill_dir = REPO_ROOT / "agent_runtime_profile" / ".claude" / "skills" / EMBEDDED_PUBLIC_SKILL
    workflow = (REPO_ROOT / ".github" / "workflows" / "sync-public-skills.yml").read_text(encoding="utf-8")

    assert _frontmatter(skill_dir / "SKILL.md")["name"] == EMBEDDED_PUBLIC_SKILL
    assert (skill_dir / "scripts" / "custom_endpoint.py").is_file()
    assert (skill_dir / "references" / "definition-format.md").is_file()
    assert "source/agent_runtime_profile/.claude/skills/adapt-custom-endpoint" in workflow


def test_setup_skill_is_model_invocable_for_agent_onboarding() -> None:
    skill_dir = SKILLS_ROOT / "setup-arcreel-skills"

    assert "disable-model-invocation" not in _frontmatter(skill_dir / "SKILL.md")
    openai = yaml.safe_load((skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    assert openai.get("policy", {}).get("allow_implicit_invocation", True) is True


def test_video_workflow_skill_has_portable_relative_references() -> None:
    skill_dir = SKILLS_ROOT / "video-workflow"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    references = re.findall(r"\]\((references/[^)]+\.md)\)", skill)

    assert references
    assert all((skill_dir / reference).is_file() for reference in references)
