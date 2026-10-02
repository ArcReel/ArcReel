"""规划产物的依据：集原文按账本记录的来源读取。"""

import json
from pathlib import Path

import pytest

from lib.artifacts.artifact_currency import resolve_current_artifact_basis
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.project.project_manager import ProjectManager
from lib.script import script_review


def _project(tmp_path: Path, *, source_origin: str) -> Path:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="narration")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script(
        "demo",
        {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": []},
        "episode_1.json",
    )
    pm.update_project("demo", lambda project: project["episodes"][0].update({"source_origin": source_origin}))
    project_dir = pm.get_project_path("demo")
    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    plan_path = script_review.script_plan_path(project_dir, project, 1)
    assert plan_path is not None
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps({"segments": []}), encoding="utf-8")
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "source" / "episode_1.txt").write_text("夜里，风吹过旷野。", encoding="utf-8")
    return project_dir


@pytest.mark.parametrize(("source_origin", "has_basis"), [("own", True), ("none", False)])
def test_script_plan_basis_reads_the_episode_file_only_when_the_ledger_records_a_source(
    tmp_path: Path, source_origin: str, has_basis: bool
) -> None:
    project_dir = _project(tmp_path, source_origin=source_origin)

    basis = resolve_current_artifact_basis(project_dir, ArtifactKey.episode_script_plan(1))

    assert (basis is not None) is has_basis
