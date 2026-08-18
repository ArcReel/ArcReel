import unicodedata

import pytest

from lib.reference_video.shot_parser import (
    missing_registered_references,
    parse_prompt,
    render_mentions_as_subjects,
    render_shots_text,
    resolve_references,
)

pytestmark = pytest.mark.unit


def test_parse_single_shot_no_header():
    shots, refs = parse_prompt("中景，主角走进房间。")
    assert len(shots) == 1
    assert shots[0].text == "中景，主角走进房间。"
    assert refs == []


def test_parse_multi_shot():
    text = "镜头1：中远景，主角推门进酒馆。\n镜头2：近景，对面的张三抬眼。\n"
    shots, _refs = parse_prompt(text)
    assert [s.text for s in shots] == ["中远景，主角推门进酒馆。", "近景，对面的张三抬眼。"]


def test_parse_accepts_ascii_colon_and_spacing():
    text = "镜头 1: 开场\n镜头2 ：中段\n镜头3：收尾"
    shots, _refs = parse_prompt(text)
    assert [s.text for s in shots] == ["开场", "中段", "收尾"]


def test_parse_legacy_header_is_not_recognized():
    """`Shot N (Xs):` 不是受支持的 header：整段按无 header 的单镜头处理、文本逐字保留。"""
    text = "Shot 1 (3s): 开场\nShot 2 (5s): 收尾"
    shots, _refs = parse_prompt(text)
    assert len(shots) == 1
    assert shots[0].text == text


def test_parse_legacy_chinese_header_with_duration_is_not_recognized():
    text = "镜头1 (3s)：开场"
    shots, _refs = parse_prompt(text)
    assert len(shots) == 1
    assert shots[0].text == text


def test_parse_empty_returns_empty_text_as_single_shot():
    shots, _refs = parse_prompt("")
    assert len(shots) == 1
    assert shots[0].text == ""


def test_extract_mentions_ordered_unique():
    text = "镜头1：@张三 看向 @酒馆\n镜头2：@张三 拔剑 @长剑"
    _shots, refs = parse_prompt(text)
    assert refs == ["张三", "酒馆", "长剑"]


def test_extract_mentions_strips_wrapped_names_before_deduplication():
    _shots, refs = parse_prompt("镜头1：@[ Hero ] 走向 @[Hero]，随后 @Hero 转身")
    assert refs == ["Hero"]


def test_extract_mentions_supports_wrapped_names():
    text = "镜头1：@[角色甲（成年）] 引导@[角色乙]靠近@[载具甲]区域，使用@[道具甲]完成动作"
    _shots, refs = parse_prompt(text)
    assert refs == ["角色甲（成年）", "角色乙", "载具甲", "道具甲"]


def test_extract_mentions_supports_punctuation_in_wrapped_scene_name():
    text = "镜头1：@[载具甲]移动到@[地点甲·版本A]"
    _shots, refs = parse_prompt(text)
    assert refs == ["载具甲", "地点甲·版本A"]


def test_extract_mentions_empty_prompt():
    _shots, refs = parse_prompt("没有任何提及")
    assert refs == []


def test_render_mentions_replaces_mentions():
    text = "中景，@张三 走进 @酒馆 找 @长剑。"
    rendered = render_mentions_as_subjects(text, {"张三", "酒馆", "长剑"})
    assert rendered == "中景，<张三> 走进 <酒馆> 找 <长剑>。"


def test_render_mentions_replaces_wrapped_mentions_without_spacing():
    text = "@[角色甲（成年）]引导@[角色乙]靠近@[载具甲]区域，使用@[道具甲]完成动作。"
    rendered = render_mentions_as_subjects(text, {"角色甲（成年）", "角色乙", "载具甲", "道具甲"})
    assert rendered == "<角色甲（成年）>引导<角色乙>靠近<载具甲>区域，使用<道具甲>完成动作。"


def test_extract_mentions_rejects_non_ascii_legacy_letters():
    from lib.reference_video.shot_parser import extract_mentions

    assert extract_mentions("@éclair @한글 @张三 @abc_123") == ["张三", "abc_123"]


