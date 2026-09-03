import unicodedata

import pytest

from lib.asset_types import DERIVATIVES_FIELD
from lib.reference_video.text_parser import (
    extract_mentions,
    leading_mention_before_colon,
    line_speech_marks,
    mention_names,
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
    """顺序即执行期参考图编号：重复提及去重，保留首次出现顺序。"""
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
    """解析层不认识资产表：未登记的名字照常派生，登记判定归 ``resolve_references``。"""
    assert extract_mentions("@张三 看向 @未登记的东西") == ["张三", "未登记的东西"]


def test_extract_mentions_rejects_non_ascii_legacy_letters():
    assert extract_mentions("@éclair @한글 @张三 @abc_123") == ["张三", "abc_123"]


def test_extract_mentions_rejects_curly_wrapped_form():
    assert extract_mentions("@[角色甲（成年）] 与 @{道具甲}") == ["角色甲（成年）"]


def test_speaker_only_before_braces_is_excluded_from_references():
    """只在花括号前出现的角色只绑声音；同一行记号之外的引用照常进参考图。"""
    assert extract_mentions("@[酒馆] 内景。@[张三]{我来了}") == ["酒馆"]
    assert extract_mentions("@[张三] 推门。@[张三]{我来了}") == ["张三"]
    assert extract_mentions("@[张三]{我来了}\n@[李四]{你也来了}") == []


# ── mention 前缀边界 ────────────────────────────────────────


def test_mention_ignores_email_like_prefix():
    """email 左侧是 \\w，不应被当成 mention。"""
    assert extract_mentions("contact a@张三 for help") == []
    assert extract_mentions("email: test@domain.com") == []
    assert extract_mentions("alice@example.com 和 bob@foo.io") == []
    assert extract_mentions("room9@张三") == []
    assert extract_mentions("user123@李四") == []


def test_mention_accepts_chinese_prefix():
    """中文左侧字符（\\u4e00-\\u9fff）不是 \\w，合法 mention 用法。"""
    assert extract_mentions("你好@张三") == ["张三"]
    assert extract_mentions("（对面）@李四 抬眼") == ["李四"]


def test_mention_accepts_whitespace_and_line_start():
    """空白字符 / 行首 / 标点前缀都应识别。"""
    assert extract_mentions("@张三") == ["张三"]
    assert extract_mentions("之后 @张三 回头") == ["张三"]
    assert extract_mentions("内景。\n@张三 开门") == ["张三"]
    assert extract_mentions("台词：@张三 起身") == ["张三"]


def test_mention_underscore_prefix_is_rejected():
    """underscore 属 \\w，`foo_@张三` 类打字错误不应触发 mention。"""
    assert extract_mentions("prefix_@张三") == []


# ── BOM / 编码归一 ────────────────────────────────────────


def test_bom_prefixed_dialogue_line_is_normative():
    """BOM 开头的台词记号两侧同判：说话人不进参考图。

    JS 的 ``\\s`` 认 U+FEFF、Python 的 ``str.strip()`` 不认；不归一时前端判台词、
    后端判描述，说话人是否落进 references 取决于哪侧先跑。
    """
    assert _marks("﻿@[张三]：{我来了}") == [("张三", "我来了")]
    assert line_speech_marks("﻿@[张三]：{我来了}")[0].raw == "@[张三]：{我来了}"
    assert extract_mentions("﻿@[张三]：{我来了}") == []


def test_bom_on_a_later_line_is_normalized_too():
    """BOM 不止出现在文档开头——粘贴拼接会把它带到任意行首，而分叉是按行发生的。"""
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
    assert "@未知" in rendered  # 未注册保留


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


#: 带组合附加符的资产名（越南语），两种编码屏幕显示相同、字节不同——资产名比对的坐标系用例。
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


@pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
@pytest.mark.parametrize("written", [_NAME_NFC, _NAME_NFD], ids=["出场NFC", "出场NFD"])
def test_resolve_references_matches_across_encoding_forms(registered: str, written: str):
    """组合字符资产名的四种 NFC/NFD 配对都判为已登记，且派生名一律是归一形式。

    ``ReferenceResource.name`` 要被下游拿去回查资产表与在正文里替换成主体记号，产出两种
    形式会让「这里判已登记、下游查不到」。
    """
    refs, missing = resolve_references([written], _proj(characters={registered: {}}))
    assert [(r.type, r.name) for r in refs] == [("character", _NAME_NFC)]
    assert missing == []


@pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
@pytest.mark.parametrize("written", [_NAME_NFC, _NAME_NFD], ids=["出场NFC", "出场NFD"])
def test_render_mentions_as_subjects_matches_across_encoding_forms(registered: str, written: str):
    """两侧编码形式不同也要替换成主体记号：漏替换时 ``@[名称]`` 会原样进供应商请求。"""
    assert render_mentions_as_subjects(f"@[{written}] 推门而入", [registered]) == f"<{_NAME_NFC}> 推门而入"


# ── 行内发声记号切分 ────────────────────────────────────────


def test_inline_dialogue_after_description_is_a_speech_mark():
    """台词跟在同一行的画面描述之后照常识别——记号不要求独立成行。"""
    assert _marks("@[张三] 推开门。@[张三]{我来了}") == [("张三", "我来了")]


def test_mention_and_brace_separator_forms_are_equivalent():
    """mention 与 `{` 之间的空白 / 冒号可选，三种写法产出同一条台词。"""
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
    """中间隔着描述文字的 mention 不是说话人——不做「行内最近 mention 猜 speaker」。"""
    assert _marks("@[张三] 推开门，屋里传出声音 {谁啊？}") == [("", "谁啊？")]
    assert extract_mentions("@[张三] 推开门，屋里传出声音 {谁啊？}") == ["张三"]


def test_empty_speech_text_is_not_a_mark():
    assert _marks("@[张三]：{}") == []
    assert _marks("{   }") == []


def test_blank_speaker_slot_is_not_a_mark():
    """``@[ ]{台词}`` 说话人位为空：dialogue 要求非空 speaker，不降级成画外音。"""
    assert _marks("@[ ]：{我来了}") == []


def test_malformed_speaker_slot_does_not_fall_back_to_voiceover():
    """``@[]：{台词}`` 作者写的是「某人说」，静默改判画外音比不识别更难发现。"""
    assert _marks("@[]：{我来了}") == []


def test_repeated_separator_colon_does_not_fall_back_to_voiceover():
    """``@[张三]：：{台词}`` 只吞一个分隔冒号，剩下的冒号说明这不是台词形态。"""
    assert _marks("@[张三]：：{我来了}") == []
    assert strip_speech_marks("@[张三]：：{我来了}") == "@[张三]：：{我来了}"
    assert _marks("门开了。@[张三]:: {我来了}") == []


def test_single_separator_colon_still_binds_the_speaker():
    assert _marks("@[张三]：{我来了}") == [("张三", "我来了")]
    assert _marks("@[张三] : {我来了}") == [("张三", "我来了")]


def test_unit_separator_counts_as_inline_whitespace():
    """U+001F 是 Python 的空白但不是 JS 的 ``\\s``——两侧空白集合须逐字符相同。"""
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
    """NFD 落盘的说话人与 NFC 登记的资产名须判等，台词文本同样归一。"""
    line = unicodedata.normalize("NFD", "@[Nguyễn]{Chào}")
    assert _marks(line) == [("Nguyễn", "Chào")]


def test_legacy_bare_mention_can_be_a_speaker():
    assert _marks("@张三{我来了}") == [("张三", "我来了")]


def test_email_like_prefix_is_not_a_speaker():
    """左侧是 ASCII 词字符时按邮箱 / id 片段跳过，与 mention 扫描同口径。"""
    assert _marks("a@b{我来了}") == [("", "我来了")]


# ── @[角色/衍生]（docs/adr/0072）────────────────────────────


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
        """同一镜头里本体与衍生同现时各注入一张图，故它们是两条不同的引用。"""
        refs, missing = resolve_references(["张三", "张三/劲装"], _proj_with_derivatives(张三=["劲装"]))

        assert [ref.name for ref in refs] == ["张三", "张三/劲装"]
        assert missing == []

    def test_derivative_mention_in_the_body_is_extracted(self):
        assert extract_mentions("@[张三/劲装] 推开门") == ["张三/劲装"]


class TestDerivativeSpeakerSlot:
    def test_speaker_binds_the_base_while_the_written_form_keeps_the_derivative(self):
        """声音属于身份：衍生说话仍绑本体的参考音频，写下的那套外观另行保留。"""
        marks = line_speech_marks("@[张三/劲装]{我来了}")

        assert [(mark.speaker, mark.derivative, mark.speaker_reference) for mark in marks] == [
            ("张三", "劲装", "张三/劲装")
        ]

    def test_bare_mention_keeps_an_empty_derivative(self):
        marks = line_speech_marks("@[张三]{我来了}")

        assert [(mark.speaker, mark.derivative, mark.speaker_reference) for mark in marks] == [("张三", "", "张三")]

    def test_speaker_slot_without_a_base_name_is_not_a_mark(self):
        """``@[/劲装]`` 没有可绑声音的身份，不静默降级成画外音。"""
        assert _marks("@[/劲装]{我来了}") == []

    def test_speaker_slot_is_still_excluded_from_reference_images(self):
        assert extract_mentions("@[张三/劲装]{我来了}") == []

    def test_speech_speaker_references_report_the_written_forms_in_order(self):
        text = "@[张三/劲装]{我来了}\n@[李四]{你好}\n{旁白}\n@[张三/劲装]{再来}"

        assert speech_speaker_references(text) == ["张三/劲装", "李四"]


class TestMentionNames:
    def test_mention_names_include_the_speaker_slot(self):
        """级联改名与「被引用」判定问的是「写下了哪些名字」，说话人位同样算。"""
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
