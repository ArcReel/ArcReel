import unicodedata
from typing import ClassVar

import pytest
import yaml

from lib.prompts.prompt_utils import (
    append_avoid_text,
    build_drama_video_prompt,
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    normalize_video_prompt,
    render_storyboard_video_prompt,
    split_avoid_lines,
    utterances_to_dialogue,
    validate_camera_motion,
    validate_shot_type,
    video_prompt_to_yaml,
    yaml_section,
)


class TestPromptUtils:
    def test_image_prompt_to_yaml_keeps_expected_shape(self):
        data = {
            "scene": "夜雨中的街道",
            "composition": {
                "shot_type": "Medium Shot",
                "lighting": "路灯暖光",
                "ambiance": "薄雾",
            },
        }

        text = image_prompt_to_yaml(data, "Anime")
        parsed = yaml.safe_load(text)
        assert list(parsed) == ["Style", "Scene", "Composition", "Avoid"]
        assert parsed["Style"] == "Anime"
        assert parsed["Scene"] == "夜雨中的街道"
        assert parsed["Composition"]["shot_type"] == "Medium Shot"
        assert parsed["Avoid"] == "水印、多余文字、Logo"

    def test_long_values_with_ascii_spaces_stay_on_one_line(self):
        scene = "A rain-soaked neon street at night, " * 6 + "a lone figure walks under a red umbrella."
        declaration = "Image 1 is the character reference; " * 3 + "keep the outfit identical."
        data = {"scene": scene, "composition": {"shot_type": "Medium Shot", "lighting": "warm", "ambiance": "mist"}}

        text = image_prompt_to_yaml(data, "Anime", reference_images=declaration)

        assert f"Scene: {scene}\n" in text
        assert f"Reference_Images: {declaration}\n" in text
        assert yaml.safe_load(text)["Scene"] == scene

    def test_video_prompt_long_values_stay_on_one_line(self):
        action = "The camera slowly pushes in while " * 5 + "the wind lifts the curtain."
        text = video_prompt_to_yaml({"action": action, "camera_motion": "Push In", "dialogue": []})

        assert f"Action: {action}\n" in text

    def test_yaml_section_long_values_stay_on_one_line(self):
        line = "Speaker one keeps a low steady voice " * 4 + "throughout the take."

        assert yaml_section({"Voice_Profiles": line}) == f"Voice_Profiles: {line}"

    def test_image_prompt_to_yaml_places_reference_images_between_style_and_scene(self):
        data = {"scene": "x", "composition": {"shot_type": "Medium Shot", "lighting": "", "ambiance": ""}}
        text = image_prompt_to_yaml(data, "Anime", reference_images="图1为角色参考图。")
        assert list(yaml.safe_load(text)) == ["Style", "Reference_Images", "Scene", "Composition", "Avoid"]
        assert "Reference_Images: 图1为角色参考图。\n" in text

    def test_video_prompt_to_yaml_includes_dialogue_conditionally(self):
        with_dialogue = {
            "action": "抬头观察",
            "camera_motion": "Static",
            "ambiance_audio": "雨声",
            "dialogue": [{"speaker": "姜月茴", "line": "有人吗"}],
        }
        without_dialogue = {
            "action": "快步前进",
            "camera_motion": "Pan Left",
            "ambiance_audio": "脚步声",
            "dialogue": [],
        }

        parsed_a = yaml.safe_load(video_prompt_to_yaml(with_dialogue))
        parsed_b = yaml.safe_load(video_prompt_to_yaml(without_dialogue))

        assert parsed_a["Action"] == "抬头观察"
        assert parsed_a["Dialogue"][0]["Speaker"] == "姜月茴"
        assert "Dialogue" not in parsed_b
        # 反向约束以 Avoid 键收尾：有对话时置于 Dialogue 之后
        assert list(parsed_a)[-2:] == ["Dialogue", "Avoid"]
        assert list(parsed_b)[-1] == "Avoid"
        assert parsed_a["Avoid"] == "BGM、文字字幕、水印、Logo"

    def test_structured_checks(self):
        assert is_structured_image_prompt({"scene": "x"})
        assert not is_structured_image_prompt("text")
        assert is_structured_video_prompt({"action": "x"})
        assert not is_structured_video_prompt([])

    def test_video_prompt_to_yaml_voice_profiles_leads_and_is_conditional(self):
        # Voice_Profiles 是顶部集中声明段：须先于 Action 出现；无声明时不出现该字段
        with_profiles = {
            "action": "抬头观察",
            "camera_motion": "Static",
            "ambiance_audio": "雨声",
            "dialogue": [{"speaker": "姜月茴", "line": "有人吗"}],
            "voice_profiles": [{"Speaker": "姜月茴", "Voice_Style": "清冷"}],
        }
        text = video_prompt_to_yaml(with_profiles)
        parsed = yaml.safe_load(text)
        assert parsed["Voice_Profiles"] == [{"Speaker": "姜月茴", "Voice_Style": "清冷"}]
        assert text.index("Voice_Profiles") < text.index("Action")

        without_profiles = {"action": "快步前进", "camera_motion": "Pan Left", "ambiance_audio": "脚步声"}
        assert "Voice_Profiles" not in yaml.safe_load(video_prompt_to_yaml(without_profiles))


