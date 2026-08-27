import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"
PUBLIC_SKILL_SELECTORS = ("setup-arcreel-skills", "video-workflow")


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


def test_setup_skill_is_explicit_only_for_claude_and_codex() -> None:
    skill_dir = SKILLS_ROOT / "setup-arcreel-skills"

    assert _frontmatter(skill_dir / "SKILL.md")["disable-model-invocation"] is True
    openai = yaml.safe_load((skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    assert openai["policy"]["allow_implicit_invocation"] is False


def test_video_workflow_skill_has_portable_relative_references() -> None:
    skill_dir = SKILLS_ROOT / "video-workflow"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    references = re.findall(r"\]\((references/[^)]+\.md)\)", skill)

    assert references
    assert all((skill_dir / reference).is_file() for reference in references)
