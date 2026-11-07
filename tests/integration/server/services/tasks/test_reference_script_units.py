"""Agent capabilities mark formal video units per read unit: read scripts only; one corrupt episode does not fail the entire query."""

from __future__ import annotations

import json
from pathlib import Path

from lib.project.project_manager import ProjectManager
from server.services.tasks.video_caps import reference_script_units


def _legacy_script(episode: int, unit_ids: list[str]) -> dict:
    """Pre-coercion script shape has durations on shots: writeback reads persist migrations, read-only reads do not."""
    return {
        "episode": episode,
        "title": f"E{episode}",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "x",
        "novel": {"title": "t", "chapter": "c"},
        "duration_seconds": 0,
        "video_units": [
            {"unit_id": unit_id, "shots": [{"duration": 8, "text": "镜头：空镜"}], "references": []}
            for unit_id in unit_ids
        ],
    }


def test_scripts_are_read_without_write_back_and_an_unreadable_episode_is_skipped(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "demo"
    (project_dir / "scripts").mkdir(parents=True)
    (project_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(_legacy_script(1, ["E1U1", "E1U2"]), ensure_ascii=False), encoding="utf-8"
    )
    (project_dir / "scripts" / "episode_2.json").write_text("{not json", encoding="utf-8")
    project = {
        "generation_mode": "reference_video",
        "episodes": [
            {"episode": 1, "script_file": "scripts/episode_1.json"},
            {"episode": 2, "script_file": "scripts/episode_2.json"},
            {"episode": 3, "script_file": "scripts/episode_3.json"},
        ],
    }

    script_paths = sorted((project_dir / "scripts").iterdir())
    before = {path: path.read_bytes() for path in script_paths}

    units = reference_script_units(ProjectManager(tmp_path), "demo", project)

    assert [unit["unit_id"] for unit in units] == ["E1U1", "E1U2"]
    assert {path: path.read_bytes() for path in script_paths} == before
