import unicodedata

import pytest

from lib.project.asset_types import DERIVATIVES_FIELD
from lib.script.reference_video.text_parser import (
    extract_mentions,
    leading_mention_before_colon,
    line_speech_marks,
    mention_names,
    render_mentions,
    render_mentions_as_subjects,
    resolve_references,
    rewrite_mentions,
    speech_speaker_references,
    split_speech_line,
    strip_speech_marks,
)


def _marks(line: str) -> list[tuple[str, str]]:
    return [(mark.speaker, mark.text) for mark in line_speech_marks(line)]


# ── extract_mentions ────────────────────────────────────────


def test_extract_mentions_ordered_unique():
    """Order determines runtime reference-image numbering: deduplicate repeated mentions while preserving first occurrence."""
    text = "@张三 看向 @酒馆\n@张三 拔剑 @长剑"
    assert extract_mentions(text) == ["张三", "酒馆", "长剑"]


def test_extract_mentions_covers_multiple_characters_across_lines():
    text = "@[张三] 与 @[李四] 对峙。\n@[王五] 从门外进来。"
    assert extract_mentions(text) == ["张三", "李四", "王五"]


def test_extract_mentions_strips_wrapped_names_before_deduplication():
    assert extract_mentions("@[ Hero ] 走向 @[Hero]，随后 @Hero 转身") == ["Hero"]


def test_extract_mentions_supports_wrapped_names():
    text = "@[角色甲（成年）] 引导@[角色乙]靠近@[载具甲]区域，使用@[道具甲]完成动作"
    assert extract_mentions(text) == ["角色甲（成年）", "角色乙", "载具甲", "道具甲"]


def test_extract_mentions_supports_punctuation_in_wrapped_scene_name():
    assert extract_mentions("@[载具甲]移动到@[地点甲·版本A]") == ["载具甲", "地点甲·版本A"]


def test_extract_mentions_without_any_mention():
    assert extract_mentions("没有任何提及") == []
    assert extract_mentions("") == []


def test_extract_mentions_keeps_unregistered_names():
    """Parsing does not know asset tables; derive even unregistered names, leaving registration to ``resolve_references``."""
    assert extract_mentions("@张三 看向 @未登记的东西") == ["张三", "未登记的东西"]


def test_extract_mentions_rejects_non_ascii_legacy_letters():
    assert extract_mentions("@éclair @한글 @张三 @abc_123") == ["张三", "abc_123"]


def test_extract_mentions_rejects_curly_wrapped_form():
    assert extract_mentions("@[角色甲（成年）] 与 @{道具甲}") == ["角色甲（成年）"]


def test_speaker_only_before_braces_is_excluded_from_references():
    """Characters only preceding braces bind voices only; same-line references outside markers enter images normally."""
    assert extract_mentions("@[酒馆] 内景。@[张三]{我来了}") == ["酒馆"]
    assert extract_mentions("@[张三] 推门。@[张三]{我来了}") == ["张三"]
    assert extract_mentions("@[张三]{我来了}\n@[李四]{你也来了}") == []


# Mention prefix boundaries.


def test_mention_ignores_email_like_prefix():
    """An email has left-side \\w and must not count as a mention."""
    assert extract_mentions("contact a@张三 for help") == []
    assert extract_mentions("email: test@domain.com") == []
    assert extract_mentions("alice@example.com 和 bob@foo.io") == []
    assert extract_mentions("room9@张三") == []
    assert extract_mentions("user123@李四") == []


def test_mention_accepts_chinese_prefix():
    """Chinese left-side characters (\\u4e00-\\u9fff) are not \\w; this is valid mention usage."""
    assert extract_mentions("你好@张三") == ["张三"]
    assert extract_mentions("（对面）@李四 抬眼") == ["李四"]


def test_mention_accepts_whitespace_and_line_start():
    """Recognize whitespace / line-start / punctuation prefixes."""
    assert extract_mentions("@张三") == ["张三"]
    assert extract_mentions("之后 @张三 回头") == ["张三"]
    assert extract_mentions("内景。\n@张三 开门") == ["张三"]
    assert extract_mentions("台词：@张三 起身") == ["张三"]