def _utterance(speaker: str | None, text: str) -> dict[str, object]:
    return {"kind": "dialogue", "speaker": speaker, "text": text}


_BASE_PROMPT = {"action": "抬头观察", "camera_motion": "Static", "ambiance_audio": "雨声"}

_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


class TestBuildDramaVideoPrompt:
    """两注入点共用的 drama dialogue + Voice_Profiles 出口。"""

    def test_one_entry_per_speaker_deduped_in_order(self):
        utterances = [_utterance("姜月茴", "有人吗"), _utterance("王", "嗯"), _utterance("姜月茴", "走吧")]
        characters = {"姜月茴": {"voice_style": "清冷"}, "王": {"voice_style": "低沉"}}
        prompt = build_drama_video_prompt(_BASE_PROMPT, utterances, characters=characters)
        assert prompt["voice_profiles"] == [
            {"Speaker": "姜月茴", "Voice_Style": "清冷"},
            {"Speaker": "王", "Voice_Style": "低沉"},
        ]
        # dialogue 逐条保序，不因 Voice_Profiles 去重而合并
        assert [d["speaker"] for d in prompt["dialogue"]] == ["姜月茴", "王", "姜月茴"]

    def test_characters_none_means_no_injection(self):
        utterances = [_utterance("姜月茴", "有人吗")]
        prompt = build_drama_video_prompt(_BASE_PROMPT, utterances, characters={"姜月茴": {"voice_style": "清冷"}})
        assert "voice_profiles" in prompt
        gated = build_drama_video_prompt(_BASE_PROMPT, utterances, characters=None)
        assert "voice_profiles" not in gated

    def test_script_carried_voice_profiles_never_survive(self):
        # 声明段唯一来源是编排层：剧本残留值一律剥离，否则会绕过 C 类（characters=None）门控
        carried = {**_BASE_PROMPT, "voice_profiles": [{"Speaker": "赝品", "Voice_Style": "越权"}]}
        assert "voice_profiles" not in build_drama_video_prompt(carried, [], characters=None)
        prompt = build_drama_video_prompt(carried, [_utterance("王", "嗯")], characters={"王": {"voice_style": "低沉"}})
        assert prompt["voice_profiles"] == [{"Speaker": "王", "Voice_Style": "低沉"}]

    def test_nfd_speaker_hits_nfc_registered_character(self):
        # speaker 与角色表 key 可能各是 NFC/NFD 中的任一形态（存量数据无需迁移），
        # 归一后索引须双向命中；Speaker 展示值保留 dialogue 原文
        for speaker, registered in ((_NAME_NFD, _NAME_NFC), (_NAME_NFC, _NAME_NFD)):
            prompt = build_drama_video_prompt(
                _BASE_PROMPT, [_utterance(speaker, "xin chào")], characters={registered: {"voice_style": "trầm"}}
            )
            assert prompt["voice_profiles"] == [{"Speaker": speaker, "Voice_Style": "trầm"}]

    def test_nfc_nfd_same_speaker_deduped_to_one_profile(self):
        utterances = [_utterance(_NAME_NFC, "một"), _utterance(_NAME_NFD, "hai")]
        prompt = build_drama_video_prompt(_BASE_PROMPT, utterances, characters={_NAME_NFC: {"voice_style": "trầm"}})
        assert prompt["voice_profiles"] == [{"Speaker": _NAME_NFC, "Voice_Style": "trầm"}]

    def test_speaker_without_matching_character_skipped_silently(self):
        prompt = build_drama_video_prompt(_BASE_PROMPT, [_utterance("路人甲", "喂")], characters={})
        assert "voice_profiles" not in prompt

    def test_character_with_empty_voice_style_skipped(self):
        prompt = build_drama_video_prompt(
            _BASE_PROMPT, [_utterance("王", "嗯")], characters={"王": {"voice_style": ""}}
        )
        assert "voice_profiles" not in prompt

    def test_robust_to_dirty_data(self):
        for utterances, characters in (
            ([], {}),
            ([_utterance(None, "x")], {}),
            ([_utterance("王", "x")], {"王": "not-a-dict"}),
            ([_utterance("王", "x")], "not-a-dict"),
        ):
            prompt = build_drama_video_prompt(_BASE_PROMPT, utterances, characters=characters)
            assert "voice_profiles" not in prompt

    def test_does_not_mutate_caller_prompt(self):
        source = {**_BASE_PROMPT}
        build_drama_video_prompt(source, [_utterance("王", "嗯")], characters={"王": {"voice_style": "低沉"}})
        assert source == _BASE_PROMPT


