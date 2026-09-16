from pathlib import Path

import pytest

from lib.prompt_builders import (
    build_character_derivative_prompt,
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
    render_storyboard_image_prompt,
)
from lib.reference_image_numbering import PREVIOUS_STORYBOARD_ROLE
from lib.visual_artifact_provenance import VisualReference


class TestCharacterPrompt:
    def test_includes_supplied_character_details(self):
        prompt = build_character_prompt(
            "姜月茴",
            "黑发，冷静神态。",
            style="古风",
            style_description="Cinematic, low-key lighting",
        )
        assert "姜月茴" in prompt
        assert "黑发，冷静神态。" in prompt
        assert "Style: 古风" in prompt
        assert "Visual style: Cinematic, low-key lighting" in prompt
        assert prompt.endswith("Avoid: 水印、多余文字、Logo")
        assert "Cinematic, low-key lighting" in prompt


class TestScenePromptAndPropPrompt:
    def test_prop_includes_supplied_details(self):
        prompt = build_prop_prompt("玉佩", "古朴温润")
        assert "玉佩" in prompt
        assert "古朴温润" in prompt

    def test_scene_includes_supplied_details(self):
        prompt = build_scene_prompt("祠堂", "昏暗古朴")
        assert "祠堂" in prompt
        assert "昏暗古朴" in prompt

    def test_empty_prop_guard_does_not_add_a_blank_paragraph(self):
        assert "\n\n\n" not in build_prop_prompt("玉佩", "古朴温润")


class TestFigureExclusion:
    """展示环境或物件的图种排除人物；画面主体本身是人物的图种不排除。"""

    # 断言完整片段而非「人物」二字：正文里的普通描述也可能出现该词，按关键词断言会误判。
    _EXCLUSION = "Avoid: 出镜人物"

    def test_environment_and_object_sheets_exclude_people(self):
        assert self._EXCLUSION in build_scene_prompt("祠堂", "昏暗古朴")
        assert self._EXCLUSION in build_prop_prompt("玉佩", "古朴温润")
        assert self._EXCLUSION in build_product_prompt("护手霜", "白色管装，哑光质感")

    def test_exclusion_survives_a_description_that_repeats_it(self):
        prompt = build_scene_prompt("祠堂", "昏暗古朴，无出镜人物、无声响。")
        assert prompt.endswith("Avoid: 出镜人物、水印、多余文字、Logo")

    def test_character_and_storyboard_keep_people(self):
        assert self._EXCLUSION not in build_character_prompt("张三", "短发青年")
        assert self._EXCLUSION not in render_storyboard_image_prompt("林清坐在窗边木桌前")


class TestStoryboardImageAvoidLine:
    def test_text_form_ends_with_one_image_avoid_line(self):
        assert (
            render_storyboard_image_prompt("林清坐在窗边木桌前") == "林清坐在窗边木桌前\n\nAvoid: 水印、多余文字、Logo"
        )

    def test_idempotent(self):
        once = render_storyboard_image_prompt("林清坐在窗边木桌前")
        assert render_storyboard_image_prompt(once) == once


def _sheet(asset_type: str, name: str) -> VisualReference:
    return VisualReference(
        path=Path(f"{name}.png"), role="asset_sheet", logical_type=asset_type, logical_id=name, kind="sheet"
    )


_PREVIOUS = VisualReference(
    path=Path("prev.png"), role=PREVIOUS_STORYBOARD_ROLE, logical_type="storyboard", logical_id="E1S02"
)

_SCENE = (
    "@[林清]坐在窗边木桌前，目光落在信纸上；@[沈茹/黑化]立在门口的阴影里。"
    "桌面摊着一只褪色的@[怀表]，@[林家老宅·书房]的木格窗棂外雨丝密集。"
)
_STRUCTURED = {
    "scene": _SCENE,
    "composition": {"shot_type": "Medium Shot", "lighting": "右侧落地窗逆光，蓝灰色调", "ambiance": "雨天，室内昏暗"},
}


