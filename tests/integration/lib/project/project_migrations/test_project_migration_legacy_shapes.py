"""过去正常流程写出的旧形态与单件损坏不再拒绝整个项目：迁移链规整或跳过，原内容留在盘上，报告列出改动（ADR 0098）。

样本取自 ``tests/legacy_project_shapes.py``，形态清单见 ``docs/agents/project-migrations.md``「已知旧形态」。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey, ArtifactManifest, ProjectArtifactManifestAdapter
from lib.project.asset_derivatives import derivative_artifact_key
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import (
    MigrationReport,
    acknowledge_migration_report,
    load_migration_report,
    visible_migration_report,
)
from lib.project.project_migrations import legacy_shapes, runner, v6_to_v7_ad_reference_video_units
from lib.project.project_migrations.legacy_shapes import normalize_legacy_shapes
from lib.project.project_migrations.runner import migrate_project_with_verdict
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.project.resource_paths import (
    CHARACTER_DERIVATIVE_RESOURCE_TYPE,
    END_FRAME_RESOURCE_TYPE,
    resource_relative_path,
)
from lib.script.script_review import content_fingerprint
from lib.workflow.workflow_state import WorkflowStateService
from tests.integration.lib.project.project_migrations.test_project_migration_v7_v8 import _project
from tests.legacy_project_shapes import (
    add_asset_names_differing_only_in_case,
    add_colliding_asset_name,
    add_duplicate_ledger_entry,
    advance_project_schema,
    bind_episode_script_to_filename,
    blank_reference_unit_shots,
    break_script_item_ids,
    cross_bind_episode_scripts,
    duplicate_legacy_ad_ids,
    refer_to_asset_in_episode,
    reuse_item_id_across_episodes,
    set_script_episode,
    share_episode_script,
    share_reference_files,
    write_legacy_ad_reference_video_project,
    write_legacy_reference_video_project,
    write_legacy_script_plan_project,
    write_legacy_shared_reference_project,
    write_legacy_storyboard_project,
    write_non_utf8_episode_source,
    write_unreadable_grid_record,
    write_unreadable_script_plan_draft,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _commits_a_rebind(path: Path, payload: Any) -> bool:
    """这次写盘是在提交改绑：写的是 ``project.json``，且有一集的剧本绑定与盘上不同。"""
    if path.name != "project.json":
        return False
    bound = {entry["episode"]: entry.get("script_file") for entry in _read_json(path)["episodes"]}
    return any(bound.get(entry["episode"]) != entry.get("script_file") for entry in payload["episodes"])


def _migrate(project_dir: Path) -> None:
    assert migrate_project_with_verdict(project_dir) is None
    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION


def _adjusted(project_dir: Path) -> list[tuple[str, int | None, dict[str, str]]]:
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.complete
    return [(item.code, item.episode, item.params) for item in report.adjusted]


def _skipped(project_dir: Path) -> list[tuple[str, str]]:
    report = load_migration_report(project_dir)
    assert report is not None
    return [(item.kind, item.artifact_path) for item in report.skipped]


def test_a_duplicate_ledger_entry_is_dropped_and_its_script_stays_on_disk(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    add_duplicate_ledger_entry(project_dir, 1, "episode_1_copy.json")
    original = (project_dir / "project.json").read_bytes()

    _migrate(project_dir)

    episodes = _read_json(project_dir / "project.json")["episodes"]
    assert [(entry["episode"], entry["script_file"]) for entry in episodes] == [(1, "scripts/episode_1.json")]
    assert (project_dir / "scripts" / "episode_1_copy.json").is_file()
    report = load_migration_report(project_dir)
    assert report is not None
    [adjustment] = report.adjusted
    assert (adjustment.code, adjustment.episode, adjustment.file) == (
        "duplicate_episode_entry_removed",
        1,
        "scripts/episode_1_copy.json",
    )
    assert adjustment.backup is not None
    assert (project_dir / adjustment.backup).read_bytes() == original


def test_a_script_shared_by_two_episodes_stays_with_the_episode_it_names(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    share_episode_script(project_dir, 1, with_episode=2)
    set_script_episode(project_dir, 1, 2)
    script_before = (project_dir / "scripts" / "episode_1.json").read_bytes()

    _migrate(project_dir)

    episodes = _read_json(project_dir / "project.json")["episodes"]
    assert [(entry["episode"], entry["script_file"]) for entry in episodes] == [
        (1, "scripts/episode_1.json"),
        (2, "scripts/episode_2.json"),
    ]
    assert not (project_dir / "scripts" / "episode_1.json").exists()
    assert _read_json(project_dir / "scripts" / "episode_2.json") == json.loads(script_before)
    assert [(code, episode) for code, episode, _params in _adjusted(project_dir)] == [
        ("shared_script_binding_split", 1),
        ("script_binding_canonicalized", 2),
    ]


def test_a_script_without_its_episode_number_takes_the_one_it_is_bound_to(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    set_script_episode(project_dir, 1, None)

    _migrate(project_dir)

    assert _read_json(project_dir / "scripts" / "episode_1.json")["episode"] == 1
    assert _adjusted(project_dir) == [("script_episode_corrected", 1, {"from": "", "to": "1"})]


def test_asset_names_that_collide_after_normalization_keep_the_first(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    colliding = add_colliding_asset_name(project_dir, "characters", "阿离")

    _migrate(project_dir)

    assert list(_read_json(project_dir / "project.json")["characters"]) == ["阿离"]
    assert _adjusted(project_dir) == [
        ("duplicate_asset_removed", None, {"asset_type": "character", "name": colliding, "kept": "阿离"})
    ]
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.adjusted[0].backup is not None
    assert colliding in _read_json(project_dir / report.adjusted[0].backup)["characters"]


def test_asset_names_that_differ_only_in_case_keep_the_first(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    variant = add_asset_names_differing_only_in_case(project_dir, "props", "prop_sheet", "Umbrella")

    _migrate(project_dir)

    assert list(_read_json(project_dir / "project.json")["props"]) == ["伞", "Umbrella"]
    assert _adjusted(project_dir) == [
        ("duplicate_asset_removed", None, {"asset_type": "prop", "name": variant, "kept": "Umbrella"})
    ]
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.adjusted[0].backup is not None
    assert variant in _read_json(project_dir / report.adjusted[0].backup)["props"]
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    assert ArtifactKey.asset_sheet("prop", "Umbrella") in entries
    assert ArtifactKey.asset_sheet("prop", variant) not in entries


_SHARED_REFERENCE_COPIES = [
    (
        "characters/refs/阿青.png",
        {"asset_type": "character", "name": "阿青", "owner": "阿离", "from": "characters/refs/阿离.png"},
    ),
    (
        "characters/refs_audio/阿青.wav",
        {"asset_type": "character", "name": "阿青", "owner": "阿离", "from": "characters/refs_audio/阿离.wav"},
    ),
    (
        "products/refs/茶_1.png",
        {"asset_type": "product", "name": "茶", "owner": "咖啡", "from": "products/refs/咖啡_1.png"},
    ),
]


def _copied_reference_files(project_dir: Path) -> list[tuple[str | None, dict[str, str]]]:
    report = load_migration_report(project_dir)
    assert report is not None
    return [(item.file, item.params) for item in report.adjusted if item.code == "shared_reference_file_copied"]


@pytest.mark.parametrize("padded_names", [False, True])
def test_schema_five_shared_references_survive_namespace_renames(tmp_path: Path, padded_names: bool) -> None:
    project_dir = write_legacy_shared_reference_project(
        ProjectManager(tmp_path).projects_dir, padded_names=padded_names
    )
    original = (project_dir / "project.json").read_bytes()

    _migrate(project_dir)

    project = _read_json(project_dir / "project.json")
    for name in ("Alice", "Bob"):
        entry = project["characters"][name]
        assert entry["reference_image"] == f"characters/refs/{name}.png"
        assert entry["reference_audio"] == f"characters/refs_audio/{name}.wav"
        assert (project_dir / entry["reference_image"]).read_bytes() == b"image"
        assert (project_dir / entry["reference_audio"]).read_bytes() == b"audio"
    product = project["products"]["Bob_product"]
    assert product["reference_images"] == ["products/refs/Bob_product_1.png"]
    assert (project_dir / product["reference_images"][0]).read_bytes() == b"image"
    assert len(_copied_reference_files(project_dir)) == 3
    for rel, params in _copied_reference_files(project_dir):
        assert rel is not None
        assert (project_dir / rel).is_file()
        assert params["name"] == ("Bob_product" if params["asset_type"] == "product" else "Bob")
    assert original in [path.read_bytes() for path in project_dir.glob("project.json.bak.v5-*")]
    shown = WorkflowStateService(ProjectManager(str(tmp_path))).get_status("demo").migration_report
    assert shown is not None
    assert [item.code for item in shown.adjusted] == ["shared_reference_file_copied"] * 3


@pytest.mark.parametrize("fail_commit", [False, True])
def test_swapped_shared_references_keep_each_original_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_commit: bool
) -> None:
    project_dir = write_legacy_shared_reference_project(ProjectManager(tmp_path).projects_dir)
    project_path = project_dir / "project.json"
    project = _read_json(project_path)
    project["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION - 1
    project["characters"]["Alice"]["reference_image"] = "characters/refs/Bob.png"
    project["products"] = {}
    project_path.write_text(json.dumps(project), encoding="utf-8")
    (project_dir / "characters/refs/Bob.png").write_bytes(b"bob-image")
    write = legacy_shapes.atomic_write_json

    def fail_on_project(path: Path, payload: Any) -> None:
        if path.name == "project.json":
            raise OSError("disk full")
        write(path, payload)

    if fail_commit:
        with monkeypatch.context() as patch:
            patch.setattr(legacy_shapes, "atomic_write_json", fail_on_project)
            with pytest.raises(OSError, match="disk full"):
                runner.migrate_project_dir(project_dir)

    _migrate(project_dir)

    migrated = _read_json(project_path)
    for name, expected in (("Alice", b"bob-image"), ("Bob", b"image")):
        assert migrated["characters"][name]["reference_image"] == f"characters/refs/{name}.png"
        assert (project_dir / f"characters/refs/{name}.png").read_bytes() == expected


def test_shared_reference_copy_failure_keeps_the_displaced_original_in_the_retry_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = write_legacy_shared_reference_project(ProjectManager(tmp_path).projects_dir)
    project_path = project_dir / "project.json"
    project = _read_json(project_path)
    project["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION - 1
    project_path.write_text(json.dumps(project), encoding="utf-8")
    target = project_dir / "characters/refs/Bob.png"
    target.write_bytes(b"displaced-original")
    write = legacy_shapes.atomic_write_bytes

    def disk_full_on_copy(path: Path, content: bytes) -> None:
        if path == target:
            raise OSError("disk full")
        write(path, content)

    with monkeypatch.context() as patch:
        patch.setattr(legacy_shapes, "atomic_write_bytes", disk_full_on_copy)
        with pytest.raises(OSError, match="disk full"):
            runner.migrate_project_dir(project_dir)
    _migrate(project_dir)

    report = load_migration_report(project_dir)
    assert report is not None
    [copied] = [item for item in report.adjusted if item.file == "characters/refs/Bob.png"]
    assert copied.backup is not None
    assert (project_dir / copied.backup).read_bytes() == b"displaced-original"
    assert target.read_bytes() == b"image"
    assert len(list(target.parent.glob("Bob.png.kept.*"))) == 1


@pytest.mark.parametrize("from_version", [7, CURRENT_PROJECT_SCHEMA_VERSION - 1])
def test_reference_files_shared_with_another_asset_get_their_own_copy(tmp_path: Path, from_version: int) -> None:
    project_dir, *_ = _project(tmp_path)
    advance_project_schema(project_dir, to_version=from_version)
    contents = share_reference_files(project_dir)

    _migrate(project_dir)

    project = _read_json(project_dir / "project.json")
    qing, ali = project["characters"]["阿青"], project["characters"]["阿离"]
    assert (qing["reference_image"], qing["reference_audio"]) == (
        "characters/refs/阿青.png",
        "characters/refs_audio/阿青.wav",
    )
    assert (ali["reference_image"], ali["reference_audio"]) == (
        "characters/refs/阿离.png",
        "characters/refs_audio/阿离.wav",
    )
    assert project["products"]["茶"]["reference_images"] == ["products/refs/茶_1.png"]
    assert project["products"]["咖啡"]["reference_images"] == ["products/refs/咖啡_1.png"]
    copies = {"characters/refs/阿青.png": "characters/refs/阿离.png"} | {
        target: params["from"] for target, params in _SHARED_REFERENCE_COPIES
    }
    for target, source in copies.items():
        assert (project_dir / target).read_bytes() == contents[source]
        assert (project_dir / source).read_bytes() == contents[source]
    assert _copied_reference_files(project_dir) == _SHARED_REFERENCE_COPIES
    report = load_migration_report(project_dir)
    assert report is not None
    [stray_backup] = [item.backup for item in report.adjusted if item.backup is not None]
    assert (project_dir / stray_backup).read_bytes() == b"qing-stray"
    shown = WorkflowStateService(ProjectManager(str(tmp_path))).get_status("demo").migration_report
    assert shown is not None
    assert [item.code for item in shown.adjusted] == ["shared_reference_file_copied"] * 3


@pytest.mark.parametrize("resolved_project_dir", [False, True])
def test_a_reference_spelled_differently_still_gets_its_own_copy(tmp_path: Path, resolved_project_dir: bool) -> None:
    # 旧版 PATCH 原样收下任意字符串：带 ``..`` / ``.`` 段、反斜杠或项目内绝对路径，运行时照样读到别的资产名下的文件。
    project_dir, *_ = _project(tmp_path)
    advance_project_schema(project_dir, to_version=CURRENT_PROJECT_SCHEMA_VERSION - 1)
    contents = share_reference_files(project_dir)
    project = _read_json(project_dir / "project.json")
    base = Path(os.path.realpath(project_dir)) if resolved_project_dir else project_dir
    project["characters"]["阿青"]["reference_image"] = "characters/./refs/阿离.png"
    project["characters"]["阿青"]["reference_audio"] = (
        base / "characters/refs_audio/../refs_audio/阿离.wav"
    ).as_posix()
    project["products"]["茶"]["reference_images"] = ["products\\refs\\..\\refs\\咖啡_1.png"]
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    _migrate(project_dir)

    project = _read_json(project_dir / "project.json")
    qing = project["characters"]["阿青"]
    assert (qing["reference_image"], qing["reference_audio"]) == (
        "characters/refs/阿青.png",
        "characters/refs_audio/阿青.wav",
    )
    assert project["products"]["茶"]["reference_images"] == ["products/refs/茶_1.png"]
    assert (project_dir / qing["reference_image"]).read_bytes() == contents["characters/refs/阿离.png"]
    assert (project_dir / qing["reference_audio"]).read_bytes() == contents["characters/refs_audio/阿离.wav"]
    assert (project_dir / "products/refs/茶_1.png").read_bytes() == contents["products/refs/咖啡_1.png"]
    assert [params["from"] for _, params in _copied_reference_files(project_dir)] == [
        "characters/refs/阿离.png",
        "characters/refs_audio/阿离.wav",
        "products/refs/咖啡_1.png",
    ]


@pytest.mark.parametrize("from_version", [5, CURRENT_PROJECT_SCHEMA_VERSION - 1])
def test_an_asset_name_with_path_separators_gets_no_copy_outside_its_directory(
    tmp_path: Path, from_version: int
) -> None:
    # 导入包在校验资产名之前跑迁移链：名字里的 ``..`` 段不能把共用参考文件的副本带出项目目录。
    if from_version == 5:
        project_dir = write_legacy_shared_reference_project(ProjectManager(tmp_path).projects_dir)
        image, audio = "characters/refs/Alice.png", "characters/refs_audio/Alice.wav"
    else:
        project_dir, *_ = _project(tmp_path)
        advance_project_schema(project_dir, to_version=from_version)
        share_reference_files(project_dir)
        image, audio = "characters/refs/阿离.png", "characters/refs_audio/阿离.wav"
    project = _read_json(project_dir / "project.json")
    escaping = {"description": "escaping", "reference_image": image, "reference_audio": audio}
    project["characters"]["../../../../escaped"] = escaping
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    migrate_project_with_verdict(project_dir)

    assert list(tmp_path.rglob("escaped*")) == []
    entry = _read_json(project_dir / "project.json")["characters"]["../../../../escaped"]
    assert (entry["reference_image"], entry["reference_audio"]) == (image, audio)


def test_copying_shared_reference_files_converges_after_a_failed_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir, *_ = _project(tmp_path)
    advance_project_schema(project_dir, to_version=CURRENT_PROJECT_SCHEMA_VERSION - 1)
    share_reference_files(project_dir)
    record: list[Any] = []
    write = legacy_shapes.atomic_write_json

    def fail_on_project(path: Path, payload: Any) -> None:
        if path.name == "project.json":
            raise OSError("disk full")
        write(path, payload)

    # 副本都已落盘、保留副本也已挪出，改指的 project.json 提交失败。
    with monkeypatch.context() as patch:
        patch.setattr(legacy_shapes, "atomic_write_json", fail_on_project)
        with pytest.raises(OSError, match="disk full"):
            normalize_legacy_shapes(project_dir, schema_version=CURRENT_PROJECT_SCHEMA_VERSION - 1, record=record)
    normalize_legacy_shapes(project_dir, schema_version=CURRENT_PROJECT_SCHEMA_VERSION - 1, record=record)

    assert [(item.file, item.params) for item in record] == _SHARED_REFERENCE_COPIES
    assert record[0].backup is not None
    assert (project_dir / record[0].backup).read_bytes() == b"qing-stray"
    assert _read_json(project_dir / "project.json")["products"]["茶"]["reference_images"] == ["products/refs/茶_1.png"]
    assert not (project_dir / "products" / "refs" / "茶_2.png").exists()
    assert len(list((project_dir / "characters" / "refs").glob("阿青.png.kept.*"))) == 1


def _issue_codes(tmp_path: Path) -> set[str]:
    status = WorkflowStateService(ProjectManager(str(tmp_path))).get_status("demo")
    return {issue.code for issue in status.issues}


def _complete_first_segment(project_dir: Path) -> None:
    """补上样本分镜缺的原文，让剧本除引用之外本身合法：引用未登记是这一集唯一可能的结构问题。"""
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][0]["novel_text"] = "雨夜"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def test_references_to_a_dropped_case_variant_follow_the_kept_name(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    _complete_first_segment(project_dir)
    variant = add_asset_names_differing_only_in_case(project_dir, "characters", "character_sheet", "Alice")
    refer_to_asset_in_episode(project_dir, 1, "characters_in_segment", variant)

    _migrate(project_dir)

    [segment] = _read_json(project_dir / "scripts" / "episode_1.json")["segments"]
    assert segment["characters_in_segment"] == ["Alice"]
    assert segment["image_prompt"] == "@[Alice]阿离站在雨中"
    draft = _read_json(project_dir / "drafts" / "episode_1" / "script_plan_segments.json")
    assert draft["segments"][0]["characters_in_segment"] == ["Alice"]
    assert _adjusted(project_dir) == [
        ("duplicate_asset_removed", None, {"asset_type": "character", "name": variant, "kept": "Alice"}),
        ("duplicate_asset_references_rewritten", 1, {"asset_type": "character", "name": variant, "kept": "Alice"}),
    ]
    assert "invalid_script_structure" not in _issue_codes(tmp_path)


def test_dialogue_speakers_and_unit_text_mentions_follow_the_kept_name(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION - 1,
                "characters": {"Alice": {"description": "保留"}, "ALICE": {"description": "变体"}},
                "episodes": [
                    {"episode": 1, "script_file": "scripts/episode_1.json"},
                    {"episode": 2, "script_file": "scripts/episode_2.json"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "scripts").mkdir()
    (project_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "video_units": [{"unit_id": "E1U1", "text": "@[ALICE]：{你好}\n@[ALICE]推门，@[Alice]回头"}],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "scripts" / "episode_2.json").write_text(
        json.dumps(
            {
                "episode": 2,
                "scenes": [
                    {
                        "scene_id": "E2S01",
                        "characters_in_scene": ["ALICE", "Alice"],
                        "dialogue": [{"speaker": "ALICE", "line": "走吧"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    record: list = []

    normalize_legacy_shapes(project_dir, schema_version=CURRENT_PROJECT_SCHEMA_VERSION - 1, record=record)

    [unit] = _read_json(project_dir / "scripts" / "episode_1.json")["video_units"]
    assert unit["text"] == "@[Alice]：{你好}\n@[Alice]推门，@[Alice]回头"
    [scene] = _read_json(project_dir / "scripts" / "episode_2.json")["scenes"]
    # 改写后与保留名重复的引用只留一个。
    assert scene["characters_in_scene"] == ["Alice"]
    assert scene["dialogue"] == [{"speaker": "Alice", "line": "走吧"}]
    assert [(item.code, item.episode) for item in record] == [
        ("duplicate_asset_removed", None),
        ("duplicate_asset_references_rewritten", 1),
        ("duplicate_asset_references_rewritten", 2),
    ]


def test_references_rewritten_before_a_failed_write_stay_in_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION - 1,
                "characters": {"Alice": {"description": "保留"}, "ALICE": {"description": "变体"}},
                "episodes": [
                    {"episode": episode, "script_file": f"scripts/episode_{episode}.json"} for episode in (1, 2)
                ],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "scripts").mkdir()
    for episode in (1, 2):
        (project_dir / "scripts" / f"episode_{episode}.json").write_text(
            json.dumps(
                {"episode": episode, "segments": [{"segment_id": f"E{episode}S01", "characters_in_segment": ["ALICE"]}]}
            ),
            encoding="utf-8",
        )
    record: list = []
    write = legacy_shapes.atomic_write_json

    def fail_on_second_episode(path: Path, payload: Any) -> None:
        if path.name == "episode_2.json":
            raise OSError("disk full")
        write(path, payload)

    # 第 1 集已改写落盘、第 2 集写盘失败：重跑发现不了第 1 集的改写，报告要靠失败时的记录带到重试。
    with monkeypatch.context() as patch:
        patch.setattr(legacy_shapes, "atomic_write_json", fail_on_second_episode)
        with pytest.raises(OSError, match="disk full"):
            normalize_legacy_shapes(project_dir, schema_version=CURRENT_PROJECT_SCHEMA_VERSION - 1, record=record)
    normalize_legacy_shapes(project_dir, schema_version=CURRENT_PROJECT_SCHEMA_VERSION - 1, record=record)

    assert sorted((item.code, item.episode or 0) for item in record) == [
        ("duplicate_asset_references_rewritten", 1),
        ("duplicate_asset_references_rewritten", 2),
        ("duplicate_asset_removed", 0),
    ]


def _add_character_derivatives(project_dir: Path, owner: str, derivatives: dict[str, dict[str, str]]) -> None:
    project = _read_json(project_dir / "project.json")
    project["characters"][owner]["derivatives"] = derivatives
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")


def test_derivatives_of_a_dropped_case_variant_join_the_kept_name(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    _complete_first_segment(project_dir)
    advance_project_schema(project_dir, to_version=CURRENT_PROJECT_SCHEMA_VERSION - 1)
    variant = add_asset_names_differing_only_in_case(project_dir, "characters", "character_sheet", "Alice")
    hat_sheet, coat_sheet = "characters/derivatives/ALICE/Hat.png", "characters/derivatives/ALICE/Coat.png"
    (project_dir / hat_sheet).parent.mkdir(parents=True)
    (project_dir / hat_sheet).write_bytes(b"hat")
    (project_dir / coat_sheet).write_bytes(b"coat")
    _add_character_derivatives(project_dir, "Alice", {"coat": {"description": "保留方的外套", "character_sheet": ""}})
    _add_character_derivatives(
        project_dir,
        variant,
        {
            "Coat": {"description": "变体的外套", "character_sheet": coat_sheet},
            "Hat": {"description": "帽子", "character_sheet": hat_sheet},
        },
    )
    hat_key, coat_key = derivative_artifact_key(variant, "Hat"), derivative_artifact_key(variant, "Coat")
    for key, sheet in ((hat_key, hat_sheet), (coat_key, coat_sheet)):
        ArtifactManifest(ProjectArtifactManifestAdapter(project_dir)).register(
            key,
            artifact_path=sheet,
            basis=ArtifactBasis.build("test/derivative-sheet", kind_version=1, inputs={"sheet": sheet}),
        )
    hat_claim = ProjectArtifactManifestAdapter(project_dir).get_entry(hat_key)
    versions_path = project_dir / "versions" / "versions.json"
    versions = _read_json(versions_path) if versions_path.is_file() else {}
    hat_history = {
        "current_version": 1,
        "versions": [
            {
                "version": 1,
                "file": "versions/character_derivatives/ALICE/Hat_v1_20260302T145652.png",
                "prompt": "帽子",
                "created_at": "2026-03-02T14:56:52Z",
            }
        ],
    }
    versions.setdefault(CHARACTER_DERIVATIVE_RESOURCE_TYPE, {}).update(
        {f"{variant}/Hat": hat_history, f"{variant}/Coat": hat_history}
    )
    versions_path.parent.mkdir(parents=True, exist_ok=True)
    versions_path.write_text(json.dumps(versions, ensure_ascii=False), encoding="utf-8")
    refer_to_asset_in_episode(project_dir, 1, "characters_in_segment", f"{variant}/Coat")
    refer_to_asset_in_episode(project_dir, 1, "characters_in_segment", f"{variant}/Hat")

    _migrate(project_dir)

    # 迁走的衍生图搬到保留名下，之后改名衍生或本体时按规范路径搬图，条目里的路径对得上。
    kept_hat_sheet = "characters/derivatives/Alice/Hat.png"
    kept = _read_json(project_dir / "project.json")["characters"]["Alice"]
    assert kept["derivatives"] == {
        "coat": {"description": "保留方的外套", "character_sheet": ""},
        "Hat": {"description": "帽子", "character_sheet": kept_hat_sheet},
    }
    assert (project_dir / kept_hat_sheet).read_bytes() == b"hat"
    [segment] = _read_json(project_dir / "scripts" / "episode_1.json")["segments"]
    assert segment["characters_in_segment"] == ["Alice/coat", "Alice/Hat"]
    assert segment["image_prompt"].startswith("@[Alice/Hat]@[Alice/coat]")
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    assert hat_key not in entries
    assert hat_claim is not None
    assert entries[derivative_artifact_key("Alice", "Hat")] == replace(hat_claim, artifact_path=kept_hat_sheet)
    # 并入已有衍生的那条撤掉登记与历史：留着会按不区分大小写的路径占住保留方衍生的登记位。
    assert coat_key not in entries
    derivative_history = _read_json(versions_path)[CHARACTER_DERIVATIVE_RESOURCE_TYPE]
    assert f"{variant}/Hat" not in derivative_history
    assert f"{variant}/Coat" not in derivative_history
    assert derivative_history["Alice/Hat"] == hat_history
    merged = [
        params for code, _episode, params in _adjusted(project_dir) if code == "duplicate_asset_derivative_merged"
    ]
    assert merged == [
        {"name": variant, "kept": "Alice", "derivative": "Coat", "into": "coat", "folded": "true"},
        {"name": variant, "kept": "Alice", "derivative": "Hat", "into": "Hat"},
    ]
    assert "invalid_script_structure" not in _issue_codes(tmp_path)


def test_a_moved_derivative_without_its_sheet_on_disk_still_points_under_the_kept_name(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    hat = {"description": "帽子", "character_sheet": "characters/derivatives/ALICE/Hat.png"}
    characters = {"Alice": {"description": "保留"}, "ALICE": {"description": "变体", "derivatives": {"Hat": hat}}}
    (project_dir / "project.json").write_text(
        json.dumps({"schema_version": CURRENT_PROJECT_SCHEMA_VERSION - 1, "characters": characters, "episodes": []}),
        encoding="utf-8",
    )

    normalize_legacy_shapes(project_dir, schema_version=CURRENT_PROJECT_SCHEMA_VERSION - 1, record=[])

    # 与改名衍生一致：路径跟着本体走，不看图在不在盘上。
    [kept] = _read_json(project_dir / "project.json")["characters"].values()
    assert kept["derivatives"]["Hat"]["character_sheet"] == "characters/derivatives/Alice/Hat.png"


def test_derivatives_of_several_case_variants_fold_into_each_other(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    characters = {
        "Alice": {"description": "保留"},
        "ALICE": {"description": "变体", "derivatives": {"Hat": {"description": "变体的帽子", "character_sheet": ""}}},
        "alice": {"description": "变体", "derivatives": {"hat": {"description": "另一顶帽子", "character_sheet": ""}}},
    }
    (project_dir / "project.json").write_text(
        json.dumps({"schema_version": CURRENT_PROJECT_SCHEMA_VERSION - 1, "characters": characters, "episodes": []}),
        encoding="utf-8",
    )

    normalize_legacy_shapes(project_dir, schema_version=CURRENT_PROJECT_SCHEMA_VERSION - 1, record=[])

    # 后一条变体的 hat 与前一条迁入的 Hat 只差大小写，并入它。
    assert _read_json(project_dir / "project.json")["characters"] == {
        "Alice": {"description": "保留", "derivatives": {"Hat": {"description": "变体的帽子", "character_sheet": ""}}}
    }


def _variant_project(root: Path) -> Path:
    project_dir, *_ = _project(root)
    variant = add_asset_names_differing_only_in_case(project_dir, "characters", "character_sheet", "Alice")
    refer_to_asset_in_episode(project_dir, 1, "characters_in_segment", variant)
    return project_dir


def _fail_committing_without_variant(patch: pytest.MonkeyPatch) -> None:
    """让去掉变体 ``ALICE`` 的那次 ``project.json`` 写盘失败：引用已改写落盘，资产表还没提交。"""

    write = legacy_shapes.atomic_write_json

    def fail_without_variant(path: Path, payload: Any) -> None:
        if path.name == "project.json" and "ALICE" not in payload.get("characters", {}):
            raise OSError("disk full")
        write(path, payload)

    patch.setattr(legacy_shapes, "atomic_write_json", fail_without_variant)


def _migrated_state(project_dir: Path) -> tuple[Any, ...]:
    report = load_migration_report(project_dir)
    assert report is not None
    return (
        {key: value for key, value in _read_json(project_dir / "project.json").items() if key != "episodes"},
        _read_json(project_dir / "scripts" / "episode_1.json"),
        _read_json(project_dir / "drafts" / "episode_1" / "script_plan_segments.json"),
        # 中途失败时先落盘的改动先记下，重跑后报告里的先后与一次跑完不同，内容一致。
        sorted((item.code, item.episode or 0, sorted(item.params.items())) for item in report.adjusted),
    )


def test_a_normalization_that_fails_after_rewriting_references_converges_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    once = _variant_project(tmp_path / "once")
    _migrate(once)
    retried = _variant_project(tmp_path / "retried")

    # 引用已改写落盘、资产表还没提交时失败：重跑仍能发现变体，结果与一次跑完一致。
    with monkeypatch.context() as patch:
        _fail_committing_without_variant(patch)
        assert migrate_project_with_verdict(retried) is not None
    assert "ALICE" in _read_json(retried / "project.json")["characters"]
    _migrate(retried)

    assert _migrated_state(retried) == _migrated_state(once)


@pytest.mark.parametrize("fails_once", [False, True], ids=["one-pass", "retried"])
def test_a_confirmed_script_plan_stays_confirmed_after_its_references_are_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails_once: bool
) -> None:
    project_dir, *_ = _project(tmp_path)
    advance_project_schema(project_dir, to_version=CURRENT_PROJECT_SCHEMA_VERSION - 1)
    variant = add_asset_names_differing_only_in_case(project_dir, "characters", "character_sheet", "Alice")
    refer_to_asset_in_episode(project_dir, 1, "characters_in_segment", variant)
    plan_path = project_dir / "drafts" / "episode_1" / "script_plan_segments.json"
    project = _read_json(project_dir / "project.json")
    project["episodes"][0]["script_plan_review"] = {
        "fingerprint": content_fingerprint(plan_path),
        "confirmed_at": "2026-03-02T14:56:52Z",
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    if fails_once:
        with monkeypatch.context() as patch:
            _fail_committing_without_variant(patch)
            assert migrate_project_with_verdict(project_dir) is not None
    _migrate(project_dir)

    assert _read_json(plan_path)["segments"][0]["characters_in_segment"] == ["Alice"]
    [entry] = _read_json(project_dir / "project.json")["episodes"]
    assert entry["script_plan_review"] == {
        "fingerprint": content_fingerprint(plan_path),
        "confirmed_at": "2026-03-02T14:56:52Z",
    }


def test_a_confirmed_script_plan_under_pre_v10_names_stays_confirmed_after_its_references_are_rewritten(
    tmp_path: Path,
) -> None:
    project_dir = write_legacy_script_plan_project(tmp_path, variant="narration", schema_version=9)
    project = _read_json(project_dir / "project.json")
    project["characters"] = {"Alice": {"description": "保留"}, "ALICE": {"description": "变体"}}
    # 第 2 集只有已确认的脚本规划，正式脚本要由 v14→v15 按这份确认转出。
    legacy_plan = project_dir / "drafts" / "episode_2" / "step1_segments.json"
    plan = _read_json(legacy_plan)
    plan["segments"][0]["characters_in_segment"] = ["ALICE"]
    legacy_plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    project["episodes"][1]["step1_review"]["fingerprint"] = content_fingerprint(legacy_plan)
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    _migrate(project_dir)

    plan_path = project_dir / "drafts" / "episode_2" / "script_plan_segments.json"
    assert _read_json(plan_path)["segments"][0]["characters_in_segment"] == ["Alice"]
    entry = _read_json(project_dir / "project.json")["episodes"][1]
    assert entry["script_plan_review"]["fingerprint"] == content_fingerprint(plan_path)
    assert (project_dir / "scripts" / "episode_2.json").is_file()


def test_missing_and_duplicate_item_ids_are_reassigned_and_the_original_media_stays_with_the_first(
    tmp_path: Path,
) -> None:
    project_dir, *_ = _project(tmp_path)
    break_script_item_ids(project_dir, 1)

    _migrate(project_dir)

    segments = _read_json(project_dir / "scripts" / "episode_1.json")["segments"]
    assert [segment["segment_id"] for segment in segments] == ["E1S01", "E1S02", "E1S03"]
    assert segments[0]["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S01.png"
    assert segments[1]["generated_assets"]["storyboard_image"] is None
    assert segments[1]["image_prompt"] == "另一镜"
    assert _adjusted(project_dir) == [
        ("item_id_reassigned", 1, {"from": "E1S01", "to": "E1S02"}),
        ("item_id_reassigned", 1, {"from": "", "to": "E1S03"}),
    ]
    assert (project_dir / "storyboards" / "scene_E1S01.png").is_file()


def test_a_renumbered_item_lets_go_of_the_end_frame_that_belongs_to_the_original_id(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    end_frame = resource_relative_path(END_FRAME_RESOURCE_TYPE, "E1S01")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][0]["end_frame_image"] = end_frame
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    break_script_item_ids(project_dir, 1)

    _migrate(project_dir)

    first, renumbered, _ = _read_json(script_path)["segments"]
    assert first["end_frame_image"] == end_frame
    assert renumbered["segment_id"] == "E1S02"
    assert "end_frame_image" not in renumbered


def test_a_renumbered_item_reads_as_not_yet_generated(tmp_path: Path) -> None:
    """换号条目不带走原 id 那一条的缩略图、视频 URI、宫格归属与生成时刻，落盘的状态也复位。"""
    project_dir, *_ = _project(tmp_path)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    generated = {
        "storyboard_image": "storyboards/scene_E1S01.png",
        "storyboard_last_image": "storyboards/scene_E1S01_last.png",
        "grid_id": "grid_123456789abc",
        "grid_cell_index": 0,
        "video_clip": "videos/scene_E1S01.mp4",
        "video_thumbnail": "thumbnails/scene_E1S01.jpg",
        "video_uri": "https://example.com/E1S01.mp4",
        "video_generated_at": "2026-01-01T00:00:00Z",
        "status": "completed",
    }
    script["segments"][0]["generated_assets"] = dict(generated)
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (project_dir / "videos").mkdir(exist_ok=True)
    (project_dir / "videos" / "scene_E1S01.mp4").write_bytes(b"video")
    break_script_item_ids(project_dir, 1)

    _migrate(project_dir)

    first, renumbered, _ = _read_json(script_path)["segments"]
    assert first["generated_assets"] == generated
    assert renumbered["segment_id"] == "E1S02"
    assert {key: value for key, value in renumbered["generated_assets"].items() if value is not None} == {
        "status": "pending"
    }


@pytest.mark.parametrize(
    ("item_id", "owner", "renumbered_to"),
    [
        pytest.param("E2S01", 2, ("E1S01", 1), id="the-episode-the-id-prefix-names"),
        pytest.param("S01", 1, ("E2S01", 2), id="the-first-episode-in-the-ledger"),
    ],
)
def test_an_item_id_two_episodes_share_stays_with_one_and_is_renumbered_in_the_other(
    tmp_path: Path, item_id: str, owner: int, renumbered_to: tuple[str, int]
) -> None:
    project_dir, *_ = _project(tmp_path)
    reuse_item_id_across_episodes(project_dir, item_id)

    _migrate(project_dir)

    new_id, other = renumbered_to
    owned = _read_json(project_dir / "scripts" / f"episode_{owner}.json")["segments"][0]
    moved = _read_json(project_dir / "scripts" / f"episode_{other}.json")["segments"][0]
    assert (owned["segment_id"], owned["generated_assets"]["storyboard_image"]) == (
        item_id,
        f"storyboards/scene_{item_id}.png",
    )
    assert (moved["segment_id"], moved["generated_assets"]["storyboard_image"]) == (new_id, None)
    assert (project_dir / "storyboards" / f"scene_{item_id}.png").read_bytes() == b"shared storyboard"
    assert ("item_id_reassigned", other, {"from": item_id, "to": new_id}) in _adjusted(project_dir)
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    assert ArtifactKey.episode_storyboard(owner, item_id) in entries
    assert ArtifactKey.episode_storyboard(other, item_id) not in entries


def test_duplicate_ids_in_a_legacy_ad_shot_index_are_renumbered(tmp_path: Path) -> None:
    project_dir = write_legacy_ad_reference_video_project(tmp_path / "projects")
    duplicate_legacy_ad_ids(project_dir)

    _migrate(project_dir)

    units = _read_json(project_dir / "scripts" / "episode_1.json")["video_units"]
    assert units[0]["unit_id"] == "E1U01"
    assert "needs_replan" not in units[0]
    assert all(unit.get("needs_replan") is True for unit in units[1:])
    renumbered = [params["from"] for code, _episode, params in _adjusted(project_dir) if code == "item_id_reassigned"]
    assert sorted(renumbered) == ["E1S1", "E1U01"]


def test_ad_renumbering_survives_a_v6_step_that_fails_after_writing_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = write_legacy_ad_reference_video_project(tmp_path / "projects")
    duplicate_legacy_ad_ids(project_dir)
    write = v6_to_v7_ad_reference_video_units.atomic_write_json

    def fail_on_project_json(path: Path, payload: Any) -> None:
        if path.name == "project.json":
            raise OSError("disk full")
        write(path, payload)

    # 剧本已改成新单元并换了号，提交 project.json 时失败：重跑时剧本里已没有可发现的换号。
    with monkeypatch.context() as patch:
        patch.setattr(v6_to_v7_ad_reference_video_units, "atomic_write_json", fail_on_project_json)
        assert migrate_project_with_verdict(project_dir) is not None

    _migrate(project_dir)

    renumbered = [params["from"] for code, _episode, params in _adjusted(project_dir) if code == "item_id_reassigned"]
    assert sorted(renumbered) == ["E1S1", "E1U01"]


def test_a_reference_unit_whose_text_comes_out_blank_is_marked_for_replanning(tmp_path: Path) -> None:
    project_dir = write_legacy_reference_video_project(tmp_path / "projects", split_unit_text=True)
    blank_reference_unit_shots(project_dir, "E1U01")

    _migrate(project_dir)

    first, second = _read_json(project_dir / "scripts" / "episode_1.json")["video_units"]
    assert (first["text"], first["duration_seconds"], first["needs_replan"]) == ("", 0, True)
    assert second["text"] == "第2个单元的画面描述。"
    report = load_migration_report(project_dir)
    assert report is None or not [item for item in report.skipped if item.resource_id == "E1U01"]


def test_a_corrupt_grid_record_is_kept_as_is_and_reported(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    record = write_unreadable_grid_record(project_dir)

    _migrate(project_dir)

    assert record.read_bytes() == b"{not json"
    assert ("episode-grid", "grids/grid_0123456789ab.json") in _skipped(project_dir)


def test_a_corrupt_script_plan_draft_is_kept_as_is_and_reported(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    draft = write_unreadable_script_plan_draft(project_dir, 1, "script_plan_segments.json")

    _migrate(project_dir)

    assert draft.read_bytes() == b"{not json"
    assert ("episode-script-plan", "drafts/episode_1/script_plan_segments.json") in _skipped(project_dir)
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    assert ArtifactKey.episode_script(1) in entries


def test_a_source_that_is_not_utf8_leaves_the_script_plan_unregistered(tmp_path: Path) -> None:
    """草稿本身读得出、只是依据算不出：不登记、读时报 missing，不进「未能带入」。"""
    project_dir, *_ = _project(tmp_path)
    source = write_non_utf8_episode_source(project_dir, 1, "雨夜")

    _migrate(project_dir)

    assert source.read_bytes() == "雨夜".encode("gbk")
    report = load_migration_report(project_dir)
    assert report is None or not [item for item in report.skipped if item.kind == "episode-script-plan"]
    assert ArtifactKey.episode_script_plan(1) not in ProjectArtifactManifestAdapter(project_dir).snapshot_entries()


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions that the current user cannot bypass"
)
def test_an_unreadable_image_stays_in_place_and_is_reported(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    image = project_dir / "storyboards" / "scene_E1S01.png"
    image.chmod(0)
    try:
        if os.access(image, os.R_OK):
            pytest.skip("running with privileges that ignore file permissions")
        _migrate(project_dir)
    finally:
        image.chmod(0o644)

    assert image.read_bytes() == b"storyboard"
    assert ("episode-storyboard", "storyboards/scene_E1S01.png") in _skipped(project_dir)


def _write_storyboard_project_with_a_video(tmp_path: Path) -> Path:
    return write_legacy_storyboard_project(tmp_path / "projects")


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions that the current user cannot bypass"
)
@pytest.mark.parametrize(
    ("make_project", "make_file", "kind"),
    [
        pytest.param(
            _write_storyboard_project_with_a_video,
            lambda project_dir: project_dir / "videos" / "scene_E1S1.mp4",
            "episode-video",
            id="current-video",
        ),
        pytest.param(
            lambda tmp_path: _project(tmp_path)[0],
            write_unreadable_grid_record,
            "episode-grid",
            id="grid-record",
        ),
        pytest.param(
            lambda tmp_path: _project(tmp_path)[0],
            lambda project_dir: write_unreadable_script_plan_draft(project_dir, 1, "script_plan_segments.json"),
            "episode-script-plan",
            id="script-plan-draft",
        ),
    ],
)
def test_a_file_the_process_cannot_open_stays_in_place_and_is_reported(
    tmp_path: Path, make_project: Any, make_file: Any, kind: str
) -> None:
    project_dir = make_project(tmp_path)
    locked = make_file(project_dir)
    content = locked.read_bytes()
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with privileges that ignore file permissions")
        _migrate(project_dir)
    finally:
        locked.chmod(0o644)

    assert locked.read_bytes() == content
    assert (kind, locked.relative_to(project_dir).as_posix()) in _skipped(project_dir)


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions that the current user cannot bypass"
)
def test_a_schema_8_reference_draft_the_process_cannot_open_is_left_as_is(tmp_path: Path) -> None:
    """v8→v9 改写参考生视频的脚本规划草稿：打不开的草稿原样留下，不拒绝整个项目。

    草稿已是新名，之后的 v9→v10 改名不碰它；旧名草稿打不开时改名前的备份同样读不出，那一步照旧失败。
    """
    project_dir = write_legacy_reference_video_project(tmp_path / "projects", schema_version=8)
    draft = project_dir / "drafts" / "episode_1" / "script_plan_reference_units.json"
    draft.write_text(json.dumps({"video_units": []}), encoding="utf-8")
    content = draft.read_bytes()
    draft.chmod(0)
    try:
        _migrate(project_dir)
    finally:
        draft.chmod(0o644)

    assert draft.read_bytes() == content


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions that the current user cannot bypass"
)
def test_a_video_whose_selected_snapshot_cannot_be_opened_is_reported(tmp_path: Path) -> None:
    project_dir = _write_storyboard_project_with_a_video(tmp_path)
    versions = _read_json(project_dir / "versions" / "versions.json")
    snapshot = project_dir / versions["videos"]["E1S1"]["versions"][0]["file"]
    snapshot.chmod(0)
    try:
        if os.access(snapshot, os.R_OK):
            pytest.skip("running with privileges that ignore file permissions")
        _migrate(project_dir)
    finally:
        snapshot.chmod(0o644)

    assert ("episode-video", "videos/scene_E1S1.mp4") in _skipped(project_dir)


@pytest.mark.parametrize(
    "unreadable",
    [
        pytest.param('{"episode": 1, "segments": [', id="not-json"),
        pytest.param("[]", id="root-not-an-object"),
    ],
)
def test_adjustments_survive_a_chain_that_fails_and_is_retried(tmp_path: Path, unreadable: str) -> None:
    project_dir, *_ = _project(tmp_path)
    add_duplicate_ledger_entry(project_dir, 1, "episode_1_copy.json")
    script_path = project_dir / "scripts" / "episode_1.json"
    script_bytes = script_path.read_bytes()
    # 剧本读不出仍拒绝整个项目：链停在半路，已做的规整写进未完成的报告。
    script_path.write_text(unreadable, encoding="utf-8")

    assert migrate_project_with_verdict(project_dir) is not None
    unfinished = load_migration_report(project_dir)
    assert unfinished is not None
    assert not unfinished.complete
    assert [item.code for item in unfinished.adjusted] == ["duplicate_episode_entry_removed"]

    script_path.write_bytes(script_bytes)
    _migrate(project_dir)

    assert [code for code, *_ in _adjusted(project_dir)] == ["duplicate_episode_entry_removed"]


def test_an_unreadable_script_under_a_noncanonical_binding_stays_where_it_is(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    bind_episode_script_to_filename(project_dir, 1, "custom.json")
    bound = project_dir / "scripts" / "custom.json"
    bound.write_text('{"episode": 1, "segments": [', encoding="utf-8")
    unreadable = bound.read_bytes()

    # 剧本读不出照旧拒绝整个项目；规整不挪它、也不改绑，拒绝点名的仍是盘上原来的文件。
    assert migrate_project_with_verdict(project_dir) is not None

    assert bound.read_bytes() == unreadable
    assert not (project_dir / "scripts" / "episode_1.json").exists()
    [entry] = _read_json(project_dir / "project.json")["episodes"]
    assert entry["script_file"] == "scripts/custom.json"


def test_adjustments_already_on_disk_survive_a_normalization_that_fails_halfway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir, *_ = _project(tmp_path)
    set_script_episode(project_dir, 1, None)
    break_script_item_ids(project_dir, 1)
    write = legacy_shapes.atomic_write_json
    script_writes = 0

    def fail_on_second_script_write(path: Path, payload: Any) -> None:
        nonlocal script_writes
        if path.name == "episode_1.json":
            script_writes += 1
            if script_writes == 2:
                raise OSError("disk full")
        write(path, payload)

    # 剧本集号已写回盘上，之后换条目 id 时写盘失败：重跑时规整发现不了集号的改动，只能靠未完成的报告带过去。
    with monkeypatch.context() as patch:
        patch.setattr(legacy_shapes, "atomic_write_json", fail_on_second_script_write)
        assert migrate_project_with_verdict(project_dir) is not None
    unfinished = load_migration_report(project_dir)
    assert unfinished is not None
    assert not unfinished.complete
    assert [item.code for item in unfinished.adjusted] == ["script_episode_corrected"]

    _migrate(project_dir)

    assert _adjusted(project_dir) == [
        ("script_episode_corrected", 1, {"from": "", "to": "1"}),
        ("item_id_reassigned", 1, {"from": "E1S01", "to": "E1S02"}),
        ("item_id_reassigned", 1, {"from": "", "to": "E1S03"}),
    ]


def test_adjustments_survive_a_last_step_that_fails_after_committing_the_current_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最后一步写下当前版本后才失败：重跑不再进链，未完成的报告补写成完成的报告，照常显示。"""
    project_dir, *_ = _project(tmp_path)
    add_duplicate_ledger_entry(project_dir, 1, "episode_1_copy.json")
    last_step = runner.MIGRATORS[CURRENT_PROJECT_SCHEMA_VERSION - 1]

    def commit_then_fail(path: Path) -> object:
        last_step(path)
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setitem(runner.MIGRATORS, CURRENT_PROJECT_SCHEMA_VERSION - 1, commit_then_fail)
        assert migrate_project_with_verdict(project_dir) is not None
    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert visible_migration_report(project_dir) is None

    _migrate(project_dir)

    report = visible_migration_report(project_dir)
    assert report is not None
    assert report.to_schema_version == CURRENT_PROJECT_SCHEMA_VERSION
    assert [code for code, *_ in _adjusted(project_dir)] == ["duplicate_episode_entry_removed"]