def test_mention_underscore_prefix_is_rejected():
    """Underscores belong to \\w; typos such as `foo_@张三` must not trigger mentions."""
    assert extract_mentions("prefix_@张三") == []


# BOM / encoding normalization.


def test_bom_prefixed_dialogue_line_is_normative():
    """Both sides classify BOM-prefixed dialogue identically: speakers do not enter reference images.

    JS ``\\s`` recognizes U+FEFF, but Python ``str.strip()`` does not. Without normalization, frontend sees dialogue
    while backend sees description; speaker inclusion in references depends on which side runs first.
    """
    assert _marks("﻿@[张三]：{我来了}") == [("张三", "我来了")]
    assert line_speech_marks("﻿@[张三]：{我来了}")[0].raw == "@[张三]：{我来了}"
    assert extract_mentions("﻿@[张三]：{我来了}") == []


def test_bom_on_a_later_line_is_normalized_too():
    """BOMs can occur beyond document beginnings; pasted concatenations introduce them at any line start, where classification diverges per line."""
    assert extract_mentions("@酒馆 内景。\n﻿@[张三]：{我来了}") == ["酒馆"]


def test_bom_stripped_from_derived_mention_names():
    assert extract_mentions("﻿@[﻿张三] 站着。") == ["张三"]


def test_dialogue_speaker_is_stripped_to_asset_comparison_key():
    assert _marks("@[ 张三 ]：{我来了}") == [("张三", "我来了")]
    assert leading_mention_before_colon("@[ 张三 ]：我来了") == "张三"


# ── render_mentions_as_subjects ────────────────────────────


def test_render_mentions_replaces_mentions():
    text = "中景，@张三 走进 @酒馆 找 @长剑。"
    rendered = render_mentions_as_subjects(text, {"张三", "酒馆", "长剑"})
    assert rendered == "中景，<张三> 走进 <酒馆> 找 <长剑>。"


def test_render_mentions_replaces_wrapped_mentions_without_spacing():
    text = "@[角色甲（成年）]引导@[角色乙]靠近@[载具甲]区域，使用@[道具甲]完成动作。"
    rendered = render_mentions_as_subjects(text, {"角色甲（成年）", "角色乙", "载具甲", "道具甲"})
    assert rendered == "<角色甲（成年）>引导<角色乙>靠近<载具甲>区域，使用<道具甲>完成动作。"


def test_render_mentions_unknown_mention_kept():
    text = "@张三 和 @未知 对话"
    rendered = render_mentions_as_subjects(text, {"张三"})
    assert "<张三>" in rendered
    assert "@未知" in rendered  # Preserve unregistered names.


def test_render_mentions_across_multiple_lines():
    text = "@张三 推门\n@张三 坐下"
    rendered = render_mentions_as_subjects(text, {"张三"})
    assert rendered == "<张三> 推门\n<张三> 坐下"


def test_render_mentions_as_subjects_strips_comparison_whitespace():
    assert render_mentions_as_subjects("@[ Hero ] 推门而入", ["Hero"]) == "<Hero> 推门而入"


# ── resolve_references ────────────────────────────────────


def _proj(characters=None, scenes=None, props=None):
    return {
        "characters": characters or {},
        "scenes": scenes or {},
        "props": props or {},
    }


def test_resolve_references_character():
    proj = _proj(characters={"张三": {}})
    refs, missing = resolve_references(["张三"], proj)
    assert len(refs) == 1
    assert refs[0].type == "character"
    assert refs[0].name == "张三"
    assert missing == []


def test_resolve_references_scene_and_prop():
    proj = _proj(scenes={"酒馆": {}}, props={"长剑": {}})
    refs, missing = resolve_references(["酒馆", "长剑"], proj)
    types = {r.name: r.type for r in refs}
    assert types == {"酒馆": "scene", "长剑": "prop"}
    assert missing == []


