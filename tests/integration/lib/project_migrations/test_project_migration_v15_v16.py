"""v15→v16：补记风格描述后，既有宫格与参考视频不因口径升级翻过期，本就过期的不被伪造成时新。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.artifact_currency import ArtifactCurrencyResolver
from lib.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifact_planner import TargetStatePlanner
from lib.media_artifact_currency import build_current_video_artifact_basis
from lib.project_manager import ProjectManager
from lib.project_migrations.runner import migrate_project_dir
from lib.project_migrations.v15_to_v16_grid_video_style_descriptions import (
    legacy_projection_bytes,
    restamp_reference_video_provenance,
)
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.version_manager import VersionManager
from lib.workflow_state import WorkflowStateService
from tests.legacy_project_shapes import (
    advance_project_schema,
    write_legacy_reference_video_project,
    write_legacy_style_project,
)

_STYLE = "写实电影感"
_DESCRIPTION = "淡彩"
_REFERENCE_UNITS = ("E1U01", "E1U02")

_GRID = ArtifactKey.episode_grid(1, "grid_123456789abc")
_GRID_MEMBER = ArtifactKey.episode_storyboard(1, "E1S01")
_STORYBOARD = ArtifactKey.episode_storyboard(1, "E1S03")
_REFERENCE_VIDEO = ArtifactKey.episode_video(1, "E1U01")


def _entries(project_dir: Path) -> Mapping[ArtifactKey, ArtifactManifestEntry]:
    return ProjectArtifactManifestAdapter(project_dir).snapshot_entries()


def _status(project_dir: Path, key: ArtifactKey) -> str:
    entry = _entries(project_dir)[key]
    return ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=entry.artifact_path).status.value


def _read_project(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _legacy_plan(project_dir: Path):
    """改写前的目标态：旧口径由「删掉描述字段」表达，且容忍逾期目标。

    参考视频的清单条目由记录里冻结的依据与现算的重建依据比对而来，而那份冻结依据正是旧口径的
    那一件；严格规划会把它判成「依据已过期」而跳过，改写前这份就只剩宫格一类。
    """

    return TargetStatePlanner(
        project_dir,
        project_bytes=legacy_projection_bytes(_read_project(project_dir)),
        allow_stale_formal_targets=True,
    ).plan()


def _is_current_before_migration(project_dir: Path, key: ArtifactKey) -> bool:
    """改写前的时新性判定：删掉描述字段规划出的目标，正是旧代码写进清单的那份登记。"""

    return _entries(project_dir).get(key) == _legacy_plan(project_dir).entries.get(key)


def _frozen_video_basis_is_current(project_dir: Path, resource_id: str) -> bool:
    """成片读模型的口径：记录里冻结的依据与现算的重建依据相符。

    它不看清单（``server/services/presentation_read_model.py`` 的 ``_video_currency``），故只改
    写清单条目不足以让参考视频继续时新。
    """

    versions = VersionManager(project_dir)
    record = next(
        item for item in versions.get_versions("reference_videos", resource_id)["versions"] if item.get("is_current")
    )
    rebuilt = build_current_video_artifact_basis(
        project_path=project_dir,
        project=_read_project(project_dir),
        script=json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")),
        resource_type="reference_videos",
        resource_id=resource_id,
        versions=versions,
        version_metadata=record,
        current_tts_settings=None,
    )
    frozen = record["artifact_video_currency"]["video_basis"]
    return rebuilt is not None and rebuilt.digest == frozen["digest"]


def _copy_twin_currency(project_dir: Path, twin: Path, unit_ids: tuple[str, ...]) -> None:
    """把记录里的冻结依据退回旧口径：旧代码写下的那一份里没有描述键。

    描述为空的双胞胎项目算出的依据正是旧口径的——条件式记录在描述为空时不写键——内容其余部分
    逐字相同，故那份依据就是本项目在旧口径下会记下的依据。
    """

    versions_path = project_dir / "versions" / "versions.json"
    data = json.loads(versions_path.read_text(encoding="utf-8"))
    twin_data = json.loads((twin / "versions" / "versions.json").read_text(encoding="utf-8"))
    for unit_id in unit_ids:
        data["reference_videos"][unit_id]["versions"][0]["artifact_video_currency"] = twin_data["reference_videos"][
            unit_id
        ]["versions"][0]["artifact_video_currency"]
    versions_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _legacy_grid_project_at_v15(root: Path, *, name: str = "legacy") -> Path:
    """停在 v15、描述非空的宫格项目，宫格与切格分镜的依据按旧口径（不记描述）登记。

    描述为空的双胞胎项目算出的登记正是旧口径——条件式记录在描述为空时不写键——把它搬过来，
    其余字段逐字相同，故规划器认得出这些登记就是本项目的改写前目标态。
    """

    project_dir = write_legacy_style_project(root, name, style=_STYLE, style_description=_DESCRIPTION)
    advance_project_schema(project_dir, to_version=15)
    twin = write_legacy_style_project(root, f"{name}-twin", style=_STYLE, style_description="")
    advance_project_schema(twin, to_version=15)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    twin_entries = _entries(twin)
    for key in (_GRID, _GRID_MEMBER):
        adapter.put_entry(key, twin_entries[key])
    return project_dir


def _legacy_reference_video_project_at_v15(root: Path, *, name: str = "legacy-video") -> Path:
    """停在 v15、描述非空的参考生视频项目，参考视频的依据与冻结依据都还是旧口径。"""

    project_dir = write_legacy_reference_video_project(root, name, style_description=_DESCRIPTION)
    advance_project_schema(project_dir, to_version=15)
    twin = write_legacy_reference_video_project(root, f"{name}-twin", style_description="")
    advance_project_schema(twin, to_version=15)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    twin_entries = _entries(twin)
    for unit_id in _REFERENCE_UNITS:
        adapter.put_entry(ArtifactKey.episode_video(1, unit_id), twin_entries[ArtifactKey.episode_video(1, unit_id)])
    _copy_twin_currency(project_dir, twin, _REFERENCE_UNITS)
    return project_dir


def test_grid_and_member_bases_are_rebased_onto_the_description(tmp_path: Path) -> None:
    project_dir = _legacy_grid_project_at_v15(tmp_path / "projects")
    before = {key: _entries(project_dir)[key] for key in (_GRID, _GRID_MEMBER)}
    assert all(_is_current_before_migration(project_dir, key) for key in before)

    assert migrate_project_dir(project_dir) is True

    assert _read_project(project_dir)["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    for key, prior in before.items():
        after = _entries(project_dir)[key]
        assert after.basis_digest != prior.basis_digest
        assert after.artifact_path == prior.artifact_path
        assert _status(project_dir, key) == "current"


def test_reference_video_basis_is_rebased_onto_the_description(tmp_path: Path) -> None:
    project_dir = _legacy_reference_video_project_at_v15(tmp_path / "projects")
    assert _is_current_before_migration(project_dir, _REFERENCE_VIDEO)
    before = _entries(project_dir)[_REFERENCE_VIDEO]

    assert migrate_project_dir(project_dir) is True

    after = _entries(project_dir)[_REFERENCE_VIDEO]
    assert after.basis_digest != before.basis_digest
    assert after.artifact_path == before.artifact_path
    assert _status(project_dir, _REFERENCE_VIDEO) == "current"
    # 只改清单条目不够：成片读模型看的是记录里冻结的那份依据。
    assert _frozen_video_basis_is_current(project_dir, "E1U01")


def test_resealed_record_still_rebases_the_manifest_on_a_rerun(tmp_path: Path) -> None:
    """记录已按新口径落盘而清单尚未改写时重跑，整步仍要收敛，不能把这次改写丢掉。"""

    project_dir = _legacy_reference_video_project_at_v15(tmp_path / "projects")
    before = _entries(project_dir)[_REFERENCE_VIDEO]
    restamp_reference_video_provenance(project_dir, _legacy_plan(project_dir).entries, description=_DESCRIPTION)
    assert _frozen_video_basis_is_current(project_dir, "E1U01")

    assert migrate_project_dir(project_dir) is True

    after = _entries(project_dir)[_REFERENCE_VIDEO]
    assert after.basis_digest != before.basis_digest
    assert _status(project_dir, _REFERENCE_VIDEO) == "current"


def test_stale_reference_video_keeps_its_frozen_basis(tmp_path: Path) -> None:
    """产物与当前内容已对不上的记录不盖章：两个读模型一致地继续判过期。"""

    project_dir = _legacy_reference_video_project_at_v15(tmp_path / "projects")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["text"] = "另一段画面描述。"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    before = _entries(project_dir)[_REFERENCE_VIDEO]

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[_REFERENCE_VIDEO] == before
    assert _status(project_dir, _REFERENCE_VIDEO) == "stale"
    assert not _frozen_video_basis_is_current(project_dir, "E1U01")


def test_rewriting_the_description_expires_the_grid_and_the_reference_video(tmp_path: Path) -> None:
    """补记之后口径才成立：描述一改，宫格、切格分镜与参考视频都该翻过期。"""

    project_dir = _legacy_reference_video_project_at_v15(tmp_path / "projects")
    migrate_project_dir(project_dir)
    project = _read_project(project_dir)
    project["style_description"] = "硬光"
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    assert _status(project_dir, _REFERENCE_VIDEO) == "stale"
    assert not _frozen_video_basis_is_current(project_dir, "E1U01")


def test_storyboard_basis_is_left_alone(tmp_path: Path) -> None:
    """单张分镜图的依据记描述早于本步，补记口径不该顺带改写它的登记。"""

    project_dir = _legacy_grid_project_at_v15(tmp_path / "projects")
    before = _entries(project_dir)[_STORYBOARD]

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[_STORYBOARD] == before
    assert _status(project_dir, _STORYBOARD) == "current"


def test_blank_description_keeps_every_basis_digest(tmp_path: Path) -> None:
    project_dir = write_legacy_style_project(tmp_path / "projects", style=_STYLE, style_description="")
    advance_project_schema(project_dir, to_version=CURRENT_PROJECT_SCHEMA_VERSION - 1)
    project_before = _read_project(project_dir)
    before = dict(_entries(project_dir))
    versions_before = (project_dir / "versions" / "versions.json").read_bytes()

    advance_project_schema(project_dir, to_version=CURRENT_PROJECT_SCHEMA_VERSION)

    assert _read_project(project_dir) == {**project_before, "schema_version": CURRENT_PROJECT_SCHEMA_VERSION}
    assert dict(_entries(project_dir)) == before
    assert (project_dir / "versions" / "versions.json").read_bytes() == versions_before


def test_already_stale_grid_member_is_not_made_current(tmp_path: Path) -> None:
    project_dir = _legacy_grid_project_at_v15(tmp_path / "projects")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["segments"][0]["image_prompt"]["scene"] = "改过的画面"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    assert not _is_current_before_migration(project_dir, _GRID_MEMBER)
    before = _entries(project_dir)[_GRID_MEMBER]

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[_GRID_MEMBER] == before
    assert _status(project_dir, _GRID_MEMBER) == "stale"


def test_non_style_dependency_change_between_plans_aborts_without_rebasing(tmp_path: Path) -> None:
    project_dir = _legacy_grid_project_at_v15(tmp_path / "projects")
    before = _entries(project_dir)[_GRID]
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

    assert _entries(project_dir)[_GRID] == before
    assert _read_project(project_dir)["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION - 1


def test_the_read_model_reports_no_grid_regression(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = _legacy_grid_project_at_v15(root)

    assert migrate_project_dir(project_dir) is True

    summary = WorkflowStateService(ProjectManager(root)).get_project_summary(project_dir.name)
    episode = summary.episodes[0]
    assert episode.storyboards.stale == 0
    assert episode.storyboards.available == 3
