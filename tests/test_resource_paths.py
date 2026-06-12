"""资源路径解析器（模块 A）单元测试。

只断言外部行为：喂 resource_type + resource_id，断言项目内相对路径、扩展名、
未知类型的处理。纯函数，无 fixture、不读盘。
"""

from __future__ import annotations

import pytest

from lib.resource_paths import (
    RESOURCE_TYPES,
    resource_extension,
    resource_relative_path,
)


@pytest.mark.unit
class TestResourceRelativePath:
    @pytest.mark.parametrize(
        ("resource_type", "resource_id", "expected"),
        [
            # storyboards / videos 带 scene_ 前缀特例
            ("storyboards", "E1S01", "storyboards/scene_E1S01.png"),
            ("videos", "E1S01", "videos/scene_E1S01.mp4"),
            # audio 带 segment_ 前缀特例
            ("audio", "E1S01", "audio/segment_E1S01.wav"),
            # 其余无前缀
            ("characters", "姜月茴", "characters/姜月茴.png"),
            ("scenes", "庙宇", "scenes/庙宇.png"),
            ("props", "玉佩", "props/玉佩.png"),
            ("grids", "grid_abc", "grids/grid_abc.png"),
            ("reference_videos", "E1U1", "reference_videos/E1U1.mp4"),
        ],
    )
    def test_canonical_paths(self, resource_type: str, resource_id: str, expected: str) -> None:
        assert resource_relative_path(resource_type, resource_id) == expected

    def test_only_storyboards_and_videos_get_scene_prefix(self) -> None:
        # 反向断言：非 storyboards/videos 不得带 scene_ 前缀（audio 用 segment_ 前缀，单独验证）
        for rt in ("characters", "scenes", "props", "grids", "reference_videos"):
            assert "scene_" not in resource_relative_path(rt, "X")
        assert resource_relative_path("audio", "X").startswith("audio/segment_")

    def test_returns_posix_forward_slashes(self) -> None:
        # 跨平台：始终用 / 分隔，不受运行平台影响
        assert "\\" not in resource_relative_path("storyboards", "E1S01")

    def test_unknown_resource_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            resource_relative_path("unknown_type", "x")


@pytest.mark.unit
class TestResourceExtension:
    @pytest.mark.parametrize(
        ("resource_type", "expected"),
        [
            ("storyboards", ".png"),
            ("videos", ".mp4"),
            ("characters", ".png"),
            ("scenes", ".png"),
            ("props", ".png"),
            ("grids", ".png"),
            ("reference_videos", ".mp4"),
            ("audio", ".wav"),
        ],
    )
    def test_extensions(self, resource_type: str, expected: str) -> None:
        assert resource_extension(resource_type) == expected

    def test_unknown_resource_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            resource_extension("unknown_type")


@pytest.mark.unit
class TestResourceTypes:
    def test_covers_canonical_types(self) -> None:
        assert set(RESOURCE_TYPES) == {
            "storyboards",
            "videos",
            "audio",
            "characters",
            "scenes",
            "props",
            "products",
            "grids",
            "reference_videos",
        }
