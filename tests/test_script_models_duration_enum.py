"""duration_seconds 枚举硬约束：剧本生成时把每个分镜时长卡在视频模型 supported_durations 内。

剧本生成器把 supported_durations 作为 response_schema 的 enum 下发，LLM 结构化输出层即被
卡死，且 model_validate 时强制成员校验——而非仅靠 prompt 文字软约束 + 执行层晚失败。
"""

import pytest
from pydantic import BaseModel, ValidationError

from lib.script_models import (
    build_ad_reference_episode_script_model,
    build_episode_script_model,
    build_reference_video_script_model,
)


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


class TestReferenceVideoModel:
    """参考视频模式：约束的是 unit 总时长（各 shot 之和），不是单个 shot。"""

    def _unit_payload(self, *, shots: list[int], total: int) -> dict:
        return {
            "title": "第一集",
            "video_units": [
                {
                    "unit_id": "E1U01",
                    "shots": [{"duration": d, "text": f"@甲 动作 {i}"} for i, d in enumerate(shots)],
                    "references": [{"type": "character", "name": "甲"}],
                    "duration_seconds": total,
                }
            ],
        }

    def test_unit_total_rendered_as_enum(self):
        model = build_reference_video_script_model([4, 6, 8])
        schema = model.model_json_schema()
        unit_def = next(d for d in schema["$defs"].values() if "shots" in d.get("properties", {}))
        assert unit_def["properties"]["duration_seconds"].get("enum") == [4, 6, 8]

    def test_sum_in_set_accepted(self):
        model = build_reference_video_script_model([4, 6, 8])
        validated = model.model_validate(self._unit_payload(shots=[4, 4], total=8))
        assert validated.video_units[0].duration_seconds == 8

    def test_sum_out_of_set_rejected(self):
        """各 shot（3+4=7）合法（1-15），但和 7 不是 supported 成员——必须拒。"""
        model = build_reference_video_script_model([4, 6, 8])
        with pytest.raises(ValidationError):
            model.model_validate(self._unit_payload(shots=[3, 4], total=7))

    def test_total_must_equal_shot_sum_preserved(self):
        """既有一致性校验保留：total=8 但 shots 之和=6 仍拒（即便 8 是 supported 成员）。"""
        model = build_reference_video_script_model([4, 6, 8])
        with pytest.raises(ValidationError):
            model.model_validate(self._unit_payload(shots=[6], total=8))

    def test_empty_supported_durations_raises(self):
        with pytest.raises(ValueError):
            build_reference_video_script_model([])


class TestAdReferenceModel:
    """ad + reference_video 路径：镜头时长 1-15 自由整数（非 supported_durations 枚举）。"""

    def _payload(self, duration: int) -> dict:
        return {
            "title": "短片",
            "shots": [
                {
                    "shot_id": "E1S01",
                    "section": "hook",
                    "duration_seconds": duration,
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

    def test_free_integers_within_range_accepted(self):
        model = build_ad_reference_episode_script_model()
        for duration in (1, 5, 7, 15):
            validated = model.model_validate(self._payload(duration))
            assert validated.shots[0].duration_seconds == duration

    @pytest.mark.parametrize("duration", [0, 16, 60])
    def test_out_of_range_rejected(self, duration):
        model = build_ad_reference_episode_script_model()
        with pytest.raises(ValidationError):
            model.model_validate(self._payload(duration))

    def test_schema_renders_range_not_enum(self):
        field_schema = _duration_field_schema(build_ad_reference_episode_script_model())
        assert "enum" not in field_schema
        assert field_schema.get("minimum") == 1
        assert field_schema.get("maximum") == 15