def test_resolve_references_missing_reports_name():
    refs, missing = resolve_references(["张三", "未知"], _proj(characters={"张三": {}}))
    assert len(refs) == 1
    assert missing == ["未知"]


def test_resolve_references_preserves_order():
    proj = _proj(characters={"B": {}}, scenes={"A": {}}, props={"C": {}})
    refs, _ = resolve_references(["A", "B", "C"], proj)
    assert [r.name for r in refs] == ["A", "B", "C"]


def test_resolve_references_deduplicates_shared_comparison_key():
    refs, missing = resolve_references(["Hero", " Hero "], _proj(characters={"Hero": {}}))

    assert [(ref.type, ref.name) for ref in refs] == [("character", "Hero")]
    assert missing == []


def test_resolve_references_empty_input():
    refs, missing = resolve_references([], _proj())
    assert refs == []
    assert missing == []


def test_resolve_references_uses_priority_for_corrupt_shared_namespace():
    project = _proj(characters={"Shared": {}}, scenes={"Shared": {}})

    refs, missing = resolve_references(["Shared"], project)

    assert [(ref.type, ref.name) for ref in refs] == [("character", "Shared")]
    assert missing == []


#: Vietnamese combining-character asset names: visually identical, byte-distinct encodings test comparison coordinates.
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


@pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
@pytest.mark.parametrize("written", [_NAME_NFC, _NAME_NFD], ids=["出场NFC", "出场NFD"])
def test_resolve_references_matches_across_encoding_forms(registered: str, written: str):
    """All four NFC/NFD combining-name pairings count as registered, with all derived names normalized.

    Downstream uses ``ReferenceResource.name`` for asset lookup and body subject-marker replacement; producing two
    forms would make registration succeed here but lookup fail later.
    """
    refs, missing = resolve_references([written], _proj(characters={registered: {}}))
    assert [(r.type, r.name) for r in refs] == [("character", _NAME_NFC)]
    assert missing == []


@pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
@pytest.mark.parametrize("written", [_NAME_NFC, _NAME_NFD], ids=["出场NFC", "出场NFD"])
def test_render_mentions_as_subjects_matches_across_encoding_forms(registered: str, written: str):
    """Replace with subject markers despite different encodings; missing replacements leak ``@[名称]`` unchanged into provider requests."""
    assert render_mentions_as_subjects(f"@[{written}] 推门而入", [registered]) == f"<{_NAME_NFC}> 推门而入"


# Inline speech-marker splitting.


def test_inline_dialogue_after_description_is_a_speech_mark():
    """Recognize dialogue following same-line visual description; markers need not occupy whole lines."""
    assert _marks("@[张三] 推开门。@[张三]{我来了}") == [("张三", "我来了")]


def test_mention_and_brace_separator_forms_are_equivalent():
    """Whitespace / colon between mention and `{` is optional; all three forms yield identical dialogue."""
    assert _marks("@[张三]{我来了}") == _marks("@[张三] {我来了}") == _marks("@[张三]：{我来了}")
    assert _marks("@[张三]:{我来了}") == [("张三", "我来了")]


def test_bare_braces_are_voiceover_anywhere_in_the_line():
    assert _marks("镜头切到窗外。{他知道，今晚不会太平。}") == [("", "他知道，今晚不会太平。")]


def test_multiple_marks_on_one_line_keep_source_order():
    assert _marks("@[张三]{你来了}@[李四]{我来了}{夜色渐深}") == [
        ("张三", "你来了"),
        ("李四", "我来了"),
        ("", "夜色渐深"),
    ]


def test_mention_not_adjacent_to_braces_stays_description():
    """A mention separated by description is not the speaker; never guess from the nearest inline mention."""
    assert _marks("@[张三] 推开门，屋里传出声音 {谁啊？}") == [("", "谁啊？")]
    assert extract_mentions("@[张三] 推开门，屋里传出声音 {谁啊？}") == ["张三"]


