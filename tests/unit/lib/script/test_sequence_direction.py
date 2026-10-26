import copy
import json

import pytest
from pydantic import ValidationError

from lib.script.script_models import build_drama_normalized_script_model
from lib.script.sequence_direction import SequenceDirection, render_sequence_context
from tests.factories import make_sequence_direction, make_shot_continuity


def test_generated_plan_requires_direction_and_shot_continuity_but_legacy_loads() -> None:
    legacy = {
        "title": "A visitor",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 4,
                "characters_in_scene": [],
                "scene_description": "A rustle interrupts the walk.",
            }
        ],
    }
    loaded = build_drama_normalized_script_model([4]).model_validate(legacy).model_dump()
    assert "sequence_direction" not in loaded
    assert "continuity" not in loaded["scenes"][0]
    generated = build_drama_normalized_script_model([4], require_direction=True)
    with pytest.raises(ValidationError) as missing:
        generated.model_validate(legacy)
    assert {error["loc"] for error in missing.value.errors()} == {("sequence_direction",), ("scenes", 0, "continuity")}
    candidate = copy.deepcopy(legacy)
    candidate["sequence_direction"] = make_sequence_direction()
    candidate["scenes"][0]["continuity"] = make_shot_continuity()
    assert generated.model_validate(candidate).model_dump()["sequence_direction"] == make_sequence_direction()
    schema = generated.model_json_schema()
    assert "sequence_direction" in schema["required"]
    assert "continuity" in schema["$defs"]["DirectedDramaSceneContent"]["required"]


def test_blank_or_unknown_directing_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SequenceDirection.model_validate({**make_sequence_direction(), "geography": "  "})
    with pytest.raises(ValidationError):
        SequenceDirection.model_validate({**make_sequence_direction(), "mandatory_backpack_side": "left"})


def test_selected_shot_receives_both_neighbors_without_distant_shots_or_media() -> None:
    script = {
        "sequence_direction": make_sequence_direction(),
        "scenes": [
            {
                "scene_id": f"E1S0{n}",
                "scene_description": f"Action {n}",
                "continuity": make_shot_continuity(),
                "generated_assets": {"video": "private.mp4"},
            }
            for n in range(1, 6)
        ],
    }
    context = render_sequence_context(script, {"E1S03"})
    content = json.loads(context[context.index('{"sequence_direction"') :])
    assert [scene["scene_id"] for scene in content["adjacent_coverage"]] == ["E1S02", "E1S03", "E1S04"]
    assert "private.mp4" not in context
    assert content["sequence_direction"] == script["sequence_direction"]
