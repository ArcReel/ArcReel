"""duration_seconds 枚举硬约束：剧本生成时把每个分镜时长卡在视频模型 supported_durations 内。

剧本生成器把 supported_durations 作为 response_schema 的 enum 下发，LLM 结构化输出层即被
卡死，且 model_validate 时强制成员校验——而非仅靠 prompt 文字软约束 + 执行层晚失败。
"""

import pytest
from pydantic import BaseModel, ValidationError

from lib.script_models import build_episode_script_model

pytestmark = pytest.mark.unit


def _duration_enum(model: type[BaseModel]) -> list[int] | None:
    """从模型 JSON schema 的 $defs 里取出 duration_seconds 的 enum（无则 None）。"""
    schema = model.model_json_schema()
    for definition in schema.get("$defs", {}).values():
        props = definition.get("properties", {})
        if "duration_seconds" in props:
            return props["duration_seconds"].get("enum")
    return None


def _duration_field_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    for definition in schema.get("$defs", {}).values():
        props = definition.get("properties", {})
        if "duration_seconds" in props:
            return props["duration_seconds"]
    raise AssertionError("未在 $defs 中找到 duration_seconds 字段")


class TestBuildEpisodeScriptModel:
    def test_narration_duration_rendered_as_enum(self):
        model = build_episode_script_model("narration", [4, 6, 8])
        assert _duration_enum(model) == [4, 6, 8]

    def test_drama_duration_rendered_as_enum(self):
        model = build_episode_script_model("drama", [4, 6, 8])
        assert _duration_enum(model) == [4, 6, 8]

    def test_ad_duration_rendered_as_enum(self):
        model = build_episode_script_model("ad", [4, 6, 8])
        assert _duration_enum(model) == [4, 6, 8]

    def test_enum_replaces_open_range(self):
        """约束后的 schema 不应再带原 ge/le 区间（minimum/maximum）。"""
        field_schema = _duration_field_schema(build_episode_script_model("narration", [4, 6, 8]))
        assert "minimum" not in field_schema
        assert "maximum" not in field_schema

    def test_durations_deduped_and_sorted(self):
        model = build_episode_script_model("narration", [8, 4, 6, 4])
        assert _duration_enum(model) == [4, 6, 8]

    def test_single_value_uses_const(self):
        field_schema = _duration_field_schema(build_episode_script_model("narration", [8]))
        # 单值集 Pydantic 渲染为 const（仍是硬约束）
        assert field_schema.get("const") == 8 or field_schema.get("enum") == [8]

    def test_empty_supported_durations_raises(self):
        with pytest.raises(ValueError):
            build_episode_script_model("narration", [])