def test_extract_mentions_rejects_curly_wrapped_form():
    from lib.reference_video.shot_parser import extract_mentions

    assert extract_mentions("@[角色甲（成年）] 与 @{道具甲}") == ["角色甲（成年）"]


def test_bom_prefixed_dialogue_line_is_normative():
    """BOM 开头的台词记号两侧同判：说话人不进参考图。

    JS 的 ``\\s`` 认 U+FEFF、Python 的 ``str.strip()`` 不认；不归一时前端判台词、
    后端判描述，说话人是否落进 references 取决于哪侧先跑。
    """
    from lib.reference_video.shot_parser import extract_mentions, line_speech_marks

    assert _marks("﻿@[张三]：{我来了}") == [("张三", "我来了")]
    assert line_speech_marks("﻿@[张三]：{我来了}")[0].raw == "@[张三]：{我来了}"
    assert extract_mentions("﻿@[张三]：{我来了}") == []


def test_dialogue_speaker_is_stripped_to_asset_comparison_key():
    from lib.reference_video.shot_parser import leading_mention_before_colon

    assert _marks("@[ 张三 ]：{我来了}") == [("张三", "我来了")]
    assert leading_mention_before_colon("@[ 张三 ]：我来了") == "张三"


def test_bom_on_a_later_line_is_normalized_too():
    """BOM 不止出现在文档开头——粘贴拼接会把它带到任意行首，而分叉是按行发生的。"""
    from lib.reference_video.shot_parser import extract_mentions

    assert extract_mentions("镜头1：@酒馆 内景。\n﻿@[张三]：{我来了}") == ["酒馆"]


def test_bom_prefixed_shot_header_still_splits():
    from lib.reference_video.shot_parser import parse_prompt

    shots, _ = parse_prompt("﻿镜头1：内景。\n镜头2：外景。")
    assert [s.text for s in shots] == ["内景。", "外景。"]


def test_bom_stripped_from_shot_text():
    """派生出的 shot 文本不带 BOM：它会进预览显示与后端渲染。"""
    from lib.reference_video.shot_parser import parse_prompt

    shots, _ = parse_prompt("﻿镜头1：@张三 站着。")
    assert "﻿" not in shots[0].text


def test_render_mentions_unknown_mention_kept():
    text = "@张三 和 @未知 对话"
    rendered = render_mentions_as_subjects(text, {"张三"})
    assert "<张三>" in rendered
    assert "@未知" in rendered  # 未注册保留


def test_render_mentions_multi_shot_text():
    text = "镜头1：@张三 推门\n镜头2：@张三 坐下"
    rendered = render_mentions_as_subjects(text, {"张三"})
    assert rendered.count("<张三>") == 2
    assert "镜头1：" in rendered  # header 保留


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


def test_missing_registered_references_skips_non_string_type():
    assert missing_registered_references([{"type": [], "name": "张三"}], _proj(characters={"张三": {}})) == []


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


def test_render_mentions_as_subjects_strips_comparison_whitespace():
    assert render_mentions_as_subjects("@[ Hero ] 推门而入", ["Hero"]) == "<Hero> 推门而入"


def test_parse_multi_shot_preserves_pre_header_text():
    text = "开场说明：这段剧本的整体基调偏紧张。\n镜头1：中远景，主角推门进酒馆。\n镜头2：近景，对面的张三抬眼。\n"
    shots, _refs = parse_prompt(text)
    assert len(shots) == 2
    # Pre-header text 前置到首 shot
    assert "开场说明" in shots[0].text
    assert "中远景" in shots[0].text
    # 第二个 shot 不受影响
    assert shots[1].text == "近景，对面的张三抬眼。"


# ── mention 前缀边界 ────────────────────────────────────────


def test_mention_ignores_email_like_prefix():
    """email 左侧是 \\w，不应被当成 mention。"""
    from lib.reference_video.shot_parser import extract_mentions

    assert extract_mentions("contact a@张三 for help") == []
    assert extract_mentions("email: test@domain.com") == []
    assert extract_mentions("alice@example.com 和 bob@foo.io") == []
    assert extract_mentions("room9@张三") == []
    assert extract_mentions("user123@李四") == []


