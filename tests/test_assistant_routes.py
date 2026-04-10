"""Unit tests for assistant router contract changes."""

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.i18n import get_translator
from server.auth import CurrentUserInfo, get_current_user, get_current_user_flexible
from server.routers import assistant
from tests.conftest import make_translator
from tests.factories import make_session_meta

PROJECT = "demo"
PREFIX = f"/api/v1/projects/{PROJECT}/assistant"

_FAKE_USER = CurrentUserInfo(id="default", sub="testuser", role="admin")


def _build_client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    app.dependency_overrides[get_current_user_flexible] = lambda: _FAKE_USER
    app.dependency_overrides[get_translator] = lambda: make_translator()
    app.include_router(assistant.router, prefix="/api/v1/projects/{project_name}/assistant")
    return TestClient(app)


class TestAssistantRoutes:
    def test_messages_endpoint_returns_410(self):
        with _build_client() as client:
            response = client.get(f"{PREFIX}/sessions/session-1/messages")

        assert response.status_code == 410
        payload = response.json()
        assert "下线" in payload.get("detail", "")

    def test_snapshot_endpoint_returns_v2_snapshot(self):
        snapshot_payload = {
            "session_id": "session-1",
            "status": "running",
            "turns": [{"type": "user", "content": [{"type": "text", "text": "hello"}]}],
            "draft_turn": {
                "type": "assistant",
                "content": [{"type": "text", "text": "Hi"}],
            },
            "pending_questions": [],
        }

        # Mock get_session for ownership validation
        session_meta = make_session_meta(id="session-1", project_name=PROJECT)
        with (
            patch.object(
                assistant.assistant_service,
                "get_session",
                return_value=session_meta,
            ),
            patch.object(
                assistant.assistant_service,
                "get_snapshot",
                new=AsyncMock(return_value=snapshot_payload),
            ),
        ):
            with _build_client() as client:
                response = client.get(f"{PREFIX}/sessions/session-1/snapshot")

        assert response.status_code == 200
        assert response.json() == snapshot_payload

    def test_interrupt_endpoint_returns_accepted(self):
        interrupt_payload = {
            "status": "accepted",
            "session_id": "session-1",
            "session_status": "interrupted",
        }

        # Mock get_session for ownership validation
        session_meta = make_session_meta(id="session-1", project_name=PROJECT)
        with (
            patch.object(
                assistant.assistant_service,
                "get_session",
                return_value=session_meta,
            ),
            patch.object(
                assistant.assistant_service,
                "interrupt_session",
                new=AsyncMock(return_value=interrupt_payload),
            ),
        ):
            with _build_client() as client:
                response = client.post(f"{PREFIX}/sessions/session-1/interrupt")

        assert response.status_code == 200
        assert response.json() == interrupt_payload
