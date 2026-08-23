"""参考生视频扁平草稿结构的机械校验（step1 / step2 共用）。"""

import unicodedata

import pytest

from lib.draft_quarantine import violation_entries
from lib.reference_video.draft_validation import (
    DraftViolation,
    DraftViolations,
    assert_dialogue_preserved,
    dialogue_speakers,
    normative_lines,
    validate_dialogue_load,
    validate_source_text_anchor,
    validate_unit_text,
)

PROJECT = {
    "characters": {"李明": {}, "王五": {}},
    "scenes": {"酒馆": {}},
    "props": {"长剑": {}},
}

#: 带组合附加符的角色名（越南语），两种编码屏幕显示相同、字节不同——资产名比对的坐标系用例。
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


class TestSourceTextAnchor:
    def test_verbatim_substring_accepted(self):
        validate_source_text_anchor("unit E1U01", "李明推开酒馆的门", "夜色深沉。李明推开酒馆的门，环视四周。")

    def test_whitespace_differences_tolerated(self):
        """空白折叠后比对：换行 / 缩进的还原不可靠，但删字改字必须被抓住。"""
        validate_source_text_anchor("unit E1U01", "李明推开\n  酒馆的门", "李明推开 酒馆的门，环视四周。")

    def test_unicode_form_differences_tolerated(self):
        """源文以 NFD 落盘、模型回写 NFC（组合附加符语种常见）不算改写：两侧先归一到 NFC。"""
        novel = unicodedata.normalize("NFD", "Anh ấy mở cửa quán rượu.")
        anchor = unicodedata.normalize("NFC", "Anh ấy mở cửa")
        validate_source_text_anchor("unit E1U01", anchor, novel)

    def test_rewritten_text_rejected(self):
        with pytest.raises(DraftViolation, match="不是小说原文的逐字片段"):
            validate_source_text_anchor("unit E1U01", "李明走进了酒馆", "李明推开酒馆的门。")

    def test_blank_anchor_rejected(self):
        with pytest.raises(DraftViolation, match="source_text 为空"):
            validate_source_text_anchor("unit E1U01", "   ", "李明推开酒馆的门。")