class TestConstrainedValidation:
    def _narration_payload(self, duration: int) -> dict:
        return {
            "title": "第一集",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": duration,
                    "segment_break": False,
                    "novel_text": "原文",
                    "characters_in_segment": ["甲"],
                    "image_prompt": {
                        "scene": "场景",
                        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                    },
                    "video_prompt": {
                        "action": "转身",
                        "camera_motion": "Static",
                        "ambiance_audio": "风声",
                        "dialogue": [],
                    },
                }
            ],
        }

    def test_in_set_duration_accepted(self):
        model = build_episode_script_model("narration", [4, 6, 8])
        validated = model.model_validate(self._narration_payload(6))
        assert validated.segments[0].duration_seconds == 6

    def test_out_of_set_duration_rejected(self):
        """5 在 [1,60] 区间内但不是 supported_durations 成员——旧 ge/le 约束会放过，枚举约束必须拒。"""
        model = build_episode_script_model("narration", [4, 6, 8])
        with pytest.raises(ValidationError):
            model.model_validate(self._narration_payload(5))

    def test_digit_string_duration_coerced_to_int(self):
        """Gemini responseSchema 通道的 enum 仅支持字符串，时长枚举 wire 层转为字符串枚举，
        约束解码下模型输出 "6"——解析/复验侧机械强转恢复 int 并命中 Literal。"""
        model = build_episode_script_model("narration", [4, 6, 8])
        payload = self._narration_payload(6)
        payload["segments"][0]["duration_seconds"] = "6"
        validated = model.model_validate(payload)
        assert validated.segments[0].duration_seconds == 6

    def test_out_of_set_digit_string_rejected(self):
        """强转仅是类型恢复，不放宽成员约束：字符串 "5" 强转后仍被枚举拒绝。"""
        model = build_episode_script_model("narration", [4, 6, 8])
        payload = self._narration_payload(6)
        payload["segments"][0]["duration_seconds"] = "5"
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    def test_non_digit_string_rejected(self):
        model = build_episode_script_model("narration", [4, 6, 8])
        payload = self._narration_payload(6)
        payload["segments"][0]["duration_seconds"] = "six"
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    def test_drama_out_of_set_duration_rejected(self):
        model = build_episode_script_model("drama", [4, 6, 8])
        payload = {
            "title": "第一集",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 7,
                    "segment_break": False,
                    "characters_in_scene": ["甲"],
                    "image_prompt": {
                        "scene": "场景",
                        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                    },
                    "video_prompt": {
                        "action": "转身",
                        "camera_motion": "Static",
                        "ambiance_audio": "风声",
                        "dialogue": [],
                    },
                }
            ],
        }
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    def test_ad_out_of_set_duration_rejected(self):
        """ad 走 storyboard 路径时同样按 supported_durations 硬枚举，不落 drama 形状。"""
        model = build_episode_script_model("ad", [4, 6, 8])
        payload = {
            "title": "短片",
            "shots": [
                {
                    "shot_id": "E1S01",
                    "section": "hook",
                    "duration_seconds": 5,
                    "voiceover_text": "口播",
                    "image_prompt": {
                        "scene": "场景",
                        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                    },
                    "video_prompt": {
                        "action": "转身",
                        "camera_motion": "Static",
                        "ambiance_audio": "风声",
                        "dialogue": [],
                    },
                }
            ],
        }
        with pytest.raises(ValidationError):
            model.model_validate(payload)
        payload["shots"][0]["duration_seconds"] = 6
        validated = model.model_validate(payload)
        assert validated.shots[0].duration_seconds == 6


class TestReferenceUnitsStep1Model:
    """build_reference_units_step1_model：扁平产出 + unit 时长枚举硬约束。"""

    def _model(self):
        from lib.script_models import build_reference_units_step1_model

        return build_reference_units_step1_model([4, 6, 8])

    def _unit(self, **overrides) -> dict:
        unit = {"duration_seconds": 6, "source_text": "他起身。", "text": "镜头1：@[甲] 起身"}
        unit.update(overrides)
        return unit

    def test_flat_shape_with_duration_enum(self):
        schema = self._model().model_json_schema()
        unit_def = next(d for d in schema.get("$defs", {}).values() if "text" in d.get("properties", {}))
        assert unit_def["properties"]["duration_seconds"]["enum"] == [4, 6, 8]
        # 机械可派生的字段一律不进 LLM 输出 schema
        assert set(unit_def["properties"]) == {"duration_seconds", "source_text", "text", "keyframe_plan"}

    def test_member_duration_accepted(self):
        draft = self._model().model_validate({"units": [self._unit()]})
        assert draft.model_dump()["units"][0]["duration_seconds"] == 6

    def test_out_of_set_duration_rejected(self):
        with pytest.raises(ValidationError):
            self._model().model_validate({"units": [self._unit(duration_seconds=5)]})

    def test_empty_units_rejected(self):
        with pytest.raises(ValidationError):
            self._model().model_validate({"units": []})

    def test_blank_required_text_rejected(self):
        with pytest.raises(ValidationError):
            self._model().model_validate({"units": [self._unit(text="")]})
        with pytest.raises(ValidationError):
            self._model().model_validate({"units": [self._unit(source_text="")]})
