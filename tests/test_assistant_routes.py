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


def _override_translator():
    """Parameterless override returning a fixed-locale translator.

    A bare ``make_translator`` reference would surface its ``locale`` default as
    an optional query parameter under FastAPI dependency resolution; wrapping it
    in a zero-arg callable keeps the override contract parameterless.
    """
    return make_translator()


def _build_client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    app.dependency_overrides[get_current_user_flexible] = lambda: _FAKE_USER
    app.dependency_overrides[get_translator] = _override_translator
    app.include_router(assistant.router, prefix="/api/v1/projects/{project_name}/assistant")
    return TestClient(app)


_INTERNAL_ERROR_DETAIL = make_translator()("internal_server_error")


def _assert_generic_500(response, sentinel: str) -> None:
    """兜底 500 契约：响应体 detail 必须是通用 i18n 文案、且不泄露内部哨兵串。

    仅断言 ``sentinel not in text`` 不够——detail 被清空或 i18n key 打错时也不含哨兵串，
    回归会静默通过；故同时正向锁定 detail 等于翻译后的 internal_server_error 文案。
    """
    assert response.status_code == 500
    assert response.json()["detail"] == _INTERNAL_ERROR_DETAIL
    assert sentinel not in response.text


class TestAssistantRoutes:
    def test_messages_endpoint_returns_410(self):
        with _build_client() as client:
            response = client.get(f"{PREFIX}/sessions/session-1/messages")

        assert response.status_code == 410
        payload = response.json()
        assert "下线" in payload.get("detail", "")

    def test_snapshot_endpoint_returns_410(self):
        with _build_client() as client:
            response = client.get(f"{PREFIX}/sessions/session-1/snapshot")

        assert response.status_code == 410
        payload = response.json()
        assert "下线" in payload.get("detail", "")

    def test_stream_endpoint_returns_410(self):
        with _build_client() as client:
            response = client.get(f"{PREFIX}/sessions/session-1/stream")

        assert response.status_code == 410
        payload = response.json()
        assert "下线" in payload.get("detail", "")

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

    def test_send_unexpected_error_no_leak(self):
        """send 末端 catch-all：未预期异常返回通用 500，不泄露内部细节。"""
        with patch.object(
            assistant.assistant_service,
            "send_or_create",
            new=AsyncMock(side_effect=RuntimeError("LEAK_send")),
        ):
            with _build_client() as client:
                response = client.post(f"{PREFIX}/sessions/send", json={"content": "hi"})

        _assert_generic_500(response, "LEAK_send")

    def test_list_sessions_unexpected_error_no_leak(self):
        with patch.object(
            assistant.assistant_service,
            "list_sessions",
            new=AsyncMock(side_effect=RuntimeError("LEAK_list")),
        ):
            with _build_client() as client:
                response = client.get(f"{PREFIX}/sessions")

        _assert_generic_500(response, "LEAK_list")

    def test_get_session_unexpected_error_no_leak(self):
        with patch.object(
            assistant.assistant_service,
            "get_session",
            new=AsyncMock(side_effect=RuntimeError("LEAK_get")),
        ):
            with _build_client() as client:
                response = client.get(f"{PREFIX}/sessions/session-1")

        _assert_generic_500(response, "LEAK_get")

    def test_delete_session_unexpected_error_no_leak(self):
        session_meta = make_session_meta(id="session-1", project_name=PROJECT)
        with (
            patch.object(assistant.assistant_service, "get_session", return_value=session_meta),
            patch.object(
                assistant.assistant_service,
                "delete_session",
                new=AsyncMock(side_effect=RuntimeError("LEAK_delete")),
            ),
        ):
            with _build_client() as client:
                response = client.delete(f"{PREFIX}/sessions/session-1")

        _assert_generic_500(response, "LEAK_delete")

    def test_interrupt_unexpected_error_no_leak(self):
        session_meta = make_session_meta(id="session-1", project_name=PROJECT)
        with (
            patch.object(assistant.assistant_service, "get_session", return_value=session_meta),
            patch.object(
                assistant.assistant_service,
                "interrupt_session",
                new=AsyncMock(side_effect=RuntimeError("LEAK_interrupt")),
            ),
        ):
            with _build_client() as client:
                response = client.post(f"{PREFIX}/sessions/session-1/interrupt")

        _assert_generic_500(response, "LEAK_interrupt")

    def test_answer_question_unexpected_error_no_leak(self):
        session_meta = make_session_meta(id="session-1", project_name=PROJECT)
        with (
            patch.object(assistant.assistant_service, "get_session", return_value=session_meta),
            patch.object(
                assistant.assistant_service,
                "answer_user_question",
                new=AsyncMock(side_effect=RuntimeError("LEAK_answer")),
            ),
        ):
            with _build_client() as client:
                response = client.post(
                    f"{PREFIX}/sessions/session-1/questions/q-1/answer",
                    json={"answers": {"q-1": "yes"}},
                )

        _assert_generic_500(response, "LEAK_answer")

    def test_list_skills_unexpected_error_no_leak(self):
        with patch.object(
            assistant.assistant_service,
            "list_available_skills",
            side_effect=RuntimeError("LEAK_skills"),
        ):
            with _build_client() as client:
                response = client.get(f"{PREFIX}/skills")

        _assert_generic_500(response, "LEAK_skills")

    def test_send_endpoint_translates_agent_startup_error_to_502(self):
        """``AgentStartupError`` 必须翻译成 502 + i18n 包装的 detail，
        透传 SDK 自带的安装指引；500 + 占位符是回归（PR #573）。"""
        from server.agent_runtime.session_manager import AgentStartupError

        stderr_text = (
            "Claude Code on Windows requires either Git for Windows (for bash) or PowerShell.\n"
            "Or set CLAUDE_CODE_GIT_BASH_PATH to your bash.exe location."
        )
        startup_err = AgentStartupError(
            "Command failed with exit code 1",
            sdk_stderr=stderr_text,
        )

        with patch.object(
            assistant.assistant_service,
            "send_or_create",
            new=AsyncMock(side_effect=startup_err),
        ):
            with _build_client() as client:
                response = client.post(
                    f"{PREFIX}/sessions/send",
                    json={"content": "hi"},
                )

        assert response.status_code == 502
        detail = response.json().get("detail", "")
        # i18n 前缀 + SDK 原文必须都在 detail 里
        assert "Agent" in detail or "agent" in detail
        assert "Git for Windows" in detail
        assert "CLAUDE_CODE_GIT_BASH_PATH" in detail
