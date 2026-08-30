import unicodedata

import pytest
import yaml

from lib.prompt_utils import (
    build_drama_video_prompt,
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    normalize_style,
    normalize_video_prompt,
    render_storyboard_video_prompt,
    utterances_to_dialogue,
    validate_camera_motion,
    validate_shot_type,
    video_prompt_to_yaml,
)


class TestNormalizeStyle:
    def test_strips_leading_huafeng_prefix(self):
        assert normalize_style("画风：真人电视剧风格，大师级构图") == "真人电视剧风格，大师级构图"

    def test_strips_halfwidth_colon_and_whitespace(self):
        assert normalize_style("  画风: 国风3D  ") == "国风3D"

    def test_idempotent_when_no_prefix(self):
        assert normalize_style("Anime") == "Anime"
        assert normalize_style("油画三渲二画风：参考双城之战") == "油画三渲二画风：参考双城之战"

    def test_empty_and_none_safe(self):
        assert normalize_style("") == ""
        assert normalize_style(None) == ""


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
        assert parsed["Style"] == "Anime"
        assert parsed["Scene"] == "夜雨中的街道"
        assert parsed["Composition"]["shot_type"] == "Medium Shot"

    def test_image_prompt_to_yaml_strips_legacy_huafeng_style(self):
        # 存量 project.json 的 style 带「画风：」前缀，注入 YAML 前兜底清理，避免 Style: 画风：叠加
        data = {"scene": "x", "composition": {"shot_type": "Medium Shot", "lighting": "", "ambiance": ""}}
        parsed = yaml.safe_load(image_prompt_to_yaml(data, "画风：真人电视剧风格"))
        assert parsed["Style"] == "真人电视剧风格"

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
            prompt = build_drama_video_prompt(_BASE_PROMPT, utterances, characters=characters)  # type: ignore[arg-type]
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
        from lib.script_models import Utterance

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
        # 词表从 lib.script_models 的 Literal 派生，扩充后的值应直接可校验
        assert validate_camera_motion("Orbit")
        assert not validate_camera_motion("Teleport")


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
        with pytest.raises(ValueError):
            normalize_video_prompt(blank)


class TestRenderStoryboardVideoPrompt:
    """文本形态的最终渲染出口：正文即提示词主体，drama 的发声声明由渲染层按 utterances 追加。"""

    ITEM = {"utterances": [{"kind": "dialogue", "speaker": "王", "text": "你来了。"}]}
    CHARACTERS = {"王": {"voice_style": "低沉沙哑"}}

    def _render(self, prompt: object, *, content_mode: str = "drama") -> str:
        return render_storyboard_video_prompt(
            prompt, self.ITEM, content_mode=content_mode, voice_characters=self.CHARACTERS
        )

    def test_text_form_body_leads_and_speech_sections_follow(self):
        rendered = self._render("镜头缓缓推近")
        assert rendered.startswith("镜头缓缓推近")
        assert "Voice_Style: 低沉沙哑" in rendered
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
