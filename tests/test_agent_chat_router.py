"""
Synchronous Agent Chat endpoint tests

Tests for the core logic of the POST /api/v1/agent/chat endpoint.
"""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import agent_chat


def _make_client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(agent_chat.router, prefix="/api/v1")
    return TestClient(app)


def _fake_session(session_id: str = "sess-1", project_name: str = "demo"):
    meta = MagicMock()
    meta.id = session_id
    meta.project_name = project_name
    return meta


class TestAgentChatEndpoint:
    def _patch_service(
        self, monkeypatch, *, project_exists=True, reply_text="hello", status="completed", session_id="sess-1"
    ):
        """Build and inject a mock AssistantService."""
        mock_service = AsyncMock()

        # Project existence check
        pm = MagicMock()
        if project_exists:
            pm.get_project_path = MagicMock(return_value="/fake/path")
        else:
            pm.get_project_path = MagicMock(side_effect=FileNotFoundError("not found"))
        mock_service.pm = pm

        # Session lookup (for ownership validation)
        mock_service.get_session = AsyncMock(return_value=_fake_session(session_id=session_id))

        # Unified send endpoint
        mock_service.send_or_create = AsyncMock(return_value={"status": "accepted", "session_id": session_id})

        monkeypatch.setattr(agent_chat, "get_assistant_service", lambda: mock_service)
        monkeypatch.setattr(
            agent_chat,
            "_collect_reply",
            AsyncMock(return_value=(reply_text, status)),
        )
        return mock_service

    def test_new_session_returns_reply(self, monkeypatch):
        self._patch_service(monkeypatch, reply_text="Script generated for you")
        with _make_client() as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={
                    "project_name": "demo",
                    "message": "Write a script for me",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "Script generated for you"
        assert body["status"] == "completed"
        assert "session_id" in body

    def test_reuse_existing_session(self, monkeypatch):
        self._patch_service(monkeypatch, reply_text="Continuing conversation")
        with _make_client() as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={
                    "project_name": "demo",
                    "message": "continue",
                    "session_id": "sess-1",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "sess-1"

    def test_project_not_found_returns_404(self, monkeypatch):
        self._patch_service(monkeypatch, project_exists=False)
        with _make_client() as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={
                    "project_name": "nonexistent",
                    "message": "test",
                },
            )
        assert resp.status_code == 404

    def test_timeout_status_propagated(self, monkeypatch):
        self._patch_service(monkeypatch, reply_text="Partial response", status="timeout")
        with _make_client() as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={
                    "project_name": "demo",
                    "message": "long running task",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "timeout"
        assert resp.json()["reply"] == "Partial response"


class TestExtractTextFromAssistantMessage:
    def test_list_content(self):
        msg = {"type": "assistant", "content": [{"type": "text", "text": "hello"}]}
        assert agent_chat._extract_text_from_assistant_message(msg) == "hello"

    def test_string_content(self):
        msg = {"type": "assistant", "content": "direct text"}
        assert agent_chat._extract_text_from_assistant_message(msg) == "direct text"

    def test_multiple_text_blocks(self):
        msg = {
            "type": "assistant",
            "content": [
                {"type": "text", "text": "first part"},
                {"type": "tool_use", "name": "Read"},
                {"type": "text", "text": "second part"},
            ],
        }
        assert agent_chat._extract_text_from_assistant_message(msg) == "first partsecond part"

    def test_no_text_blocks(self):
        msg = {"type": "assistant", "content": [{"type": "tool_use", "name": "Read"}]}
        assert agent_chat._extract_text_from_assistant_message(msg) == ""
