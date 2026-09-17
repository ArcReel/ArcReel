"""v14→v15：退役条目指纹与整集指纹，已确认无正式脚本的集整份转出，存量正式脚本补齐内容层与待编写标记。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib import project_schema
from lib.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifact_provenance import build_episode_script_basis
from lib.project_migrations.v14_to_v15_formal_script_truth import TARGET_SCHEMA_VERSION, migrate_v14_to_v15
from lib.script_review import content_fingerprint
from tests.legacy_project_shapes import ScriptPlanVariantName, write_legacy_script_plan_project

_VARIANTS: tuple[ScriptPlanVariantName, ...] = ("drama", "narration", "reference_video")
_SCRIPT_ITEMS = {"drama": "scenes", "narration": "segments", "reference_video": "video_units"}
_PLAN_FILES = {
    "drama": "script_plan_normalized_script.json",
    "narration": "script_plan_segments.json",
    "reference_video": "script_plan_reference_units.json",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _project(project_dir: Path) -> dict[str, Any]:
    return _read_json(project_dir / "project.json")


def _episode(project_dir: Path, episode: int) -> dict[str, Any]:
    return next(entry for entry in _project(project_dir)["episodes"] if entry["episode"] == episode)


def _script_items(project_dir: Path, variant: ScriptPlanVariantName, episode: int) -> list[dict[str, Any]]:
    return _read_json(project_dir / "scripts" / f"episode_{episode}.json")[_SCRIPT_ITEMS[variant]]


def _plan_path(project_dir: Path, variant: ScriptPlanVariantName, episode: int) -> Path:
    return project_dir / "drafts" / f"episode_{episode}" / _PLAN_FILES[variant]


@pytest.mark.parametrize("variant", _VARIANTS)
def test_entry_and_script_fingerprints_are_removed(tmp_path: Path, variant: ScriptPlanVariantName) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant=variant)

    migrate_v14_to_v15(project_dir)

    assert _project(project_dir)["schema_version"] == TARGET_SCHEMA_VERSION
    for episode in (1, 3):
        script = _read_json(project_dir / "scripts" / f"episode_{episode}.json")
        assert "script_plan_revision" not in script["metadata"]
        assert all("script_plan_entry_revision" not in item for item in script[_SCRIPT_ITEMS[variant]])


@pytest.mark.parametrize("variant", ["drama", "narration"])
def test_only_storyboards_with_an_empty_visual_layer_are_marked_pending(
    tmp_path: Path, variant: ScriptPlanVariantName
) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant=variant)

    migrate_v14_to_v15(project_dir)

    authored, empty = _script_items(project_dir, variant, 1)
    assert "pending_authoring" not in authored
    assert empty["pending_authoring"] is True


def test_reference_units_are_never_marked_pending_by_the_migration(tmp_path: Path) -> None:
    """参考单元的视觉层就是正文，不按「为空」判待编写。"""

    project_dir = write_legacy_script_plan_project(tmp_path, variant="reference_video")

    migrate_v14_to_v15(project_dir)

    assert all("pending_authoring" not in unit for unit in _script_items(project_dir, "reference_video", 1))


def test_drama_scene_description_is_backfilled_from_the_script_plan(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["scenes"][0]["scene_description"] = "用户改过的视觉改编。"
    _write_json(script_path, script)

    migrate_v14_to_v15(project_dir)

    first, second = _script_items(project_dir, "drama", 1)
    assert first["scene_description"] == "用户改过的视觉改编。"
    assert second["scene_description"] == "第1集第2镜的视觉改编。"


def test_reference_source_text_is_filled_from_the_script_plan_or_left_empty(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="reference_video")
    plan_path = _plan_path(project_dir, "reference_video", 1)
    plan = _read_json(plan_path)
    plan["units"] = plan["units"][:1]
    _write_json(plan_path, plan)

    migrate_v14_to_v15(project_dir)

    first, second = _script_items(project_dir, "reference_video", 1)
    assert first["source_text"] == "第1集第1段原文。"
    assert second["source_text"] == ""


@pytest.mark.parametrize("variant", _VARIANTS)
def test_confirmed_episode_without_a_formal_script_is_materialized(
    tmp_path: Path, variant: ScriptPlanVariantName
) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant=variant)

    outcome = migrate_v14_to_v15(project_dir)

    script = _read_json(project_dir / "scripts" / "episode_2.json")
    items = script[_SCRIPT_ITEMS[variant]]
    id_field = {"drama": "scene_id", "narration": "segment_id", "reference_video": "unit_id"}[variant]
    assert [item[id_field] for item in items] == [
        f"E2{'U' if variant == 'reference_video' else 'S'}0{i}" for i in (1, 2)
    ]
    assert all(item["pending_authoring"] is True for item in items)
    assert script["metadata"]["generator"] == "script_plan_conversion"
    assert "script_plan_revision" not in script["metadata"]
    ledger = _episode(project_dir, 2)
    assert ledger["script_file"] == "scripts/episode_2.json"
    assert ledger["title"] == script["title"]
    assert outcome is None or not outcome.skipped


def test_drama_materialization_takes_the_script_plan_title(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")

    migrate_v14_to_v15(project_dir)

    assert _read_json(project_dir / "scripts" / "episode_2.json")["title"] == "规划第2集"
    assert _episode(project_dir, 2)["title"] == "规划第2集"


def test_unconfirmed_episode_without_a_formal_script_is_not_materialized(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="narration")
    plan_path = _plan_path(project_dir, "narration", 2)
    plan = _read_json(plan_path)
    plan["segments"][0]["novel_text"] = "确认之后改过的旁白。"
    _write_json(plan_path, plan)

    migrate_v14_to_v15(project_dir)

    assert not (project_dir / "scripts" / "episode_2.json").exists()


def test_confirmed_script_plan_without_readable_entries_is_reported_instead_of_materialized(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="narration")
    plan_path = _plan_path(project_dir, "narration", 2)
    _write_json(plan_path, {"segments": []})
    project = _project(project_dir)
    project["episodes"][1]["script_plan_review"]["fingerprint"] = content_fingerprint(plan_path)
    _write_json(project_dir / "project.json", project)

    outcome = migrate_v14_to_v15(project_dir)

    assert not (project_dir / "scripts" / "episode_2.json").exists()
    assert outcome is not None
    skipped = [item for item in outcome.skipped if item.episode == 2]
    assert [(item.kind, item.artifact_path) for item in skipped] == [("episode-script", "scripts/episode_2.json")]
    assert skipped[0].reason == "confirmed script_plan has no readable entries"


def test_grandfathered_episode_records_the_current_script_plan_as_its_confirmation(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    confirmed_before = _episode(project_dir, 1)["script_plan_review"]

    migrate_v14_to_v15(project_dir)

    assert _episode(project_dir, 3)["script_plan_review"]["fingerprint"] == content_fingerprint(
        _plan_path(project_dir, "drama", 3)
    )
    assert _episode(project_dir, 1)["script_plan_review"] == confirmed_before


def test_ledger_stale_episode_gets_no_confirmation_baseline(tmp_path: Path) -> None:
    """账本 stale 的集要等重规划后的首次确认，迁移不替它记。"""

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    project = _project(project_dir)
    project["episodes"][2]["ledger_status"] = "stale"
    _write_json(project_dir / "project.json", project)

    migrate_v14_to_v15(project_dir)

    assert "script_plan_review" not in _episode(project_dir, 3)


def test_script_is_registered_when_a_later_schema_version_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本步只把项目推进到 v15；链上还有后续版本时，剧本照常按 v3 依据登记。"""

    project_dir = write_legacy_script_plan_project(tmp_path, variant="narration")
    monkeypatch.setattr(project_schema, "CURRENT_PROJECT_SCHEMA_VERSION", TARGET_SCHEMA_VERSION + 1)

    migrate_v14_to_v15(project_dir)

    assert ProjectArtifactManifestAdapter(project_dir).get_entry(
        ArtifactKey.episode_script(1)
    ) == ArtifactManifestEntry(
        artifact_path="scripts/episode_1.json",
        basis_digest=build_episode_script_basis(project=_project(project_dir)).digest,
    )


