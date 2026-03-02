from pathlib import Path

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
            "clues_in_segment",
        )
        assert drama_items[1:] == (
            "scene_id",
            "characters_in_scene",
            "clues_in_scene",
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