def test_empty_speech_text_is_not_a_mark():
    assert _marks("@[张三]：{}") == []
    assert _marks("{   }") == []


def test_blank_speaker_slot_is_not_a_mark():
    """``@[ ]{台词}`` has an empty speaker; dialogue requires a nonempty speaker and does not degrade to voiceover."""
    assert _marks("@[ ]：{我来了}") == []


def test_malformed_speaker_slot_does_not_fall_back_to_voiceover():
    """``@[]：{台词}`` means someone speaks; silent conversion to voiceover is harder to detect than nonrecognition."""
    assert _marks("@[]：{我来了}") == []


def test_repeated_separator_colon_does_not_fall_back_to_voiceover():
    """``@[张三]：：{台词}`` consumes only one separator colon; the remaining colon makes it nondialogue syntax."""
    assert _marks("@[张三]：：{我来了}") == []
    assert strip_speech_marks("@[张三]：：{我来了}") == "@[张三]：：{我来了}"
    assert _marks("门开了。@[张三]:: {我来了}") == []


def test_single_separator_colon_still_binds_the_speaker():
    assert _marks("@[张三]：{我来了}") == [("张三", "我来了")]
    assert _marks("@[张三] : {我来了}") == [("张三", "我来了")]


def test_unit_separator_counts_as_inline_whitespace():
    """U+001F is Python whitespace but not JS ``\\s``; both whitespace sets must match character by character."""
    assert _marks("@[张三]\x1f{我来了}") == [("张三", "我来了")]


def test_nested_braces_are_not_marks():
    assert _marks("{外层 {内层}}") == [("", "内层")]


def test_unclosed_brace_leaves_residue_in_description():
    assert _marks("@[张三]{我来了") == []
    assert strip_speech_marks("@[张三]{我来了") == "@[张三]{我来了"


def test_strip_speech_marks_is_the_other_half_of_a_lossless_split():
    line = "@[张三] 推门。@[张三]{我来了}屋里安静。"
    parts = split_speech_line(line)
    joined = "".join(part if isinstance(part, str) else part.raw for part in parts)
    assert joined == line


def test_speech_marks_normalize_to_nfc():
    """NFD persisted speakers equal NFC registered asset names; normalize dialogue text too."""
    line = unicodedata.normalize("NFD", "@[Nguyễn]{Chào}")
    assert _marks(line) == [("Nguyễn", "Chào")]


def test_legacy_bare_mention_can_be_a_speaker():
    assert _marks("@张三{我来了}") == [("张三", "我来了")]


def test_email_like_prefix_is_not_a_speaker():
    """ASCII word characters on the left mean an email / ID fragment to skip, matching mention scanning."""
    assert _marks("a@b{我来了}") == [("", "我来了")]


# @[角色/衍生] (docs/adr/0072).


def _proj_with_derivatives(**by_character: list[str]) -> dict:
    return _proj(
        characters={
            name: {DERIVATIVES_FIELD: {derivative: {} for derivative in derivatives}}
            for name, derivatives in by_character.items()
        }
    )


class TestDerivativeReferenceParsing:
    def test_registered_derivative_resolves_to_a_character_reference(self):
        refs, missing = resolve_references(["张三/劲装"], _proj_with_derivatives(张三=["劲装"]))

        assert [(ref.type, ref.name) for ref in refs] == [("character", "张三/劲装")]
        assert missing == []

    def test_unregistered_derivative_is_reported_missing(self):
        refs, missing = resolve_references(["张三/夜行衣"], _proj_with_derivatives(张三=["劲装"]))

        assert refs == []
        assert missing == ["张三/夜行衣"]

    def test_base_and_derivative_are_two_references(self):
        """Base and derivative appearing in one shot each inject an image, so they are distinct references."""
        refs, missing = resolve_references(["张三", "张三/劲装"], _proj_with_derivatives(张三=["劲装"]))

        assert [ref.name for ref in refs] == ["张三", "张三/劲装"]
        assert missing == []

    def test_derivative_mention_in_the_body_is_extracted(self):
        assert extract_mentions("@[张三/劲装] 推开门") == ["张三/劲装"]


