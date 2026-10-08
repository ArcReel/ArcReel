"""多集项目夹具：每集一个分镜，分镜图与视频齐备并补录进产物清单。"""

from __future__ import annotations

from pathlib import Path

from lib.episode.episode_ledger import SOURCE_FINGERPRINTS_KEY, compute_source_fingerprints
from lib.episode.episode_sources import discover_sources
from lib.infra.json_io import atomic_write_json
from lib.project.project_manager import ProjectManager
from tests.integration.lib.workflow.test_workflow_state import (
    _complete_episode_media,
    _register_produced_artifacts,
    _valid_narration_segment,
    _write_episode_source,
    _write_registered_script,
    _write_source,
)


def episodes_with_media(pm: ProjectManager, project_path: Path, count: int) -> None:
    """``count`` 集，每集一个分镜，分镜图与视频齐备（视频带版本记录）并补录进清单。"""

    source_text = "完整原文"
    _write_source(pm, project_path, source_text)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {"episode": episode, "script_file": f"scripts/episode_{episode}.json", "ledger_status": "planned"}
            for episode in range(1, count + 1)
        ]
        project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path, project))

    pm.update_project("demo", _plan)
    for episode in range(1, count + 1):
        draft_dir = project_path / "drafts" / f"episode_{episode}"
        draft_dir.mkdir(parents=True, exist_ok=True)
        _write_episode_source(project_path, episode)
        atomic_write_json(draft_dir / "script_plan_segments.json", {"episode": episode, "segments": []})
        resource_id = f"E{episode}S01"
        _write_registered_script(
            project_path,
            {
                "episode": episode,
                "title": f"第{episode}集",
                "content_mode": "narration",
                "segments": [
                    _valid_narration_segment(
                        segment_id=resource_id,
                        generated_assets=_complete_episode_media(project_path, resource_id),
                    )
                ],
            },
            episode=episode,
            filename=f"episode_{episode}.json",
        )
    _register_produced_artifacts(project_path)
