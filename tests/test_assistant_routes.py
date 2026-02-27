"""Unit tests for assistant router contract changes."""

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import assistant


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(assistant.router, prefix="/api/v1/assistant")
    return TestClient(app)


class TestAssistantRoutes:
    def test_messages_endpoint_returns_410(self):
        with _build_client() as client:
            response = client.get("/api/v1/assistant/sessions/session-1/messages")

        assert response.status_code == 410
        payload = response.json()
        assert "snapshot" in payload.get("detail", "")

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

        with patch.object(
            assistant.assistant_service,
            "get_snapshot",
            new=AsyncMock(return_value=snapshot_payload),
        ):
            with _build_client() as client:
                response = client.get("/api/v1/assistant/sessions/session-1/snapshot")

        assert response.status_code == 200
        assert response.json() == snapshot_payload

    def test_interrupt_endpoint_returns_accepted(self):
        interrupt_payload = {
            "status": "accepted",
            "session_id": "session-1",
            "session_status": "interrupted",
        }

        with patch.object(
            assistant.assistant_service,
            "interrupt_session",
            new=AsyncMock(return_value=interrupt_payload),
        ):
            with _build_client() as client:
                response = client.post("/api/v1/assistant/sessions/session-1/interrupt")

        assert response.status_code == 200
        assert response.json() == interrupt_payload
