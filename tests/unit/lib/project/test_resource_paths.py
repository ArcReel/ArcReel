"""Resource path resolver (module A) unit tests.

Assert only external behavior: supply resource_type + resource_id and check project-relative paths, extensions,
and unknown-type handling. Pure functions without fixtures or disk reads.
"""

from __future__ import annotations

import pytest

from lib.project.resource_paths import (
    RESOURCE_TYPES,
    resource_extension,
    resource_of_path,
    resource_relative_path,
)


class TestResourceRelativePath:
    @pytest.mark.parametrize(
        ("resource_type", "resource_id", "expected"),
        [
            # storyboards / videos special-case the scene_ prefix.
            ("storyboards", "E1S01", "storyboards/scene_E1S01.png"),
            ("end_frames", "E1S01", "end_frames/scene_E1S01.png"),
            ("videos", "E1S01", "videos/scene_E1S01.mp4"),
            # audio special-cases the segment_ prefix.
            ("audio", "E1S01", "audio/segment_E1S01.wav"),
            # Others have no prefix.
            ("characters", "姜月茴", "characters/姜月茴.png"),
            ("scenes", "庙宇", "scenes/庙宇.png"),
            ("props", "玉佩", "props/玉佩.png"),
            ("grids", "grid_abc", "grids/grid_abc.png"),
            ("reference_videos", "E1U1", "reference_videos/E1U1.mp4"),
        ],
    )
    def test_canonical_paths(self, resource_type: str, resource_id: str, expected: str) -> None:
        assert resource_relative_path(resource_type, resource_id) == expected

    def test_only_shot_keyed_types_get_scene_prefix(self) -> None:
        # Inverse assertion: types not named by shot IDs must not carry scene_; audio uses segment_, checked separately.
        for rt in ("characters", "scenes", "props", "grids", "reference_videos"):
            assert "scene_" not in resource_relative_path(rt, "X")
        assert resource_relative_path("audio", "X").startswith("audio/segment_")

    def test_returns_posix_forward_slashes(self) -> None:
        # Cross-platform: always separate with /, independent of host platform.
        assert "\\" not in resource_relative_path("storyboards", "E1S01")

    def test_unknown_resource_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match=r"不支持的资源类型: unknown_type"):
            resource_relative_path("unknown_type", "x")


class TestResourceExtension:
    @pytest.mark.parametrize(
        ("resource_type", "expected"),
        [
            ("storyboards", ".png"),
            ("end_frames", ".png"),
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
        with pytest.raises(ValueError, match=r"不支持的资源类型: unknown_type"):
            resource_extension("unknown_type")


class TestResourceTypes:
    def test_covers_canonical_types(self) -> None:
        assert set(RESOURCE_TYPES) == {
            "storyboards",
            "end_frames",
            "videos",
            "audio",
            "characters",
            "scenes",
            "props",
            "products",
            "grids",
            "reference_videos",
            "character_derivatives",
        }


class TestResourceOfPath:
    @pytest.mark.parametrize(
        ("rel", "expected"),
        [
            ("storyboards/scene_E1S01.png", ("storyboards", "E1S01")),
            ("audio/segment_E1S01.wav", ("audio", "E1S01")),
            ("characters/姜月茴.png", ("characters", "姜月茴")),
            # Derivatives have deeper subdirectories; match them before characters.
            ("characters/derivatives/姜月茴/少年.png", ("character_derivatives", "姜月茴/少年")),
            # Nonmatching path shapes are not current files for any resource.
            ("characters/derivatives/少年.png", None),
            ("videos/E1S01.mp4", None),
            ("storyboards/scene_E1S01.jpg", None),
            ("notes.txt", None),
        ],
    )
    def test_inverts_resource_relative_path(self, rel: str, expected: tuple[str, str] | None) -> None:
        assert resource_of_path(rel) == expected