class TestUnitText:
    def test_mixed_speech_is_a_structured_planning_violation(self):
        with pytest.raises(DraftViolations) as exc_info:
            validate_unit_text(
                "unit E1U01",
                "镜头1：门被推开\n@[李明]：{快走。}\n{风吹过旷野。}",
                PROJECT,
                max_refs=None,
            )

        problem = exc_info.value.items[0]
        assert problem.code == "mixed_speech"
        assert problem.label == "unit E1U01"
        assert violation_entries([problem]) == [
            {
                "code": "mixed_speech",
                "label": "unit E1U01",
                "message": str(problem),
                "line": None,
                "locations": [
                    {"path": ["text"], "line": 1},
                    {"path": ["text"], "line": 2},
                ],
                "reason": "character_and_narrator_mixed",
                "action": "replan_unit",
            }
        ]
        assert "character_and_narrator_mixed" in str(problem)
        assert "replan_unit" in str(problem)

    def test_derives_references_in_first_mention_order(self):
        refs = validate_unit_text(
            "unit E1U01",
            "@[李明] 推开 @[酒馆] 的门\n@[李明] 放下 @[长剑]",
            PROJECT,
            max_refs=None,
        )
        assert [(r.type, r.name) for r in refs] == [
            ("character", "李明"),
            ("scene", "酒馆"),
            ("prop", "长剑"),
        ]

    def test_dialogue_speaker_not_a_reference_image(self):
        """台词记号的说话人位只驱动音色声明，不进参考图（画外说话的角色不该被画进来）。"""
        refs = validate_unit_text("unit E1U01", "镜头1：门在风里晃动\n@[李明]：{我来了。}", PROJECT, max_refs=None)
        assert refs == []

    def test_blank_text_rejected(self):
        with pytest.raises(DraftViolation, match="正文为空"):
            validate_unit_text("unit E1U01", "   \n  ", PROJECT, max_refs=None)

    def test_unclosed_brace_rejected(self):
        with pytest.raises(DraftViolation, match="未闭合的花括号") as exc_info:
            validate_unit_text("unit E1U01", "镜头1：@[李明] 说 {我来了", PROJECT, max_refs=None)
        assert exc_info.value.line == 0

    def test_fullwidth_braces_rejected_carries_line(self):
        with pytest.raises(DraftViolation, match="全角花括号") as exc_info:
            validate_unit_text("unit E1U01", "镜头1：门开了\n@[李明]：｛我来了。｝", PROJECT, max_refs=None)
        assert exc_info.value.line == 1

    def test_brace_in_description_rejected(self):
        """没被识别成发声记号的花括号仍判违约：空台词会派生出没有内容的发声。"""
        with pytest.raises(DraftViolation, match="画面描述里使用了花括号"):
            validate_unit_text("unit E1U01", "镜头1：@[李明] 推门，音量 {}", PROJECT, max_refs=None)

    def test_inline_speech_marks_are_accepted(self):
        """台词与画外音写在同一行的画面描述之后照常放行；说话人位不进参考图。"""
        refs = validate_unit_text(
            "unit E1U01", "镜头1：@[李明] 推开 @[酒馆] 木门。@[李明]{我来了}", PROJECT, max_refs=None
        )
        assert [r.name for r in refs] == ["李明", "酒馆"]

    def test_unregistered_mention_rejected(self):
        with pytest.raises(DraftViolation, match="未登记的资产名"):
            validate_unit_text("unit E1U01", "镜头1：@[路人甲] 走过", PROJECT, max_refs=None)

    def test_unregistered_speaker_rejected(self):
        with pytest.raises(DraftViolation, match="说话人未登记"):
            validate_unit_text("unit E1U01", "镜头1：门开了\n@[无名氏]：{我来了。}", PROJECT, max_refs=None)

    @pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
    @pytest.mark.parametrize("written", [_NAME_NFC, _NAME_NFD], ids=["出场NFC", "出场NFD"])
    def test_combining_char_name_passes_in_every_encoding_pairing(self, registered: str, written: str):
        """组合字符角色名的四种 NFC/NFD 配对判定一致：肉眼同字，不该有任一组合被判未登记。

        画面描述 mention 与台词记号说话人同时出现——两者走不同的比对点（引用派生 / 说话人登记），
        任一处漏归一都会在这里以不同的违约 code 冒出来。
        """
        project = {"characters": {registered: {}}, "scenes": {}, "props": {}}
        text = f"镜头1：@[{written}] 推门而入\n@[{written}]：{{Tôi đến rồi.}}"

        refs = validate_unit_text("unit E1U01", text, project, max_refs=None)

        # 派生出的引用一律是归一形式：下游拿它回查资产表、在正文里替换成主体记号，须与此处同形
        assert [(r.type, r.name) for r in refs] == [("character", _NAME_NFC)]

    def test_unregistered_mention_still_rejected_after_normalization(self):
        """归一只消除编码形式差异，不放宽登记判定：真没登记的名字照常拒。"""
        project = {"characters": {_NAME_NFC: {}}, "scenes": {}, "props": {}}
        with pytest.raises(DraftViolation, match="未登记的资产名"):
            validate_unit_text("unit E1U01", f"镜头1：@[{_NAME_NFD}Ⅱ] 推门而入", project, max_refs=None)

    def test_over_max_refs_rejected(self):
        with pytest.raises(DraftViolation, match="超过模型上限"):
            validate_unit_text("unit E1U01", "镜头1：@[李明] 与 @[王五] 在 @[酒馆]", PROJECT, max_refs=2)

    def test_fullwidth_braces_rejected(self):
        """全角花括号不被发声记号语法识别，放行会让台词静默降级成描述、说话人反被派生成参考图。"""
        with pytest.raises(DraftViolation, match="全角花括号"):
            validate_unit_text("unit E1U01", "镜头1：门开了\n@[李明]：｛我来了。｝", PROJECT, max_refs=None)

    def test_dialogue_without_braces_rejected(self):
        """漏花括号的台词会被当成画面描述：台词整句消失、说话人反被派生成参考图。"""
        with pytest.raises(DraftViolation, match="台词写法不合法"):
            validate_unit_text("unit E1U01", "镜头1：门开了\n@[李明]：我来了。", PROJECT, max_refs=None)

    def test_dialogue_followed_by_description_is_accepted(self):
        """行首台词后接描述不再判违约：记号可写在行内任意位置，其余是画面描述。"""
        refs = validate_unit_text("unit E1U01", "镜头1：门开了\n@[李明]：{我来了}，然后转身", PROJECT, max_refs=None)
        assert [r.name for r in refs] == []

    def test_non_character_mention_with_colon_is_a_description(self):
        """场景 / 道具做小标题是合法的画面描述写法，不能按「@[名称]：」形态一概判成写坏的台词。"""
        refs = validate_unit_text("unit E1U01", "镜头1：@[酒馆]：木门被风吹开，灯笼摇晃", PROJECT, max_refs=None)
        assert [(r.type, r.name) for r in refs] == [("scene", "酒馆")]

    def test_fullwidth_mention_delimiters_rejected(self):
        """全角 `＠` / `［］` 不被 mention 语法识别：参考图会从视频请求里静默消失。"""
        with pytest.raises(DraftViolation, match="写坏的资产引用"):
            validate_unit_text("unit E1U01", "镜头1：@［李明］ 推开门", PROJECT, max_refs=None)

    def test_malformed_mention_rejected(self):
        """写坏的 `@[` 既不进 references，又会原样进入供应商请求（渲染只替换认得的 mention）。"""
        with pytest.raises(DraftViolation, match="写坏的资产引用"):
            validate_unit_text("unit E1U01", "镜头1：@[李明 推开门", PROJECT, max_refs=None)

    def test_empty_mention_rejected(self):
        with pytest.raises(DraftViolation, match="写坏的资产引用"):
            validate_unit_text("unit E1U01", "镜头1：@[] 推开门", PROJECT, max_refs=None)

    def test_dialogue_only_text_rejected(self):
        """只有台词的正文没有可生成的画面：画面是 unit 要产出的东西，不能只有声音。"""
        with pytest.raises(DraftViolation, match="没有画面描述"):
            validate_unit_text("unit E1U01", "@[李明]：{我来了。}\n{风吹过。}", PROJECT, max_refs=None)

    def test_dialogue_written_on_shot_header_line_is_normative(self):
        """写在 ``镜头N：`` 同一行的台词在切分后就是规范行，判定须在剥 header 之后。"""
        refs = validate_unit_text("unit E1U01", "镜头1：@[李明]：{我来了。}\n门在风里晃动", PROJECT, max_refs=None)
        assert refs == []


