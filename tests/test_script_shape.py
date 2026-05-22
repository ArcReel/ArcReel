"""剧本形状分派（模块 B）单元测试。

只断言外部行为：喂 content_mode，断言返回的字段名三元组
（列表字段 / 每项 id 字段 / 角色字段）正确。
"""

from __future__ import annotations

import pytest

from lib.script_models import SCRIPT_SHAPES, script_shape


@pytest.mark.unit
class TestScriptShape:
    def test_narration(self) -> None:
        shape = script_shape("narration")
        assert (shape.items_key, shape.id_field, shape.chars_field) == (
            "segments",
            "segment_id",
            "characters_in_segment",
        )

    def test_drama(self) -> None:
        shape = script_shape("drama")
        assert (shape.items_key, shape.id_field, shape.chars_field) == (
            "scenes",
            "scene_id",
            "characters_in_scene",
        )

    def test_non_narration_maps_to_drama(self) -> None:
        # 忠实于现状二分 `"segments" if content_mode == "narration" else "scenes"`：
        # 只有 "narration" 走 narration 形状，其余一切（含未知值）落 drama。
        assert script_shape("???") == SCRIPT_SHAPES["drama"]
        assert script_shape("drama") == SCRIPT_SHAPES["drama"]

    def test_registry_covers_narration_and_drama(self) -> None:
        assert set(SCRIPT_SHAPES) == {"narration", "drama"}
