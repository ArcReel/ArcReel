"""Profile translation upgrades and downgrades preserve project-owned state."""

from pathlib import Path

import pytest

from lib.agent.profile_manifest import (
    ContentMode,
    get_profile_status,
    load_manifest,
    sync_profile_to_project,
)


@pytest.mark.parametrize("mode", ["drama", "narration", "ad"])
@pytest.mark.parametrize("edit_after_upgrade", [False, True])
def test_translation_roundtrip_preserves_user_state(
    tmp_path: Path, mode: ContentMode, edit_after_upgrade: bool
) -> None:
    profile = tmp_path / "profile"
    project = tmp_path / "project"
    skill_rel = Path(".claude/skills/demo/SKILL.md")
    deleted_rel = Path(".claude/agents/deleted.md")
    collision_rel = Path(".claude/agents/collision.md")
    original = "按既有工作流继续。\n"
    translated = "Continue using the existing workflow.\n"
    user_text = "用户自己的规则，保持原样。\n"
    later_edit = "User edit made after the translation upgrade.\n"

    for relative in (Path("CLAUDE.md"), skill_rel, deleted_rel):
        source = profile / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(original, encoding="utf-8")
    sync_profile_to_project(profile, project, mode)
    (project / skill_rel).write_text(user_text, encoding="utf-8")
    (project / deleted_rel).unlink()  # hard-delete: temp -- simulate a user deletion in a disposable project
    (project / collision_rel).write_text(user_text, encoding="utf-8")

    for relative in (Path("CLAUDE.md"), skill_rel, deleted_rel, collision_rel):
        (profile / relative).write_text(translated, encoding="utf-8")
    sync_profile_to_project(profile, project, mode)

    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == translated
    assert (project / skill_rel).read_text(encoding="utf-8") == user_text
    assert not (project / deleted_rel).exists()
    assert (project / collision_rel).read_text(encoding="utf-8") == user_text
    upgraded = load_manifest(project)
    assert upgraded is not None
    assert upgraded[0].content_mode == mode
    assert upgraded[0].entries[deleted_rel.as_posix()]["source"] == "tombstone"

    if edit_after_upgrade:
        (project / "CLAUDE.md").write_text(later_edit, encoding="utf-8")
    for relative in (Path("CLAUDE.md"), skill_rel, deleted_rel, collision_rel):
        (profile / relative).write_text(original, encoding="utf-8")
    sync_profile_to_project(profile, project, mode)

    expected = later_edit if edit_after_upgrade else original
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == expected
    assert (project / skill_rel).read_text(encoding="utf-8") == user_text
    assert not (project / deleted_rel).exists()
    assert (project / collision_rel).read_text(encoding="utf-8") == user_text
    status = get_profile_status(profile, project, mode)
    assert skill_rel.as_posix() in status["customized_files"]
    assert collision_rel.as_posix() in status["customized_files"]
