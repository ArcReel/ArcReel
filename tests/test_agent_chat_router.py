"""
同步 Agent 对话端点测试

测试 POST /api/v1/agent/chat 端点的核心逻辑。
"""

import asyncio
import contextlib
from types import SimpleNamespace
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
        self, monkeypatch, *, project_exists=True, reply_text="你好", status="completed", session_id="sess-1"
    ):
        """构建 mock AssistantService 并注入。"""
        mock_service = AsyncMock()

        # 项目存在性检查
        pm = MagicMock()
        if project_exists:
            pm.get_project_path = MagicMock(return_value="/fake/path")
        else:
            pm.get_project_path = MagicMock(side_effect=FileNotFoundError("not found"))
        mock_service.pm = pm

        # 会话查询（用于归属校验）
        mock_service.get_session = AsyncMock(return_value=_fake_session(session_id=session_id))

        # 统一发送端点
        mock_service.send_or_create = AsyncMock(return_value={"status": "accepted", "session_id": session_id})

        monkeypatch.setattr(agent_chat, "get_assistant_service", lambda: mock_service)
        monkeypatch.setattr(
            agent_chat,
            "_collect_reply",
            AsyncMock(return_value=(reply_text, status)),
        )
        return mock_service

    def test_new_session_returns_reply(self, monkeypatch):
        self._patch_service(monkeypatch, reply_text="已为你生成剧本")
        with _make_client() as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={
                    "project_name": "demo",
                    "message": "帮我写剧本",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "已为你生成剧本"
        assert body["status"] == "completed"
        assert "session_id" in body

    def test_reuse_existing_session(self, monkeypatch):
        self._patch_service(monkeypatch, reply_text="继续对话")
        with _make_client() as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={
                    "project_name": "demo",
                    "message": "继续",
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
        self._patch_service(monkeypatch, reply_text="部分响应", status="timeout")
        with _make_client() as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={
                    "project_name": "demo",
                    "message": "长时间任务",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "timeout"
        assert resp.json()["reply"] == "部分响应"


class _StubSessionManager:
    """A SessionManager whose stream_messages yields a scripted sequence."""

    def __init__(self, live_messages, *, status="running", flood=False):
        self._live = list(live_messages)
        self.status = status
        self._flood = flood

    async def get_status(self, session_id):
        return self.status

    @contextlib.asynccontextmanager
    async def stream_messages(self, session_id, *, replay=True, idle_timeout=5.0):
        live = self._live
        flood = self._flood

        async def _iter():
            yield {"type": "_replay_done"}
            for msg in live:
                yield msg
            # flood: 持续以 <idle_timeout 间隔吐消息,_idle 永不触发。
            while flood:
                await asyncio.sleep(0.01)
                yield {"type": "assistant", "content": [{"type": "text", "text": "x"}]}

        yield _iter()


class TestCollectReply:
    async def test_enforces_deadline_under_continuous_traffic(self):
        """持续 <idle_timeout 间隔的消息流下,deadline 仍被每轮检查 → timeout。

        回归保护:若 deadline 只在 _idle 上判,这里会无限挂起(由外层 wait_for 兜底失败)。
        """
        service = SimpleNamespace(session_manager=_StubSessionManager([], flood=True))
        reply, status = await asyncio.wait_for(
            agent_chat._collect_reply(service, "sess-1", timeout=0.05),
            timeout=5.0,
        )
        assert status == "timeout"

    async def test_queue_overflow_yields_error(self):
        """直播阶段 _queue_overflow → 显式收尾为 error,不傻等超时。"""
        service = SimpleNamespace(
            session_manager=_StubSessionManager([{"type": "_queue_overflow", "session_id": "sdk-1"}]),
        )
        reply, status = await asyncio.wait_for(
            agent_chat._collect_reply(service, "sess-1", timeout=5.0),
            timeout=5.0,
        )
        assert status == "error"

    async def test_result_message_completes(self):
        service = SimpleNamespace(
            session_manager=_StubSessionManager(
                [
                    {"type": "assistant", "content": [{"type": "text", "text": "你好"}]},
                    {"type": "result", "subtype": "success", "is_error": False},
                ]
            ),
        )
        reply, status = await asyncio.wait_for(
            agent_chat._collect_reply(service, "sess-1", timeout=5.0),
            timeout=5.0,
        )
        assert status == "completed"
        assert reply == "你好"


class TestExtractTextFromAssistantMessage:
    def test_list_content(self):
        msg = {"type": "assistant", "content": [{"type": "text", "text": "你好"}]}
        assert agent_chat._extract_text_from_assistant_message(msg) == "你好"

    def test_string_content(self):
        msg = {"type": "assistant", "content": "直接文本"}
        assert agent_chat._extract_text_from_assistant_message(msg) == "直接文本"

    def test_multiple_text_blocks(self):
        msg = {
            "type": "assistant",
            "content": [
                {"type": "text", "text": "第一段"},
                {"type": "tool_use", "name": "Read"},
                {"type": "text", "text": "第二段"},
            ],
        }
        assert agent_chat._extract_text_from_assistant_message(msg) == "第一段第二段"

    def test_no_text_blocks(self):
        msg = {"type": "assistant", "content": [{"type": "tool_use", "name": "Read"}]}
        assert agent_chat._extract_text_from_assistant_message(msg) == ""
