"""v14→v15：退役条目指纹与整集指纹，已确认无正式脚本的集整份转出，存量正式脚本补齐内容层与待编写标记。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib import project_schema
from lib.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifact_provenance import build_episode_script_basis
from lib.project_migration_failure import ProjectMigrationError
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


def test_ad_project_binding_is_normalized_and_its_registration_follows(tmp_path: Path) -> None:
    project_dir = tmp_path / "ad"
    project = {
        "schema_version": 14,
        "title": "广告",
        "content_mode": "ad",
        "generation_mode": "reference_video",
        "episodes": [{"episode": 1, "title": "第1集", "script_file": "scripts/ad-cut.json"}],
    }
    script = {"episode": 1, "title": "第1集", "content_mode": "ad", "video_units": [{"unit_id": "E1U01", "text": ""}]}
    _write_json(project_dir / "project.json", project)
    _write_json(project_dir / "scripts" / "ad-cut.json", script)
    ProjectArtifactManifestAdapter(project_dir).put_entry(
        ArtifactKey.episode_script(1),
        ArtifactManifestEntry(artifact_path="scripts/ad-cut.json", basis_digest=f"sha256-v1:{'a' * 64}"),
    )

    outcome = migrate_v14_to_v15(project_dir)

    assert _episode(project_dir, 1)["script_file"] == "scripts/episode_1.json"
    assert not (project_dir / "scripts" / "ad-cut.json").exists()
    # 剧本本身无需改写，只是改名；旧路径上的内容仍要留一份备份，否则按备份的 project.json 回退的
    # 项目会绑到一个不存在的文件上。
    [backup] = (project_dir / "scripts").glob("ad-cut.json.bak.v14-*")
    assert _read_json(backup) == script
    assert _read_json(project_dir / "scripts" / "episode_1.json") == script
    assert ProjectArtifactManifestAdapter(project_dir).get_entry(
        ArtifactKey.episode_script(1)
    ) == ArtifactManifestEntry(artifact_path="scripts/episode_1.json", basis_digest=f"sha256-v1:{'a' * 64}")
    assert outcome is not None
    assert [(item.from_path, item.to_path) for item in outcome.normalized_bindings] == [
        ("scripts/ad-cut.json", "scripts/episode_1.json")
    ]


def test_binding_alias_of_the_canonical_path_only_changes_the_binding_text(tmp_path: Path) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    project = _project(project_dir)
    project["episodes"][2]["script_file"] = "episode_3.json"
    _write_json(project_dir / "project.json", project)

    outcome = migrate_v14_to_v15(project_dir)

    assert _episode(project_dir, 3)["script_file"] == "scripts/episode_3.json"
    assert outcome is not None
    assert [(item.from_path, item.displaced_path) for item in outcome.normalized_bindings] == [("episode_3.json", None)]


def test_binding_whose_episode_is_a_boolean_does_not_pass_as_episode_one(tmp_path: Path) -> None:
    """剧本内 ``episode`` 为 JSON ``true``：``True == 1``，按 ``!=`` 比会冒充第 1 集通过归属校验。

    冒充通过就会被改名到规范路径，而目标态规划随后仍拒绝它——文件已不在原处，重跑把绑定留在
    已消失的旧路径上。
    """

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    scripts_dir = project_dir / "scripts"
    script = _read_json(scripts_dir / "episode_1.json")
    script["episode"] = True
    _write_json(scripts_dir / "custom.json", script)
    (scripts_dir / "episode_1.json").unlink()
    project = _project(project_dir)
    project["episodes"][0]["script_file"] = "scripts/custom.json"
    _write_json(project_dir / "project.json", project)

    with pytest.raises(ProjectMigrationError, match="does not hold this episode") as excinfo:
        migrate_v14_to_v15(project_dir)

    assert (excinfo.value.episode, excinfo.value.file) == (1, "scripts/custom.json")
    assert (scripts_dir / "custom.json").is_file()
    assert not (scripts_dir / "episode_1.json").exists()
    assert _project(project_dir)["schema_version"] == 14


def test_nested_binding_that_would_be_retained_is_rejected_with_the_episode(tmp_path: Path) -> None:
    """规范路径已绑给另一集、本集绑定带目录段：原样保留会让目标态规划整体拒绝这个项目。

    ``normalize_script_binding`` 不允许绑定文件名里有 ``/``，跳过项因此写不进报告，失败裁决也只
    剩 ``project.json`` 可指。改名到规范路径那条路不受此限（绑定会被收敛掉）。
    """

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    scripts_dir = project_dir / "scripts"
    nested = scripts_dir / "archive" / "custom.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "episode_3.json").rename(nested)
    project = _project(project_dir)
    # 第 3 集的规范路径绑给第 1 集：本集绑定只能原样保留。
    project["episodes"][0]["script_file"] = "scripts/episode_3.json"
    project["episodes"][2]["script_file"] = "scripts/archive/custom.json"
    _write_json(project_dir / "project.json", project)

    with pytest.raises(ProjectMigrationError, match="not a flat name under scripts/") as excinfo:
        migrate_v14_to_v15(project_dir)

    assert (excinfo.value.episode, excinfo.value.file) == (3, "scripts/archive/custom.json")
    assert nested.is_file()
    assert _project(project_dir)["schema_version"] == 14


def test_retained_binding_with_a_backslash_segment_is_rejected_with_the_episode(tmp_path: Path) -> None:
    """要原样保留的绑定带反斜杠目录段：与正斜杠写法同法拒绝。

    平坦性校验按目标态规划自己的归一口径判——它先把 ``\\`` 换成 ``/`` 再判，只按 ``/`` 判的话这种
    写法会漏过去，保留下来后规划仍整体拒绝项目，跳过项照样写不进报告。
    """

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    scripts_dir = project_dir / "scripts"
    nested = scripts_dir / "archive" / "custom.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "episode_3.json").rename(nested)
    project = _project(project_dir)
    # 第 3 集的规范路径绑给第 1 集：本集绑定只能原样保留。
    project["episodes"][0]["script_file"] = "scripts/episode_3.json"
    project["episodes"][2]["script_file"] = "scripts/archive\\custom.json"
    _write_json(project_dir / "project.json", project)

    with pytest.raises(ProjectMigrationError, match="not a flat name under scripts/") as excinfo:
        migrate_v14_to_v15(project_dir)

    assert (excinfo.value.episode, excinfo.value.file) == (3, "scripts/archive\\custom.json")
    assert nested.is_file()
    assert _project(project_dir)["schema_version"] == 14


def test_retained_bare_binding_still_has_its_script_rewritten(tmp_path: Path) -> None:
    """绑定是裸文件名、规范路径又绑给另一集：绑定原样留在账本，但预检仍按 ``scripts/`` 读到它。

    交给预检的读路径按裸名直接拼项目根的话会指到 ``<project>/custom.json``，那一集的权威剧本读不
    到，退役的指纹字段与待编写标记都不会落上去，而迁移照常提交 v15。
    """

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    scripts_dir = project_dir / "scripts"
    (scripts_dir / "episode_3.json").rename(scripts_dir / "custom.json")
    (scripts_dir / "episode_1.json").rename(scripts_dir / "episode_3.json")
    project = _project(project_dir)
    project["episodes"][0]["script_file"] = "scripts/episode_3.json"
    project["episodes"][2]["script_file"] = "custom.json"
    _write_json(project_dir / "project.json", project)

    outcome = migrate_v14_to_v15(project_dir)

    assert _project(project_dir)["schema_version"] == TARGET_SCHEMA_VERSION
    # 账本保留裸名字面。
    assert _episode(project_dir, 3)["script_file"] == "custom.json"
    assert not (project_dir / "custom.json").exists()
    retained = _read_json(scripts_dir / "custom.json")
    assert "script_plan_revision" not in retained["metadata"]
    assert all("script_plan_entry_revision" not in item for item in retained["scenes"])
    assert outcome is not None
    assert [(item.artifact_path, item.reason) for item in outcome.skipped if item.episode == 3] == [
        ("scripts/episode_3.json", "canonical script path is bound to another episode")
    ]


def test_binding_outside_the_scripts_directory_is_rejected_with_the_episode(tmp_path: Path) -> None:
    """越出 ``scripts/`` 的绑定：不改名、不改绑，按集号与绑定拒绝这个项目。

    改名前拒绝而不是记跳过项：绑定原样保留时目标态规划必然拒绝这个项目，跳过项写不进报告，
    失败裁决也只剩 ``project.json`` 可指。
    """

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    project = _project(project_dir)
    project["episodes"][2]["script_file"] = "../outside/episode_3.json"
    _write_json(project_dir / "project.json", project)

    with pytest.raises(ProjectMigrationError, match="points outside the scripts directory") as excinfo:
        migrate_v14_to_v15(project_dir)

    assert (excinfo.value.episode, excinfo.value.file) == (3, "../outside/episode_3.json")
    assert _episode(project_dir, 3)["script_file"] == "../outside/episode_3.json"
    assert _project(project_dir)["schema_version"] == 14
    assert (project_dir / "scripts" / "episode_3.json").is_file()


def test_two_ledger_entries_for_the_same_episode_are_rejected_before_any_move(tmp_path: Path) -> None:
    """同一集号两条账本条目、各绑一份剧本：两条绑定都会改名到同一个规范路径。

    顺序 os.replace 先把前一份顶掉，目标态规划之后才按「绑定不唯一」拒绝——项目停在 v14 而两份
    来源都已离开原处。在只读预检里拒绝，两份剧本一个字节都不动。
    """

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    scripts_dir = project_dir / "scripts"
    (scripts_dir / "episode_1.json").rename(scripts_dir / "custom_a.json")
    (scripts_dir / "episode_3.json").rename(scripts_dir / "custom_b.json")
    before = {name: (scripts_dir / name).read_bytes() for name in ("custom_a.json", "custom_b.json")}
    project = _project(project_dir)
    project["episodes"][0]["script_file"] = "scripts/custom_a.json"
    project["episodes"][2]["episode"] = 1
    project["episodes"][2]["script_file"] = "scripts/custom_b.json"
    _write_json(project_dir / "project.json", project)

    with pytest.raises(ProjectMigrationError, match="more than one entry for this episode") as excinfo:
        migrate_v14_to_v15(project_dir)

    assert (excinfo.value.episode, excinfo.value.file) == (1, "scripts/custom_b.json")
    assert {name: (scripts_dir / name).read_bytes() for name in before} == before
    assert not (scripts_dir / "episode_1.json").exists()
    assert _project(project_dir)["schema_version"] == 14


def test_script_file_bound_to_several_episodes_is_rejected_with_the_episode(tmp_path: Path) -> None:
    """同一文件绑给多集：不改名、不改绑，按集号与绑定拒绝这个项目。"""

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    scripts_dir = project_dir / "scripts"
    (scripts_dir / "episode_1.json").rename(scripts_dir / "custom.json")
    project = _project(project_dir)
    project["episodes"][0]["script_file"] = "scripts/custom.json"
    project["episodes"][2]["script_file"] = "custom.json"
    _write_json(project_dir / "project.json", project)

    with pytest.raises(ProjectMigrationError, match="shared by several episodes") as excinfo:
        migrate_v14_to_v15(project_dir)

    assert (excinfo.value.episode, excinfo.value.file) == (1, "scripts/custom.json")

    assert _read_json(scripts_dir / "custom.json")["episode"] == 1
    assert not (scripts_dir / "episode_1.json").exists()
    assert _project(project_dir)["schema_version"] == 14
    assert [entry["script_file"] for entry in _project(project_dir)["episodes"]] == [
        "scripts/custom.json",
        project["episodes"][1]["script_file"],
        "custom.json",
    ]


@pytest.mark.parametrize("content", ["another episode", "unreadable"])
def test_binding_that_does_not_hold_this_episode_is_rejected_before_the_move(tmp_path: Path, content: str) -> None:
    """绑定文件不是本集剧本：不改名，按集号与绑定拒绝，绑定与文件都留在原处。"""

    project_dir = write_legacy_script_plan_project(tmp_path, variant="drama")
    scripts_dir = project_dir / "scripts"
    (scripts_dir / "episode_1.json").rename(scripts_dir / "custom.json")
    custom = scripts_dir / "custom.json"
    if content == "another episode":
        script = _read_json(custom)
        script["episode"] = 2
        _write_json(custom, script)
    else:
        custom.write_text("{ 读不成对象", encoding="utf-8")
    project = _project(project_dir)
    project["episodes"][0]["script_file"] = "scripts/custom.json"
    _write_json(project_dir / "project.json", project)
    before = custom.read_bytes()

    with pytest.raises(ProjectMigrationError, match="does not hold this episode") as excinfo:
        migrate_v14_to_v15(project_dir)

    assert (excinfo.value.episode, excinfo.value.file) == (1, "scripts/custom.json")
    # 改名已发生的话，重跑会跳过这一集、把绑定留在消失的旧路径上，项目带着失联的绑定升到 v15。
    assert custom.read_bytes() == before
    assert not (scripts_dir / "episode_1.json").exists()
    assert _project(project_dir)["schema_version"] == 14
    assert _episode(project_dir, 1)["script_file"] == "scripts/custom.json"


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