class TestDialogueLoad:
    def test_within_budget_accepted(self):
        validate_dialogue_load("unit E1U01", "镜头1：门开了\n@[李明]：{我来了。}", 4, "zh")

    def test_overload_rejected(self):
        long_line = "这是一段非常长的台词" * 6  # 60 字 ÷ 5 字/秒 ≈ 12 秒
        with pytest.raises(DraftViolation, match="超过该 unit"):
            validate_dialogue_load("unit E1U01", f"镜头1：门开了\n@[李明]：{{{long_line}}}", 4, "zh")

    def test_tolerance_admits_slight_overrun(self):
        """宽容系数内放行：语速是统计估算，「刚好写满」的正常产出不该被判违约。"""
        # 21 字 ÷ 5 字/秒 = 4.2 秒，落在 4 秒 × 1.2 = 4.8 秒的宽容上限内
        line = "一二三四五六七八九十一二三四五六七八九十。"
        validate_dialogue_load("unit E1U01", f"@[李明]：{{{line}}}", 4, "zh")

    def test_voiceover_counts_toward_budget(self):
        long_line = "画外音很长很长的一段" * 6
        with pytest.raises(DraftViolation, match="超过该 unit"):
            validate_dialogue_load("unit E1U01", f"镜头1：空镜\n{{{long_line}}}", 4, "zh")

    def test_project_override_changes_budget(self):
        """项目级语速覆盖生效：同一段台词在慢速覆盖下判超载、在快速覆盖下放行。"""
        text = "镜头1：门开了\n@[李明]：{一二三四五六七八九十一二三四五六七八九十。}"
        with pytest.raises(DraftViolation, match="超过该 unit"):
            validate_dialogue_load("unit E1U01", text, 4, "zh", 2.0)
        validate_dialogue_load("unit E1U01", text, 4, "zh", 10.0)

    def test_non_string_language_falls_back_to_default_rate(self):
        """project.json 的 source_language 可能是脏数据：估算按默认语速走，不抛 AttributeError。"""
        validate_dialogue_load("unit E1U01", "@[李明]：{我来了。}", 4, 123)  # pyright: ignore[reportArgumentType]

    def test_normalizes_unicode_before_estimating(self):
        """NFD 台词先归一再估：组合附加符会被词计数拆成多个单位，不归一会把念得完的 unit 判超载。"""
        line = "Anh ấy mở cửa quán rượu ngay lập tức"
        text = f"镜头1：@[李明] 推门\n@[李明]：{{{unicodedata.normalize('NFD', line)}}}"
        validate_dialogue_load("unit E1U01", text, 4, "vi")


