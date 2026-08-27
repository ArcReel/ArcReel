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


def test_public_skill_descriptions_are_chinese() -> None:
    for selector in PUBLIC_SKILL_SELECTORS:
        description = _frontmatter(SKILLS_ROOT / selector / "SKILL.md")["description"]
        assert isinstance(description, str)
        assert re.search(r"[\u4e00-\u9fff]", description)


def test_setup_skill_is_explicit_only_for_claude_and_codex() -> None:
    skill_dir = SKILLS_ROOT / "setup-arcreel-skills"

    assert _frontmatter(skill_dir / "SKILL.md")["disable-model-invocation"] is True
    openai = yaml.safe_load((skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    assert openai["policy"]["allow_implicit_invocation"] is False
    assert "/setup-arcreel-skills" in openai["interface"]["default_prompt"]
    assert "$setup-arcreel-skills" not in openai["interface"]["default_prompt"]


def test_public_skills_subtree_is_mit_licensed() -> None:
    license_text = (SKILLS_ROOT / "LICENSE").read_text(encoding="utf-8")

    assert license_text.startswith("MIT License")


def test_setup_skill_sends_bearer_keys_only_over_tls_or_loopback() -> None:
    skill = (SKILLS_ROOT / "setup-arcreel-skills" / "SKILL.md").read_text(encoding="utf-8")

    assert "端点使用 `https`" in skill
    assert "回环端点可以使用 `http`" in skill


def test_video_workflow_skill_has_portable_relative_references() -> None:
    skill_dir = SKILLS_ROOT / "video-workflow"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    references = re.findall(r"\]\((references/[^)]+\.md)\)", skill)

    assert references
    assert all((skill_dir / reference).is_file() for reference in references)
    package = "\n".join(path.read_text(encoding="utf-8") for path in skill_dir.rglob("*.md"))
    assert all(term not in package for term in ("cwd", ".claude", "Claude Code", "AskUserQuestion"))


def test_video_workflow_hands_final_export_to_an_arcreel_host() -> None:
    skill = (SKILLS_ROOT / "video-workflow" / "SKILL.md").read_text(encoding="utf-8")

    assert "远程 MCP 不负责合成或导出成片" in skill
    assert "WebUI 或内嵌宿主" in skill


def test_video_workflow_polls_batches_and_allows_migration_recovery() -> None:
    skill_dir = SKILLS_ROOT / "video-workflow"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    plan_safety = (skill_dir / "references" / "plan-safety.md").read_text(encoding="utf-8")

    assert "按每次返回的 `poll_after_seconds` 调用 `get_generation_batch`" in skill
    assert "直到结果为 `done: true`" in skill
    assert "`retry_project_migration` 只允许恢复迁移" in plan_safety
