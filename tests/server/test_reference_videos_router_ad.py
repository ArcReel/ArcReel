"""广告参考路线复用通用 video-unit Web API。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project_migrations.runner import CURRENT_SCHEMA_VERSION
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from tests.auth_deps import AUTH_DEPENDENCIES


@pytest.fixture
def ad_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "ad-demo"
    (project_dir / "scripts").mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "title": "带货短片",
                "creation_type": "ad",
                "generation_mode": "reference_video",
                "characters": {"小美": {}},
                "scenes": {},
                "props": {},
                "products": {"按摩仪": {"product_sheet": "products/按摩仪.png", "reference_images": []}},
                "episodes": [{"episode": 1, "title": "短片", "script_file": "scripts/episode_1.json"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_dir / "products").mkdir()
    (project_dir / "products" / "按摩仪.png").write_bytes(b"image")
    (project_dir / "scripts/episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "短片",
                "creation_type": "ad",
                "video_units": [
                    {
                        "unit_id": "E1U1",
                        "shots": [{"text": "@[按摩仪] 放在桌面上"}],
                        "references": [{"type": "product", "name": "按摩仪"}],
                        "duration_seconds": 5,
                        "generated_assets": {},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from lib.project_manager import ProjectManager
    from server.routers import reference_videos as router_mod

    monkeypatch.setattr(router_mod, "get_project_manager", lambda: ProjectManager(projects_root))
    monkeypatch.setattr(router_mod, "tts_task_in_progress", AsyncMock(return_value=False))
    from tests.fakes import fake_reference_request_projector

    monkeypatch.setattr(
        router_mod,
        "project_reference_unit_request",
        fake_reference_request_projector(durations=(4, 8, 12)),
    )
    fake_queue = AsyncMock()
    fake_queue.enqueue_task = AsyncMock(return_value={"task_id": "t1", "deduped": False})
    monkeypatch.setattr(router_mod, "get_generation_queue", lambda: fake_queue)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    client = TestClient(app)
    client.fake_queue = fake_queue  # type: ignore[attr-defined]
    client.project_dir = project_dir  # type: ignore[attr-defined]
    return client


def _script(client: TestClient) -> dict:
    path: Path = client.project_dir / "scripts/episode_1.json"  # type: ignore[attr-defined]
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.integration
def test_list_and_legacy_derive_removal(ad_client: TestClient) -> None:
    response = ad_client.get("/api/v1/projects/ad-demo/reference-videos/episodes/1/units")
    assert response.status_code == 200
    assert response.json()["units"][0]["unit_id"] == "E1U1"
    assert ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/derive-units").status_code == 404


@pytest.mark.integration
def test_ad_units_support_crud_and_product_references(ad_client: TestClient) -> None:
    added = ad_client.post(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units",
        json={
            "prompt": "@[按摩仪] 被 @[小美] 举到镜头前",
            "references": [{"type": "product", "name": "按摩仪"}, {"type": "character", "name": "小美"}],
            "duration_seconds": 6,
        },
    )
    assert added.status_code == 201, added.text
    assert added.json()["unit"]["unit_id"] == "E1U2"

    patched = ad_client.patch(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U2",
        json={"prompt": "@[按摩仪] 正面朝向镜头", "references": [{"type": "product", "name": "按摩仪"}]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["unit"]["references"] == [{"type": "product", "name": "按摩仪"}]

    reordered = ad_client.post(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/reorder",
        json={"unit_ids": ["E1U2", "E1U1"]},
    )
    assert reordered.status_code == 200
    assert [unit["unit_id"] for unit in _script(ad_client)["video_units"]] == ["E1U2", "E1U1"]

    deleted = ad_client.delete("/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U2")
    assert deleted.status_code == 204, deleted.text


@pytest.mark.integration
def test_generate_enqueues_self_contained_unit(ad_client: TestClient) -> None:
    response = ad_client.post(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1/generate",
        json={"confirmed_request_duration_seconds": 8},
    )
    assert response.status_code == 202, response.text
    kwargs = ad_client.fake_queue.enqueue_task.call_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["resource_id"] == "E1U1"
    assert kwargs["script_file"] == "scripts/episode_1.json"
    assert "prompt" not in kwargs["payload"]
    assert kwargs["payload"]["reference_request_options"] == {
        "narration_delivery": "post_production",
        "confirmed_request_duration_seconds": 8,
    }


@pytest.mark.integration
def test_replan_shell_and_mixed_speech_are_blocked_before_enqueue(ad_client: TestClient) -> None:
    script = _script(ad_client)
    script["video_units"][0].update({"shots": [], "duration_seconds": 0, "needs_replan": True})
    path: Path = ad_client.project_dir / "scripts/episode_1.json"  # type: ignore[attr-defined]
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    response = ad_client.post("/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1/generate")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["allowed"] is False
    assert detail["unit_id"] == "E1U1"
    assert detail["problems"][0]["code"] == "needs_replan"
    assert detail["problems"][0]["locations"] == [{"path": ["needs_replan"], "line": None}]
    ad_client.fake_queue.enqueue_task.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.integration
def test_non_planning_patch_keeps_replan_marker_until_content_is_repaired(ad_client: TestClient) -> None:
    script = _script(ad_client)
    script["video_units"][0]["needs_replan"] = True
    script["video_units"][0]["migration_requires_content_replan"] = True
    path: Path = ad_client.project_dir / "scripts/episode_1.json"  # type: ignore[attr-defined]
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    noted = ad_client.patch(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1",
        json={"note": "待复核"},
    )
    assert noted.status_code == 200, noted.text
    assert noted.json()["unit"]["needs_replan"] is True

    resized = ad_client.patch(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1",
        json={"duration_seconds": 12},
    )
    assert resized.status_code == 200, resized.text
    assert resized.json()["unit"]["needs_replan"] is True

    resubmitted = ad_client.patch(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1",
        json={"prompt": "@[按摩仪] 放在桌面上"},
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()["unit"]["needs_replan"] is True

    repaired = ad_client.patch(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1",
        json={"prompt": "@[按摩仪] 正面朝向镜头"},
    )
    assert repaired.status_code == 200, repaired.text
    assert "needs_replan" not in repaired.json()["unit"]
    assert "migration_requires_content_replan" not in repaired.json()["unit"]


@pytest.mark.integration
def test_duration_repair_clears_an_independent_migration_marker(ad_client: TestClient) -> None:
    script = _script(ad_client)
    # 旧聚合时长非法时迁移会夹到结构下限并标 replan，但不会留下内容归属 provenance。
    script["video_units"][0].update({"duration_seconds": 1, "needs_replan": True})
    path: Path = ad_client.project_dir / "scripts/episode_1.json"  # type: ignore[attr-defined]
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    repaired = ad_client.patch(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1",
        json={"duration_seconds": 1},
    )

    assert repaired.status_code == 200, repaired.text
    assert "needs_replan" not in repaired.json()["unit"]


@pytest.mark.integration
def test_empty_migration_shell_can_repair_body_and_duration_atomically(ad_client: TestClient) -> None:
    script = _script(ad_client)
    script["video_units"][0].update(
        {
            "shots": [],
            "duration_seconds": 0,
            "needs_replan": True,
            "migration_requires_content_replan": True,
        }
    )
    path: Path = ad_client.project_dir / "scripts/episode_1.json"  # type: ignore[attr-defined]
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    repaired = ad_client.patch(
        "/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1",
        json={"prompt": "@[按摩仪] 正面朝向镜头", "duration_seconds": 6},
    )

    assert repaired.status_code == 200, repaired.text
    assert "needs_replan" not in repaired.json()["unit"]
    assert "migration_requires_content_replan" not in repaired.json()["unit"]


@pytest.mark.integration
def test_precheck_uses_unit_orchestration_duration(ad_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from server.routers import reference_videos as router_mod
    from tests.fakes import fake_reference_request_projector

    monkeypatch.setattr(
        router_mod,
        "project_reference_unit_request",
        fake_reference_request_projector(durations=(4, 8, 12)),
    )
    body = ad_client.get("/api/v1/projects/ad-demo/reference-videos/episodes/1/units/E1U1/duration-precheck").json()
    assert body["needs_confirmation"] is True
    assert body["script_duration"] == 5
    assert body["duration_input"] == 5
    assert body["request_duration"] == 8
    assert body["adjustment"] == "up"
    assert body["declared_capability"] == "r2v"
    assert body["hydrated_capability"] == "r2v"
    assert body["provider_id"] == "fake"
    assert body["model_id"] == "fake-model"
    assert [problem["code"] for problem in body["problems"]] == ["reference_duration_confirmation_required"]
