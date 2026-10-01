"""资产图批量生成服务：选目标、批内依赖、集范围与卡片状态，落在真实项目目录与真实队列上。"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.artifacts.artifact_currency import resolve_current_artifact_target
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_result import GenerationProblemCode
from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE
from lib.project.project_manager import ProjectManager
from server.services.admission.asset_sheet_batch import (
    AssetBatchEpisodeNotFound,
    AssetSheetScope,
    asset_sheet_statuses,
    load_asset_sheet_batch_plan,
    submit_asset_sheet_batch,
)

PROJECT = "demo"


@pytest.fixture
def sheet_projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project(PROJECT)
    manager.create_project_metadata(PROJECT, "Demo")
    return manager


@pytest.fixture
async def sheet_queue(session_factory, sheet_projects: ProjectManager) -> GenerationQueue:
    generation_queue = GenerationQueue(session_factory=session_factory, project_manager=sheet_projects)
    await generation_queue.acquire_or_renew_worker_lease(name="default", owner_id="worker-a", ttl_seconds=60)
    return generation_queue


def _add_derivative(sheet_projects: ProjectManager, owner: str, derivative: str, description: str) -> None:
    def _mutate(project: dict) -> None:
        project["characters"][owner].setdefault("derivatives", {})[derivative] = {"description": description}

    sheet_projects.update_project(PROJECT, _mutate)


def _blank_description(sheet_projects: ProjectManager, bucket: str, name: str) -> None:
    def _mutate(project: dict) -> None:
        project[bucket][name]["description"] = "   "

    sheet_projects.update_project(PROJECT, _mutate)


def _write_sheet(sheet_projects: ProjectManager, relative_path: str) -> Path:
    path = sheet_projects.get_project_path(PROJECT) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(relative_path.encode("utf-8"))
    return path


def _claim_current(sheet_projects: ProjectManager, key: ArtifactKey) -> None:
    project_dir = sheet_projects.get_project_path(PROJECT)
    entry = resolve_current_artifact_target(project_dir, key)
    assert entry is not None
    ProjectArtifactManifestAdapter(project_dir).put_entry(key, entry)


def _claim_stale(sheet_projects: ProjectManager, key: ArtifactKey, relative_path: str) -> None:
    ProjectArtifactManifestAdapter(sheet_projects.get_project_path(PROJECT)).put_entry(
        key, ArtifactManifestEntry(artifact_path=relative_path, basis_digest=f"sha256-v1:{'a' * 64}")
    )


def _current_character_sheet(sheet_projects: ProjectManager, name: str) -> None:
    relative_path = f"characters/{name}.png"
    _write_sheet(sheet_projects, relative_path)
    sheet_projects.update_project_character_sheet(PROJECT, name, relative_path)
    _claim_current(sheet_projects, ArtifactKey.asset_sheet("character", name))


def _statuses(sheet_projects: ProjectManager) -> dict[str, str]:
    project = sheet_projects.load_project(PROJECT)
    resolver = active_artifact_currency_resolver(sheet_projects.get_project_path(PROJECT), project)
    return {row["unit_id"]: row["status"] for row in asset_sheet_statuses(project, resolver)}


async def _plan(sheet_projects: ProjectManager, sheet_queue: GenerationQueue, scope: AssetSheetScope):
    _project, plan = await load_asset_sheet_batch_plan(
        sheet_projects, sheet_queue, project_name=PROJECT, scope=scope, user_id=DEFAULT_USER_ID
    )
    return plan


def _codes(plan) -> dict[str, str | None]:
    return {item.unit_id: item.problem.code if item.problem else None for item in plan.preflight.items}


async def test_type_scope_selects_only_pending_sheets(
    sheet_projects: ProjectManager, sheet_queue: GenerationQueue
) -> None:
    sheet_projects.add_project_scene(PROJECT, "客厅", "宽敞的客厅")
    sheet_projects.add_project_scene(PROJECT, "卧室", "宁静的卧室")
    sheet_projects.add_project_scene(PROJECT, "书房", "堆满书的书房")
    sheet_projects.add_project_scene(PROJECT, "阁楼", "占位")
    _blank_description(sheet_projects, "scenes", "阁楼")
    _write_sheet(sheet_projects, "scenes/卧室.png")
    sheet_projects.update_scene_sheet(PROJECT, "卧室", "scenes/卧室.png")
    _claim_current(sheet_projects, ArtifactKey.asset_sheet("scene", "卧室"))
    _write_sheet(sheet_projects, "scenes/书房.png")
    sheet_projects.update_scene_sheet(PROJECT, "书房", "scenes/书房.png")
    _claim_stale(sheet_projects, ArtifactKey.asset_sheet("scene", "书房"), "scenes/书房.png")

    plan = await _plan(sheet_projects, sheet_queue, AssetSheetScope(asset_type="scene"))

    assert _statuses(sheet_projects)["scene/书房"] == "stale"
    assert plan.target_ids == ["scene/客厅"]
    assert _codes(plan) == {"scene/阁楼": "asset_description_required"}
    assert plan.missing_description_ids == ["scene/阁楼"]


def test_a_deleted_sheet_file_reads_as_pending(sheet_projects: ProjectManager) -> None:
    sheet_projects.add_character(PROJECT, "Alice", "勇敢的少女")
    _current_character_sheet(sheet_projects, "Alice")
    assert _statuses(sheet_projects)["character/Alice"] == "current"

    (sheet_projects.get_project_path(PROJECT) / "characters/Alice.png").unlink()

    assert _statuses(sheet_projects)["character/Alice"] == "missing"


async def test_owner_and_derivative_both_pending_run_in_one_batch_in_order(
    sheet_projects: ProjectManager, sheet_queue: GenerationQueue
) -> None:
    sheet_projects.add_character(PROJECT, "Alice", "勇敢的少女")
    _add_derivative(sheet_projects, "Alice", "战损", "衣服破损，脸上有伤")

    plan = await _plan(sheet_projects, sheet_queue, AssetSheetScope(asset_type="character"))
    assert plan.target_ids == ["character/Alice", "character/Alice/战损"]
    assert plan.dependencies == {"character/Alice/战损": "character/Alice"}

    batch, enqueued = await submit_asset_sheet_batch(
        plan, project_name=PROJECT, queue=sheet_queue, source="webui", user_id=DEFAULT_USER_ID
    )
    by_unit = {task.unit_id: task.task_id for task in enqueued}
    owner_task = await sheet_queue.claim_next_task("image")
    assert owner_task is not None
    assert owner_task["task_id"] == by_unit["character/Alice"]
    assert await sheet_queue.claim_next_task("image") is None

    await sheet_queue.mark_task_succeeded(owner_task["task_id"], {"file_path": "characters/Alice.png"})
    derivative_task = await sheet_queue.claim_next_task("image")

    assert derivative_task is not None
    assert derivative_task["task_id"] == by_unit["character/Alice/战损"]
    assert derivative_task["task_type"] == DERIVATIVE_TASK_TYPE
    assert derivative_task["resource_id"] == "Alice/战损"
    assert batch.batch_id


async def test_a_failed_owner_leaves_its_derivative_unsubmitted(
    sheet_projects: ProjectManager, sheet_queue: GenerationQueue
) -> None:
    sheet_projects.add_character(PROJECT, "Alice", "勇敢的少女")
    _add_derivative(sheet_projects, "Alice", "战损", "衣服破损，脸上有伤")
    plan = await _plan(sheet_projects, sheet_queue, AssetSheetScope(asset_type="character"))
    batch, _enqueued = await submit_asset_sheet_batch(
        plan, project_name=PROJECT, queue=sheet_queue, source="webui", user_id=DEFAULT_USER_ID
    )

    owner_task = await sheet_queue.claim_next_task("image")
    assert owner_task is not None
    await sheet_queue.mark_task_failed(owner_task["task_id"], "provider rejected the prompt")

    assert await sheet_queue.claim_next_task("image") is None
    read = await sheet_queue.get_generation_batch(
        project_name=PROJECT, batch_id=batch.batch_id, user_id=DEFAULT_USER_ID
    )
    assert read.done is True
    assert read.generation_result is not None
    problems = {item.unit_id: item.problem for item in read.generation_result.items}
    derivative_problem = problems["character/Alice/战损"]
    assert derivative_problem is not None
    assert derivative_problem.code == GenerationProblemCode.DEPENDENCY_FAILED
    assert derivative_problem.params == {"dependency": "character/Alice"}


async def test_an_owner_that_cannot_enqueue_reports_its_derivative_as_dependency_failed(
    sheet_projects: ProjectManager, session_factory
) -> None:
    sheet_projects.add_character(PROJECT, "Alice", "勇敢的少女")
    _add_derivative(sheet_projects, "Alice", "战损", "衣服破损")
    queue = GenerationQueue(session_factory=session_factory, project_manager=sheet_projects)
    plan = await _plan(sheet_projects, queue, AssetSheetScope(asset_type="character"))

    batch, enqueued = await submit_asset_sheet_batch(
        plan, project_name=PROJECT, queue=queue, source="webui", user_id=DEFAULT_USER_ID
    )

    assert enqueued == []
    assert batch.done is True
    assert batch.generation_result is not None
    problems = {item.unit_id: item.problem for item in batch.generation_result.items}
    assert problems["character/Alice"].code == GenerationProblemCode.ENQUEUE_FAILED
    assert problems["character/Alice/战损"].code == GenerationProblemCode.DEPENDENCY_FAILED
    assert problems["character/Alice/战损"].params == {"dependency": "character/Alice"}


async def test_a_derivative_needs_a_usable_owner_sheet(
    sheet_projects: ProjectManager, sheet_queue: GenerationQueue
) -> None:
    sheet_projects.add_character(PROJECT, "Alice", "占位")
    _blank_description(sheet_projects, "characters", "Alice")
    _add_derivative(sheet_projects, "Alice", "战损", "衣服破损")
    sheet_projects.add_character(PROJECT, "Bob", "沉默的剑客")
    _current_character_sheet(sheet_projects, "Bob")
    _add_derivative(sheet_projects, "Bob", "雨夜", "浑身湿透")

    plan = await _plan(sheet_projects, sheet_queue, AssetSheetScope(asset_type="character"))

    assert plan.target_ids == ["character/Bob/雨夜"]
    assert plan.dependencies == {}
    assert _codes(plan) == {
        "character/Alice": "asset_description_required",
        "character/Alice/战损": "derivative_owner_sheet_missing",
    }


async def test_a_stale_owner_sheet_still_feeds_its_derivative(
    sheet_projects: ProjectManager, sheet_queue: GenerationQueue
) -> None:
    sheet_projects.add_character(PROJECT, "Alice", "勇敢的少女")
    _write_sheet(sheet_projects, "characters/Alice.png")
    sheet_projects.update_project_character_sheet(PROJECT, "Alice", "characters/Alice.png")
    _claim_stale(sheet_projects, ArtifactKey.asset_sheet("character", "Alice"), "characters/Alice.png")
    _add_derivative(sheet_projects, "Alice", "战损", "衣服破损")

    plan = await _plan(sheet_projects, sheet_queue, AssetSheetScope(asset_type="character"))

    assert plan.target_ids == ["character/Alice/战损"]
    assert plan.dependencies == {}


async def test_a_generating_target_stays_in_the_batch_and_dedupes(
    sheet_projects: ProjectManager, sheet_queue: GenerationQueue
) -> None:
    sheet_projects.add_prop(PROJECT, "玉佩", "古玉")
    sheet_projects.add_prop(PROJECT, "宝剑", "利剑")
    first = await _plan(sheet_projects, sheet_queue, AssetSheetScope(asset_type="prop", names=("玉佩",)))
    await submit_asset_sheet_batch(
        first, project_name=PROJECT, queue=sheet_queue, source="webui", user_id=DEFAULT_USER_ID
    )

    plan = await _plan(sheet_projects, sheet_queue, AssetSheetScope(asset_type="prop"))
    _batch, enqueued = await submit_asset_sheet_batch(
        plan, project_name=PROJECT, queue=sheet_queue, source="webui", user_id=DEFAULT_USER_ID
    )

    assert plan.generating == frozenset({"prop/玉佩"})
    assert plan.new_task_ids == ["prop/宝剑"]
    assert {task.unit_id: task.deduped for task in enqueued} == {"prop/玉佩": True, "prop/宝剑": False}


async def test_named_selection_resolves_spelling_and_blocks_blank_descriptions(
    sheet_projects: ProjectManager, sheet_queue: GenerationQueue
) -> None:
    name_nfd = unicodedata.normalize("NFD", "Hiếu")

    def _register_nfd(project: dict) -> None:
        project["characters"][name_nfd] = {"description": "存量 NFD 角色"}

    sheet_projects.update_project(PROJECT, _register_nfd)
    sheet_projects.add_character(PROJECT, "Carol", "占位")
    _blank_description(sheet_projects, "characters", "Carol")

    plan = await _plan(
        sheet_projects,
        sheet_queue,
        AssetSheetScope(asset_type="character", names=(unicodedata.normalize("NFC", "Hiếu"), name_nfd, "Carol")),
    )

    assert plan.target_ids == [f"character/{name_nfd}"]
    assert _codes(plan) == {"character/Carol": "asset_description_required"}


def _write_episode(sheet_projects: ProjectManager, episode: int, segments: list[dict]) -> None:
    scripts = sheet_projects.get_project_path(PROJECT) / "scripts"
    scripts.mkdir(exist_ok=True)
    script = {"episode": episode, "title": "第一集", "content_mode": "narration", "segments": segments}
    (scripts / f"episode_{episode}.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    sheet_projects.add_episode(PROJECT, episode, "第一集", f"scripts/episode_{episode}.json")


async def test_episode_scope_covers_what_the_episode_references(
    sheet_projects: ProjectManager, sheet_queue: GenerationQueue
) -> None:
    sheet_projects.add_character(PROJECT, "Alice", "勇敢的少女")
    _add_derivative(sheet_projects, "Alice", "战损", "衣服破损")
    sheet_projects.add_character(PROJECT, "Bob", "沉默的剑客")
    sheet_projects.add_project_scene(PROJECT, "客厅", "宽敞的客厅")
    sheet_projects.add_prop(PROJECT, "玉佩", "古玉")
    sheet_projects.add_product(PROJECT, "手机", "旗舰手机")
    _write_episode(
        sheet_projects,
        1,
        [
            {
                "segment_id": "E1S01",
                "novel_text": "她举起手机。",
                "characters_in_segment": ["Alice/战损"],
                "scenes": ["客厅"],
                "products_in_shot": ["手机"],
            }
        ],
    )

    plan = await _plan(sheet_projects, sheet_queue, AssetSheetScope(episode_id=1))

    assert plan.target_ids == ["character/Alice", "character/Alice/战损", "scene/客厅", "product/手机"]
    assert plan.dependencies == {"character/Alice/战损": "character/Alice"}


async def test_episode_scope_rejects_an_unknown_episode(
    sheet_projects: ProjectManager, sheet_queue: GenerationQueue
) -> None:
    with pytest.raises(AssetBatchEpisodeNotFound):
        await _plan(sheet_projects, sheet_queue, AssetSheetScope(episode_id=9))


def test_statuses_cover_derivative_sheets(sheet_projects: ProjectManager) -> None:
    sheet_projects.add_character(PROJECT, "Alice", "勇敢的少女")
    _current_character_sheet(sheet_projects, "Alice")
    _add_derivative(sheet_projects, "Alice", "战损", "")

    project = sheet_projects.load_project(PROJECT)
    resolver = active_artifact_currency_resolver(sheet_projects.get_project_path(PROJECT), project)
    rows = {row["unit_id"]: row for row in asset_sheet_statuses(project, resolver)}

    assert rows["character/Alice/战损"] == {
        "unit_id": "character/Alice/战损",
        "asset_type": "character",
        "name": "Alice",
        "derivative": "战损",
        "status": "missing",
        "description_missing": True,
        "image_to_image": True,
    }
