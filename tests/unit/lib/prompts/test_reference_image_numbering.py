"""Tests for reference_image_numbering."""

from pathlib import Path

import pytest

from lib.artifacts.visual_artifact_provenance import VisualReference
from lib.backends.image_errors import ImageCapabilityError
from lib.prompts.reference_image_numbering import (
    PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION,
    PREVIOUS_STORYBOARD_ROLE,
    mention_replacements,
    reference_images_declaration,
    render_reference_mentions,
    require_reference_image_limit,
)


def _sheet(asset_type: str, name: str) -> VisualReference:
    return VisualReference(
        path=Path(f"{name}.png"), role="asset_sheet", logical_type=asset_type, logical_id=name, kind="sheet"
    )


def _product(name: str, kind: str) -> VisualReference:
    return VisualReference(
        path=Path(f"{name}-{kind}.png"),
        role="asset_sheet" if kind == "sheet" else "source",
        logical_type="product",
        logical_id=name,
        kind=kind,
    )


_PREVIOUS = VisualReference(
    path=Path("prev.png"), role=PREVIOUS_STORYBOARD_ROLE, logical_type="storyboard", logical_id="E1S01"
)
_EXTRA = VisualReference(path=Path("extra.png"), role="extra_reference")


class TestReferenceImagesDeclaration:
    def test_numbers_follow_list_position_and_group_by_type(self):
        references = [_sheet("character", "林清"), _sheet("character", "沈茹/黑化"), _sheet("scene", "书房"), _PREVIOUS]
        assert reference_images_declaration(references) == (
            f"图1、图2为角色参考图；图3为场景参考图；图4为上一分镜图，{PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION}。"
        )

    def test_product_images_lead_and_carry_the_fidelity_clause(self):
        references = [_product("保温杯", "sheet"), _product("保温杯", "original"), _sheet("character", "Alice")]
        assert reference_images_declaration(references) == (
            "图1、图2为商品参考图，画面中的商品须与之完全一致；图3为角色参考图。"
        )

    def test_props_and_extra_images_have_their_own_types(self):
        assert reference_images_declaration([_sheet("prop", "怀表"), _EXTRA]) == "图1为道具参考图；图2为补充参考图。"

    def test_empty_references_render_nothing(self):
        assert reference_images_declaration([]) == ""


class TestMentionReplacements:
    def test_maps_registered_names_to_their_first_image(self):
        references = [_product("保温杯", "sheet"), _product("保温杯", "original"), _sheet("character", "林清")]
        assert mention_replacements(references) == {"保温杯": "图1", "林清": "图3"}

    def test_previous_storyboard_and_extra_images_are_not_addressable_by_name(self):
        assert mention_replacements([_PREVIOUS, _EXTRA]) == {}


class TestRenderReferenceMentions:
    def test_replaces_matching_mentions_and_renders_the_rest_as_bare_names(self):
        references = [_sheet("character", "林清"), _sheet("character", "沈茹/黑化"), _sheet("scene", "林家老宅·书房")]
        text = "@[林清]坐在窗边；@[沈茹/黑化]立在门口。桌面摊着一只褪色的@[怀表]，@[林家老宅·书房]的窗棂外雨丝密集。"
        assert (
            render_reference_mentions(text, references)
            == "图1坐在窗边；图2立在门口。桌面摊着一只褪色的怀表，图3的窗棂外雨丝密集。"
        )

    def test_malformed_mentions_stay_as_written(self):
        text = "@[林清 坐在窗边"
        assert render_reference_mentions(text, [_sheet("character", "林清")]) == text

    def test_text_without_mentions_is_untouched(self):
        text = "林清坐在窗边木桌前。"
        assert render_reference_mentions(text, [_sheet("character", "林清")]) == text


def test_excess_reports_each_asset_and_automatic_reference():
    references = [_sheet("character", "角色"), _sheet("prop", "怀表"), _PREVIOUS]
    with pytest.raises(ImageCapabilityError) as caught:
        require_reference_image_limit(references, 1, model="m")
    assert caught.value.code == "image_reference_images_exceeded"
    assert caught.value.params["names"] == "怀表, E1S01"
    assert caught.value.params["previous_storyboard"] == "E1S01"
    from lib.i18n import _

    for locale in ("en", "zh", "ru", "vi"):
        message = _(caught.value.code, locale=locale, **caught.value.params)
        assert message.count("E1S01") == 2
        assert "previous_storyboard" not in message
