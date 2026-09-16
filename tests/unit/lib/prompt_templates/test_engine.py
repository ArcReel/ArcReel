from pathlib import Path

import pytest
import yaml

from lib.prompt_templates import PromptTemplates, TemplateError


def write_template(directory: Path, body: str, **metadata: object) -> Path:
    path = directory / "entry.md"
    path.write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "id": "text/example",
                "category": "text",
                "title": "示例",
                "description": "测试提示词",
                "applies_to": {},
                "slots": {"name": "名称"},
                "protected": False,
                **metadata,
            },
            allow_unicode=True,
        )
        + "---\n"
        + body,
        encoding="utf-8",
    )
    return path


def test_render_list_and_read_source(tmp_path):
    write_template(tmp_path, "你好，{{ name }}。", output_schema="lib.script_models:Script")
    templates = PromptTemplates(tmp_path)
    assert templates.render("text/example", name="林清") == "你好，林清。"
    metadata = templates.list_templates()[0]
    assert metadata.id == "text/example"
    assert metadata.output_schema == "lib.script_models:Script"
    assert templates.read_source("text/example") == ("你好，{{ name }}。", {})


def test_duplicate_id_rejected_at_load(tmp_path):
    path = write_template(tmp_path, "{{ name }}")
    (tmp_path / "duplicate.md").write_text(path.read_text(), encoding="utf-8")
    with pytest.raises(TemplateError, match="重复"):
        PromptTemplates(tmp_path)


@pytest.mark.parametrize(
    ("body", "slots", "message"),
    [
        ("{{ missing }}", {}, "未声明.*missing"),
        ("固定正文", {"unused": "未使用"}, "未使用.*unused"),
    ],
)
def test_slot_declarations_match_body_at_load(tmp_path, body, slots, message):
    write_template(tmp_path, body, slots=slots)
    with pytest.raises(TemplateError, match=message):
        PromptTemplates(tmp_path)


def test_render_requires_all_slots_and_nested_keys(tmp_path):
    write_template(tmp_path, "{% if name.hook %}{{ name.hook }}{% endif %}")
    templates = PromptTemplates(tmp_path)
    with pytest.raises(TemplateError, match=r"缺少.*name"):
        templates.render("text/example")
    with pytest.raises(TemplateError, match=r"多余.*unknown"):
        templates.render("text/example", name={"hook": None}, unknown="x")
    with pytest.raises(TemplateError, match="hook"):
        templates.render("text/example", name={})
    assert templates.render("text/example", name={"hook": None}) == ""


def write_partial(directory: Path, name: str, body: str) -> None:
    path = directory / "partials" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_variant_partials_are_complete_and_readable(tmp_path):
    body = '{{ variant("text/example/intro", source_kind) }}\n{{ name }}'
    write_template(
        tmp_path,
        body,
        applies_to={"source_kind": ["novel", "screenplay"]},
        slots={"source_kind": "源文类型", "name": "名称"},
    )
    write_partial(tmp_path, "text/example/intro/novel", '{{ partial("shared/label") }}\n')
    write_partial(tmp_path, "shared/label", "小说\n")
    with pytest.raises(TemplateError, match="screenplay"):
        PromptTemplates(tmp_path)
    write_partial(tmp_path, "text/example/intro/screenplay", "")
    templates = PromptTemplates(tmp_path)
    assert templates.render("text/example", source_kind="novel", name="林清") == "小说\n林清"
    assert templates.render("text/example", source_kind="screenplay", name="林清") == "林清"
    source, partials = templates.read_source("text/example")
    assert source == body
    assert set(partials.values()) == {'{{ partial("shared/label") }}\n', "小说\n", ""}
    with pytest.raises(TemplateError, match="source_kind"):
        templates.render("text/example", source_kind="unknown", name="林清")


@pytest.mark.parametrize(
    "body",
    [
        '{% if name == "novel" %}小说{% endif %}',
        '{{ "小说" if name == "novel" else "剧本" }}',
        "{% for item in name %}{{ item }}{% endfor %}",
        '{% include "shared/label.md" %}{{ name }}',
        "{{ name | upper }}",
    ],
)
def test_unsupported_template_syntax_rejected_at_load(tmp_path, body):
    write_template(tmp_path, body)
    with pytest.raises(TemplateError):
        PromptTemplates(tmp_path)


def test_loop_is_allowed_only_in_list_partial(tmp_path):
    write_template(tmp_path, '{{ partial("shared/lists/names", items=name) }}')
    write_partial(tmp_path, "shared/lists/names", "{% for item in items %}- {{ item }}\n{% endfor %}")
    templates = PromptTemplates(tmp_path)
    assert templates.render("text/example", name=["林清", "沈茹"]) == "- 林清\n- 沈茹"
    write_partial(tmp_path, "shared/lists/names", "{% for item in [1, 2] %}{{ item }}{% endfor %}")
    with pytest.raises(TemplateError):
        PromptTemplates(tmp_path)


