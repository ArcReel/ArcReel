"""Unit tests for assistant router contract changes."""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from webui.server.routers import assistant


class TestAssistantRoutes(unittest.TestCase):
    def _build_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(assistant.router, prefix="/api/v1/assistant")
        return TestClient(app)

    def test_messages_endpoint_returns_410(self):
        with self._build_client() as client:
            response = client.get("/api/v1/assistant/sessions/session-1/messages")

        self.assertEqual(response.status_code, 410)
        payload = response.json()
        self.assertIn("snapshot", payload.get("detail", ""))

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
            with self._build_client() as client:
                response = client.get("/api/v1/assistant/sessions/session-1/snapshot")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), snapshot_payload)

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
            with self._build_client() as client:
                response = client.post("/api/v1/assistant/sessions/session-1/interrupt")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), interrupt_payload)


if __name__ == "__main__":
    unittest.main()