def test_ad_project_only_bumps_the_schema_version(tmp_path: Path) -> None:
    project_dir = tmp_path / "ad"
    project = {
        "schema_version": 14,
        "title": "广告",
        "content_mode": "ad",
        "generation_mode": "reference_video",
        "episodes": [{"episode": 1, "title": "第1集", "script_file": "scripts/episode_1.json"}],
    }
    script = {"episode": 1, "title": "第1集", "content_mode": "ad", "video_units": [{"unit_id": "E1U01", "text": ""}]}
    _write_json(project_dir / "project.json", project)
    _write_json(project_dir / "scripts" / "episode_1.json", script)

    assert migrate_v14_to_v15(project_dir) is None

    assert _project(project_dir) == {**project, "schema_version": TARGET_SCHEMA_VERSION}
    assert _read_json(project_dir / "scripts" / "episode_1.json") == script


def test_project_without_episodes_only_bumps_the_schema_version(tmp_path: Path) -> None:
    project_dir = tmp_path / "empty"
    project = {"schema_version": 14, "title": "空", "content_mode": "drama", "generation_mode": "storyboard"}
    _write_json(project_dir / "project.json", project)

    assert migrate_v14_to_v15(project_dir) is None

    assert _project(project_dir) == {**project, "schema_version": TARGET_SCHEMA_VERSION}


