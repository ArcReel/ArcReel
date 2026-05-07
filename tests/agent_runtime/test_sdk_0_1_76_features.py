"""SDK 0.1.76 新字段消费的单元测试。

覆盖：
- ResultMessage.api_error_status 透传到 SSE 状态 payload
- ToolPermissionContext.decision_reason 拼到 _can_use_tool default-deny hint
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from server.agent_runtime.service import AssistantService


class TestApiErrorStatusInStatusPayload:
    """0.1.76 新字段 api_error_status 透传到 SSE payload。"""

    def test_api_error_status_present_when_set(self):
        result_message: dict[str, Any] = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "api_error_status": 429,
            "stop_reason": "api_error",
        }
        payload = AssistantService._build_status_event_payload(
            status="error",
            session_id="sess-1",
            result_message=result_message,
        )
        assert payload["api_error_status"] == 429

    def test_api_error_status_absent_when_none(self):
        result_message: dict[str, Any] = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "api_error_status": None,
        }
        payload = AssistantService._build_status_event_payload(
            status="completed",
            session_id="sess-2",
            result_message=result_message,
        )
        assert "api_error_status" not in payload

    def test_api_error_status_absent_when_field_missing(self):
        # 老 SDK / 老消息没有 api_error_status 字段
        result_message: dict[str, Any] = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
        }
        payload = AssistantService._build_status_event_payload(
            status="completed",
            session_id="sess-3",
            result_message=result_message,
        )
        assert "api_error_status" not in payload


class TestResultErrorLogging:
    """result 消息错误路径写结构化 logger.warning，含 api_error_status。"""

    @pytest.mark.asyncio
    async def test_logger_warning_emitted_on_error_with_api_status(
        self,
        caplog: pytest.LogCaptureFixture,
        tmp_path,
    ):
        from unittest.mock import MagicMock

        service = AssistantService(project_root=tmp_path)
        projector = MagicMock()
        projector.apply_message.return_value = {}  # 无 patch/delta/question

        result_message: dict[str, Any] = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "api_error_status": 429,
            "stop_reason": "api_error",
        }
        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.service"):
            events, terminal = await service._dispatch_live_message(
                message=result_message,
                projector=projector,
                session_id="sess-err",
            )
        assert terminal is True
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(getattr(r, "api_error_status", None) == 429 for r in warnings), (
            f"expected a warning with api_error_status=429, got {[r.__dict__ for r in warnings]}"
        )

    @pytest.mark.asyncio
    async def test_no_warning_on_completed_result(
        self,
        caplog: pytest.LogCaptureFixture,
        tmp_path,
    ):
        from unittest.mock import MagicMock

        service = AssistantService(project_root=tmp_path)
        projector = MagicMock()
        projector.apply_message.return_value = {}

        result_message: dict[str, Any] = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
        }
        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.service"):
            await service._dispatch_live_message(
                message=result_message,
                projector=projector,
                session_id="sess-ok",
            )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not warnings, f"unexpected warnings on completed result: {warnings}"


class TestCanUseToolDecisionReason:
    """0.1.74 新字段 ToolPermissionContext.decision_reason 拼到 default-deny hint。"""

    @pytest.mark.asyncio
    async def test_default_deny_includes_decision_reason(self, tmp_path):
        from server.agent_runtime.session_manager import SessionManager
        from server.agent_runtime.session_store import SessionMetaStore

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        sm = SessionManager(
            project_root=tmp_path,
            data_dir=data_dir,
            meta_store=SessionMetaStore(),
        )
        callback = await sm._build_can_use_tool_callback(session_id="sess-x")
        ctx = SimpleNamespace(decision_reason="No matching allow rule")
        result = await callback("Bash", {"command": "rm -rf /"}, ctx)
        assert hasattr(result, "message"), f"expected PermissionResultDeny, got {type(result)}"
        assert "No matching allow rule" in result.message
        assert "上游决策原因" in result.message

    @pytest.mark.asyncio
    async def test_default_deny_without_decision_reason_does_not_raise(self, tmp_path):
        from server.agent_runtime.session_manager import SessionManager
        from server.agent_runtime.session_store import SessionMetaStore

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        sm = SessionManager(
            project_root=tmp_path,
            data_dir=data_dir,
            meta_store=SessionMetaStore(),
        )
        callback = await sm._build_can_use_tool_callback(session_id="sess-y")
        ctx = SimpleNamespace()  # no decision_reason
        result = await callback("Bash", {"command": "echo hi"}, ctx)
        assert hasattr(result, "message")
        assert "未授权的工具调用" in result.message
        assert "上游决策原因" not in result.message