def test_adjustments_survive_a_failed_final_report_write_and_show_after_the_next_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """链走完、收尾那次写报告失败：调整与跳过留在未完成的报告里，下次启动扫描时补写成完成的报告。"""
    project_dir, *_ = _project(tmp_path)
    add_duplicate_ledger_entry(project_dir, 1, "episode_1_copy.json")
    write_unreadable_grid_record(project_dir)
    write = runner.write_migration_report

    def fail_complete_report(path: Path, report: MigrationReport) -> None:
        if report.complete:
            raise OSError("disk full")
        write(path, report)

    with monkeypatch.context() as patch:
        patch.setattr(runner, "write_migration_report", fail_complete_report)
        assert migrate_project_with_verdict(project_dir) is not None
    unfinished = load_migration_report(project_dir)
    assert unfinished is not None
    assert not unfinished.complete

    summary = runner.run_project_migrations(project_dir.parent)

    assert summary.skipped == [project_dir.name]
    assert visible_migration_report(project_dir) is not None
    assert [code for code, *_ in _adjusted(project_dir)] == ["duplicate_episode_entry_removed"]
    assert ("episode-grid", "grids/grid_0123456789ab.json") in _skipped(project_dir)


def test_an_unreadable_script_two_episodes_share_keeps_both_bindings(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    share_episode_script(project_dir, 1, with_episode=2)
    (project_dir / "scripts" / "episode_1.json").write_text('{"episode": 1, "segments": [', encoding="utf-8")
    ledger = _read_json(project_dir / "project.json")["episodes"]

    # 剧本读不出照旧拒绝整个项目；规整不拆绑定，账本原样留下。
    assert migrate_project_with_verdict(project_dir) is not None

    assert _read_json(project_dir / "project.json")["episodes"] == ledger


_CROSSED = {1: "episode_2.json", 2: "custom.json"}
_SWAPPED = {1: "episode_2.json", 2: "episode_1.json"}


@pytest.mark.parametrize(
    ("filenames", "ledger_order"),
    [
        pytest.param(_CROSSED, [2, 1], id="crossed-later-episode-first"),
        pytest.param(_CROSSED, [1, 2], id="crossed-earlier-episode-first"),
        pytest.param(_SWAPPED, [1, 2], id="swapped"),
    ],
)
def test_crossed_bindings_each_land_on_their_own_canonical_script(
    tmp_path: Path, filenames: dict[int, str], ledger_order: list[int]
) -> None:
    project_dir, *_ = _project(tmp_path)
    cross_bind_episode_scripts(project_dir, filenames, ledger_order=ledger_order)

    _migrate(project_dir)

    for episode, title in ((1, "第一集"), (2, "第2集")):
        script = _read_json(project_dir / "scripts" / f"episode_{episode}.json")
        assert (script["episode"], script["title"]) == (episode, title)
    # 没有剧本被挪成保留副本，交叉挪动的临时名也没留下。
    leftovers = [path.name for path in (project_dir / "scripts").iterdir() if ".bak." not in path.name]
    assert sorted(leftovers) == ["episode_1.json", "episode_2.json"]
    report = load_migration_report(project_dir)
    assert report is not None
    # 改绑按挪动的先后提交，报告也按这个顺序记。
    assert sorted((item.code, item.episode, item.backup) for item in report.adjusted) == [
        ("script_binding_canonicalized", 1, None),
        ("script_binding_canonicalized", 2, None),
    ]


def test_records_pointing_at_swapped_bindings_follow_their_own_episode(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    cross_bind_episode_scripts(project_dir, _SWAPPED, ledger_order=[1, 2])
    versions_path = project_dir / "versions" / "versions.json"
    versions_path.parent.mkdir(exist_ok=True)
    versions_path.write_text(
        json.dumps(
            {
                "videos": {
                    resource_id: {"versions": [{"version": 1, "execution_script_file": bound}]}
                    for resource_id, bound in (("E1S01", "episode_2.json"), ("E2S01", "scripts/episode_1.json"))
                }
            }
        ),
        encoding="utf-8",
    )

    normalize_legacy_shapes(project_dir, schema_version=7, record=[])

    videos = _read_json(versions_path)["videos"]
    # 写回不带目录的文件名，与生成任务、宫格请求写下的形态一致。
    assert {
        resource_id: resource["versions"][0]["execution_script_file"] for resource_id, resource in videos.items()
    } == {
        "E1S01": "episode_1.json",
        "E2S01": "episode_2.json",
    }


@pytest.mark.parametrize(
    ("filenames", "interrupted_at"),
    [
        pytest.param(_CROSSED, 1, id="crossed-after-first-move"),
        pytest.param(_CROSSED, 2, id="crossed-after-second-move"),
        pytest.param(_SWAPPED, 1, id="swapped-after-staging"),
        pytest.param(_SWAPPED, 2, id="swapped-after-first-move"),
        pytest.param(_SWAPPED, 3, id="swapped-after-second-move"),
    ],
)
def test_a_rebind_interrupted_between_a_move_and_its_commit_finishes_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filenames: dict[int, str], interrupted_at: int
) -> None:
    project_dir, *_ = _project(tmp_path)
    cross_bind_episode_scripts(project_dir, filenames, ledger_order=[2, 1])
    write = legacy_shapes.atomic_write_json
    commits = 0

    def interrupt(path: Path, payload: Any) -> None:
        nonlocal commits
        # 数改绑提交而不是 project.json 的写盘次数：别的步骤多写一次，中断点也不会错位。
        if _commits_a_rebind(path, payload):
            commits += 1
            if commits == interrupted_at:
                # 每挪一次剧本就提交一次账本：剧本已挪到位、账本还没改，进程在这里停下。
                raise KeyboardInterrupt
        write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(legacy_shapes, "atomic_write_json", interrupt)
        with pytest.raises(KeyboardInterrupt):
            normalize_legacy_shapes(project_dir, schema_version=7, record=[])

    _migrate(project_dir)

    for episode, title in ((1, "第一集"), (2, "第2集")):
        script = _read_json(project_dir / "scripts" / f"episode_{episode}.json")
        assert (script["episode"], script["title"]) == (episode, title)
    leftovers = [path.name for path in (project_dir / "scripts").iterdir() if ".bak." not in path.name]
    assert sorted(leftovers) == ["episode_1.json", "episode_2.json"]
    # 中断前已改绑到临时名的集，报告仍写原来的绑定。
    report = load_migration_report(project_dir)
    assert report is not None
    for item in report.adjusted:
        if item.code == "script_binding_canonicalized":
            assert item.episode is not None
            assert item.params["from"] == f"scripts/{filenames[item.episode]}"


def test_a_kept_copy_made_before_a_failed_rebind_commit_stays_in_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir, *_ = _project(tmp_path)
    bind_episode_script_to_filename(project_dir, 1, "custom.json")
    stray = project_dir / "scripts" / "episode_1.json"
    stray.write_text('{"episode": 1, "title": "游离的旧稿", "segments": []}', encoding="utf-8")
    stray_bytes = stray.read_bytes()

    write = legacy_shapes.atomic_write_json

    def fail_on_rebind_commit(path: Path, payload: Any) -> None:
        if _commits_a_rebind(path, payload):
            raise OSError("disk full")
        write(path, payload)

    # 规范路径上的旧稿已挪成保留副本、剧本也挪到位，提交账本时失败。
    with monkeypatch.context() as patch:
        patch.setattr(legacy_shapes, "atomic_write_json", fail_on_rebind_commit)
        assert migrate_project_with_verdict(project_dir) is not None

    _migrate(project_dir)

    report = load_migration_report(project_dir)
    assert report is not None
    [adjustment] = [item for item in report.adjusted if item.code == "script_binding_canonicalized"]
    assert adjustment.backup is not None
    assert (project_dir / adjustment.backup).read_bytes() == stray_bytes


def test_the_status_shows_the_report_until_it_is_acknowledged(tmp_path: Path) -> None:
    project_dir, *_ = _project(tmp_path)
    add_colliding_asset_name(project_dir, "characters", "阿离")
    _migrate(project_dir)
    service = WorkflowStateService(ProjectManager(str(tmp_path)))

    shown = service.get_status("demo").migration_report
    assert shown is not None
    assert [item.code for item in shown.adjusted] == ["duplicate_asset_removed"]

    assert acknowledge_migration_report(project_dir) is True
    assert service.get_status("demo").migration_report is None
