"""剧情演绎项目缺 ``aspect_ratio``：分镜图依据按项目比例规则取 16:9，经整条迁移链升级后的制作状态。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.visual_artifact_provenance import build_storyboard_image_visual_basis
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.workflow.workflow_state import WorkflowStateService
from tests.legacy_project_shapes import write_legacy_drama_storyboard_project

_STORYBOARD = ArtifactKey.episode_storyboard(1, "E1S01")
_STORYBOARD_PATH = "storyboards/scene_E1S01.png"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _status(project_dir: Path) -> str:
    return ArtifactCurrencyResolver(project_dir).compare(_STORYBOARD, artifact_path=_STORYBOARD_PATH).status.value


def _storyboard_counts(data_root: Path, project_dir: Path) -> tuple[int, int]:
    summary = WorkflowStateService(ProjectManager(data_root)).get_project_summary(project_dir.name)
    storyboards = summary.episodes[0].storyboards
    return storyboards.available, storyboards.stale


def test_storyboard_of_a_legacy_drama_project_without_ratio_is_current_after_upgrade(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = write_legacy_drama_storyboard_project(root)

    assert migrate_project_dir(project_dir) is True

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert "aspect_ratio" not in project
    assert _status(project_dir) == "current"
    assert _storyboard_counts(tmp_path, project_dir) == (1, 0)


def test_storyboard_registered_with_a_portrait_basis_reads_stale(tmp_path: Path) -> None:
    """缺比例的剧情项目按 16:9 判定分镜图依据；清单里按 9:16 登记的分镜图读为过期，并计入制作状态。"""

    root = tmp_path / "projects"
    project_dir = write_legacy_drama_storyboard_project(root)
    migrate_project_dir(project_dir)
    project = _read_json(project_dir / "project.json")
    item = _read_json(project_dir / "scripts" / "episode_1.json")["scenes"][0]
    portrait_basis = build_storyboard_image_visual_basis(
        resource_id="E1S01",
        image_prompt=item["image_prompt"],
        style=project["style"],
        style_description=project["style_description"],
        aspect_ratio="9:16",
    )
    ProjectArtifactManifestAdapter(project_dir).put_entry(
        _STORYBOARD, ArtifactManifestEntry(artifact_path=_STORYBOARD_PATH, basis_digest=portrait_basis.digest)
    )

    assert _status(project_dir) == "stale"
    assert _storyboard_counts(tmp_path, project_dir) == (1, 1)