class TestUtterancesToDialogue:
    def test_takes_dialogue_kind_in_order_maps_text_to_line(self):
        # 仅 dialogue-kind 进 video YAML 的 {speaker, line}，按时序保留，voiceover 不进
        utterances = [
            {"kind": "voiceover", "speaker": None, "text": "旁白一"},
            {"kind": "dialogue", "speaker": "姜月茴", "text": "你来了。"},
            {"kind": "voiceover", "speaker": None, "text": "旁白二"},
            {"kind": "dialogue", "speaker": "王", "text": "嗯。"},
        ]
        assert utterances_to_dialogue(utterances) == [
            {"speaker": "姜月茴", "line": "你来了。"},
            {"speaker": "王", "line": "嗯。"},
        ]

    def test_robust_to_dirty_data(self):
        # 非 list / 非 dict 元素 / 缺 kind / 全空一律跳过，不抛
        assert utterances_to_dialogue(None) == []
        assert utterances_to_dialogue("nope") == []
        assert utterances_to_dialogue([1, "x", {"kind": "dialogue", "speaker": " ", "text": " "}]) == []

    def test_drops_dialogue_missing_speaker_or_text(self):
        # dialogue 须 speaker 与 line 同时非空才进口型音轨：缺 speaker（契约要求 dialogue 必带非空
        # speaker）或缺 text 的脏 dialogue 一律丢弃，不把无主台词重新喂给 lip-sync / video YAML
        utterances = [
            {"kind": "dialogue", "speaker": "", "text": "无主台词"},
            {"kind": "dialogue", "speaker": "王", "text": ""},
            {"kind": "dialogue", "speaker": "姜月茴", "text": "你来了。"},
        ]
        assert utterances_to_dialogue(utterances) == [{"speaker": "姜月茴", "line": "你来了。"}]

    def test_supports_pydantic_utterance_instances(self):
        # 兼容已实例化的 Pydantic Utterance 模型对象（不止原始 dict）：取属性而非键，
        # dialogue-kind 正确派生、voiceover 跳过，与 dict 形态同口径
        from lib.script.script_models import Utterance

        utterances = [
            Utterance(kind="voiceover", speaker=None, text="旁白"),
            Utterance(kind="dialogue", speaker="王", text="走吧。"),
        ]
        assert utterances_to_dialogue(utterances) == [{"speaker": "王", "line": "走吧。"}]

    def test_feeds_video_prompt_to_yaml_dialogue(self):
        # 与 video_prompt_to_yaml 串联：drama 台词从 utterances 派生后正确出现在 YAML Dialogue
        dialogue = utterances_to_dialogue([{"kind": "dialogue", "speaker": "王", "text": "走吧。"}])
        parsed = yaml.safe_load(
            video_prompt_to_yaml(
                {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声", "dialogue": dialogue}
            )
        )
        assert parsed["Dialogue"] == [{"Speaker": "王", "Line": "走吧。"}]

    def test_validators(self):
        assert validate_shot_type("Close-up")
        assert not validate_shot_type("Bad Shot")
        assert validate_camera_motion("Zoom In")
        # 词表从 lib.script.script_models 的 Literal 派生，扩充后的值应直接可校验
        assert validate_camera_motion("Orbit")
        assert not validate_camera_motion("Teleport")


class TestSplitAvoidLines:
    """正文里的排除项行按前缀识别，正文与排除项文本各自成形。"""

    def test_every_avoid_line_is_lifted_in_order(self):
        body, avoid = split_avoid_lines("一只猫\n\nAvoid: 水印\n\n镜头推近\n\nAvoid: 模糊、畸变")

        assert body == "一只猫\n\n镜头推近"
        assert avoid == "水印，模糊、畸变"

    @pytest.mark.parametrize(
        "line",
        ["Avoid: BGM、文字字幕、水印", "Avoid: BGM、文字字幕、水印、Logo", "Avoid:BGM、文字字幕、水印"],
        ids=["legacy", "current", "no-space"],
    )
    def test_any_historical_wording_of_the_declaration_is_recognised(self, line: str):
        body, avoid = split_avoid_lines(f"一只猫\n{line}")

        assert body == "一只猫"
        assert avoid == line.removeprefix("Avoid:").strip()

    def test_a_body_without_any_avoid_line_is_only_trimmed(self):
        assert split_avoid_lines("  一只猫\n镜头推近  ") == ("一只猫\n镜头推近", "")

    def test_an_empty_declaration_contributes_no_wording(self):
        assert split_avoid_lines("一只猫\nAvoid:") == ("一只猫", "")

    def test_an_indented_avoid_line_stays_in_the_body(self):
        """前缀顶行起才算声明行；缩进的 Avoid 是正文自己的措辞。"""
        body, avoid = split_avoid_lines("一只猫\n  Avoid: 水印")

        assert body == "一只猫\n  Avoid: 水印"
        assert avoid == ""


class TestAppendAvoidText:
    def test_a_non_empty_literal_keeps_its_wording_and_takes_the_suffix(self):
        assert append_avoid_text("low quality", "水印") == "low quality，水印"

    @pytest.mark.parametrize("literal", ["", "   "])
    def test_a_blank_literal_takes_the_wording_without_a_separator(self, literal: str):
        assert append_avoid_text(literal, "水印") == "水印"

    def test_an_empty_wording_leaves_the_literal_alone(self):
        assert append_avoid_text("low quality", "") == "low quality"


class TestNormalizeVideoPrompt:
    def test_structured_prompt_renders_yaml_with_defaults_filled(self):
        rendered = normalize_video_prompt(
            {
                "action": "行走",
                "camera_motion": "",
                "ambiance_audio": "风声",
                "dialogue": [{"speaker": "Alice", "line": "hello"}],
            }
        )
        assert "Camera_Motion" in rendered

    @pytest.mark.parametrize("blank", [{"action": ""}, "", "   "])
    def test_blank_prompt_is_rejected(self, blank):
        with pytest.raises(ValueError, match=r"prompt(\.action)? must not be empty"):
            normalize_video_prompt(blank)


class TestRenderStoryboardVideoPrompt:
    """文本形态的最终渲染出口：正文即提示词主体，drama 的发声声明由渲染层按 utterances 追加。"""

    ITEM: ClassVar[dict[str, object]] = {"utterances": [{"kind": "dialogue", "speaker": "王", "text": "你来了。"}]}
    CHARACTERS: ClassVar[dict[str, object]] = {"王": {"voice_style": "低沉沙哑"}}

    def _render(self, prompt: object, *, content_mode: str = "drama") -> str:
        return render_storyboard_video_prompt(
            prompt, self.ITEM, content_mode=content_mode, voice_characters=self.CHARACTERS
        )

    def test_text_form_body_leads_and_speech_sections_follow(self):
        rendered = self._render("镜头缓缓推近")
        assert rendered.startswith("镜头缓缓推近")
        assert "Voice_Style: 低沉沙哑" in rendered
        assert "Line: 你来了。" in rendered

    @pytest.mark.parametrize("prompt", ["镜头缓缓推近", {"action": "起身", "camera_motion": "Static"}])
    def test_both_forms_exclude_logos_and_keep_one_avoid_line_on_rerender(self, prompt):
        rendered = self._render(prompt)
        assert rendered.endswith("Avoid: BGM、文字字幕、水印、Logo")
        assert self._render(rendered) == rendered
        assert rendered.count("Avoid:") == 1

    @pytest.mark.parametrize(
        "legacy",
        [
            "镜头缓缓推近\n\nAvoid: BGM、文字字幕、水印",
            "Action: 起身\nCamera_Motion: Static\nAmbiance_Audio: ''\nAvoid: BGM、文字字幕、水印\n",
        ],
        ids=["text", "structured-preview"],
    )
    def test_legacy_avoid_line_without_logo_is_upgraded_on_rerender(self, legacy):
        """纯文本回贴中不含 Logo 的 Avoid 行由模版完整声明替换，不叠出第二行。"""
        rendered = self._render(legacy, content_mode="narration")
        assert rendered.count("Avoid:") == 1
        assert rendered.endswith("Avoid: BGM、文字字幕、水印、Logo")
        assert self._render(rendered, content_mode="narration") == rendered

    def test_a_hand_edited_avoid_line_is_replaced_by_the_shared_declaration(self):
        """排除项声明是按产出媒体共享的片段，正文里改写它不会被带到供应商——渲染出口只留那一份。"""
        rendered = self._render("镜头缓缓推近\n\nAvoid: BGM、文字字幕、水印、Logo、红色", content_mode="narration")

        assert rendered.count("Avoid:") == 1
        assert rendered.endswith("Avoid: BGM、文字字幕、水印、Logo")

    def test_speech_sections_appended_after_an_avoid_line_keep_the_declaration_last(self):
        """发声声明段追加在正文的 Avoid 行之后时，渲染出口仍把那一行归回末尾。"""
        rendered = self._render("镜头缓缓推近\n\nAvoid: BGM、文字字幕、水印、Logo")

        assert rendered.count("Avoid:") == 1
        assert rendered.endswith("Avoid: BGM、文字字幕、水印、Logo")
        assert "Line: 你来了。" in rendered

    def test_speech_sections_already_in_body_are_not_appended_twice(self):
        """结构化 → 文本以当前渲染结果为初值：正文已带发声声明段时不叠出第二份。"""
        seed = self._render({"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"})

        assert self._render(seed) == seed
        assert seed.count("Line: 你来了。") == 1
        assert seed.count("Voice_Style: 低沉沙哑") == 1

    def test_non_drama_text_form_gets_no_speech_sections(self):
        rendered = self._render("镜头缓缓推近", content_mode="narration")
        assert "Line:" not in rendered
