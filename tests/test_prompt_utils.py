import yaml

from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    normalize_style,
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

    def test_validators(self):
        assert validate_shot_type("Close-up")
        assert not validate_shot_type("Bad Shot")
        assert validate_camera_motion("Zoom In")
        assert not validate_camera_motion("Teleport")
