"""v14→v15：正式脚本成为唯一内容真相后，存量项目在用户口径上可继续制作，剧本登记不因依据改版翻过期。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

import pytest

from lib.artifact_currency import ArtifactCurrencyResolver
from lib.artifact_manifest import ArtifactBasis, ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifact_provenance import build_episode_script_basis, project_episode_script_prompt_inputs
from lib.grid.models import GridGeneration
from lib.grid_manager import GridManager
from lib.narration_delivery import POST_PRODUCTION
from lib.project_manager import ProjectManager
from lib.project_migration_report import MigrationNormalizedBinding, load_migration_report
from lib.project_migrations.runner import migrate_project_dir
from lib.project_migrations.v14_to_v15_formal_script_truth import migrate_v14_to_v15
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script_generator import PromptAuthoringTargetError, ScriptGenerator
from lib.script_review import content_fingerprint, formal_script_filename, prompt_authoring_generated, review_status
from lib.speech_presentation import presentation_artifact_paths
from lib.workflow_state import WorkflowStateService
from tests.legacy_project_shapes import (
    ScriptPlanVariantName,
    advance_project_schema,
    bind_episode_script_to_filename,
    write_legacy_script_plan_project,
)

_VARIANTS: tuple[ScriptPlanVariantName, ...] = ("drama", "narration", "reference_video")
_PLAN_FILES = {
    "drama": "script_plan_normalized_script.json",
    "narration": "script_plan_segments.json",
    "reference_video": "script_plan_reference_units.json",
}
_SCRIPT = ArtifactKey.episode_script(1)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _script_status(project_dir: Path, key: ArtifactKey) -> str:
    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
    assert entry is not None
    return ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=entry.artifact_path).status.value


def _legacy_script_digest(project: dict[str, Any], plan_document: object) -> str:
    """v2 剧本依据（以脚本规划内容为输入）的摘要：历史事实，按当时的形状写死。"""

    return ArtifactBasis.build(
        "structured-content/episode-script",
        kind_version=2,
        inputs={
            "content_mode": project["content_mode"],
            "generation_mode": project["generation_mode"],
            "step1_content": plan_document,
            "prompt_context": project_episode_script_prompt_inputs(project),
        },
    ).digest


def _planless_script_digest(episode: int) -> str:
    return ArtifactBasis.build(
        "structured-content/episode-script-without-plan",
        kind_version=1,
        inputs={"episode": episode},
    ).digest


def _project_at_v14(root: Path, variant: ScriptPlanVariantName = "narration") -> Path:
    project_dir = write_legacy_script_plan_project(root, variant=variant, schema_version=7)
    advance_project_schema(project_dir, to_version=14)
    return project_dir


def _put_script_digest(project_dir: Path, digest: str) -> None:
    ProjectArtifactManifestAdapter(project_dir).put_entry(
        _SCRIPT, ArtifactManifestEntry(artifact_path="scripts/episode_1.json", basis_digest=digest)
    )


@pytest.mark.parametrize("variant", _VARIANTS)
def test_whole_chain_leaves_every_episode_ready_for_the_next_step(
    tmp_path: Path, variant: ScriptPlanVariantName
) -> None:
    root = tmp_path / "projects"
    project_dir = write_legacy_script_plan_project(root, variant=variant, schema_version=7)

    assert migrate_project_dir(project_dir) is True

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    service = WorkflowStateService(ProjectManager(root))
    materialized = service.get_status(project_dir.name, 2)
    assert materialized.next_action is not None
    assert materialized.next_action.type.value == "author_prompts"
    unit = "U" if variant == "reference_video" else "S"
    assert materialized.next_action.requested_ids == [f"E2{unit}01", f"E2{unit}02"]
    for episode in (1, 2, 3):
        status = service.get_status(project_dir.name, episode)
        assert status.artifacts["script"]["state"] == "current"
        assert status.state not in {"SCRIPT_PLAN_CONTENT", "SCRIPT_PLAN_REVIEW"}
        assert review_status(project_dir, project, episode) == "confirmed"
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.registered["episode-script"] == 3


def test_rerunning_the_script_plan_of_a_grandfathered_episode_only_asks_for_confirmation(tmp_path: Path) -> None:
    """grandfather 集记下确认基线后，重跑脚本规划回到待确认，但正式脚本照常在用、不挡下游。"""

    root = tmp_path / "projects"
    project_dir = write_legacy_script_plan_project(root, variant="narration", schema_version=7)
    migrate_project_dir(project_dir)
    plan_path = project_dir / "drafts" / "episode_3" / _PLAN_FILES["narration"]
    plan = _read_json(plan_path)
    plan["segments"][0]["novel_text"] = "重跑后的旁白。"
    _write_json(plan_path, plan)

    project = _read_json(project_dir / "project.json")
    assert review_status(project_dir, project, 3) == "pending_review"
    status = WorkflowStateService(ProjectManager(root)).get_status(project_dir.name, 3)
    assert status.artifacts["script"]["state"] == "current"
    assert status.next_action is not None
    assert status.next_action.type.value == "author_prompts"


def test_script_registered_by_the_plan_based_basis_is_rebased_and_stays_current(tmp_path: Path) -> None:
    project_dir = _project_at_v14(tmp_path / "projects")
    project = _read_json(project_dir / "project.json")
    plan = _read_json(project_dir / "drafts" / "episode_1" / _PLAN_FILES["narration"])
    _put_script_digest(project_dir, _legacy_script_digest(project, plan))

    migrate_project_dir(project_dir)

    migrated = _read_json(project_dir / "project.json")
    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(_SCRIPT)
    assert entry == ArtifactManifestEntry(
        artifact_path="scripts/episode_1.json", basis_digest=build_episode_script_basis(project=migrated).digest
    )
    assert _script_status(project_dir, _SCRIPT) == "current"


def test_planless_script_registration_is_rebased_and_stays_current(tmp_path: Path) -> None:
    project_dir = _project_at_v14(tmp_path / "projects")
    _put_script_digest(project_dir, _planless_script_digest(1))

    migrate_project_dir(project_dir)

    assert _script_status(project_dir, _SCRIPT) == "current"


def test_script_already_stale_before_the_migration_stays_stale(tmp_path: Path) -> None:
    project_dir = _project_at_v14(tmp_path / "projects")
    project = _read_json(project_dir / "project.json")
    stale_digest = _legacy_script_digest(project, {"segments": [{"novel_text": "更早的规划"}]})
    _put_script_digest(project_dir, stale_digest)

    migrate_project_dir(project_dir)

    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(_SCRIPT)
    assert entry is not None
    assert entry.basis_digest == stale_digest
    assert _script_status(project_dir, _SCRIPT) == "stale"


def test_unmaterializable_confirmed_episode_appears_in_the_migration_report(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project_dir = _project_at_v14(root)
    plan_path = project_dir / "drafts" / "episode_2" / _PLAN_FILES["narration"]
    _write_json(plan_path, {"segments": []})
    project = _read_json(project_dir / "project.json")
    project["episodes"][1]["script_plan_review"]["fingerprint"] = content_fingerprint(plan_path)
    _write_json(project_dir / "project.json", project)

    migrate_project_dir(project_dir)

    report = load_migration_report(project_dir)
    assert report is not None
    assert [(item.kind, item.episode) for item in report.skipped if item.episode == 2] == [("episode-script", 2)]
    assert not (project_dir / "scripts" / "episode_2.json").exists()


_CanonicalPath = Literal["free", "occupied"]


def _bind_episode_1_to_custom(project_dir: Path, canonical_path: _CanonicalPath) -> tuple[bytes, bytes | None]:
    """第 1 集绑到 ``scripts/custom.json``；``occupied`` 时规范路径上另有一份内容不同的剧本。

    返回绑定文件与规范路径上原文件的字节。
    """

    bind_episode_script_to_filename(project_dir, 1, "custom.json")
    custom = project_dir / "scripts" / "custom.json"
    occupant: bytes | None = None
    if canonical_path == "occupied":
        script = _read_json(custom)
        script["title"] = "规范路径上的另一份"
        _write_json(project_dir / "scripts" / "episode_1.json", script)
        occupant = (project_dir / "scripts" / "episode_1.json").read_bytes()
    return custom.read_bytes(), occupant


def _script_file_references(project_dir: Path) -> tuple[str, str, str]:
    """第 1 集宫格记录、持久化呈现与版本记录里写下的剧本文件名。"""

    [grid] = GridManager(project_dir).list_all()
    _subtitle, presentation = presentation_artifact_paths(1, "E1S01", POST_PRODUCTION)
    versions = _read_json(project_dir / "versions" / "versions.json")
    return (
        grid.script_file,
        _read_json(project_dir / presentation)["script_file"],
        versions["videos"]["E1S01"]["versions"][0]["execution_script_file"],
    )


def _reference_the_bound_script(project_dir: Path) -> None:
    GridManager(project_dir).save(
        GridGeneration.create(
            episode=1,
            script_file="custom.json",
            scene_ids=["E1S01", "E1S02"],
            rows=1,
            cols=2,
            grid_size="2K",
            provider="fake",
            model="fake-model",
            video_aspect_ratio="9:16",
        )
    )
    _subtitle, presentation = presentation_artifact_paths(1, "E1S01", POST_PRODUCTION)
    (project_dir / presentation).parent.mkdir(parents=True)
    _write_json(project_dir / presentation, {"episode": 1, "script_file": "custom.json", "persisted": True})
    versions_path = project_dir / "versions" / "versions.json"
    versions = _read_json(versions_path)
    versions["videos"]["E1S01"] = {
        "current_version": 1,
        "versions": [
            {"version": 1, "file": "versions/videos/E1S01_v1.mp4", "execution_script_file": "scripts/custom.json"}
        ],
    }
    _write_json(versions_path, versions)


@pytest.mark.parametrize("canonical_path", ["free", "occupied"])
def test_non_canonical_binding_moves_to_the_canonical_path_with_its_registrations(
    tmp_path: Path, canonical_path: _CanonicalPath
) -> None:
    project_dir = _project_at_v14(tmp_path / "projects")
    custom_bytes, occupant = _bind_episode_1_to_custom(project_dir, canonical_path)
    _reference_the_bound_script(project_dir)
    title = _read_json(project_dir / "scripts" / "custom.json")["title"]

    assert migrate_project_dir(project_dir) is True

    scripts_dir = project_dir / "scripts"
    assert not (scripts_dir / "custom.json").exists()
    assert _read_json(scripts_dir / "episode_1.json")["title"] == title
    [backup] = scripts_dir.glob("episode_1.json.bak.v14-*")
    assert backup.read_bytes() == custom_bytes
    ledger = _read_json(project_dir / "project.json")["episodes"][0]
    assert ledger["script_file"] == "scripts/episode_1.json"
    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(_SCRIPT)
    assert entry is not None
    assert entry.artifact_path == "scripts/episode_1.json"
    assert _script_status(project_dir, _SCRIPT) == "current"
    status = WorkflowStateService(ProjectManager(project_dir.parent)).get_status(project_dir.name, 1)
    assert status.artifacts["script"]["state"] == "current"
    assert _script_file_references(project_dir) == ("episode_1.json", "episode_1.json", "scripts/episode_1.json")
    report = load_migration_report(project_dir)
    assert report is not None
    displaced = None if occupant is None else "scripts/episode_1.json.displaced-v14"
    assert report.normalized_bindings == [
        MigrationNormalizedBinding(
            episode=1, from_path="scripts/custom.json", to_path="scripts/episode_1.json", displaced_path=displaced
        )
    ]
    if occupant is not None:
        assert (project_dir / "scripts" / "episode_1.json.displaced-v14").read_bytes() == occupant


@pytest.mark.parametrize("canonical_path", ["free", "occupied"])
def test_rerun_after_a_crash_before_project_json_finishes_the_same_normalization(
    tmp_path: Path, canonical_path: _CanonicalPath
) -> None:
    """改名与记录改写已落盘而 ``project.json`` 仍停在 v14：重跑补上绑定，不再改名、不另存第二份。"""

    project_dir = _project_at_v14(tmp_path / "projects")
    _bind_episode_1_to_custom(project_dir, canonical_path)
    _reference_the_bound_script(project_dir)
    project_before = (project_dir / "project.json").read_bytes()
    migrate_v14_to_v15(project_dir)

    def _state() -> dict[str, bytes]:
        return {
            path.relative_to(project_dir).as_posix(): path.read_bytes()
            for path in project_dir.rglob("*")
            if path.is_file() and ".bak.v" not in path.name and path.name != "project.json"
        }

    state = _state()
    migrated_ledger = _read_json(project_dir / "project.json")["episodes"][0]
    (project_dir / "project.json").write_bytes(project_before)

    migrate_v14_to_v15(project_dir)

    assert _state() == state
    ledger = _read_json(project_dir / "project.json")["episodes"][0]
    assert (ledger["script_file"], ledger["title"]) == (migrated_ledger["script_file"], migrated_ledger["title"])
    assert _read_json(project_dir / "project.json")["schema_version"] == 15


def test_canonical_bindings_are_left_verbatim(tmp_path: Path) -> None:
    project_dir = _project_at_v14(tmp_path / "projects")
    before = [entry["script_file"] for entry in _read_json(project_dir / "project.json")["episodes"]]
    scripts_before = sorted(path.name for path in (project_dir / "scripts").glob("*.json"))

    migrate_project_dir(project_dir)

    assert [entry["script_file"] for entry in _read_json(project_dir / "project.json")["episodes"]] == before
    # 第 2 集是确认后转出的正式脚本，其余剧本原地改写，不改名。
    assert sorted(path.name for path in (project_dir / "scripts").glob("*.json")) == sorted(
        [*scripts_before, "episode_2.json"]
    )
    report = load_migration_report(project_dir)
    assert report is not None
    assert report.normalized_bindings == []
    assert not list((project_dir / "scripts").glob("*.displaced-*"))


@pytest.mark.parametrize("canonical_path", ["free", "occupied"])
def test_every_reader_resolves_the_same_script_after_normalization(
    tmp_path: Path, canonical_path: _CanonicalPath
) -> None:
    """内容确认、提示词编写与 prompt_authoring 已产出判定读的是同一份剧本。"""

    project_dir = _project_at_v14(tmp_path / "projects")
    _bind_episode_1_to_custom(project_dir, canonical_path)
    migrate_project_dir(project_dir)
    project = _read_json(project_dir / "project.json")
    confirmed = formal_script_filename(project_dir, project, 1)
    assert project["episodes"][0]["script_file"] == f"scripts/{confirmed}"
    assert prompt_authoring_generated(project_dir, project, 1) is True

    # 从内容确认解析出的那份剧本里拿掉一个条目，提示词编写随之认不出它。
    asyncio.run(ScriptGenerator(project_dir).build_prompt(1, entry_ids=["E1S02"]))
    script_path = project_dir / "scripts" / confirmed
    script = _read_json(script_path)
    script["segments"] = [item for item in script["segments"] if item["segment_id"] != "E1S02"]
    _write_json(script_path, script)
    with pytest.raises(PromptAuthoringTargetError, match="E1S02"):
        asyncio.run(ScriptGenerator(project_dir).build_prompt(1, entry_ids=["E1S02"]))
