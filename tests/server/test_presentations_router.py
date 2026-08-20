"""REST projection for presentation preview and editable download."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.error_handlers import register_error_handlers
from server.routers import presentations
from server.services.presentation_read_model import PresentationUnavailableError

pytestmark = pytest.mark.integration


class _Result:
    def to_dict(self):
        return {
            "schema_version": 1,
            "unit_id": "E1S01",
            "variant": "post_production",
            "selection": "current",
            "currency": "stale",
        }


class _ReadModel:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = []

    async def materialize_unit(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return _Result()


class _Bundle:
    def __init__(self, path: Path):
        self.path = path
        self.calls = []

    async def export_unit(self, **kwargs):
        self.calls.append(kwargs)
        return self.path


def _client(monkeypatch, tmp_path: Path, *, reader=None, bundle=None) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(presentations.router, prefix="/api/v1")
    if reader is not None:
        monkeypatch.setattr(presentations, "get_presentation_read_model", lambda: reader)
    if bundle is not None:
        monkeypatch.setattr(presentations, "get_presentation_bundle_service", lambda: bundle)
    return TestClient(app, raise_server_exceptions=False)


def test_preview_returns_shared_model_and_forwards_version_selection(monkeypatch, tmp_path: Path) -> None:
    reader = _ReadModel()
    client = _client(monkeypatch, tmp_path, reader=reader)

    response = client.get(
        "/api/v1/projects/demo/presentations/videos/E1S01",
        params={"variant": "post_production", "video_version": 3},
    )

    assert response.status_code == 200
    assert response.json()["currency"] == "stale"
    assert reader.calls == [
        {
            "project_name": "demo",
            "resource_type": "videos",
            "resource_id": "E1S01",
            "variant": "post_production",
            "video_version": 3,
            "audio_version": None,
        }
    ]


def test_preview_maps_unavailable_selection_to_localized_422(monkeypatch, tmp_path: Path) -> None:
    reader = _ReadModel(PresentationUnavailableError("secret/internal/path"))
    client = _client(monkeypatch, tmp_path, reader=reader)

    response = client.get("/api/v1/projects/demo/presentations/videos/E1S01")

    assert response.status_code == 422
    assert "secret/internal/path" not in response.text


def test_bundle_returns_zip_and_forwards_same_selection(monkeypatch, tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    path = bundle_dir / "presentation.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("presentation.json", json.dumps({"unit_id": "E1S01"}))
    bundle = _Bundle(path)
    client = _client(monkeypatch, tmp_path, bundle=bundle)

    response = client.get(
        "/api/v1/projects/demo/presentations/reference_videos/E1U01/bundle",
        params={"variant": "use_tts", "video_version": 2, "audio_version": 4},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert bundle.calls == [
        {
            "project_name": "demo",
            "resource_type": "reference_videos",
            "resource_id": "E1U01",
            "variant": "use_tts",
            "video_version": 2,
            "audio_version": 4,
        }
    ]
