"""Translation checks protect literal paths and instruction metadata."""

from scripts.audit_translation import audit_markdown


def test_reports_path_that_loses_inline_code_and_becomes_html() -> None:
    source = "记忆目录 `<root>/<user_id>/memory/`。"
    target = "Memory directory <root>/<user_id>/memory/."
    assert audit_markdown(source, target) == ["missing inline code (1): '<root>/<user_id>/memory/'"]


def test_translated_prose_and_fenced_comments_preserve_literal_contract() -> None:
    source = "使用 ``project.json`` 与 [规范](rules.md)。\n```sh\n# 注释\nuv run app\n```\n"
    target = "Use ``project.json`` and [standards](rules.md).\n```sh\n# Comment\nuv run app\n```\n"
    assert audit_markdown(source, target) == []


def test_inline_fence_example_does_not_turn_neighboring_prose_into_code() -> None:
    source = "包括 `- ```sh`、`<pre>` 和 `<script>`。"
    target = "Including `- ```sh`, `<pre>` and `<script>`."
    assert audit_markdown(source, target) == []


def test_rejects_schema_key_change_while_allowing_description_translation() -> None:
    source = "---\nid: text/example\ntitle: 示例\nslots:\n  lang: 语言\nprotected: true\n---\n说明\n"
    translation = "---\nid: text/example\ntitle: Example\nslots:\n  lang: Language\nprotected: true\n---\nDescription\n"
    assert audit_markdown(source, translation) == []
    assert audit_markdown(source, translation.replace("  lang:", "  language:")) == ["functional frontmatter changed"]


def test_multiline_code_span_does_not_capture_translated_neighboring_prose() -> None:
    source = "用 `tool(\n  id=1)` 修改条目，每条一个 `update`。"
    target = "Use `tool(\n  id=1)` to modify entries, one `update` per entry."
    assert audit_markdown(source, target) == []
    assert audit_markdown(source, target.replace("  id=1", "  id=2")) == ["missing inline code (1): 'tool(   id=1)'"]


def test_reports_unquoted_yaml_colon_introduced_by_translation() -> None:
    source = "---\nid: style/example\ntitle: 示例\n---\n"
    target = "---\nid: style/example\ntitle: Animation: Kyoto\n---\n"
    findings = audit_markdown(source, target)
    assert len(findings) == 1
    assert findings[0].startswith("invalid frontmatter:")


def test_reports_extra_brace_in_previously_valid_tool_example() -> None:
    source = '```\nmcp__arcreel__patch_project({"overview": {"genre": "悬疑"}})\n```\n'
    target = '```\nmcp__arcreel__patch_project({"overview": {"genre": "悬疑"}}})\n```\n'
    assert audit_markdown(source, target) == ["new invalid JSON tool example (1): mcp__arcreel__patch_project"]
    assert audit_markdown(target, target) == []


def test_preserves_existing_english_quotation_while_translating_surrounding_prose() -> None:
    source = '文档说明："Keep everything else unchanged." 来源见 [文档](guide.md)。'
    target = "Documentation says: “Keep everything else unchanged.” See [the guide](guide.md)."
    assert audit_markdown(source, target) == []
    assert audit_markdown(source, target.replace("Keep everything else unchanged.", "Change only this part.")) == [
        "changed or dequoted English quotation (1): 'Keep everything else unchanged.'"
    ]
    assert audit_markdown(source, target.replace("“", "").replace("”", "")) == [
        "changed or dequoted English quotation (1): 'Keep everything else unchanged.'"
    ]


def test_empty_quotation_in_example_is_not_a_preservation_error() -> None:
    assert audit_markdown('空值为 ""。', 'Empty values use "".') == []


def test_adjacent_quoted_code_fields_do_not_capture_translated_prose() -> None:
    source = '读取 `"project_name"`、`"call_type"`，作为两个字段。'
    target = 'Read `"project_name"` and `"call_type"` as the two fields.'
    assert audit_markdown(source, target) == []


def test_preserves_english_quotations_in_chinese_delimiters() -> None:
    for opening, closing in [("「", "」"), ("『", "』")]:
        source = f"已有原文：{opening}scale + stack + path + HTML tooltip{closing}。"
        target = "Existing wording: “scale + stack + path + HTML tooltip”."
        assert audit_markdown(source, target) == []
        assert audit_markdown(source, target.replace("HTML tooltip", "custom tooltip")) == [
            "changed or dequoted English quotation (1): 'scale + stack + path + HTML tooltip'"
        ]
