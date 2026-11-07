"""Hard duration_seconds enums constrain generated storyboard durations to video-model supported_durations.

The generator supplies supported_durations as response_schema enum, constraining LLM structured output,
and model_validate enforces membership instead of relying only on soft prompt text and late execution failure.
"""

import pytest
from pydantic import BaseModel, ValidationError

from lib.script.script_models import build_episode_script_model


def _duration_enum(model: type[BaseModel]) -> list[int] | None:
    """Extract duration_seconds enum from model JSON-schema $defs, or None if absent."""
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
        """Constrained schemas must no longer contain old ge/le ranges (minimum/maximum)."""
        field_schema = _duration_field_schema(build_episode_script_model("narration", [4, 6, 8]))
        assert "minimum" not in field_schema
        assert "maximum" not in field_schema

    def test_durations_deduped_and_sorted(self):
        model = build_episode_script_model("narration", [8, 4, 6, 4])
        assert _duration_enum(model) == [4, 6, 8]

    def test_single_value_uses_const(self):
        field_schema = _duration_field_schema(build_episode_script_model("narration", [8]))
        # Pydantic renders singleton sets as const, still a hard constraint.
        assert field_schema.get("const") == 8 or field_schema.get("enum") == [8]

    def test_empty_supported_durations_raises(self):
        with pytest.raises(ValueError, match=r"supported_durations 为空"):
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
        """5 lies within [1,60] but outside supported_durations; old ge/le allowed it, while enums must reject it."""
        model = build_episode_script_model("narration", [4, 6, 8])
        with pytest.raises(ValidationError):
            model.model_validate(self._narration_payload(5))

    def test_digit_string_duration_coerced_to_int(self):
        """Gemini responseSchema supports string enums only; wire conversion makes duration enums strings,
        so constrained decoding returns "6" and parsing/revalidation mechanically restores int to match Literal."""
        model = build_episode_script_model("narration", [4, 6, 8])
        payload = self._narration_payload(6)
        payload["segments"][0]["duration_seconds"] = "6"
        validated = model.model_validate(payload)
        assert validated.segments[0].duration_seconds == 6

    def test_out_of_set_digit_string_rejected(self):
        """Coercion restores types only, not relaxed membership; converted string "5" remains rejected by the enum."""
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
        """Ad storyboard paths also enforce supported_durations enums without falling into drama shapes."""
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


class TestReferenceUnitsScriptPlanModel:
    """build_reference_units_script_plan_model: flat output plus hard unit-duration enums."""

    def _model(self):
        from lib.script.script_models import build_reference_units_script_plan_model

        return build_reference_units_script_plan_model([4, 6, 8])

    def _unit(self, **overrides) -> dict:
        unit = {"duration_seconds": 6, "source_text": "他起身。", "text": "镜头1：@[甲] 起身"}
        unit.update(overrides)
        return unit

    def test_flat_shape_with_duration_enum(self):
        schema = self._model().model_json_schema()
        unit_def = next(d for d in schema.get("$defs", {}).values() if "text" in d.get("properties", {}))
        assert unit_def["properties"]["duration_seconds"]["enum"] == [4, 6, 8]
        # Keep mechanically derivable fields out of LLM output schemas.
        assert set(unit_def["properties"]) == {"duration_seconds", "source_text", "text"}

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
