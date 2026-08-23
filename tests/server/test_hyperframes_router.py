"""HTTP wiring for the project-local HyperFrames Studio boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import hyperframes
from server.services.hyperframes_workspace import HyperframesWorkspace

pytestmark = pytest.mark.unit


def _workspace(tmp_path: Path) -> HyperframesWorkspace:
    path = tmp_path / "demo" / "hyperframes" / "episode_01"
    path.mkdir(parents=True)
    return HyperframesWorkspace(
        project_name="demo",
        episode=1,
        path=path,
        relative_path="hyperframes/episode_01",
        composition_path="hyperframes/episode_01/index.html",
        manifest_path="hyperframes/episode_01/manifest.json",
    )


class _Service:
    def __init__(self, workspace: HyperframesWorkspace | None) -> None:
        self.workspace = workspace
        self.calls: list[tuple[str, int, str]] = []

    def status(self, _project_name: str, _episode: int):
        return self.workspace

    async def prepare(self, project_name: str, episode: int, *, variant: str):
        self.calls.append((project_name, episode, variant))
        assert self.workspace is not None
        return self.workspace


class _Manager:
    async def ensure_started(self, _workspace: Path) -> int:
        return 12507

    @staticmethod
    def public_url(port: int, _origin: str) -> str:
        return f"http://localhost:{port}"


def _client(monkeypatch: pytest.MonkeyPatch, service: _Service) -> TestClient:
    app = FastAPI()
    app.include_router(hyperframes.router, prefix="/api/v1")
    monkeypatch.setattr(hyperframes, "get_hyperframes_workspace_service", lambda: service)
    monkeypatch.setattr(hyperframes, "get_hyperframes_studio_manager", lambda: _Manager())
    return TestClient(app)


def test_get_reports_absent_workspace_without_starting_studio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(monkeypatch, _Service(None)).get("/api/v1/projects/demo/hyperframes/episodes/1")

    assert response.status_code == 200
    assert response.json()["exists"] is False
    assert response.json()["studio_url"] is None


def test_prepare_uses_shared_service_and_returns_official_studio_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = _Service(_workspace(tmp_path))
    response = _client(monkeypatch, service).post(
        "/api/v1/projects/demo/hyperframes/episodes/1",
        json={"narration_delivery": "use_tts"},
    )

    assert response.status_code == 200
    assert service.calls == [("demo", 1, "use_tts")]
    assert response.json()["workspace_path"] == "hyperframes/episode_01"
    assert response.json()["studio_status"] == "ready"
    assert response.json()["studio_url"] == "http://localhost:12507"


def test_episode_path_rejects_non_positive_values(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch, _Service(None)).get("/api/v1/projects/demo/hyperframes/episodes/0")

    assert response.status_code == 422