class TestRenderStoryboardImagePrompt:
    """图N 编号由实际发出的参考图列表机械派生，对全部图像后端同一口径。"""

    @pytest.mark.parametrize("prompt", [_STRUCTURED, _SCENE])
    def test_both_forms_share_style_block_and_wrappers_survive_text_roundtrip(self, prompt):
        references = [_sheet("character", "林清")]
        rendered = render_storyboard_image_prompt(
            prompt, style="Anime", style_description="cinematic", references=references
        )
        assert rendered.startswith("Style: Anime\nVisual style: cinematic\nReference_Images:")
        assert (
            render_storyboard_image_prompt(
                rendered, style="Anime", style_description="cinematic", references=references
            )
            == rendered
        )
        for label in ("Style:", "Visual style:", "Reference_Images:", "Avoid:"):
            assert rendered.count(label) == 1

    def test_structured_prompt_declares_types_between_style_and_scene_and_numbers_mentions(self):
        references = [
            _sheet("character", "林清"),
            _sheet("character", "沈茹/黑化"),
            _sheet("scene", "林家老宅·书房"),
            _PREVIOUS,
        ]
        rendered = render_storyboard_image_prompt(_STRUCTURED, style="电影感写实，冷色调", references=references)
        assert rendered == (
            "Style: 电影感写实，冷色调\n"
            "Reference_Images: 图1、图2为角色参考图；图3为场景参考图；图4为上一分镜图，只参考构图与色调。\n"
            "Scene: 图1坐在窗边木桌前，目光落在信纸上；图2立在门口的阴影里。桌面摊着一只褪色的怀表，图3的木格窗棂外雨丝密集。\n"
            "Composition:\n"
            "  shot_type: Medium Shot\n"
            "  lighting: 右侧落地窗逆光，蓝灰色调\n"
            "  ambiance: 雨天，室内昏暗\n"
            "Avoid: 水印、多余文字、Logo"
        )

    def test_product_images_lead_the_numbering_and_replace_the_fidelity_tail(self):
        references = [
            VisualReference(
                path=Path("p.png"), role="asset_sheet", logical_type="product", logical_id="保温杯", kind="sheet"
            ),
            VisualReference(
                path=Path("o.jpg"), role="source", logical_type="product", logical_id="保温杯", kind="original"
            ),
            _sheet("character", "Alice"),
        ]
        rendered = render_storyboard_image_prompt(
            {
                "scene": "@[Alice]手持@[保温杯]特写",
                "composition": {"shot_type": "Close-up", "lighting": "", "ambiance": ""},
            },
            style="Anime",
            references=references,
        )
        assert "Reference_Images: 图1、图2为商品参考图，画面中的商品须与之完全一致；图3为角色参考图。\n" in rendered
        assert "Scene: 图3手持图1特写\n" in rendered
        assert "商品高保真还原" not in rendered

    def test_mentions_without_a_reference_image_render_as_bare_names(self):
        rendered = render_storyboard_image_prompt(_STRUCTURED, style="Anime", references=[_sheet("character", "林清")])
        assert "Reference_Images: 图1为角色参考图。\n" in rendered
        assert (
            "Scene: 图1坐在窗边木桌前，目光落在信纸上；沈茹/黑化立在门口的阴影里。桌面摊着一只褪色的怀表，林家老宅·书房的"
            in rendered
        )

    def test_without_references_there_is_no_declaration_and_mentions_stay_bare(self):
        rendered = render_storyboard_image_prompt(_STRUCTURED, style="Anime")
        assert "Reference_Images" not in rendered
        assert "Scene: 林清坐在窗边木桌前" in rendered

    def test_text_form_gets_the_same_declaration_and_replacement(self):
        references = [_sheet("character", "林清"), _PREVIOUS]
        rendered = render_storyboard_image_prompt(
            "@[林清]坐在窗边木桌前", style="Anime", style_description="cinematic", references=references
        )
        assert rendered == (
            "Style: Anime\n"
            "Visual style: cinematic\n"
            "Reference_Images: 图1为角色参考图；图2为上一分镜图，只参考构图与色调。\n\n"
            "图1坐在窗边木桌前\n\n"
            "Avoid: 水印、多余文字、Logo"
        )

    def test_rendering_a_rendered_text_again_is_idempotent(self):
        references = [_sheet("character", "林清"), _PREVIOUS]
        once = render_storyboard_image_prompt(
            _STRUCTURED, style="Anime", style_description="cinematic", references=references
        )
        assert (
            render_storyboard_image_prompt(once, style="Anime", style_description="cinematic", references=references)
            == once
        )


def test_product_sheet_preserves_product_and_ignores_project_style():
    prompt = build_product_prompt("护手霜", "白色管装", "水彩", "柔和笔触")
    assert "商品「护手霜」的标准资产图。" in prompt
    assert "logo、文字、配色、材质、比例与结构不得改变或臆造" in prompt
    assert "包装上印刷的人像图案属于商品外观，须原样保留。" in prompt
    assert "水彩" not in prompt
    assert "柔和笔触" not in prompt
    assert prompt.endswith("Avoid: 出镜人物、水印、多余文字、Logo")


def test_derivative_keeps_reference_layout_and_has_no_style_block():
    prompt = build_character_derivative_prompt("衣服变为黑色")
    assert prompt.startswith("衣服变为黑色")
    assert "保持原图的三视图版式" in prompt
    assert "Style:" not in prompt
    assert prompt.endswith("Avoid: 水印、多余文字、Logo")
