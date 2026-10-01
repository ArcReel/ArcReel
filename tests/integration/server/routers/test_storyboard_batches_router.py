"""一集分镜图 / 分镜视频批量的 HTTP 面：预览与提交、跳过项、整批拒绝与错误映射。

使用真实项目、产物清单、队列与供应商配置；分镜图费用按测试数据库中的各通道单价计算。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from lib.artifacts.artifact_currency import resolve_current_artifact_target
from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.custom_provider import make_provider_id
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_queue import GenerationQueue
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import storyboard_batches
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

PROJECT = "demo"
BASE = f"/api/v1/projects/{PROJECT}/episodes"


@pytest.fixture
def board_projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project(PROJECT)
    manager.create_project_metadata(PROJECT, "Demo", content_mode="narration")
    return manager


@pytest.fixture
async def board_queue(session_factory, board_projects: ProjectManager) -> GenerationQueue:
    generation_queue = GenerationQueue(session_factory=session_factory, project_manager=board_projects)
    await generation_queue.acquire_or_renew_worker_lease(name="default", owner_id="worker-a", ttl_seconds=60)
    return generation_queue


@pytest.fixture
async def board_client(
    session_factory, board_projects: ProjectManager, board_queue: GenerationQueue, monkeypatch
) -> AsyncIterator[httpx.AsyncClient]:
    async with session_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="Images",
            discovery_format="openai",
            base_url="https://api.example.com",
            api_key="test",
            models=[
                {
                    "model_id": "text-image",
                    "display_name": "Text image",
                    "endpoint": "openai-images-generations",
                    "price_unit": "image",
                    "price_input": 0.04,
                    "currency": "USD",
                },
                {
                    "model_id": "edit-image",
                    "display_name": "Edit image",
                    "endpoint": "openai-images-edits",
                    "price_unit": "image",
                    "price_input": 0.09,
                    "currency": "USD",
                },
            ],
        )
        provider_id = make_provider_id(provider.id)
        await session.commit()
    board_projects.update_project(
        PROJECT,
        lambda project: project.update(
            {
                "image_provider_t2i": f"{provider_id}/text-image",
                "image_provider_i2i": f"{provider_id}/edit-image",
            }
        ),
    )

    monkeypatch.setattr(storyboard_batches, "get_project_manager", lambda: board_projects)
    monkeypatch.setattr(storyboard_batches, "get_generation_queue", lambda: board_queue)
    monkeypatch.setattr(storyboard_batches, "async_session_factory", session_factory)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(storyboard_batches.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _segment(segment_id: str, **overrides: object) -> dict:
    segment: dict = {
        "segment_id": segment_id,
        "novel_text": "黄昏时分，风吹过村口。",
        "image_prompt": "村口黄昏",
        "video_prompt": {"action": "镜头平移", "camera_motion": "Pan", "ambiance_audio": "风声"},
        "duration_seconds": 4,
        "generated_assets": {},
    }
    segment.update(overrides)
    return segment


def _write_episode(board_projects: ProjectManager, segments: list[dict]) -> None:
    scripts = board_projects.get_project_path(PROJECT) / "scripts"
    scripts.mkdir(exist_ok=True)
    script = {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": segments}
    (scripts / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    board_projects.add_episode(PROJECT, 1, "第一集", "scripts/episode_1.json")


def _with_storyboard(board_projects: ProjectManager, segment_id: str) -> dict:
    relative_path = f"storyboards/scene_{segment_id}.png"
    path = board_projects.get_project_path(PROJECT) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(relative_path.encode("utf-8"))
    return _segment(segment_id, generated_assets={"storyboard_image": relative_path})


def _claim_storyboard(board_projects: ProjectManager, segment_id: str) -> None:
    project_dir = board_projects.get_project_path(PROJECT)
    key = ArtifactKey.episode_storyboard(1, segment_id)
    entry = resolve_current_artifact_target(project_dir, key)
    assert entry is not None
    ProjectArtifactManifestAdapter(project_dir).put_entry(key, entry)


async def test_storyboard_preview_lists_targets_skips_and_cost_without_queueing(
    board_client: httpx.AsyncClient, board_projects: ProjectManager, board_queue: GenerationQueue
) -> None:
    _write_episode(
        board_projects,
        [_segment("E1S01"), _segment("E1S02"), _segment("E1S03", image_prompt=None)],
    )

    response = await board_client.post(f"{BASE}/1/storyboards/batch/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["targets"] == [{"unit_id": "E1S01"}, {"unit_id": "E1S02"}]
    assert body["skipped"] == [{"unit_id": "E1S03", "reason": "missing_prompt"}]
    # 首张没有参考图走文生图（0.04），第二张接在上一张分镜图之后走图生图（0.09）。
    assert body["estimated_cost"] == {"USD": pytest.approx(0.13)}
    tasks = await board_queue.get_active_tasks_for_resources(
        project_name=PROJECT, task_type="storyboard", resource_ids=["E1S01", "E1S02"], user_id=DEFAULT_USER_ID
    )
    assert tasks == []


async def test_storyboard_submit_queues_the_targets_as_one_batch(
    board_client: httpx.AsyncClient, board_projects: ProjectManager, board_queue: GenerationQueue
) -> None:
    _write_episode(board_projects, [_segment("E1S01"), _segment("E1S02", image_prompt=None)])

    response = await board_client.post(f"{BASE}/1/storyboards/batch")

    assert response.status_code == 200
    body = response.json()
    assert body["batch_id"]
    assert list(body["task_ids_by_unit"]) == ["E1S01"]
    assert body["skipped"] == [{"unit_id": "E1S02", "reason": "missing_prompt"}]
    assert body["enqueue_failures"] == []
    tasks = await board_queue.get_active_tasks_for_resources(
        project_name=PROJECT,
        task_type="storyboard",
        resource_ids=["E1S01", "E1S02"],
        script_file="episode_1.json",
        user_id=DEFAULT_USER_ID,
    )
    assert [task["resource_id"] for task in tasks] == ["E1S01"]


async def test_video_batch_without_a_video_model_is_refused_whole_with_reasons(
    board_client: httpx.AsyncClient, board_projects: ProjectManager, board_queue: GenerationQueue
) -> None:
    _write_episode(board_projects, [_with_storyboard(board_projects, "E1S01"), _segment("E1S02")])
    _claim_storyboard(board_projects, "E1S01")

    preview = await board_client.post(f"{BASE}/1/videos/batch/preview")
    submitted = await board_client.post(f"{BASE}/1/videos/batch")

    assert preview.status_code == 200
    body = preview.json()
    assert body["targets"] == [{"unit_id": "E1S01"}]
    assert body["skipped"] == [{"unit_id": "E1S02", "reason": "missing_storyboard"}]
    assert body["admission"]["decision"] == "blocked"
    assert body["estimated_cost"] is None
    (unit,) = body["admission"]["units"]
    assert unit["unit_id"] == "E1S01"
    assert unit["problems"]
    assert all(problem["message"] for problem in unit["problems"])

    assert submitted.status_code == 200
    refused = submitted.json()
    assert refused["admission"]["decision"] == "blocked"
    assert refused["batch_id"] is None
    assert refused["task_ids_by_unit"] == {}
    tasks = await board_queue.get_active_tasks_for_resources(
        project_name=PROJECT,
        task_type="video",
        resource_ids=["E1S01"],
        script_file="episode_1.json",
        user_id=DEFAULT_USER_ID,
    )
    assert tasks == []


@pytest.mark.parametrize("kind", ["storyboards", "videos"])
async def test_batch_maps_unknown_episode_and_reference_project(
    board_client: httpx.AsyncClient, board_projects: ProjectManager, kind: str
) -> None:
    _write_episode(board_projects, [_segment("E1S01")])

    missing = await board_client.post(f"{BASE}/9/{kind}/batch/preview")
    board_projects.update_project(PROJECT, lambda project: project.update({"generation_mode": "reference_video"}))
    reference = await board_client.post(f"{BASE}/1/{kind}/batch/preview")

    assert missing.status_code == 404
    assert reference.status_code == 409