@pytest.mark.parametrize(
    "body",
    [
        '{{ partial("shared/" ~ name) }}',
        '{{ name.get("hook") }}',
        '{{ partial("../secret") }}{{ name }}',
        '{{ partial("shared/missing") }}{{ name }}',
    ],
)
def test_invalid_partial_references_rejected_at_load(tmp_path, body):
    write_template(tmp_path, body)
    with pytest.raises(TemplateError):
        PromptTemplates(tmp_path)


def test_nested_partial_slots_and_cycles_checked_at_load(tmp_path):
    write_template(tmp_path, '{{ partial("shared/label") }}{{ name }}')
    write_partial(tmp_path, "shared/label", "{{ undeclared }}")
    with pytest.raises(TemplateError, match=r"未声明.*undeclared"):
        PromptTemplates(tmp_path)
    write_partial(tmp_path, "shared/label", '{{ partial("shared/label") }}')
    with pytest.raises(TemplateError, match="循环引用"):
        PromptTemplates(tmp_path)


def test_sandbox_blocks_private_attributes(tmp_path):
    write_template(tmp_path, "{{ name.__class__.__mro__ }}")
    templates = PromptTemplates(tmp_path)
    with pytest.raises(TemplateError, match="unsafe"):
        templates.render("text/example", name="x")


def test_partial_in_body_is_not_injected_twice(tmp_path):
    write_template(tmp_path, '{{ partial("shared/style") }}\n\n{{ name }}')
    write_partial(tmp_path, "shared/style", "Style: 电影感\n")
    templates = PromptTemplates(tmp_path)
    once = templates.render("text/example", name="人物走进房间。")
    assert once == "Style: 电影感\n\n人物走进房间。"
    assert templates.render("text/example", name=once) == once


def test_nested_partial_sees_caller_context(tmp_path):
    write_template(tmp_path, '{{ partial("shared/outer", label=name) }}')
    write_partial(tmp_path, "shared/outer", '{{ partial("shared/inner") }}：{{ label }}')
    write_partial(tmp_path, "shared/inner", "电影感\n")
    templates = PromptTemplates(tmp_path)
    assert templates.render("text/example", name="林清") == "电影感：林清"


def test_inline_partial_is_injected_even_when_body_repeats_it(tmp_path):
    """行内片段常是短措辞，正文里偶然同形的字串不得把它吞掉。"""
    write_template(tmp_path, '排除：{{ partial("shared/people") }}水印\n\n{{ name }}')
    write_partial(tmp_path, "shared/people", "出镜人物、")
    templates = PromptTemplates(tmp_path)
    rendered = templates.render("text/example", name="昏暗古朴，无出镜人物、无声响。")
    assert rendered.startswith("排除：出镜人物、水印")


def test_block_partial_is_skipped_only_on_a_full_line_match(tmp_path):
    write_template(tmp_path, '{{ partial("shared/avoid") }}\n\n{{ name }}')
    write_partial(tmp_path, "shared/avoid", "Avoid: 水印")
    templates = PromptTemplates(tmp_path)
    assert templates.render("text/example", name="Avoid: 水印") == "Avoid: 水印"
    assert templates.render("text/example", name="含 Avoid: 水印 的一句话").startswith("Avoid: 水印\n\n含")


def test_empty_block_partial_leaves_no_blank_line(tmp_path):
    write_template(tmp_path, '{{ partial("shared/head") }}\n\n{{ name }}\n\n{{ partial("shared/tail") }}')
    write_partial(tmp_path, "shared/head", "")
    write_partial(tmp_path, "shared/tail", "")
    templates = PromptTemplates(tmp_path)
    assert templates.render("text/example", name="正文") == "正文"


def test_filters_apply_to_rendered_partial_text(tmp_path):
    write_template(tmp_path, '{{ partial("shared/lines") | indent(2, true) }}{{ name }}')
    write_partial(tmp_path, "shared/lines", "甲\n乙\n")
    assert PromptTemplates(tmp_path).render("text/example", name="。") == "  甲\n  乙。"


def test_trailing_partial_does_not_accumulate_blank_lines(tmp_path):
    write_template(tmp_path, '{{ name }}\n\n{{ partial("shared/avoid") }}')
    write_partial(tmp_path, "shared/avoid", "Avoid: 水印")
    templates = PromptTemplates(tmp_path)
    once = templates.render("text/example", name="主体")
    assert once == "主体\n\nAvoid: 水印"
    assert templates.render("text/example", name=once) == once
