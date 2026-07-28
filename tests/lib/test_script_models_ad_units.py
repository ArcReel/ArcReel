"""ad 剧本模型的 reference_units 索引字段（lib/script_models.py）单测。"""

import pydantic
import pytest

from lib.script_models import AdEpisodeScript


def _shot_dict(shot_id: str) -> dict:
    return {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": 3,
        "voiceover_text": "口播",
        "products_in_shot": [],
        "image_prompt": {
            "scene": "画面",
            "composition": {"shot_type": "Close-up", "lighting": "自然光", "ambiance": "明亮"},
        },
        "video_prompt": {"action": "动作", "camera_motion": "Static", "ambiance_audio": "", "dialogue": []},
    }


def _script(units: list[dict] | None) -> dict:
    data: dict = {"title": "短片", "shots": [_shot_dict("E1S1"), _shot_dict("E1S2")]}
    if units is not None:
        data["reference_units"] = units
    return data


def test_script_without_index_still_validates():
    script = AdEpisodeScript.model_validate(_script(None))
    assert script.reference_units is None


def test_script_with_derived_index_validates():
    units = [
        {
            "unit_id": "E1U1",
            "shot_ids": ["E1S1", "E1S2"],
            "references": [{"type": "product", "name": "按摩仪"}, {"type": "character", "name": "小美"}],
        }
    ]

    script = AdEpisodeScript.model_validate(_script(units))

    assert script.reference_units is not None
    unit = script.reference_units[0]
    assert unit.shot_ids == ["E1S1", "E1S2"]
    assert unit.references[0].type == "product"
    assert unit.generated_assets.status == "pending"


def test_unit_entry_rejects_unknown_fields():
    units = [{"unit_id": "E1U1", "shot_ids": ["E1S1"], "references": [], "shots": [{"duration": 3}]}]

    with pytest.raises(pydantic.ValidationError):
        AdEpisodeScript.model_validate(_script(units))


def test_unit_entry_rejects_more_than_four_shot_ids():
    units = [{"unit_id": "E1U1", "shot_ids": [f"E1S{n}" for n in range(1, 6)], "references": []}]

    with pytest.raises(pydantic.ValidationError):
        AdEpisodeScript.model_validate(_script(units))


def test_index_hidden_from_llm_schema():
    schema = AdEpisodeScript.model_json_schema()
    assert "reference_units" not in schema.get("properties", {})
