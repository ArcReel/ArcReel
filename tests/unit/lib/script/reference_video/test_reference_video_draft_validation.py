"""Mechanical validation of flat reference-to-video drafts, shared by script_plan / prompt_authoring."""

import unicodedata

import pytest

from lib.script.draft_quarantine import violation_entries
from lib.script.reference_video.draft_validation import (
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

#: Project payload with character derivatives for registration checks of ``@[角色/衍生]`` (docs/adr/0072).
DERIVATIVE_PROJECT = {
    "characters": {"李明": {"derivatives": {"劲装": {}}}, "王五": {}},
    "scenes": {"酒馆": {}},
    "props": {"长剑": {}},
}

#: A Vietnamese combining-character name: visually identical but byte-distinct encodings test asset-name comparison coordinates.
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


class TestSourceTextAnchor:
    def test_verbatim_substring_accepted(self):
        assert (
            validate_source_text_anchor("unit E1U01", "李明推开酒馆的门", "夜色深沉。李明推开酒馆的门，环视四周。")
            is None
        )

    def test_whitespace_differences_tolerated(self):
        """Compare after whitespace folding: newline/indentation restoration is unreliable, but deleted or changed characters must be detected."""
        assert (
            validate_source_text_anchor("unit E1U01", "李明推开\n  酒馆的门", "李明推开 酒馆的门，环视四周。") is None
        )

    def test_unicode_form_differences_tolerated(self):
        """NFD source with NFC model writeback is an encoding difference, common in combining-character languages, not rewriting; normalize both to NFC."""
        novel = unicodedata.normalize("NFD", "Anh ấy mở cửa quán rượu.")
        anchor = unicodedata.normalize("NFC", "Anh ấy mở cửa")
        assert validate_source_text_anchor("unit E1U01", anchor, novel) is None

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
        """Dialogue-marker speakers drive voice declarations only, not reference images; offscreen speakers must not be drawn into the image."""
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
        """Braces not recognized as speech markers still violate the contract; empty dialogue would derive contentless speech."""
        with pytest.raises(DraftViolation, match="画面描述里使用了花括号"):
            validate_unit_text("unit E1U01", "镜头1：@[李明] 推门，音量 {}", PROJECT, max_refs=None)

    def test_inline_speech_marks_are_accepted(self):
        """Allow dialogue and voiceover after visual description on the same line; speaker positions do not enter reference images."""
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

    def test_registered_derivative_accepted_in_a_mention_and_in_the_speaker_slot(self):
        """``@[角色/衍生]`` and ``@[角色/衍生]{台词}`` both accept registered derivatives."""
        refs = validate_unit_text(
            "unit E1U01", "镜头1：@[李明/劲装] 推门。@[李明/劲装]{我来了}", DERIVATIVE_PROJECT, max_refs=None
        )

        assert [(r.type, r.name) for r in refs] == [("character", "李明/劲装")]

    def test_unregistered_derivative_in_a_mention_rejected(self):
        with pytest.raises(DraftViolation, match="未登记的资产名"):
            validate_unit_text("unit E1U01", "镜头1：@[李明/夜行衣] 推门", DERIVATIVE_PROJECT, max_refs=None)

    def test_unregistered_derivative_in_the_speaker_slot_rejected(self):
        """Speaker positions do not derive references, so detect unregistered speakers separately here."""
        with pytest.raises(DraftViolation, match="写了该角色没有的衍生") as excinfo:
            validate_unit_text("unit E1U01", "镜头1：门开了\n@[李明/夜行衣]{我来了}", DERIVATIVE_PROJECT, max_refs=None)

        assert excinfo.value.code == "unregistered_speaker_derivative"

    def test_derivative_speaker_is_the_base_character(self):
        """Derivative speakers still bind base voices; derivative notation must not make the speaker appear unregistered."""
        assert dialogue_speakers("@[李明/劲装]{我来了}") == ["李明"]

    @pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
    @pytest.mark.parametrize("written", [_NAME_NFC, _NAME_NFD], ids=["出场NFC", "出场NFD"])
    def test_combining_char_name_passes_in_every_encoding_pairing(self, registered: str, written: str):
        """All four NFC/NFD combinations of combining-character names agree; no visually identical pairing may appear unregistered.

        Visual mentions and dialogue speakers coexist but use different comparison points (reference derivation / speaker registration);
        missing normalization at either point surfaces here under different violation codes.
        """
        project = {"characters": {registered: {}}, "scenes": {}, "props": {}}
        text = f"镜头1：@[{written}] 推门而入\n@[{written}]：{{Tôi đến rồi.}}"

        refs = validate_unit_text("unit E1U01", text, project, max_refs=None)

        # Derived references are normalized; downstream asset lookup and body subject-marker replacement must use the same form.
        assert [(r.type, r.name) for r in refs] == [("character", _NAME_NFC)]

    def test_unregistered_mention_still_rejected_after_normalization(self):
        """Normalization removes encoding differences only, without relaxing registration; genuinely unregistered names still reject."""
        project = {"characters": {_NAME_NFC: {}}, "scenes": {}, "props": {}}
        with pytest.raises(DraftViolation, match="未登记的资产名"):
            validate_unit_text("unit E1U01", f"镜头1：@[{_NAME_NFD}Ⅱ] 推门而入", project, max_refs=None)

    def test_over_max_refs_rejected(self):
        with pytest.raises(DraftViolation, match="超过模型上限"):
            validate_unit_text("unit E1U01", "镜头1：@[李明] 与 @[王五] 在 @[酒馆]", PROJECT, max_refs=2)

    def test_fullwidth_braces_rejected(self):
        """Speech syntax does not recognize fullwidth braces; admitting them silently turns dialogue into description and derives speakers as reference images."""
        with pytest.raises(DraftViolation, match="全角花括号"):
            validate_unit_text("unit E1U01", "镜头1：门开了\n@[李明]：｛我来了。｝", PROJECT, max_refs=None)

    def test_dialogue_without_braces_rejected(self):
        """Dialogue missing braces becomes visual description: the entire spoken sentence disappears, while its speaker becomes a reference image."""
        with pytest.raises(DraftViolation, match="台词写法不合法"):
            validate_unit_text("unit E1U01", "镜头1：门开了\n@[李明]：我来了。", PROJECT, max_refs=None)

    def test_dialogue_followed_by_description_is_accepted(self):
        """Description after line-leading dialogue is no longer invalid; markers may occur anywhere inline, with other text treated as visual description."""
        refs = validate_unit_text("unit E1U01", "镜头1：门开了\n@[李明]：{我来了}，然后转身", PROJECT, max_refs=None)
        assert [r.name for r in refs] == []

    def test_non_character_mention_with_colon_is_a_description(self):
        """Scene/prop subheadings are valid visual descriptions; do not classify every @[名称]： shape as malformed dialogue."""
        refs = validate_unit_text("unit E1U01", "镜头1：@[酒馆]：木门被风吹开，灯笼摇晃", PROJECT, max_refs=None)
        assert [(r.type, r.name) for r in refs] == [("scene", "酒馆")]

    def test_fullwidth_mention_delimiters_rejected(self):
        """Mention syntax rejects fullwidth `＠` / `［］`; otherwise reference images silently disappear from video requests."""
        with pytest.raises(DraftViolation, match="写坏的资产引用"):
            validate_unit_text("unit E1U01", "镜头1：@［李明］ 推开门", PROJECT, max_refs=None)

    def test_malformed_mention_rejected(self):
        """Malformed `@[` enters neither references nor recognized replacement, so it reaches provider requests unchanged."""
        with pytest.raises(DraftViolation, match="写坏的资产引用"):
            validate_unit_text("unit E1U01", "镜头1：@[李明 推开门", PROJECT, max_refs=None)

    def test_empty_mention_rejected(self):
        with pytest.raises(DraftViolation, match="写坏的资产引用"):
            validate_unit_text("unit E1U01", "镜头1：@[] 推开门", PROJECT, max_refs=None)

    def test_dialogue_only_text_rejected(self):
        """Dialogue-only bodies lack generatable visuals; a unit must produce imagery, not only sound."""
        with pytest.raises(DraftViolation, match="没有画面描述"):
            validate_unit_text("unit E1U01", "@[李明]：{我来了。}\n{风吹过。}", PROJECT, max_refs=None)

    def test_dialogue_written_on_shot_header_line_is_normative(self):
        """Dialogue on the ``镜头N：`` line becomes a canonical line after splitting; evaluate after stripping headers."""
        refs = validate_unit_text("unit E1U01", "镜头1：@[李明]：{我来了。}\n门在风里晃动", PROJECT, max_refs=None)
        assert refs == []


class TestDialogueLoad:
    def test_within_budget_accepted(self):
        assert validate_dialogue_load("unit E1U01", "镜头1：门开了\n@[李明]：{我来了。}", 4, "zh") is None

    def test_overload_rejected(self):
        long_line = "这是一段非常长的台词" * 6  # 60 characters ÷ 5 characters/second ≈ 12 seconds.
        with pytest.raises(DraftViolation, match="超过该 unit"):
            validate_dialogue_load("unit E1U01", f"镜头1：门开了\n@[李明]：{{{long_line}}}", 4, "zh")

    def test_tolerance_admits_slight_overrun(self):
        """Allow within the tolerance factor: speech rate is statistical, so normal output filling available time must not be called a violation."""
        # 21 characters ÷ 5 characters/second = 4.2 seconds, within 4 seconds × 1.2 = 4.8 seconds tolerance.
        line = "一二三四五六七八九十一二三四五六七八九十。"
        assert validate_dialogue_load("unit E1U01", f"@[李明]：{{{line}}}", 4, "zh") is None

    def test_voiceover_counts_toward_budget(self):
        long_line = "画外音很长很长的一段" * 6
        with pytest.raises(DraftViolation, match="超过该 unit"):
            validate_dialogue_load("unit E1U01", f"镜头1：空镜\n{{{long_line}}}", 4, "zh")

    def test_project_override_changes_budget(self):
        """Project speech-rate overrides apply: the same dialogue overloads under slow settings and passes under fast settings."""
        text = "镜头1：门开了\n@[李明]：{一二三四五六七八九十一二三四五六七八九十。}"
        with pytest.raises(DraftViolation, match="超过该 unit"):
            validate_dialogue_load("unit E1U01", text, 4, "zh", 2.0)
        validate_dialogue_load("unit E1U01", text, 4, "zh", 10.0)

    def test_non_string_language_falls_back_to_default_rate(self):
        """project.json source_language may be corrupt; estimation uses default speech rate without AttributeError."""
        assert validate_dialogue_load("unit E1U01", "@[李明]：{我来了。}", 4, 123) is None

    def test_normalizes_unicode_before_estimating(self):
        """Normalize NFD dialogue before estimation; combining marks can split word-count units and falsely overload a speakable unit."""
        line = "Anh ấy mở cửa quán rượu ngay lập tức"
        text = f"镜头1：@[李明] 推门\n@[李明]：{{{unicodedata.normalize('NFD', line)}}}"
        assert validate_dialogue_load("unit E1U01", text, 4, "vi") is None


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
    SCRIPT_PLAN = "镜头1：@[李明] 推门\n@[李明]：{我来了。}"

    def test_description_expansion_accepted(self):
        assert_dialogue_preserved(
            "unit E1U01",
            self.SCRIPT_PLAN,
            "镜头1：中景，平视。@[李明] 推开 @[酒馆] 的门，跨过门槛\n@[李明]：{我来了。}",
        )

    def test_unicode_form_difference_not_a_rewrite(self):
        """NFD script_plan with NFC prompt_authoring writeback is only an encoding difference; do not reject paid expansion as changed wording."""
        line = "Anh ấy mở cửa"
        script_plan = f"镜头1：@[李明] 推门\n@[李明]：{{{unicodedata.normalize('NFD', line)}}}"
        prompt_authoring = f"镜头1：中景。@[李明] 推开木门\n@[李明]：{{{unicodedata.normalize('NFC', line)}}}"
        assert_dialogue_preserved("unit E1U01", script_plan, prompt_authoring)

    def test_rewritten_dialogue_rejected(self):
        with pytest.raises(DraftViolation, match="第 1 条台词被改写"):
            assert_dialogue_preserved("unit E1U01", self.SCRIPT_PLAN, "镜头1：@[李明] 推门\n@[李明]：{我到了。}")

    def test_speaker_change_rejected(self):
        with pytest.raises(DraftViolation, match="第 1 条台词被改写"):
            assert_dialogue_preserved("unit E1U01", self.SCRIPT_PLAN, "镜头1：@[李明] 推门\n@[王五]：{我来了。}")

    def test_added_dialogue_rejected(self):
        with pytest.raises(DraftViolation, match="台词条数被改动"):
            assert_dialogue_preserved(
                "unit E1U01", self.SCRIPT_PLAN, "镜头1：@[李明] 推门\n@[李明]：{我来了。}\n{夜色深沉。}"
            )

    def test_dropped_dialogue_rejected(self):
        with pytest.raises(DraftViolation, match="台词条数被改动"):
            assert_dialogue_preserved("unit E1U01", self.SCRIPT_PLAN, "镜头1：@[李明] 推门")


class TestNeutralLayerReexport:
    """Violation entries are defined in route-neutral ``lib.script.draft_violation`` and merely reexported here.

    Two type definitions would make ``except DraftViolation`` silently miss half the exceptions depending on import path, although validators
    and draft envelopes for all three routes share that except. import-linter enforces the neutral layer
    not depending on the reference-to-video package; this case enforces identity.
    """

    def test_symbols_are_the_neutral_layer_objects(self):
        from lib.script import draft_violation
        from lib.script.reference_video import draft_validation

        assert draft_validation.DraftViolation is draft_violation.DraftViolation
        assert draft_validation.DraftViolations is draft_violation.DraftViolations
        assert draft_validation.collect_violations is draft_violation.collect_violations
        assert draft_validation.render_violation_report is draft_violation.render_violation_report
        assert draft_validation.violation_items is draft_violation.violation_items
