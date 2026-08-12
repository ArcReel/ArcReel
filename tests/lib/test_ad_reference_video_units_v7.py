"""广告参考路线共用自包含 video_units 的模型与解析契约。"""

import pydantic
import pytest

from lib.reference_video.shot_parser import derive_references_from_text
from lib.script_models import ReferenceVideoScript, ReferenceVideoUnit
from lib.script_skeleton import ensure_route_skeleton, resolve_declared_kind
from lib.speech_composition import SpeechComposition, SpeechMode, adapt_video_unit

pytestmark = pytest.mark.unit


def _unit(**overrides: object) -> dict:
    payload: dict = {
        "unit_id": "E1U1",
        "shots": [{"text": "@[产品] 放在 @[场景] 的桌面上"}],
        "references": [{"type": "product", "name": "产品"}, {"type": "scene", "name": "场景"}],
        "duration_seconds": 8,
        "generated_assets": {},
    }
    payload.update(overrides)
    return payload


def test_ad_reference_route_declares_video_units_while_storyboard_stays_shots() -> None:
    assert resolve_declared_kind("ad", "reference_video") == "video_units"
    assert resolve_declared_kind("ad", "storyboard") == "shots"
    assert ensure_route_skeleton({"content_mode": "ad", "video_units": []}, "ad", "reference_video") == "video_units"


def test_reference_video_script_accepts_ad_products_and_replan_state() -> None:
    script = ReferenceVideoScript.model_validate(
        {"title": "广告", "content_mode": "ad", "video_units": [_unit(needs_replan=False)]}
    )

    assert script.content_mode == "ad"
    assert script.video_units[0].references[0].type == "product"
    assert script.video_units[0].needs_replan is False


def test_only_replan_shell_may_be_empty_and_zero_duration() -> None:
    shell = ReferenceVideoUnit.model_validate(
        _unit(
            shots=[],
            references=[],
            duration_seconds=0,
            needs_replan=True,
            migration_requires_content_replan=True,
        )
    )
    assert shell.needs_replan is True
    assert shell.migration_requires_content_replan is True

    with pytest.raises(pydantic.ValidationError):
        ReferenceVideoUnit.model_validate(_unit(shots=[], references=[], duration_seconds=0, needs_replan=False))
    with pytest.raises(pydantic.ValidationError):
        ReferenceVideoUnit.model_validate(_unit(shots=[], references=[], duration_seconds=8, needs_replan=True))
    with pytest.raises(pydantic.ValidationError):
        ReferenceVideoUnit.model_validate(_unit(migration_requires_content_replan=True, needs_replan=False))


def test_product_mentions_resolve_first_even_in_corrupt_duplicate_namespace() -> None:
    project = {
        "products": {"同名": {}},
        "characters": {"同名": {}},
        "scenes": {"同名": {}},
        "props": {"同名": {}},
    }
    refs, missing = derive_references_from_text("镜头1：@[同名] 位于桌面", project)

    assert [(ref.type, ref.name) for ref in refs] == [("product", "同名")]
    assert missing == []


def test_product_label_before_colon_is_not_misparsed_as_character_speech() -> None:
    unit = _unit(shots=[{"text": "@[产品]：瓶身正面朝向镜头"}])
    result = SpeechComposition.prepare(adapt_video_unit(unit))

    assert result.mode is SpeechMode.SILENT
    assert result.problems == ()
