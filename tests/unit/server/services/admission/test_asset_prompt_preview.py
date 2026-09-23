"""资产草稿描述经执行期模板渲染，不写项目数据。"""

import pytest

from server.services.admission.asset_prompt_preview import render_asset_prompt


@pytest.mark.parametrize("asset_type", ["character", "scene", "prop"])
def test_render_includes_draft_and_project_style(asset_type):
    result = render_asset_prompt(asset_type, "阿岚", "银色衣裳", "水墨", "淡墨留白")

    assert result.unavailable is None
    assert result.text is not None
    assert "银色衣裳" in result.text
    assert "阿岚" in result.text
    assert "水墨" in result.text
    assert "淡墨留白" in result.text


@pytest.mark.parametrize("asset_type", ["product", "character_derivative"])
def test_product_and_derivative_keep_execution_style_policy(asset_type):
    result = render_asset_prompt(asset_type, "阿岚", "  银色衣裳  ", "水墨", "淡墨留白")

    assert result.text is not None
    assert "银色衣裳" in result.text
    assert "水墨" not in result.text
    assert "淡墨留白" not in result.text
    if asset_type == "character_derivative":
        assert "保持原图的三视图版式" in result.text
        assert "角色的面部、发型、体型及其余外观一律与原图保持一致" in result.text


@pytest.mark.parametrize(
    ("asset_type", "description", "reason"),
    [("character", "  ", "prompt_preview_missing"), ("unknown", "描述", "prompt_preview_invalid")],
)
def test_unavailable_result_reuses_preview_reason_codes(asset_type, description, reason):
    result = render_asset_prompt(asset_type, "阿岚", description)

    assert result.text is None
    assert result.unavailable == reason
    assert result.is_text_form is True
    assert result.warnings == ()