def test_second_run_is_a_no_op(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    migrate_v14_to_v15(project_dir)
    snapshot = {path: path.read_bytes() for path in project_dir.rglob("*") if path.is_file()}

    assert migrate_v14_to_v15(project_dir) is None

    assert {path: path.read_bytes() for path in project_dir.rglob("*") if path.is_file()} == snapshot


def test_rerun_after_a_crash_before_project_json_keeps_the_materialized_script(tmp_path: Path) -> None:
    """剧本已落盘而 ``project.json`` 仍停在 v14：重跑按已有正式脚本处理，不转出第二份。"""

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    project_before = (project_dir / "project.json").read_bytes()
    migrate_v14_to_v15(project_dir)
    materialized = (project_dir / "scripts" / "episode_2.json").read_bytes()
    (project_dir / "project.json").write_bytes(project_before)

    migrate_v14_to_v15(project_dir)

    assert (project_dir / "scripts" / "episode_2.json").read_bytes() == materialized
    assert _project(project_dir)["schema_version"] == TARGET_SCHEMA_VERSION


def test_rerun_rebinds_a_materialized_script_left_at_the_canonical_path(tmp_path: Path) -> None:
    """集原先绑在非规范路径且文件缺席：转出落在规范路径，重跑时补上绑定。"""

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    project = _project(project_dir)
    project["episodes"][1]["script_file"] = "scripts/legacy_episode_2.json"
    _write_json(project_dir / "project.json", project)
    project_before = (project_dir / "project.json").read_bytes()
    migrate_v14_to_v15(project_dir)
    materialized = (project_dir / "scripts" / "episode_2.json").read_bytes()
    (project_dir / "project.json").write_bytes(project_before)

    migrate_v14_to_v15(project_dir)

    assert (project_dir / "scripts" / "episode_2.json").read_bytes() == materialized
    ledger = _episode(project_dir, 2)
    assert (ledger["script_file"], ledger["title"]) == ("scripts/episode_2.json", "规划第2集")


def test_unrelated_file_at_the_canonical_path_is_reported_instead_of_bound(tmp_path: Path) -> None:
    """集原先绑在非规范路径且文件缺席，规范路径上却是一份与确认规划对不上的文件：不补绑定，进迁移报告。"""

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    project = _project(project_dir)
    project["episodes"][1]["script_file"] = "scripts/legacy_episode_2.json"
    _write_json(project_dir / "project.json", project)
    orphan = {"title": "旧文件", "scenes": [{"scene_id": "E2S09", "duration_seconds": 4}]}
    _write_json(project_dir / "scripts" / "episode_2.json", orphan)

    outcome = migrate_v14_to_v15(project_dir)

    assert _read_json(project_dir / "scripts" / "episode_2.json") == orphan
    assert _episode(project_dir, 2)["script_file"] == "scripts/legacy_episode_2.json"
    assert outcome is not None
    assert [(item.episode, item.artifact_path) for item in outcome.skipped if item.episode == 2] == [
        (2, "scripts/episode_2.json")
    ]


def test_confirmed_episode_whose_canonical_path_is_bound_elsewhere_is_reported(tmp_path: Path) -> None:
    """集原先绑在非规范路径且文件缺席，规范路径已绑给另一集：不转出，进迁移报告。"""

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    project = _project(project_dir)
    project["episodes"][1]["script_file"] = "scripts/legacy_episode_2.json"
    project["episodes"][0]["script_file"] = "scripts/episode_2.json"
    (project_dir / "scripts" / "episode_1.json").rename(project_dir / "scripts" / "episode_2.json")
    _write_json(project_dir / "project.json", project)

    outcome = migrate_v14_to_v15(project_dir)

    assert _episode(project_dir, 2)["script_file"] == "scripts/legacy_episode_2.json"
    assert outcome is not None
    skipped = [item for item in outcome.skipped if item.episode == 2]
    assert [(item.artifact_path, item.reason) for item in skipped] == [
        ("scripts/episode_2.json", "canonical script path is bound to another episode")
    ]


def test_inputs_are_backed_up_before_they_are_rewritten(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    script_before = (project_dir / "scripts" / "episode_1.json").read_bytes()

    migrate_v14_to_v15(project_dir)

    assert len(list(project_dir.glob("project.json.bak.v14-*"))) == 1
    [script_backup] = (project_dir / "scripts").glob("episode_1.json.bak.v14-*")
    assert script_backup.read_bytes() == script_before
    assert not list((project_dir / "scripts").glob("episode_2.json.bak.v14-*"))


def test_newer_project_is_left_untouched(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama", schema_version=15)
    snapshot = {path: path.read_bytes() for path in project_dir.rglob("*") if path.is_file()}

    assert migrate_v14_to_v15(project_dir) is None

    assert {path: path.read_bytes() for path in project_dir.rglob("*") if path.is_file()} == snapshot
