"""Agent 能力载荷逐单元标注读取的正式视频单元：只读取剧本，单集损坏不拖垮整份能力查询。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.project.project_manager import ProjectManager
from server.services.tasks.video_caps import reference_script_units


def _legacy_script(episode: int, unit_ids: list[str]) -> dict:
    """收编前的剧本形状（时长挂在镜头上）：写回式读取会把迁移结果落盘，只读读取不会。"""
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
    project_dir = tmp_path / "demo"
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
