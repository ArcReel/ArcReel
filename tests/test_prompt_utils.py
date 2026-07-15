import yaml

from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    normalize_style,
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
