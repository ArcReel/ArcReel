from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.profile_frontmatter import parse_profile_metadata
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


def test_ignores_supporting_skill_markdown_files(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / ".claude" / "skills" / "demo" / "SKILL_NOTES.md").write_text(
        "# Supporting notes without frontmatter\n", encoding="utf-8"
    )

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


def test_excludes_sentence_punctuation_from_mcp_tool_ids(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    skill = profile / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "Use mcp__arcreel__patch_project. Avoid mcp__arcreel__not_registered!\n",
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"})

    assert not any("patch_project." in error for error in errors)
    assert any("mcp__arcreel__not_registered" in error for error in errors)


def test_reports_duplicate_eval_ids_in_root_array(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / "evals" / "array.json").write_text(
        json.dumps([{"id": "duplicate"}, {"id": "duplicate"}]),
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"})

    assert any("duplicate eval id" in error for error in errors)


def test_rejects_non_standard_json_constants_in_eval_ids(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / "evals" / "constants.json").write_text('{"evals":[{"id":NaN}]}', encoding="utf-8")

    errors = lint_profile(profile, registered_tools={"patch_project"})

    assert any("non-standard JSON constant 'NaN'" in error for error in errors)


def test_normalizes_relative_markdown_pointers(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    skill = profile / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8")
        + "See [mode](../../references/mode.md) and [outside](../../../../outside.md).\n",
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"})

    assert not any("../../references/mode.md" in error for error in errors)
    assert any("missing Markdown pointer '../../../../outside.md'" in error for error in errors)


def test_validates_titled_and_reference_markdown_links(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    skill = profile / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8")
        + '[mode](<../../references/mode.md> "Mode")\n'
        + "[missing](missing-inline.md 'Missing')\n"
        + "[mode reference][mode]\n"
        + '[mode]: ../../references/mode.md "Mode"\n'
        + "[missing reference][missing]\n"
        + '[missing]: missing-reference.md "Missing"\n',
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"})

    assert not any("../../references/mode.md" in error for error in errors)
    assert any("missing Markdown pointer 'missing-inline.md'" in error for error in errors)
    assert any("missing Markdown pointer 'missing-reference.md'" in error for error in errors)


def test_decodes_url_escaped_markdown_pointers(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    reference = profile / ".claude" / "references" / "my guide.md"
    reference.write_text("# Guide\n", encoding="utf-8")
    skill = profile / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8")
        + "See [guide](../../references/my%20guide.md) and [missing](missing%20guide.md).\n",
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"})

    assert not any("../../references/my guide.md" in error for error in errors)
    assert any("missing Markdown pointer 'missing guide.md'" in error for error in errors)


def test_ignores_external_markdown_uri_schemes(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    skill = profile / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "See [docs](HTTPS://example.com/guide.md) or [mail](mailto:guide.md).\n",
        encoding="utf-8",
    )

    assert lint_profile(profile, registered_tools={"patch_project"}) == []


def test_target_deprecation_rules_are_explicit_for_variant_profile(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    skill = profile / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "Run --scene-ids.\n", encoding="utf-8")

    assert not any("deprecated" in error for error in lint_profile(profile, registered_tools={"patch_project"}))
    assert any(
        "deprecated" in error
        for error in lint_profile(profile, registered_tools={"patch_project"}, enforce_target_rules=True)
    )


def test_target_deprecation_rules_flag_routing_and_spare_reverse_notes(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / ".claude" / "agents" / "router.md").write_text(
        "---\nname: router\ndescription: Routing agent\n---\n"
        "- 读取 `drafts/episode_1/step1_normalized_script.md` 作为剧本生成输入。\n"
        "- 重生成指定场景时运行 generate-storyboard --scene-ids E1S01。\n",
        encoding="utf-8",
    )
    (profile / ".claude" / "agents" / "note.md").write_text(
        "---\nname: note\ndescription: Reverse note agent\n---\n"
        "- 旧项目残留的 `step1_normalized_script.md`（结构化前自由文本稿）不算有效 step1，"
        "须重跑 normalize 产出 `.json`。\n"
        "- 旧脚本的 --scene-ids 参数已废弃，不要再传。\n",
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"}, enforce_target_rules=True)

    assert ".claude/agents/router.md: deprecated profile string 'step1_normalized_script.md'" in errors
    assert ".claude/agents/router.md: deprecated profile string '--scene-ids'" in errors
    assert not any(error.startswith(".claude/agents/note.md") for error in errors)


def test_target_deprecation_rules_flag_code_fences_and_parenthesised_paths(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / ".claude" / "agents" / "fenced.md").write_text(
        "---\nname: fenced\ndescription: Fenced command agent\n---\n"
        "```bash\n"
        "generate-storyboard --scene-ids E1S01\n"
        "cat drafts/episode_1/step1_normalized_script.md\n"
        "```\n",
        encoding="utf-8",
    )
    (profile / ".claude" / "agents" / "inline.md").write_text(
        "---\nname: inline\ndescription: Inline reference agent\n---\n"
        "- 读取剧本草稿（`step1_normalized_script.md`）后继续。\n"
        "- 请使用 generate-storyboard 重生成；--scene-ids E1S01\n",
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"}, enforce_target_rules=True)

    for source in ("fenced.md", "inline.md"):
        assert f".claude/agents/{source}: deprecated profile string 'step1_normalized_script.md'" in errors
        assert f".claude/agents/{source}: deprecated profile string '--scene-ids'" in errors


def test_target_deprecation_rules_flag_soft_wrapped_routing_clause(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / ".claude" / "agents" / "wrapped.md").write_text(
        "---\nname: wrapped\ndescription: Soft-wrapped routing agent\n---\n"
        "- 请读取\n"
        "  step1_normalized_script.md 后再继续下一步。\n",
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"}, enforce_target_rules=True)

    assert ".claude/agents/wrapped.md: deprecated profile string 'step1_normalized_script.md'" in errors


def test_target_deprecation_rules_flag_nested_fence_content(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / ".claude" / "agents" / "nested-fence.md").write_text(
        "---\nname: nested-fence\ndescription: Nested fence agent\n---\n"
        "````markdown\n"
        "```bash\n"
        "cat drafts/episode_1/step1_normalized_script.md\n"
        "```\n"
        "````\n",
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"}, enforce_target_rules=True)

    assert ".claude/agents/nested-fence.md: deprecated profile string 'step1_normalized_script.md'" in errors


def test_target_deprecation_rules_flag_routing_clause_alongside_reverse_note(tmp_path: Path) -> None:
    """反向说明子句与真实路由子句同文共存同一 needle 时，仍须按各自子句独立判定并报违规。"""
    profile = _valid_profile(tmp_path)
    (profile / ".claude" / "agents" / "mixed.md").write_text(
        "---\nname: mixed\ndescription: Mixed clause agent\n---\n"
        "- 旧项目残留的 step1_normalized_script.md 不算有效 step1。\n"
        "- 读取 step1_normalized_script.md 作为剧本生成输入。\n",
        encoding="utf-8",
    )

    errors = lint_profile(profile, registered_tools={"patch_project"}, enforce_target_rules=True)

    assert ".claude/agents/mixed.md: deprecated profile string 'step1_normalized_script.md'" in errors


def test_reports_invalid_utf8_across_profile_inputs(tmp_path: Path) -> None:
    profile = _valid_profile(tmp_path)
    (profile / "CLAUDE.narration.md").write_bytes(b"\xff")
    (profile / "evals" / "cases.json").write_bytes(b"\xff")

    errors = lint_profile(profile, registered_tools={"patch_project"}, enforce_target_rules=True)

    assert any("cannot read projected file" in error for error in errors)
    assert any("invalid eval JSON" in error for error in errors)
    assert any(error.startswith("CLAUDE.narration.md: cannot read:") for error in errors)


def test_frontmatter_accepts_utf8_bom(tmp_path: Path) -> None:
    metadata_path = tmp_path / "SKILL.md"
    metadata_path.write_bytes(b"\xef\xbb\xbf---\nname: demo\ndescription: Demo skill\n---\n")

    metadata = parse_profile_metadata(metadata_path)

    assert metadata.name == "demo"
    assert metadata.description == "Demo skill"


def test_shipped_profile_passes_current_lint() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert lint_profile(repo_root / "agent_runtime_profile") == []


def test_shipped_profile_has_no_deprecated_string_routing() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    errors = lint_profile(repo_root / "agent_runtime_profile", enforce_target_rules=True)

    assert not any("deprecated profile string" in error for error in errors)
