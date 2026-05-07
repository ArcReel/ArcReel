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
