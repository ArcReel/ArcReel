"""Tests for asset_prompt_preview."""

import pytest

from lib.prompt_builders import (
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
)
from server.services.asset_prompt_preview import AssetNotFound, preview_asset_prompt
from tests.integration.server.services.generation_tasks_support import _FakePM, prepare_files


@pytest.mark.parametrize(
    ("asset_type", "resource_name", "description", "builder"),
    [
        ("character", "Alice", "hero", build_character_prompt),
        ("scene", "祠堂", "temple", build_scene_prompt),
        ("prop", "玉佩", "jade", build_prop_prompt),
        ("product", "保温杯", "不锈钢保温杯", build_product_prompt),
    ],
)
async def test_preview_matches_execution_prompt_builder(tmp_path, asset_type, resource_name, description, builder):
    """预览文本与执行期（generation_tasks._prepare* 内部调用同一 build_*_prompt）逐字一致。"""
    project_path = prepare_files(tmp_path)
    pm = _FakePM(project_path)

    preview = await preview_asset_prompt("demo", asset_type, resource_name, projects=pm)

    assert preview.asset_type == asset_type
    assert preview.resource_id == resource_name
    assert preview.prompt == builder(resource_name, description, "Anime", "cinematic")


async def test_preview_raises_asset_not_found_for_unknown_resource(tmp_path):
    project_path = prepare_files(tmp_path)
    pm = _FakePM(project_path)

    with pytest.raises(AssetNotFound):
        await preview_asset_prompt("demo", "character", "不存在", projects=pm)