def test_mention_accepts_chinese_prefix():
    """中文左侧字符（\\u4e00-\\u9fff）不是 \\w，合法 mention 用法。"""
    from lib.reference_video.shot_parser import extract_mentions

    assert extract_mentions("你好@张三") == ["张三"]
    assert extract_mentions("（对面）@李四 抬眼") == ["李四"]


def test_mention_accepts_whitespace_and_line_start():
    """空白字符 / 行首 / 标点前缀都应识别。"""
    from lib.reference_video.shot_parser import extract_mentions

    assert extract_mentions("@张三") == ["张三"]
    assert extract_mentions("之后 @张三 回头") == ["张三"]
    assert extract_mentions("镜头1：\n@张三 开门") == ["张三"]
    assert extract_mentions("台词：@张三 起身") == ["张三"]


def test_mention_underscore_prefix_is_rejected():
    """underscore 属 \\w，`foo_@张三` 类打字错误不应触发 mention。"""
    from lib.reference_video.shot_parser import extract_mentions

    assert extract_mentions("prefix_@张三") == []


def test_render_shots_text_round_trips_parse_prompt():
    """``render_shots_text`` 是 ``parse_prompt`` 的逆向：header 复原，再解析回同一组 shots。"""
    text = "镜头1：@[甲] 起身\n@[甲]：{走了。}\n镜头2：@[甲] 出门"
    shots, _mentions = parse_prompt(text)
    rendered = render_shots_text([s.model_dump() for s in shots])
    assert rendered == text
    assert [s.text for s in parse_prompt(rendered)[0]] == [s.text for s in shots]


def test_render_shots_text_normalizes_dirty_entries():
    """Agent 可裸写 JSON：非 dict 条目 / 缺失 text 按空正文渲染，不注入 "None" 字面量。"""
    assert render_shots_text([{"text": None}, "x", {}]) == "镜头1：\n镜头2：\n镜头3："


def _marks(line: str) -> list[tuple[str, str]]:
    from lib.reference_video.shot_parser import line_speech_marks

    return [(mark.speaker, mark.text) for mark in line_speech_marks(line)]


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
    from lib.reference_video.shot_parser import extract_mentions

    assert _marks("@[张三] 推开门，屋里传出声音 {谁啊？}") == [("", "谁啊？")]
    assert extract_mentions("@[张三] 推开门，屋里传出声音 {谁啊？}") == ["张三"]


def test_speaker_only_before_braces_is_excluded_from_references():
    """只在花括号前出现的角色只绑声音；同一行记号之外的引用照常进参考图。"""
    from lib.reference_video.shot_parser import extract_mentions

    assert extract_mentions("镜头1：@[酒馆] 内景。@[张三]{我来了}") == ["酒馆"]
    assert extract_mentions("镜头1：@[张三] 推门。@[张三]{我来了}") == ["张三"]


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
    from lib.reference_video.shot_parser import strip_speech_marks

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
    from lib.reference_video.shot_parser import strip_speech_marks

    assert _marks("@[张三]{我来了") == []
    assert strip_speech_marks("@[张三]{我来了") == "@[张三]{我来了"


def test_strip_speech_marks_is_the_other_half_of_a_lossless_split():
    from lib.reference_video.shot_parser import split_speech_line

    line = "@[张三] 推门。@[张三]{我来了}屋里安静。"
    parts = split_speech_line(line)
    joined = "".join(part if isinstance(part, str) else part.raw for part in parts)
    assert joined == line


def test_speech_marks_normalize_to_nfc():
    """NFD 落盘的说话人与 NFC 登记的资产名须判等，台词文本同样归一。"""
    import unicodedata

    line = unicodedata.normalize("NFD", "@[Nguyễn]{Chào}")
    assert _marks(line) == [("Nguyễn", "Chào")]


def test_legacy_bare_mention_can_be_a_speaker():
    assert _marks("@张三{我来了}") == [("张三", "我来了")]


def test_email_like_prefix_is_not_a_speaker():
    """左侧是 ASCII 词字符时按邮箱 / id 片段跳过，与 mention 扫描同口径。"""
    assert _marks("a@b{我来了}") == [("", "我来了")]
