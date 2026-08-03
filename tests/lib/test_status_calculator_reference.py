"""Ensure StatusCalculator computes episode stats for reference_video projects.

路线是项目级事实，剧本不携带戳——各用例把项目路线显式传给计分/注入入口。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from lib.status_calculator import StatusCalculator


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    return ProjectManager(tmp_path)


def _mk_reference_script(units_total: int, units_done: int) -> dict:
    units = []
    for i in range(units_total):
        has_video = i < units_done
        units.append(
            {
                "unit_id": f"E1U{i + 1}",
                "shots": [{"text": f"u{i}"}],
                "references": [],
                "duration_seconds": 3,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {
                    "storyboard_image": None,
                    "storyboard_last_image": None,
                    "grid_id": None,
                    "grid_cell_index": None,
                    "video_clip": f"reference_videos/E1U{i + 1}.mp4" if has_video else None,
                    "video_uri": None,
                    "status": "completed" if has_video else "pending",
                },
            }
        )
    return {
        "episode": 1,
        "title": "E1",
        "content_mode": "narration",
        "duration_seconds": 0,
        "summary": "",
        "novel": {"title": "t", "chapter": "c"},
        "video_units": units,
    }


def test_calculate_episode_stats_reference_video_all_ready(pm: ProjectManager) -> None:
    calc = StatusCalculator(pm)
    stats = calc.calculate_episode_stats(
        "proj", _mk_reference_script(units_total=3, units_done=3), generation_mode="reference_video"
    )
    assert stats["status"] == "completed"
    assert stats["units_count"] == 3
    assert stats["videos"] == {"total": 3, "completed": 3}
    # storyboards stays zeroed — reference mode does not produce storyboards
    assert stats["storyboards"] == {"total": 3, "completed": 0}
    assert stats["duration_seconds"] == 9


def test_calculate_episode_stats_reference_video_partial(pm: ProjectManager) -> None:
    calc = StatusCalculator(pm)
    stats = calc.calculate_episode_stats(
        "proj", _mk_reference_script(units_total=3, units_done=1), generation_mode="reference_video"
    )
    assert stats["status"] == "in_production"
    assert stats["videos"] == {"total": 3, "completed": 1}


def test_calculate_episode_stats_reference_video_empty_draft(pm: ProjectManager) -> None:
    calc = StatusCalculator(pm)
    stats = calc.calculate_episode_stats(
        "proj", _mk_reference_script(units_total=0, units_done=0), generation_mode="reference_video"
    )
    assert stats["status"] == "draft"
    assert stats["units_count"] == 0
    assert stats["duration_seconds"] == 0


def test_calculate_episode_stats_reference_video_tolerates_corrupt_generated_assets(
    pm: ProjectManager,
) -> None:
    """generated_assets 为非 dict 脏数据（如字符串）时按缺失处理，不抛 AttributeError。"""
    calc = StatusCalculator(pm)
    script = _mk_reference_script(units_total=2, units_done=1)
    script["video_units"][0]["generated_assets"] = "corrupt"
    stats = calc.calculate_episode_stats("proj", script, generation_mode="reference_video")
    assert stats["status"] == "draft"
    assert stats["videos"] == {"total": 2, "completed": 0}


def test_enrich_script_reference_video_aggregates_references(pm: ProjectManager) -> None:
    """enrich_script must collect @character/@scene/@prop references from units."""
    calc = StatusCalculator(pm)
    script = {
        "episode": 1,
        "title": "E1",
        "content_mode": "narration",
        "duration_seconds": 0,
        "summary": "",
        "novel": {"title": "t", "chapter": "c"},
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"text": "x"}],
                "references": [
                    {"type": "character", "name": "张三"},
                    {"type": "scene", "name": "酒馆"},
                ],
                "duration_seconds": 3,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {
                    "storyboard_image": None,
                    "storyboard_last_image": None,
                    "grid_id": None,
                    "grid_cell_index": None,
                    "video_clip": None,
                    "video_uri": None,
                    "status": "pending",
                },
            },
            {
                "unit_id": "E1U2",
                "shots": [{"text": "y"}],
                "references": [
                    {"type": "character", "name": "张三"},  # duplicate — should dedupe
                    {"type": "prop", "name": "长剑"},
                ],
                "duration_seconds": 5,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {
                    "storyboard_image": None,
                    "storyboard_last_image": None,
                    "grid_id": None,
                    "grid_cell_index": None,
                    "video_clip": None,
                    "video_uri": None,
                    "status": "pending",
                },
            },
        ],
    }
    enriched = calc.enrich_script(script, generation_mode="reference_video")
    assert enriched["characters_in_episode"] == ["张三"]
    assert enriched["scenes_in_episode"] == ["酒馆"]
    assert enriched["props_in_episode"] == ["长剑"]
    assert enriched["duration_seconds"] == 8
