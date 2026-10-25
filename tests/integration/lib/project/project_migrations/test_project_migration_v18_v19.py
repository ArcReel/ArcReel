"""v18→v19 迁移：每集记下条目编号用过的最大号，此后的新增不再取回删掉的编号；分镜图与宫格的依据平移到不记资产名的口径，时效不变。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_planner import TargetStatePlanner
from lib.artifacts.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD, parse_image_version_basis
from lib.edit_timeline import EditTimelineService
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v18_to_v19_item_id_high_water import migrate_v18_to_v19
from lib.script.script_editor import new_item_id
from lib.workflow.workflow_state import WorkflowStateService
from tests.fakes import failing_manifest_replace
from tests.legacy_project_shapes import write_legacy_item_id_reuse_project, write_legacy_named_reference_bases_project


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _structure_bytes(project_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project_dir).as_posix(): path.read_bytes()
        for path in sorted(project_dir.rglob("*"))
        if path.is_file() and path.name != "project.json" and ".bak." not in path.name
    }


async def test_schema_18_project_records_the_highest_number_without_renaming_anything(tmp_path: Path) -> None:
    project_dir = write_legacy_item_id_reuse_project(tmp_path / "projects")
    legacy = _read_json(project_dir / "project.json")
    assert legacy["schema_version"] == 18
    files_before = _structure_bytes(project_dir)

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json") == {
        **legacy,
        # 剧本到 E1S2，版本历史到 E1S5，删除记录到 E1S7，剪辑时间线的片段到 E1S9
        "episodes": [{**entry, "item_id_high_water": 9} for entry in legacy["episodes"]],
        "schema_version": CURRENT_SCHEMA_VERSION,
    }
    assert _structure_bytes(project_dir) == files_before

    pm = ProjectManager(tmp_path)
    # 样本剧本按当前模型自检不过，批量编辑会拒绝；直接走同一取号规则追加一条（批量编辑的发号见其测试）
    with pm.locked_script(project_dir.name, "episode_1.json", validate=False) as script:
        floor = pm.item_id_floor(pm.load_project(project_dir.name), script, 1)
        added = {**script["segments"][-1], "segment_id": new_item_id(script, floor=floor), "generated_assets": {}}
        script["segments"].append(added)
    assert added["segment_id"] == "E1S10"
    assert pm.reserve_item_ids(project_dir.name, 1, "segments", 1) == ["E1S11"]
    readout = await EditTimelineService(pm).read(project_dir.name, "tl-00000e19")
    statuses = {clip.id: (clip.unit_id, clip.status) for clip in readout.clips}
    assert statuses["c2"] == ("E1S9", "unit_deleted")


def test_rerunning_the_step_keeps_the_recorded_number(tmp_path: Path) -> None:
    project_dir = write_legacy_item_id_reuse_project(tmp_path)
    migrate_v18_to_v19(project_dir)
    migrated = (project_dir / "project.json").read_bytes()

    migrate_v18_to_v19(project_dir)

    assert (project_dir / "project.json").read_bytes() == migrated


# —— 分镜图与宫格的依据不再记资产名 ——

_GRID = ArtifactKey.episode_grid(1, "grid_123456789abc")
_GRID_MEMBERS = (ArtifactKey.episode_storyboard(1, "E1S01"), ArtifactKey.episode_storyboard(1, "E1S02"))
_STORYBOARD = ArtifactKey.episode_storyboard(1, "E1S03")
_NAMED = (_GRID, *_GRID_MEMBERS, _STORYBOARD)


def _entries(project_dir: Path) -> Mapping[ArtifactKey, ArtifactManifestEntry]:
    return ProjectArtifactManifestAdapter(project_dir).snapshot_entries()


def _status(project_dir: Path, key: ArtifactKey) -> str:
    entry = _entries(project_dir)[key]
    return ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=entry.artifact_path).status.value


def _is_current_before_migration(project_dir: Path, key: ArtifactKey) -> bool:
    """迁移前的时新性判定。读模型只服务当前 schema 的项目，此处直接比对登记与目标态。"""
    return _entries(project_dir).get(key) == TargetStatePlanner(project_dir).plan().entries.get(key)


def _frozen_bases(project_dir: Path) -> dict[tuple[str, str, int], dict]:
    versions = _read_json(project_dir / "versions" / "versions.json")
    return {
        (bucket, resource_id, record["version"]): record[IMAGE_ARTIFACT_BASIS_FIELD]
        for bucket in ("storyboards", "grids")
        for resource_id, resource in versions[bucket].items()
        for record in resource["versions"]
    }


def _stale_storyboard_scene(project_dir: Path) -> None:
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][2]["image_prompt"]["scene"] = "@[阿离]改过的画面"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def test_storyboards_and_grids_keep_their_currency_across_the_upgrade(tmp_path: Path) -> None:
    project_dir = write_legacy_named_reference_bases_project(tmp_path / "projects")
    for key in _NAMED:
        assert _is_current_before_migration(project_dir, key)
    before = _entries(project_dir)

    assert migrate_project_dir(project_dir) is True

    after = _entries(project_dir)
    for key in _NAMED:
        assert after[key].basis_digest != before[key].basis_digest
        assert after[key].artifact_path == before[key].artifact_path
        assert _status(project_dir, key) == "current"
    summary = WorkflowStateService(ProjectManager(tmp_path)).get_project_summary(project_dir.name)
    assert (summary.episodes[0].storyboards.available, summary.episodes[0].storyboards.stale) == (3, 0)
    # 播放器与版本恢复读冻结依据：选中与未选中的版本都改写到新口径，且不再含资产名。
    frozen = _frozen_bases(project_dir)
    assert "阿离" not in json.dumps(list(frozen.values()), ensure_ascii=False)
    for bucket, resource_id, key in (("grids", "grid_123456789abc", _GRID), ("storyboards", "E1S03", _STORYBOARD)):
        record = _read_json(project_dir / "versions" / "versions.json")[bucket][resource_id]["versions"][0]
        assert parse_image_version_basis(bucket, resource_id, record).digest == after[key].basis_digest
    assert frozen["storyboards", "E1S01", 1] == frozen["storyboards", "E1S01", 2]


def test_renaming_the_character_after_the_upgrade_keeps_storyboards_and_grids_current(tmp_path: Path) -> None:
    project_dir = write_legacy_named_reference_bases_project(tmp_path / "projects")
    migrate_project_dir(project_dir)

    ProjectManager(tmp_path).rename_asset(project_dir.name, "characters", "阿离", "阿璃")

    for key in _NAMED:
        assert _status(project_dir, key) == "current"
    assert _status(project_dir, ArtifactKey.asset_sheet("character", "阿璃")) == "stale"


def test_an_already_stale_storyboard_stays_stale(tmp_path: Path) -> None:
    project_dir = write_legacy_named_reference_bases_project(tmp_path / "projects")
    _stale_storyboard_scene(project_dir)
    assert not _is_current_before_migration(project_dir, _STORYBOARD)
    before = _entries(project_dir)[_STORYBOARD]

    migrate_project_dir(project_dir)

    assert _entries(project_dir)[_STORYBOARD] == before
    assert _status(project_dir, _STORYBOARD) == "stale"
    for key in (_GRID, *_GRID_MEMBERS):
        assert _status(project_dir, key) == "current"


def test_killed_after_version_records_are_written_resumes_on_rerun(tmp_path: Path) -> None:
    project_dir = write_legacy_named_reference_bases_project(tmp_path / "projects")
    with (
        failing_manifest_replace(KeyboardInterrupt()),
        patch("lib.project.project_migrations.v18_to_v19_item_id_high_water.atomic_write_bytes"),
        pytest.raises(KeyboardInterrupt),
    ):
        migrate_project_dir(project_dir)
    assert _read_json(project_dir / "project.json")["schema_version"] == 18
    rewritten = _frozen_bases(project_dir)

    migrate_project_dir(project_dir)

    assert _frozen_bases(project_dir) == rewritten
    for key in _NAMED:
        assert _status(project_dir, key) == "current"


def test_failed_manifest_commit_rolls_back_version_records(tmp_path: Path) -> None:
    project_dir = write_legacy_named_reference_bases_project(tmp_path / "projects")
    versions_before = (project_dir / "versions" / "versions.json").read_bytes()
    manifest_before = _entries(project_dir)

    with (
        failing_manifest_replace(OSError("manifest write failed")),
        pytest.raises(OSError, match="manifest write failed"),
    ):
        migrate_project_dir(project_dir)

    assert (project_dir / "versions" / "versions.json").read_bytes() == versions_before
    assert _entries(project_dir) == manifest_before
    assert _read_json(project_dir / "project.json")["schema_version"] == 18
