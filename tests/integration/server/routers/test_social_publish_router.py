"""REST projection for social distribution: credentials gate, selection forwarding, error mapping."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.api_errors import ServiceUnavailableError, UnprocessableError
from lib.social_publish import PlatformOutcome, PublishProgress, PublishSubmission
from server.error_handlers import register_error_handlers
from server.routers import social_publish
from server.services.presentation_read_model import PresentationUnavailableError
from server.services.social_publish import UploadPostCredentials

_CREDENTIALS = UploadPostCredentials(api_key="secret-key", profile="studio", base_url="https://upload.example/api")


class _Service:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def list_profiles(self, credentials: UploadPostCredentials):
        self.calls.append({"op": "list_profiles", "profile": credentials.profile})
        if self.error is not None:
            raise self.error
        return ()

    async def publish_unit(self, credentials: UploadPostCredentials, **kwargs: Any) -> PublishSubmission:
        self.calls.append({"op": "publish_unit", **kwargs})
        if self.error is not None:
            raise self.error
        return PublishSubmission(request_id="arcreel-1", job_id=None, scheduled_date=None, total_platforms=1)

    async def fetch_progress(self, credentials: UploadPostCredentials, *, request_id: str) -> PublishProgress:
        self.calls.append({"op": "fetch_progress", "request_id": request_id})
        if self.error is not None:
            raise self.error
        return PublishProgress(
            request_id=request_id,
            job_id=None,
            status="completed",
            completed=1,
            total=1,
            outcomes=(
                PlatformOutcome(platform="tiktok", status="completed", success=True, url="https://tk/1", error=None),
            ),
        )


def _client(service: _Service, *, credentials_error: Exception | None = None) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(social_publish.router, prefix="/api/v1")
    app.dependency_overrides[social_publish.get_social_publish_service] = lambda: service

    async def _credentials() -> UploadPostCredentials:
        if credentials_error is not None:
            raise credentials_error
        return _CREDENTIALS

    app.dependency_overrides[social_publish.get_credentials] = _credentials
    return TestClient(app, raise_server_exceptions=False)


def test_profiles_endpoint_reports_active_profile_and_video_platforms() -> None:
    service = _Service()
    response = _client(service).get("/api/v1/social/publish/profiles")

    assert response.status_code == 200
    body = response.json()
    assert body["active_profile"] == "studio"
    assert "tiktok" in body["video_platforms"]
    # reddit 不收视频，不进下拉
    assert "reddit" not in body["video_platforms"]


def test_publish_forwards_selection_and_returns_request_id() -> None:
    service = _Service()
    response = _client(service).post(
        "/api/v1/projects/demo/presentations/videos/E1S01/publish",
        json={
            "platforms": ["tiktok", "youtube"],
            "title": "第一章",
            "variant": "use_tts",
            "video_version": 3,
            "audio_version": 2,
            "description": "说明",
            "scheduled_date": "2026-12-31T23:45:00Z",
            "timezone": "Europe/Madrid",
        },
    )

    assert response.status_code == 202
    assert response.json()["request_id"] == "arcreel-1"
    assert service.calls == [
        {
            "op": "publish_unit",
            "project_name": "demo",
            "resource_type": "videos",
            "resource_id": "E1S01",
            "variant": "use_tts",
            "platforms": ("tiktok", "youtube"),
            "title": "第一章",
            "video_version": 3,
            "audio_version": 2,
            "description": "说明",
            "scheduled_date": "2026-12-31T23:45:00Z",
            "timezone": "Europe/Madrid",
        }
    ]


def test_publish_without_credentials_is_refused_before_touching_the_project() -> None:
    service = _Service()
    client = _client(service, credentials_error=UnprocessableError("social_publish_not_configured"))

    response = client.post(
        "/api/v1/projects/demo/presentations/videos/E1S01/publish",
        json={"platforms": ["tiktok"], "title": "第一章"},
    )

    assert response.status_code == 422
    assert service.calls == []


def test_publish_requires_at_least_one_platform() -> None:
    response = _client(_Service()).post(
        "/api/v1/projects/demo/presentations/videos/E1S01/publish",
        json={"platforms": [], "title": "第一章"},
    )

    assert response.status_code == 422


def test_publish_maps_unavailable_selection_to_localized_422() -> None:
    service = _Service(error=PresentationUnavailableError("secret/internal/path"))
    response = _client(service).post(
        "/api/v1/projects/demo/presentations/videos/E1S01/publish",
        json={"platforms": ["tiktok"], "title": "第一章"},
    )

    assert response.status_code == 422
    assert "secret/internal/path" not in response.text


def test_publish_surfaces_upstream_quota_exhaustion_as_503() -> None:
    service = _Service(error=ServiceUnavailableError("social_publish_quota_exceeded"))
    response = _client(service).post(
        "/api/v1/projects/demo/presentations/videos/E1S01/publish",
        json={"platforms": ["tiktok"], "title": "第一章"},
    )

    assert response.status_code == 503


def test_status_endpoint_projects_platform_outcomes() -> None:
    service = _Service()
    response = _client(service).get("/api/v1/social/publish/status", params={"request_id": "arcreel-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["terminal"] is True
    assert body["outcomes"] == [
        {
            "platform": "tiktok",
            "status": "completed",
            "success": True,
            "url": "https://tk/1",
            "error": None,
        }
    ]
    assert service.calls == [{"op": "fetch_progress", "request_id": "arcreel-1"}]


@pytest.mark.parametrize("params", [{}, {"request_id": ""}])
def test_status_endpoint_requires_a_request_id(params: dict[str, str]) -> None:
    response = _client(_Service()).get("/api/v1/social/publish/status", params=params)

    assert response.status_code == 422
