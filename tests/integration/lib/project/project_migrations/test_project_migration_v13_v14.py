"""v13→v14：遗留风格值归一、依据补记风格描述后，既有产物不因升级翻过期，本就过期的不被伪造成时新。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_planner import TargetStatePlanner
from lib.artifacts.artifact_version_provenance import (
    IMAGE_ARTIFACT_BASIS_FIELD,
    parse_image_version_basis,
    parse_typed_media_version_target,
)
from lib.artifacts.media_artifact_currency import build_current_video_artifact_basis
from lib.artifacts.version_manager import VersionManager
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.prompts.style_templates import resolve_template_prompt
from lib.workflow.workflow_state import WorkflowStateService
from tests.legacy_project_shapes import (
    advance_project_schema,
    write_legacy_reference_video_project,
    write_legacy_style_project,
    write_undescribed_style_bases_project,
)

_NORMALIZED_STYLE = "写实电影感"
_PREFIXED_STYLE = f"画风：{_NORMALIZED_STYLE}"

_CHARACTER_SHEET = ArtifactKey.asset_sheet("character", "阿离")
_SCENE_SHEET = ArtifactKey.asset_sheet("scene", "雨巷")
_GRID = ArtifactKey.episode_grid(1, "grid_123456789abc")
_GRID_MEMBER = ArtifactKey.episode_storyboard(1, "E1S01")
_STORYBOARD = ArtifactKey.episode_storyboard(1, "E1S03")
_SCRIPT = ArtifactKey.episode_script(1)


def _entries(project_dir: Path) -> Mapping[ArtifactKey, ArtifactManifestEntry]:
    return ProjectArtifactManifestAdapter(project_dir).snapshot_entries()


def _status(project_dir: Path, key: ArtifactKey) -> str:
    entry = _entries(project_dir)[key]
    return ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=entry.artifact_path).status.value


def _is_current_before_migration(project_dir: Path, key: ArtifactKey) -> bool:
    """迁移前的时新性判定。读模型只服务当前 schema 的项目，此处直接比对登记与目标态。"""
    return _entries(project_dir).get(key) == TargetStatePlanner(project_dir).plan().entries.get(key)


def _read_project(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _write_project(project_dir: Path, project: dict) -> None:
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_project_at_v13(root: Path, *, style: str = _PREFIXED_STYLE, name: str = "legacy") -> Path:
    """一个停在 v13、产物齐全的遗留风格项目，单张分镜图的依据按归一后的风格值登记。

    单张分镜图的依据在风格值进入摘要前先剥前缀，存量清单里它的摘要因此已是归一形态；风格值
    已归一的孪生项目算出的登记与之逐字相同，用它补上这一事实。资产图、宫格（含宫格切分出的
    分镜图）与剧本的依据记的是带前缀的原始值，保持构造器写出的形状。
    """

    project_dir = write_legacy_style_project(root, name, style=style)
    advance_project_schema(project_dir, to_version=13)
    twin = write_legacy_style_project(root, f"{name}-twin", style=_NORMALIZED_STYLE)
    advance_project_schema(twin, to_version=13)
    ProjectArtifactManifestAdapter(project_dir).put_entry(_STORYBOARD, _entries(twin)[_STORYBOARD])
    return project_dir


def test_prefixed_style_is_normalized_across_the_whole_chain(tmp_path: Path) -> None:
    project_dir = write_legacy_style_project(tmp_path / "projects", style=_PREFIXED_STYLE)

    assert migrate_project_dir(project_dir) is True

    project = _read_project(project_dir)
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert project["style"] == _NORMALIZED_STYLE
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.registered["asset-sheet"] == 2


def test_legacy_short_label_resolves_to_its_template_across_the_whole_chain(tmp_path: Path) -> None:
    project_dir = write_legacy_style_project(tmp_path / "projects", style="Photographic")

    assert migrate_project_dir(project_dir) is True

    project = _read_project(project_dir)
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert project["style_template_id"] == "live_premium_drama"
    assert project["style"] == resolve_template_prompt("live_premium_drama")


def test_storyboard_basis_digest_survives_the_cleanup(tmp_path: Path) -> None:
    project_dir = _legacy_project_at_v13(tmp_path / "projects")
    before = _entries(project_dir)[_STORYBOARD]

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[_STORYBOARD] == before
    assert _status(project_dir, _STORYBOARD) == "current"


def test_asset_sheets_and_grids_stay_current_after_the_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = _legacy_project_at_v13(root)
    for key in (_CHARACTER_SHEET, _SCENE_SHEET, _GRID, _GRID_MEMBER):
        assert _is_current_before_migration(project_dir, key)

    migrate_project_dir(project_dir)

    for key in (_CHARACTER_SHEET, _SCENE_SHEET, _GRID, _GRID_MEMBER):
        assert _status(project_dir, key) == "current"
    summary = WorkflowStateService(ProjectManager(root)).get_project_summary(project_dir.name)
    assert (summary.assets["character"].available, summary.assets["character"].stale) == (1, 0)
    assert (summary.assets["scene"].available, summary.assets["scene"].stale) == (1, 0)
    assert (summary.episodes[0].storyboards.available, summary.episodes[0].storyboards.stale) == (3, 0)


def test_asset_sheet_basis_digest_is_rebased_onto_the_normalized_style(tmp_path: Path) -> None:
    """资产图依据记的是原始风格值，归一必然改摘要——保住时新性靠改写登记，不靠摘要不变。"""

    project_dir = _legacy_project_at_v13(tmp_path / "projects")
    before = _entries(project_dir)[_CHARACTER_SHEET]

    migrate_project_dir(project_dir)

    after = _entries(project_dir)[_CHARACTER_SHEET]
    assert after.basis_digest != before.basis_digest
    assert after.artifact_path == before.artifact_path


def test_already_stale_artifact_is_not_made_current(tmp_path: Path) -> None:
    project_dir = _legacy_project_at_v13(tmp_path / "projects")
    project = _read_project(project_dir)
    project["scenes"]["雨巷"]["description"] = "改过的场景描述"
    _write_project(project_dir, project)
    assert not _is_current_before_migration(project_dir, _SCENE_SHEET)
    before = _entries(project_dir)[_SCENE_SHEET]

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[_SCENE_SHEET] == before
    assert _status(project_dir, _SCENE_SHEET) == "stale"
    assert _status(project_dir, _CHARACTER_SHEET) == "current"


def test_non_style_dependency_change_between_plans_aborts_without_rebasing(tmp_path: Path) -> None:
    project_dir = _legacy_project_at_v13(tmp_path / "projects")
    before = _entries(project_dir)[_SCRIPT]
    original_plan = TargetStatePlanner.plan
    calls = 0

    def plan_with_dependency_change(planner: TargetStatePlanner):
        nonlocal calls
        result = original_plan(planner)
        calls += 1
        if calls == 1:
            (project_dir / "drafts" / "episode_1" / "script_plan_segments.json").write_text(
                json.dumps({"segments": [{"novel_text": "非风格输入已改变"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
        return result

    with (
        patch.object(TargetStatePlanner, "plan", plan_with_dependency_change),
        pytest.raises(RuntimeError, match="dependency changed after preflight"),
    ):
        migrate_project_dir(project_dir)

    assert _entries(project_dir)[_SCRIPT] == before
    assert _read_project(project_dir)["schema_version"] == 13


_GRID_MEMBERS = (ArtifactKey.episode_storyboard(1, "E1S01"), ArtifactKey.episode_storyboard(1, "E1S02"))
_REFERENCE_VIDEOS = (ArtifactKey.episode_video(1, "E1U01"), ArtifactKey.episode_video(1, "E1U02"))


def _set_style_description(project_dir: Path, description: str) -> None:
    project = _read_project(project_dir)
    project["style_description"] = description
    _write_project(project_dir, project)


def _read_versions(project_dir: Path) -> dict:
    return json.loads((project_dir / "versions" / "versions.json").read_text(encoding="utf-8"))


def _add_grid_version_record(project_dir: Path, *, style_description: str = "") -> None:
    """宫格版本记录冻结的依据；描述为空时是 v13 时期与清单登记同一口径的形态，非空时按 v14 口径记描述。"""

    project = {
        **_read_project(project_dir),
        "style_description": style_description,
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
    }
    planner = TargetStatePlanner(project_dir, project_bytes=json.dumps(project, ensure_ascii=False).encode("utf-8"))
    planner.plan()
    versions = _read_versions(project_dir)
    snapshot = "versions/grids/grid_123456789abc_v1_20260101T000000.png"
    (project_dir / snapshot).parent.mkdir(parents=True, exist_ok=True)
    (project_dir / snapshot).write_bytes(b"composite")
    versions["grids"]["grid_123456789abc"] = {
        "current_version": 1,
        "versions": [
            {
                "version": 1,
                "file": snapshot,
                "prompt": "grid",
                "created_at": "2026-01-01T00:00:00Z",
                IMAGE_ARTIFACT_BASIS_FIELD: planner.bases[_GRID].to_evidence_dict(),
            }
        ],
    }
    (project_dir / "versions" / "versions.json").write_text(json.dumps(versions, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("schema_version", [12, 13])
def test_described_grids_stay_current_and_bases_that_already_tracked_the_description_stay_stale(
    tmp_path: Path, schema_version: int
) -> None:
    root = tmp_path / "projects"
    project_dir = write_undescribed_style_bases_project(root, "grid", route="grid", schema_version=schema_version)
    _add_grid_version_record(project_dir)
    before = _entries(project_dir)

    assert migrate_project_dir(project_dir) is True

    after = _entries(project_dir)
    for key in (_GRID, *_GRID_MEMBERS):
        assert after[key].basis_digest != before[key].basis_digest
        assert after[key].artifact_path == before[key].artifact_path
        assert _status(project_dir, key) == "current"
    record = _read_versions(project_dir)["grids"]["grid_123456789abc"]["versions"][0]
    assert parse_image_version_basis("grids", "grid_123456789abc", record).digest == after[_GRID].basis_digest
    if schema_version == 12:
        # v12→v13 的整份激活把在场产物一律登记为时新，资产图与单张分镜图的过期标记不跨这一步保留。
        return
    # 资产图与单张分镜图的依据一向记描述：描述出现在它们登记之后，迁移前就过期，迁移不改写。
    for key in (_CHARACTER_SHEET, _STORYBOARD):
        assert after[key] == before[key]
        assert _status(project_dir, key) == "stale"
    summary = WorkflowStateService(ProjectManager(root)).get_project_summary(project_dir.name)
    assert (summary.episodes[0].storyboards.available, summary.episodes[0].storyboards.stale) == (3, 1)


def test_prefixed_style_and_description_are_rebased_together(tmp_path: Path) -> None:
    project_dir = write_undescribed_style_bases_project(
        tmp_path / "projects", "grid", route="grid", style=_PREFIXED_STYLE
    )

    _add_grid_version_record(project_dir)

    migrate_project_dir(project_dir)

    assert _read_project(project_dir)["style"] == _NORMALIZED_STYLE
    for key in (_GRID, *_GRID_MEMBERS):
        assert _status(project_dir, key) == "current"
    record = _read_versions(project_dir)["grids"]["grid_123456789abc"]["versions"][0]
    assert (
        parse_image_version_basis("grids", "grid_123456789abc", record).digest
        == _entries(project_dir)[_GRID].basis_digest
    )


@pytest.mark.parametrize(
    ("schema_version", "style"),
    [(12, _NORMALIZED_STYLE), (13, _NORMALIZED_STYLE), (13, _PREFIXED_STYLE)],
)
def test_described_reference_videos_stay_current_in_workflow_and_player(
    tmp_path: Path, schema_version: int, style: str
) -> None:
    root = tmp_path / "projects"
    project_dir = write_undescribed_style_bases_project(
        root, "reference", route="reference_video", style=style, schema_version=schema_version
    )
    before = _entries(project_dir)

    migrate_project_dir(project_dir)

    after = _entries(project_dir)
    project = _read_project(project_dir)
    script = json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    versions = _read_versions(project_dir)
    for key in _REFERENCE_VIDEOS:
        resource_id = str(key.components[-1])
        assert after[key].basis_digest != before[key].basis_digest
        assert _status(project_dir, key) == "current"
        record = versions["reference_videos"][resource_id]["versions"][0]
        frozen = parse_typed_media_version_target("reference_videos", record).basis
        # 播放器的「最新 / 比当前内容旧」标签按冻结依据与当前依据是否一致判断。
        current = build_current_video_artifact_basis(
            project_path=project_dir,
            project=project,
            script=script,
            resource_type="reference_videos",
            resource_id=resource_id,
            versions=VersionManager(project_dir),
            version_metadata=record,
        )
        assert frozen == current
        assert frozen.digest == after[key].basis_digest
    summary = WorkflowStateService(ProjectManager(root)).get_project_summary(project_dir.name)
    assert (summary.episodes[0].videos.available, summary.episodes[0].videos.stale) == (2, 0)


def test_rewriting_the_description_after_migration_makes_grids_and_reference_videos_stale(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    grid_project = write_undescribed_style_bases_project(root, "grid", route="grid")
    reference_project = write_undescribed_style_bases_project(root, "reference", route="reference_video")
    migrate_project_dir(grid_project)
    migrate_project_dir(reference_project)

    _set_style_description(grid_project, "换了参考图后重新分析的描述")
    _set_style_description(reference_project, "换了参考图后重新分析的描述")

    for key in (_GRID, *_GRID_MEMBERS):
        assert _status(grid_project, key) == "stale"
    for key in _REFERENCE_VIDEOS:
        assert _status(reference_project, key) == "stale"


@pytest.mark.parametrize("route", ["grid", "reference_video"])
def test_normalized_project_without_description_keeps_every_byte(tmp_path: Path, route: str) -> None:
    root = tmp_path / "projects"
    if route == "grid":
        project_dir = write_legacy_style_project(root, style=_NORMALIZED_STYLE, style_description="")
    else:
        project_dir = write_legacy_reference_video_project(root, style_description="")
    advance_project_schema(project_dir, to_version=13)
    project_before = _read_project(project_dir)
    manifest_before = (project_dir / ".arcreel_artifacts.json").read_bytes()
    versions_before = (project_dir / "versions" / "versions.json").read_bytes()

    with patch.object(TargetStatePlanner, "plan", side_effect=AssertionError("风格值已归一且描述为空不应规划目标态")):
        advance_project_schema(project_dir, to_version=14)

    assert _read_project(project_dir) == {**project_before, "schema_version": 14}
    assert (project_dir / ".arcreel_artifacts.json").read_bytes() == manifest_before
    assert (project_dir / "versions" / "versions.json").read_bytes() == versions_before


def test_already_stale_grid_member_is_not_made_current_by_the_description_rebase(tmp_path: Path) -> None:
    project_dir = write_undescribed_style_bases_project(tmp_path / "projects", "grid", route="grid")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"][1]["image_prompt"]["scene"] = "改过的第二格画面"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    stale_member = _GRID_MEMBERS[1]
    before = _entries(project_dir)

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[stale_member] == before[stale_member]
    assert _entries(project_dir)[_GRID] == before[_GRID]
    assert _status(project_dir, stale_member) == "stale"
    assert _status(project_dir, _GRID) == "stale"
    assert _status(project_dir, _GRID_MEMBERS[0]) == "current"


def test_failed_manifest_commit_rolls_back_version_records(tmp_path: Path) -> None:
    project_dir = write_undescribed_style_bases_project(tmp_path / "projects", "reference", route="reference_video")
    versions_before = (project_dir / "versions" / "versions.json").read_bytes()
    manifest_before = _entries(project_dir)

    with (
        patch.object(
            ProjectArtifactManifestAdapter,
            "replace_entries_if_matches_atomically",
            side_effect=OSError("manifest write failed"),
        ),
        pytest.raises(OSError, match="manifest write failed"),
    ):
        migrate_project_dir(project_dir)

    assert (project_dir / "versions" / "versions.json").read_bytes() == versions_before
    assert _entries(project_dir) == manifest_before
    assert _read_project(project_dir)["schema_version"] == 13


def test_killed_after_version_records_are_written_resumes_on_rerun(tmp_path: Path) -> None:
    """版本记录已落盘、清单未改写时进程被杀：重跑不再改写记录，清单照常改写。"""

    project_dir = write_undescribed_style_bases_project(tmp_path / "projects", "reference", route="reference_video")
    with (
        patch.object(
            ProjectArtifactManifestAdapter, "replace_entries_if_matches_atomically", side_effect=KeyboardInterrupt
        ),
        patch("lib.project.project_migrations.v13_to_v14_legacy_style_values.atomic_write_bytes"),
        pytest.raises(KeyboardInterrupt),
    ):
        migrate_project_dir(project_dir)
    rewritten = (project_dir / "versions" / "versions.json").read_bytes()

    migrate_project_dir(project_dir)

    assert (project_dir / "versions" / "versions.json").read_bytes() == rewritten
    for key in _REFERENCE_VIDEOS:
        assert _status(project_dir, key) == "current"


def test_grid_whose_version_record_disagrees_with_the_manifest_is_left_stale_and_reported(tmp_path: Path) -> None:
    project_dir = write_undescribed_style_bases_project(tmp_path / "projects", "grid", route="grid")
    _add_grid_version_record(project_dir, style_description="与清单登记不同时期的描述")
    before = _entries(project_dir)

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[_GRID] == before[_GRID]
    assert _status(project_dir, _GRID) == "stale"
    for key in _GRID_MEMBERS:
        assert _status(project_dir, key) == "current"
    report = load_migration_report(project_dir)
    assert report is not None
    assert [(item.kind, item.resource_id) for item in report.skipped if "style description" in item.reason] == [
        ("episode-grid", "grid_123456789abc")
    ]


def test_described_project_reports_artifacts_the_planner_cannot_project(tmp_path: Path) -> None:
    project_dir = write_undescribed_style_bases_project(tmp_path / "projects", "reference", route="reference_video")
    versions = _read_versions(project_dir)
    for key in _REFERENCE_VIDEOS:
        versions["reference_videos"][str(key.components[-1])]["versions"][0].pop("artifact_video_currency")
    (project_dir / "versions" / "versions.json").write_text(json.dumps(versions, ensure_ascii=False), encoding="utf-8")

    migrate_project_dir(project_dir)

    report = load_migration_report(project_dir)
    assert report is not None
    assert {(item.kind, item.resource_id) for item in report.skipped} >= {
        ("episode-video", str(key.components[-1])) for key in _REFERENCE_VIDEOS
    }
