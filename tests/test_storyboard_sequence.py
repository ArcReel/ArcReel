from pathlib import Path

import pytest

from lib.storyboard_sequence import (
    build_storyboard_dependency_plan,
    get_storyboard_items,
    resolve_previous_storyboard_path,
)


class TestStoryboardSequence:
    def test_get_storyboard_items_supports_narration_and_drama(self):
        narration = {"content_mode": "narration", "segments": [{"segment_id": "E1S01"}]}
        drama = {"content_mode": "drama", "scenes": [{"scene_id": "E1S01"}]}

        narration_items = get_storyboard_items(narration)
        drama_items = get_storyboard_items(drama)

        assert narration_items[1:] == (
            "segment_id",
            "characters_in_segment",
            "scenes",
            "props",
        )
        assert drama_items[1:] == (
            "scene_id",
            "characters_in_scene",
            "scenes",
            "props",
        )

    def test_resolve_previous_storyboard_path_respects_first_item_and_segment_break(self, tmp_path: Path):
        project_path = tmp_path / "demo"
        (project_path / "storyboards").mkdir(parents=True)
        previous_path = project_path / "storyboards" / "scene_E1S01.png"
        previous_path.write_bytes(b"png")

        items = [
            {"segment_id": "E1S01", "segment_break": False},
            {"segment_id": "E1S02", "segment_break": False},
            {"segment_id": "E1S03", "segment_break": True},
        ]

        assert resolve_previous_storyboard_path(project_path, items, "segment_id", "E1S01") is None
        assert resolve_previous_storyboard_path(project_path, items, "segment_id", "E1S02") == previous_path
        assert resolve_previous_storyboard_path(project_path, items, "segment_id", "E1S03") is None

    def test_resolve_previous_storyboard_path_does_not_backtrack(self, tmp_path: Path):
        project_path = tmp_path / "demo"
        (project_path / "storyboards").mkdir(parents=True)
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")

        items = [
            {"segment_id": "E1S01", "segment_break": False},
            {"segment_id": "E1S02", "segment_break": False},
            {"segment_id": "E1S03", "segment_break": False},
        ]

        assert resolve_previous_storyboard_path(project_path, items, "segment_id", "E1S03") is None

    def test_build_storyboard_dependency_plan_groups_contiguous_ranges(self):
        items = [
            {"segment_id": "E1S01", "segment_break": False},
            {"segment_id": "E1S02", "segment_break": False},
            {"segment_id": "E1S03", "segment_break": True},
            {"segment_id": "E1S04", "segment_break": False},
            {"segment_id": "E1S05", "segment_break": False},
        ]

        plans = build_storyboard_dependency_plan(
            items,
            "segment_id",
            ["E1S01", "E1S02", "E1S03", "E1S04"],
            "episode_1.json",
        )

        assert [(plan.resource_id, plan.dependency_resource_id, plan.dependency_index) for plan in plans] == [
            ("E1S01", None, 0),
            ("E1S02", "E1S01", 1),
            ("E1S03", None, 0),
            ("E1S04", "E1S03", 1),
        ]
        assert plans[0].dependency_group == plans[1].dependency_group
        assert plans[2].dependency_group == plans[3].dependency_group
        assert plans[0].dependency_group != plans[2].dependency_group

    def test_build_storyboard_dependency_plan_starts_new_group_when_selection_has_gap(self):
        items = [
            {"scene_id": "E1S01", "segment_break": False},
            {"scene_id": "E1S02", "segment_break": False},
            {"scene_id": "E1S03", "segment_break": False},
            {"scene_id": "E1S04", "segment_break": False},
        ]

        plans = build_storyboard_dependency_plan(
            items,
            "scene_id",
            ["E1S01", "E1S03", "E1S04"],
            "episode_1.json",
        )

        assert [(plan.resource_id, plan.dependency_resource_id) for plan in plans] == [
            ("E1S01", None),
            ("E1S03", None),
            ("E1S04", "E1S03"),
        ]


class TestAdStoryboardItems:
    def test_ad_script_resolves_shots_with_chars_field(self):
        from lib.storyboard_sequence import get_storyboard_items

        script = {
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01", "characters_in_shot": ["主播"]}],
        }
        items, id_field, char_field, scenes_field, props_field = get_storyboard_items(script)
        assert id_field == "shot_id"
        assert char_field == "characters_in_shot"
        assert (scenes_field, props_field) == ("scenes", "props")
        assert items[0]["shot_id"] == "E1S01"


# 骨架种类 → 触发该骨架的最小剧本（取证解析 / reference 短路各覆盖）。
_SCRIPT_BY_KIND = {
    "segments": {"content_mode": "narration", "segments": [{"segment_id": "E1S01"}]},
    "scenes": {"content_mode": "drama", "scenes": [{"scene_id": "E1S01"}]},
    "shots": {"content_mode": "ad", "shots": [{"shot_id": "E1S01"}]},
    "video_units": {"generation_mode": "reference_video", "video_units": [{"unit_id": "E1U01"}]},
}


class TestStoryboardSkeletonExhaustiveness:
    """穷尽性断言：get_storyboard_items 的 char_field 覆盖 SKELETONS 全部键。

    char_field 直接查 SKELETONS，第五种骨架加入时 _SCRIPT_BY_KIND 未登记即报红。
    """

    @pytest.mark.parametrize("kind", list(_SCRIPT_BY_KIND))
    def test_get_storyboard_items_char_field_matches_registry(self, kind):
        from lib.script_skeleton import SKELETONS

        # 遍历 SKELETONS 全键：新增第五种骨架而 _SCRIPT_BY_KIND 未登记即 KeyError 报红。
        assert set(_SCRIPT_BY_KIND) == set(SKELETONS)

        _items, _id_field, char_field, _scenes, _props = get_storyboard_items(_SCRIPT_BY_KIND[kind])
        assert char_field == SKELETONS[kind].chars_field
