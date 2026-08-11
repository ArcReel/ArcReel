from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.lint_agent_runtime_profile import lint_profile

pytestmark = pytest.mark.unit


def _valid_profile(root: Path) -> Path:
    profile = root / "profile"
    (profile / ".claude" / "skills" / "demo").mkdir(parents=True)
    (profile / ".claude" / "agents").mkdir(parents=True)
    (profile / ".claude" / "references").mkdir(parents=True)
    (profile / "evals").mkdir()
    for mode in ("narration", "drama", "ad"):
        (profile / f"CLAUDE.{mode}.md").write_text(
            f"See `.claude/references/mode.md`.\n<!-- {mode} -->\n",
            encoding="utf-8",
        )
        (profile / ".claude" / "references" / f"mode.{mode}.md").write_text(f"# {mode}\n", encoding="utf-8")
    (profile / ".claude" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 'Calls: tools safely'\n---\nUse `mcp__arcreel__patch_project`.\n",
        encoding="utf-8",
    )
    (profile / ".claude" / "skills" / "demo" / "compiled.pyc").write_bytes(b"\xcb\x00\x01")
    (profile / ".claude" / "agents" / "helper.md").write_text(
        "---\nname: helper\ndescription: >-\n  A multiline helper\n  agent.\n---\n",
        encoding="utf-8",
    )
    (profile / "evals" / "cases.json").write_text(
        json.dumps({"evals": [{"id": "unique"}]}),
        encoding="utf-8",
    )
    return profile


def test_validates_all_profile_contracts_for_each_mode(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)

    assert lint_profile(profile, registered_tools={"patch_project"}) == []


def test_reports_invalid_frontmatter_pointer_mcp_and_eval_ids(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / ".claude" / "agents" / "helper.md").write_text("---\n- invalid\n---\n", encoding="utf-8")
    skill = profile / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8")
        + "See `.claude/references/missing.md`; call `mcp__arcreel__not_registered`.\n",
        encoding="utf-8",
    )
    (profile / "evals" / "more.json").write_text(json.dumps({"id": "unique"}), encoding="utf-8")

    errors = lint_profile(profile, registered_tools={"patch_project"})

    assert any("frontmatter" in error for error in errors)
    assert any("missing Markdown pointer" in error for error in errors)
    assert any("unregistered MCP tool" in error for error in errors)
    assert any("duplicate eval id" in error for error in errors)


def test_reports_duplicate_eval_ids_in_root_array(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / "evals" / "array.json").write_text(
        json.dumps([{"id": "duplicate"}, {"id": "duplicate"}]),
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"})

    assert any("duplicate eval id" in error for error in errors)


def test_target_deprecation_rules_are_explicit_for_variant_profile(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    skill = profile / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "Run --scene-ids.\n", encoding="utf-8")

    assert not any("deprecated" in error for error in lint_profile(profile, registered_tools={"patch_project"}))
    assert any(
        "deprecated" in error
        for error in lint_profile(profile, registered_tools={"patch_project"}, enforce_target_rules=True)
    )


def test_shipped_profile_passes_current_lint() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert lint_profile(repo_root / "agent_runtime_profile") == []
