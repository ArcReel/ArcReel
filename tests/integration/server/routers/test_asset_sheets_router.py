"""资产图 HTTP 面：卡片状态、批量生成的预览与提交、单张重生的连带影响。

预览与提交使用真实项目、产物清单、队列与供应商配置；费用按测试数据库中的各通道单价计算。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from lib.artifacts.artifact_manifest import MANIFEST_FILENAME
from lib.custom_provider import make_provider_id
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_queue import GenerationQueue
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import asset_sheets
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

PROJECT = "demo"


@pytest.fixture
def sheet_projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project(PROJECT)
    manager.create_project_metadata(PROJECT, "Demo")
    return manager


@pytest.fixture
async def sheet_client(
    session_factory, sheet_projects: ProjectManager, monkeypatch
) -> AsyncIterator[httpx.AsyncClient]:
    queue = GenerationQueue(session_factory=session_factory, project_manager=sheet_projects)
    await queue.acquire_or_renew_worker_lease(name="default", owner_id="worker-a", ttl_seconds=60)

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
    sheet_projects.update_project(
        PROJECT,
        lambda project: project.update(
            {
                "image_provider_t2i": f"{provider_id}/text-image",
                "image_provider_i2i": f"{provider_id}/edit-image",
            }
        ),
    )

    monkeypatch.setattr(asset_sheets, "get_project_manager", lambda: sheet_projects)
    monkeypatch.setattr(asset_sheets, "get_generation_queue", lambda: queue)
    monkeypatch.setattr(asset_sheets, "async_session_factory", session_factory)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.include_router(asset_sheets.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _add_derivative(sheet_projects: ProjectManager, owner: str, derivative: str, description: str) -> None:
    def _mutate(project: dict) -> None:
        project["characters"][owner].setdefault("derivatives", {})[derivative] = {"description": description}

    sheet_projects.update_project(PROJECT, _mutate)


def _seed_pending_cast(sheet_projects: ProjectManager) -> None:
    sheet_projects.add_character(PROJECT, "Alice", "勇敢的少女")
    _add_derivative(sheet_projects, "Alice", "战损", "衣服破损")
    _add_derivative(sheet_projects, "Alice", "雨夜", "")


async def test_status_reports_every_sheet_with_its_description_gap(
    sheet_client: httpx.AsyncClient, sheet_projects
) -> None:
    _seed_pending_cast(sheet_projects)

    response = await sheet_client.get(f"/api/v1/projects/{PROJECT}/asset-sheets/status")

    assert response.status_code == 200
    rows = {row["unit_id"]: (row["status"], row["description_missing"]) for row in response.json()["assets"]}
    assert rows == {
        "character/Alice": ("missing", False),
        "character/Alice/战损": ("missing", False),
        "character/Alice/雨夜": ("missing", True),
    }


async def test_preview_lists_targets_skips_and_cost_without_queueing(
    sheet_client: httpx.AsyncClient, sheet_projects
) -> None:
    _seed_pending_cast(sheet_projects)

    response = await sheet_client.post(
        f"/api/v1/projects/{PROJECT}/asset-sheets/batch/preview", json={"asset_type": "character"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["targets"] == [
        {
            "unit_id": "character/Alice",
            "asset_type": "character",
            "name": "Alice",
            "derivative": None,
            "depends_on": None,
        },
        {
            "unit_id": "character/Alice/战损",
            "asset_type": "character",
            "name": "Alice",
            "derivative": "战损",
            "depends_on": "character/Alice",
        },
    ]
    assert body["skipped"] == [
        {
            "unit_id": "character/Alice/雨夜",
            "asset_type": "character",
            "name": "Alice",
            "derivative": "雨夜",
            "reason": "missing_description",
        }
    ]
    assert body["estimated_cost"] == {"USD": pytest.approx(0.13)}


async def test_submit_returns_member_tasks_and_a_second_run_reports_them_generating(
    sheet_client: httpx.AsyncClient, sheet_projects
) -> None:
    _seed_pending_cast(sheet_projects)
    url = f"/api/v1/projects/{PROJECT}/asset-sheets/batch"

    submitted = await sheet_client.post(url, json={"asset_type": "character"})

    assert submitted.status_code == 200
    members = {member["unit_id"]: member for member in submitted.json()["members"]}
    assert set(members) == {"character/Alice", "character/Alice/战损", "character/Alice/雨夜"}
    assert members["character/Alice"]["task_id"]
    assert members["character/Alice/战损"]["task_id"]
    assert members["character/Alice/雨夜"]["task_id"] is None
    assert members["character/Alice/雨夜"]["status"] == "blocked"

    preview = await sheet_client.post(f"{url}/preview", json={"asset_type": "character"})
    body = preview.json()
    assert body["targets"] == []
    assert [(item["unit_id"], item["reason"]) for item in body["skipped"]] == [
        ("character/Alice", "generating"),
        ("character/Alice/战损", "generating"),
        ("character/Alice/雨夜", "missing_description"),
    ]


@pytest.mark.parametrize("body", [{}, {"asset_type": "character", "episode_id": 1}])
async def test_a_batch_needs_exactly_one_scope(sheet_client: httpx.AsyncClient, body: dict) -> None:
    response = await sheet_client.post(f"/api/v1/projects/{PROJECT}/asset-sheets/batch/preview", json=body)

    assert response.status_code == 422


async def test_an_unknown_episode_is_not_found(sheet_client: httpx.AsyncClient) -> None:
    response = await sheet_client.post(f"/api/v1/projects/{PROJECT}/asset-sheets/batch/preview", json={"episode_id": 7})

    assert response.status_code == 404


async def test_regeneration_impact_of_an_unknown_asset_is_not_found(sheet_client: httpx.AsyncClient) -> None:
    response = await sheet_client.get(f"/api/v1/projects/{PROJECT}/asset-sheets/scene/不存在/regeneration-impact")

    assert response.status_code == 404


async def test_status_reports_every_sheet_blocked_when_the_manifest_is_unreadable(
    sheet_client: httpx.AsyncClient, sheet_projects
) -> None:
    _seed_pending_cast(sheet_projects)
    (sheet_projects.get_project_path(PROJECT) / MANIFEST_FILENAME).write_text("{", encoding="utf-8")

    response = await sheet_client.get(f"/api/v1/projects/{PROJECT}/asset-sheets/status")

    assert response.status_code == 200
    rows = {row["unit_id"]: (row["status"], row["description_missing"]) for row in response.json()["assets"]}
    assert rows == {
        "character/Alice": ("blocked", False),
        "character/Alice/战损": ("blocked", False),
        "character/Alice/雨夜": ("blocked", True),
    }