class TestNormativeLines:
    def test_extracts_dialogue_and_voiceover_in_order(self):
        text = "镜头1：门开了\n@[李明]：{我来了。}\n{夜色深沉。}"
        assert normative_lines(text) == [
            ("dialogue", "李明", "我来了。"),
            ("voiceover", "", "夜色深沉。"),
        ]

    def test_dialogue_speakers_deduped_in_first_seen_order(self):
        text = "@[王五]：{在。}\n@[李明]：{我来了。}\n@[王五]：{知道了。}"
        assert dialogue_speakers(text) == ["王五", "李明"]


class TestDialoguePreserved:
    STEP1 = "镜头1：@[李明] 推门\n@[李明]：{我来了。}"

    def test_description_expansion_accepted(self):
        assert_dialogue_preserved(
            "unit E1U01",
            self.STEP1,
            "镜头1：中景，平视。@[李明] 推开 @[酒馆] 的门，跨过门槛\n@[李明]：{我来了。}",
        )

    def test_unicode_form_difference_not_a_rewrite(self):
        """step1 存 NFD、step2 回写 NFC 是纯编码差异，不该把已付费的展开判成改词。"""
        line = "Anh ấy mở cửa"
        step1 = f"镜头1：@[李明] 推门\n@[李明]：{{{unicodedata.normalize('NFD', line)}}}"
        step2 = f"镜头1：中景。@[李明] 推开木门\n@[李明]：{{{unicodedata.normalize('NFC', line)}}}"
        assert_dialogue_preserved("unit E1U01", step1, step2)

    def test_rewritten_dialogue_rejected(self):
        with pytest.raises(DraftViolation, match="第 1 条台词被改写"):
            assert_dialogue_preserved("unit E1U01", self.STEP1, "镜头1：@[李明] 推门\n@[李明]：{我到了。}")

    def test_speaker_change_rejected(self):
        with pytest.raises(DraftViolation, match="第 1 条台词被改写"):
            assert_dialogue_preserved("unit E1U01", self.STEP1, "镜头1：@[李明] 推门\n@[王五]：{我来了。}")

    def test_added_dialogue_rejected(self):
        with pytest.raises(DraftViolation, match="台词条数被改动"):
            assert_dialogue_preserved(
                "unit E1U01", self.STEP1, "镜头1：@[李明] 推门\n@[李明]：{我来了。}\n{夜色深沉。}"
            )

    def test_dropped_dialogue_rejected(self):
        with pytest.raises(DraftViolation, match="台词条数被改动"):
            assert_dialogue_preserved("unit E1U01", self.STEP1, "镜头1：@[李明] 推门")


class TestNeutralLayerReexport:
    """违约条目类型的定义处是路线中立的 ``lib.draft_violation``，本模块只再导出。

    分叉成两份类型定义时，``except DraftViolation`` 会按导入路径的不同静默漏接一半——三条
    路线的校验器与草稿信封都在同一条 except 上。import-linter 的「路线中立层不依赖参考
    生视频子包」契约管方向，本用例管同一性。
    """

    def test_symbols_are_the_neutral_layer_objects(self):
        from lib import draft_violation
        from lib.reference_video import draft_validation

        assert draft_validation.DraftViolation is draft_violation.DraftViolation
        assert draft_validation.DraftViolations is draft_violation.DraftViolations
        assert draft_validation.collect_violations is draft_violation.collect_violations
        assert draft_validation.render_violation_report is draft_violation.render_violation_report
        assert draft_validation.violation_items is draft_violation.violation_items