class TestDerivativeSpeakerSlot:
    def test_speaker_binds_the_base_while_the_written_form_keeps_the_derivative(self):
        """Voices belong to identity: derivatives bind base reference audio while preserving their specified appearance separately."""
        marks = line_speech_marks("@[张三/劲装]{我来了}")

        assert [(mark.speaker, mark.derivative, mark.speaker_reference) for mark in marks] == [
            ("张三", "劲装", "张三/劲装")
        ]

    def test_bare_mention_keeps_an_empty_derivative(self):
        marks = line_speech_marks("@[张三]{我来了}")

        assert [(mark.speaker, mark.derivative, mark.speaker_reference) for mark in marks] == [("张三", "", "张三")]

    def test_speaker_slot_without_a_base_name_is_not_a_mark(self):
        """``@[/劲装]`` has no voice-bindable identity; do not silently degrade to voiceover."""
        assert _marks("@[/劲装]{我来了}") == []

    def test_speaker_slot_is_still_excluded_from_reference_images(self):
        assert extract_mentions("@[张三/劲装]{我来了}") == []

    def test_speech_speaker_references_report_the_written_forms_in_order(self):
        text = "@[张三/劲装]{我来了}\n@[李四]{你好}\n{旁白}\n@[张三/劲装]{再来}"

        assert speech_speaker_references(text) == ["张三/劲装", "李四"]


class TestMentionNames:
    def test_mention_names_include_the_speaker_slot(self):
        """Cascading rename and referenced-status checks ask which names were written; speaker positions count too."""
        assert mention_names("@[张三/劲装]{我来了}@[酒馆]") == ["张三/劲装", "酒馆"]

    def test_mention_names_deduplicate_in_first_appearance_order(self):
        assert mention_names("@[乙] @[甲]\n@[甲]") == ["乙", "甲"]


class TestRewriteDerivativeMentions:
    def test_character_rename_rewrites_the_derivative_form(self):
        assert rewrite_mentions("@[张三] 与 @[张三/劲装]", "张三", "李四") == ("@[李四] 与 @[李四/劲装]", 2)

    def test_derivative_rename_only_touches_that_form(self):
        text = "@[张三] @[张三/劲装] @[张三/兽化]"

        assert rewrite_mentions(text, "张三/劲装", "张三/夜行衣") == ("@[张三] @[张三/夜行衣] @[张三/兽化]", 1)

    def test_speaker_slot_mention_is_rewritten_too(self):
        assert rewrite_mentions("@[张三/劲装]{我来了}", "张三", "李四") == ("@[李四/劲装]{我来了}", 1)

    def test_other_characters_derivatives_are_untouched(self):
        assert rewrite_mentions("@[王五/劲装]", "张三", "李四") == ("@[王五/劲装]", 0)


# ── render_mentions ─────────────────────────────────────────


def test_render_mentions_hands_each_name_to_the_renderer_in_comparison_form():
    """Pass every mention name, including derivatives, to rendering in comparison coordinates; replacement text returns to its original position."""
    mapping = {"张三": "图1", "张三/黑化": "图2"}
    text = "@[张三]坐在窗边，@[怀表]与 @[李四] 同框；@[张三/黑化]立在门口"
    assert render_mentions(text, lambda name: mapping.get(name, name)) == "图1坐在窗边，怀表与 李四 同框；图2立在门口"


def test_render_mentions_matches_across_unicode_forms():
    nfc = unicodedata.normalize("NFC", "Hiếu")
    nfd = unicodedata.normalize("NFD", "Hiếu")
    assert render_mentions(f"@[{nfd}] 抬头", lambda name: "图1" if name == nfc else name) == "图1 抬头"


def test_render_mentions_without_mentions_returns_the_exact_text():
    nfd_text = unicodedata.normalize("NFD", "Hiếu 抬头看向窗外")
    assert render_mentions(nfd_text, lambda name: "图1") is nfd_text
