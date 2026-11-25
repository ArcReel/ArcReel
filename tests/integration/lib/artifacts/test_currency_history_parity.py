"""Compare canonical currency across both on-disk history formats."""

import json
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import (
    activate_artifact_target_state,
    assert_artifact_target_state_plan_unchanged,
)
from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactKind
from lib.artifacts.artifact_planner import TargetStatePlanner
from lib.artifacts.version_manager import VersionManager
from lib.final_cut.service import FinalCutService
from lib.project.asset_derivatives import derivative_artifact_key, derivative_sheet_relative_path
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService
from server.services.presentation.timeline_units import TimelineUnitMaterials
from tests.integration.server.services.presentation.timeline_render_support import edited_timeline, setup_project
from tests.legacy_project_shapes import write_legacy_style_project


def _comparisons(project: Path):
    resolver = ArtifactCurrencyResolver(project)
    return {
        key: (resolver.compare(key, artifact_path=entry.artifact_path), resolver.resolve_basis(key))
        for key, entry in resolver.manifest_snapshot.entries.items()
    }


def _compare_formats(project: Path) -> set[ArtifactKey]:
    before = _comparisons(project)
    manager = VersionManager(project)
    records = json.loads(manager.versions_file.read_bytes())
    manager.versions_file.rename(manager.versions_file.with_suffix(".original"))
    manager.enable_sqlite_history(records)
    assert _comparisons(project) == before
    return set(before)


def test_visual_and_script_history_formats_agree(tmp_path: Path) -> None:
    project = write_legacy_style_project(tmp_path, schema_version=CURRENT_PROJECT_SCHEMA_VERSION)
    metadata = project / "project.json"
    data = json.loads(metadata.read_bytes())
    sheet = derivative_sheet_relative_path("阿离", "冬装")
    data["characters"]["阿离"]["derivatives"] = {"冬装": {"description": "围巾", "character_sheet": sheet}}
    metadata.write_text(json.dumps(data), encoding="utf-8")
    (project / sheet).parent.mkdir(parents=True)
    (project / sheet).write_bytes(b"winter")
    activate_artifact_target_state(project, bump_schema=False)

    keys = _compare_formats(project)
    assert derivative_artifact_key("阿离", "冬装") in keys
    assert {key.kind for key in keys} >= {
        ArtifactKind.EPISODE_SCRIPT_PLAN,
        ArtifactKind.EPISODE_SCRIPT,
        ArtifactKind.ASSET_SHEET,
        ArtifactKind.EPISODE_GRID,
        ArtifactKind.EPISODE_STORYBOARD,
    }


async def test_rendered_media_history_formats_agree(tmp_path: Path) -> None:
    manager, project = setup_project(tmp_path)
    timeline = await edited_timeline(manager)
    await TimelineJianyingDraftService(manager).render("demo", timeline, narration="with_narration")
    await FinalCutService(manager, unit_materials=TimelineUnitMaterials(manager)).render(
        "demo", timeline, narration="with_narration", subtitles="no_subtitles"
    )

    keys = _compare_formats(project)
    assert {key.kind for key in keys} >= {
        ArtifactKind.EPISODE_VIDEO,
        ArtifactKind.EPISODE_AUDIO,
        ArtifactKind.EPISODE_PRESENTATION,
        ArtifactKind.EPISODE_SUBTITLE,
        ArtifactKind.EPISODE_FINAL_CUT,
        ArtifactKind.EPISODE_JIANYING_DRAFT,
    }


@pytest.mark.parametrize("use_alias", [False, True])
def test_preflight_detects_history_revision(tmp_path: Path, monkeypatch, use_alias: bool) -> None:
    project = write_legacy_style_project(tmp_path, schema_version=CURRENT_PROJECT_SCHEMA_VERSION)
    manager = VersionManager(project)
    manager.versions_file.rename(manager.versions_file.with_suffix(".original"))
    manager.enable_sqlite_history()
    sheet = project / "characters" / "阿离.png"
    manager.add_version("characters", "阿离", "", sheet)
    manager.add_version("characters", "阿离", "", sheet)
    alias = tmp_path / "alias"
    if use_alias:
        try:
            alias.symlink_to(project, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            pytest.skip("Creating directory symlinks requires Windows symlink privileges")
    monkeypatch.chdir(tmp_path)
    relative = alias.relative_to(tmp_path) if use_alias else project.relative_to(tmp_path)
    plan = TargetStatePlanner(relative).plan()
    assert_artifact_target_state_plan_unchanged(relative, plan)
    manager.restore_version("characters", "阿离", 1, sheet)
    with pytest.raises(RuntimeError, match="dependency changed"):
        assert_artifact_target_state_plan_unchanged(relative, plan)
