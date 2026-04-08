import yaml

from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    validate_camera_motion,
    validate_shot_type,
    validate_style,
    video_prompt_to_yaml,
)


class TestPromptUtils:
    def test_image_prompt_to_yaml_keeps_expected_shape(self):
        data = {
            "scene": "rainy night street",
            "composition": {
                "shot_type": "Medium Shot",
                "lighting": "warm street lamp",
                "ambiance": "thin mist",
            },
        }

        text = image_prompt_to_yaml(data, "Anime")
        parsed = yaml.safe_load(text)
        assert parsed["Style"] == "Anime"
        assert parsed["Scene"] == "rainy night street"
        assert parsed["Composition"]["shot_type"] == "Medium Shot"

    def test_video_prompt_to_yaml_includes_dialogue_conditionally(self):
        with_dialogue = {
            "action": "looks up to observe",
            "camera_motion": "Static",
            "ambiance_audio": "rain sound",
            "dialogue": [{"speaker": "Jiang Yuehui", "line": "Is anyone there"}],
        }
        without_dialogue = {
            "action": "walks quickly forward",
            "camera_motion": "Pan Left",
            "ambiance_audio": "footstep sound",
            "dialogue": [],
        }

        parsed_a = yaml.safe_load(video_prompt_to_yaml(with_dialogue))
        parsed_b = yaml.safe_load(video_prompt_to_yaml(without_dialogue))

        assert parsed_a["Action"] == "looks up to observe"
        assert parsed_a["Dialogue"][0]["Speaker"] == "Jiang Yuehui"
        assert "Dialogue" not in parsed_b

    def test_structured_checks(self):
        assert is_structured_image_prompt({"scene": "x"})
        assert not is_structured_image_prompt("text")
        assert is_structured_video_prompt({"action": "x"})
        assert not is_structured_video_prompt([])

    def test_validators(self):
        assert validate_style("Anime")
        assert not validate_style("Unknown")
        assert validate_shot_type("Close-up")
        assert not validate_shot_type("Bad Shot")
        assert validate_camera_motion("Zoom In")
        assert not validate_camera_motion("Teleport")
