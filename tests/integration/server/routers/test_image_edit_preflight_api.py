"""图片编辑 HTTP 入口预检的集成回归测试（真实 ConfigResolver + ImageRuntime.read_bucket）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.catalog import query as catalog_query
from lib.custom_provider import make_provider_id
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.project.project_migrations.runner import migrate_project_dir
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import generate
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.catalog_support import install_catalog, with_entry_facts
from tests.fakes import bind_session_factory, use_generation_queue, use_project_manager


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}


class _FakePM:
    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.project = json.loads((project_path / "project.json").read_text(encoding="utf-8"))

    def load_project(self, project_name: str) -> dict[str, Any]:
        del project_name
        return self.project

    def get_project_path(self, project_name: str) -> Path:
        del project_name
        return self.project_path


def _seed_project(tmp_path: Path, **project_overrides: Any) -> Path:
    project_path = tmp_path / "projects" / "demo"
    for subdir in ("characters", "scripts", "source", "drafts/episode_1"):
        (project_path / subdir).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4)).save(project_path / "characters" / "Alice.png")
    project: dict[str, Any] = {
        "name": "demo",
        "title": "Demo",
        "schema_version": 7,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "style": "anime",
        "style_description": "anime style",
        "aspect_ratio": "9:16",
        "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        "characters": {"Alice": {"description": "少女", "character_sheet": "characters/Alice.png"}},
        **project_overrides,
    }
    (project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (project_path / "scripts" / "episode_1.json").write_text(
        json.dumps({"episode": 1, "content_mode": "narration", "segments": []}),
        encoding="utf-8",
    )
    (project_path / "source" / "episode_1.txt").write_text("原文", encoding="utf-8")
    (project_path / "drafts" / "episode_1" / "script_plan_segments.json").write_text(
        json.dumps({"episode": 1, "segments": []}),
        encoding="utf-8",
    )
    activate_artifact_target_state(project_path, bump_schema=True)
    migrate_project_dir(project_path)
    return project_path


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
    set_error_handler_sessions,
    project_path: Path,
    queue: _FakeQueue,
) -> TestClient:
    bind_session_factory(monkeypatch, session_factory)
    set_error_handler_sessions(session_factory)
    use_project_manager(monkeypatch, _FakePM(project_path))
    use_generation_queue(monkeypatch, queue)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(generate.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app, raise_server_exceptions=False)


class TestEditImagePreflightIntegration:
    def test_builtin_model_removed_from_catalog_returns_400_reselect(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        concurrent_session_factory,
        set_error_handler_sessions,
    ) -> None:
        project_path = _seed_project(tmp_path, default_image_backend="ark/retired-image-model")
        queue = _FakeQueue()
        client = _build_client(monkeypatch, concurrent_session_factory, set_error_handler_sessions, project_path, queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "Alice", "instruction": "换发色"},
                headers={"Accept-Language": "en"},
            )

        assert resp.status_code == 400, resp.text
        assert "re-select a model" in resp.json()["detail"]
        assert queue.calls == []

    def test_builtin_model_lacking_i2i_returns_400_missing_i2i(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        concurrent_session_factory,
        set_error_handler_sessions,
    ) -> None:
        install_catalog(
            monkeypatch,
            with_entry_facts(
                catalog_query.factory_snapshot(),
                "ark",
                "doubao-seedream-4-5-251128",
                image_to_image=False,
            ),
        )
        project_path = _seed_project(tmp_path, default_image_backend="ark/doubao-seedream-4-5-251128")
        queue = _FakeQueue()
        client = _build_client(monkeypatch, concurrent_session_factory, set_error_handler_sessions, project_path, queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "Alice", "instruction": "换发色"},
                headers={"Accept-Language": "en"},
            )

        assert resp.status_code == 400, resp.text
        assert "does not support image-to-image" in resp.json()["detail"]
        assert queue.calls == []

    def test_builtin_i2i_model_without_credentials_enqueues(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        concurrent_session_factory,
        set_error_handler_sessions,
    ) -> None:
        project_path = _seed_project(tmp_path, default_image_backend="ark/doubao-seedream-4-5-251128")
        queue = _FakeQueue()
        client = _build_client(monkeypatch, concurrent_session_factory, set_error_handler_sessions, project_path, queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "Alice", "instruction": "换发色"},
            )

        assert resp.status_code == 200, resp.text
        assert len(queue.calls) == 1
        assert queue.calls[0]["provider_id"] == "ark"

    @pytest.mark.asyncio
    async def test_custom_provider_i2i_allowed_and_t2i_only_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        concurrent_session_factory,
        set_error_handler_sessions,
    ) -> None:
        async with concurrent_session_factory() as session:
            provider = CustomProvider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://relay.example.com",
                api_key="",
            )
            session.add(provider)
            await session.flush()
            session.add_all(
                [
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="custom-edit-model",
                        display_name="Custom Edit",
                        endpoint="openai-images-edits",
                        is_default=True,
                        is_enabled=True,
                    ),
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="custom-t2i-only",
                        display_name="Custom T2I Only",
                        endpoint="openai-images-generations",
                        is_default=False,
                        is_enabled=True,
                    ),
                ]
            )
            await session.commit()
            provider_id = make_provider_id(provider.id)

        ok_path = _seed_project(tmp_path / "ok", image_provider_i2i=f"{provider_id}/custom-edit-model")
        ok_queue = _FakeQueue()
        ok_client = _build_client(
            monkeypatch, concurrent_session_factory, set_error_handler_sessions, ok_path, ok_queue
        )
        with ok_client:
            ok_resp = ok_client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "Alice", "instruction": "换发色"},
            )
        assert ok_resp.status_code == 200, ok_resp.text
        assert ok_queue.calls[0]["provider_id"] == provider_id

        bad_path = _seed_project(tmp_path / "bad", image_provider_i2i=f"{provider_id}/custom-t2i-only")
        bad_queue = _FakeQueue()
        bad_client = _build_client(
            monkeypatch, concurrent_session_factory, set_error_handler_sessions, bad_path, bad_queue
        )
        with bad_client:
            bad_resp = bad_client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "Alice", "instruction": "换发色"},
                headers={"Accept-Language": "en"},
            )
        assert bad_resp.status_code == 400, bad_resp.text
        assert "does not support image-to-image" in bad_resp.json()["detail"]
        assert bad_queue.calls == []
