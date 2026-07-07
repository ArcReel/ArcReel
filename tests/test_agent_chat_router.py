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

from server.agent_runtime.models import Heartbeat, LiveMessage, SubscriptionReady
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
    """A SessionManager whose stream_messages yields a scripted event sequence.

    脚本事件吐完即结束流——与真实 seam 的「溢出以流结束表达」一致;
    flood=True 时持续以 <idle_timeout 间隔吐直播消息,心跳与流结束都不出现。
    """

    def __init__(self, events, *, status="running", flood=False):
        self._events = list(events)
        self.status = status
        self._flood = flood

    async def get_status(self, session_id):
        return self.status

    @contextlib.asynccontextmanager
    async def stream_messages(self, session_id, *, idle_timeout=5.0):
        events = self._events
        flood = self._flood

        async def _iter():
            yield SubscriptionReady()
            for event in events:
                yield event
            while flood:
                await asyncio.sleep(0.01)
                yield LiveMessage(message={"type": "assistant", "content": [{"type": "text", "text": "x"}]})

        yield _iter()


class TestCollectReply:
    async def test_enforces_deadline_under_continuous_traffic(self):
        """持续 <idle_timeout 间隔的消息流下,deadline 仍被每轮检查 → timeout。

        回归保护:若 deadline 只在心跳事件上判,这里会无限挂起(由外层 wait_for 兜底失败)。
        """
        service = SimpleNamespace(session_manager=_StubSessionManager([], flood=True))
        reply, status = await asyncio.wait_for(
            agent_chat._collect_reply(service, "sess-1", timeout=0.05),
            timeout=5.0,
        )
        assert status == "timeout"

    async def test_stream_end_yields_error(self):
        """订阅队列溢出以流结束表达:流结束 → 显式收尾为 error,不傻等超时。"""
        service = SimpleNamespace(session_manager=_StubSessionManager([]))
        reply, status = await asyncio.wait_for(
            agent_chat._collect_reply(service, "sess-1", timeout=5.0),
            timeout=5.0,
        )
        assert status == "error"

    async def test_result_message_completes(self):
        service = SimpleNamespace(
            session_manager=_StubSessionManager(
                [
                    LiveMessage(message={"type": "assistant", "content": [{"type": "text", "text": "你好"}]}),
                    LiveMessage(message={"type": "result", "subtype": "success", "is_error": False}),
                ]
            ),
        )
        reply, status = await asyncio.wait_for(
            agent_chat._collect_reply(service, "sess-1", timeout=5.0),
            timeout=5.0,
        )
        assert status == "completed"
        assert reply == "你好"

    async def test_heartbeat_checks_session_status(self):
        """心跳事件上判会话状态:非 running 即收尾,不等 deadline。"""
        service = SimpleNamespace(
            session_manager=_StubSessionManager([Heartbeat()], status="idle"),
        )
        reply, status = await asyncio.wait_for(
            agent_chat._collect_reply(service, "sess-1", timeout=5.0),
            timeout=5.0,
        )
        assert status == "completed"


class TestExtractReplyFromEntries:
    def test_extracts_mainline_assistant_text_after_user_seq(self):
        entries = [
            {"seq": 0, "type": "user", "content": [{"type": "text", "text": "问题"}]},
            {"seq": 1, "type": "assistant", "content": [{"type": "text", "text": "第一段"}]},
            {"seq": 2, "type": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read"}]},
            {"seq": 3, "type": "assistant", "content": [{"type": "text", "text": "第二段"}]},
        ]
        assert agent_chat._extract_reply_from_entries(entries, 0) == "第一段第二段"

    def test_skips_entries_at_or_before_user_seq(self):
        entries = [
            {"seq": 0, "type": "assistant", "content": [{"type": "text", "text": "上一轮回复"}]},
            {"seq": 1, "type": "user", "content": [{"type": "text", "text": "新问题"}]},
            {"seq": 2, "type": "assistant", "content": [{"type": "text", "text": "本轮回复"}]},
        ]
        assert agent_chat._extract_reply_from_entries(entries, 1) == "本轮回复"

    def test_skips_subagent_entries(self):
        entries = [
            {"seq": 1, "type": "assistant", "parent_tool_use_id": "t1", "content": [{"type": "text", "text": "子"}]},
            {"seq": 2, "type": "assistant", "content": [{"type": "text", "text": "主线"}]},
        ]
        assert agent_chat._extract_reply_from_entries(entries, -1) == "主线"

    def test_empty_entries(self):
        assert agent_chat._extract_reply_from_entries([], -1) == ""


class TestEntryLogFallback:
    def test_fallback_reads_event_log_when_live_reply_empty(self, monkeypatch):
        """直播收集为空时，从事件日志按本轮用户条目 seq 之后提取回复。"""
        mock_service = AsyncMock()
        pm = MagicMock()
        pm.get_project_path = MagicMock(return_value="/fake/path")
        mock_service.pm = pm
        mock_service.get_session = AsyncMock(return_value=_fake_session())
        mock_service.send_or_create = AsyncMock(
            return_value={
                "status": "accepted",
                "session_id": "sess-1",
                "entry": {"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]},
            }
        )
        mock_service.list_session_entries = AsyncMock(
            return_value={
                "session_id": "sess-1",
                "status": "completed",
                "entries": [
                    {"seq": 3, "type": "assistant", "content": [{"type": "text", "text": "旧回复"}]},
                    {"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]},
                    {"seq": 5, "type": "assistant", "content": [{"type": "text", "text": "日志兜底回复"}]},
                ],
                "draft": None,
                "draft_rev": 0,
            }
        )
        monkeypatch.setattr(agent_chat, "get_assistant_service", lambda: mock_service)
        monkeypatch.setattr(agent_chat, "_collect_reply", AsyncMock(return_value=("", "completed")))

        with _make_client() as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={"project_name": "demo", "message": "问题"},
            )

        assert resp.status_code == 200
        assert resp.json()["reply"] == "日志兜底回复"


class TestTruncatedReplyBackfill:
    """非空但被截断的直播回复不得当作完整回复静默返回：或从事件日志补齐、或标记截断。"""

    def _patch_truncated_service(self, monkeypatch, *, live_reply, live_status, entries_payload=None, entries_exc=None):
        assert (entries_payload is not None) != (entries_exc is not None), (
            "必须显式提供 entries_payload 或 entries_exc 之一（且仅提供一个），避免误配置的 mock 悄悄落入异常分支"
        )
        mock_service = AsyncMock()
        pm = MagicMock()
        pm.get_project_path = MagicMock(return_value="/fake/path")
        mock_service.pm = pm
        mock_service.get_session = AsyncMock(return_value=_fake_session())
        mock_service.send_or_create = AsyncMock(
            return_value={
                "status": "accepted",
                "session_id": "sess-1",
                "entry": {"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]},
            }
        )
        if entries_exc is not None:
            mock_service.list_session_entries = AsyncMock(side_effect=entries_exc)
        else:
            mock_service.list_session_entries = AsyncMock(return_value=entries_payload)
        monkeypatch.setattr(agent_chat, "get_assistant_service", lambda: mock_service)
        monkeypatch.setattr(agent_chat, "_collect_reply", AsyncMock(return_value=(live_reply, live_status)))
        return mock_service

    def test_error_status_backfills_nonempty_truncated_reply(self, monkeypatch):
        """error 收尾（如订阅队列溢出）时，非空的部分回复也从事件日志补齐为完整回复。"""
        self._patch_truncated_service(
            monkeypatch,
            live_reply="被截断的部分",
            live_status="error",
            entries_payload={
                "session_id": "sess-1",
                "status": "completed",
                "entries": [
                    {"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]},
                    {"seq": 5, "type": "assistant", "content": [{"type": "text", "text": "被截断的部分与完整后续"}]},
                ],
                "draft": None,
                "draft_rev": 0,
            },
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "被截断的部分与完整后续"
        assert body["status"] == "error"
        assert body["truncated"] is False

    def test_terminal_session_but_log_shorter_than_live_keeps_live_marks_truncated(self, monkeypatch):
        """会话已转终态但日志比直播已收文本更短（如取消路径丢弃了已广播未落库的尾部消息）：
        不得用更短的日志内容覆盖直播回复，且不能当作已确认完整。"""
        self._patch_truncated_service(
            monkeypatch,
            live_reply="第一部分第二部分",
            live_status="interrupted",
            entries_payload={
                "session_id": "sess-1",
                "status": "interrupted",
                "entries": [
                    {"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]},
                    {"seq": 5, "type": "assistant", "content": [{"type": "text", "text": "第一部分"}]},
                ],
                "draft": None,
                "draft_rev": 0,
            },
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "第一部分第二部分"
        assert body["truncated"] is True

    def test_terminal_session_but_empty_log_keeps_live_marks_truncated(self, monkeypatch):
        """会话已转终态但日志本轮无主线回复条目（未给出任何佐证）：
        保留直播部分文本，且不能仅凭终态就当作已确认完整。"""
        self._patch_truncated_service(
            monkeypatch,
            live_reply="被截断的部分",
            live_status="error",
            entries_payload={
                "session_id": "sess-1",
                "status": "completed",
                "entries": [{"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]}],
                "draft": None,
                "draft_rev": 0,
            },
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "被截断的部分"
        assert body["truncated"] is True

    def test_session_still_running_marks_truncated(self, monkeypatch):
        """回读时会话仍在 running：日志同样未收全，保留直播部分文本并标记截断态。"""
        self._patch_truncated_service(
            monkeypatch,
            live_reply="被截断的部分",
            live_status="error",
            entries_payload={
                "session_id": "sess-1",
                "status": "running",
                "entries": [
                    {"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]},
                    {"seq": 5, "type": "assistant", "content": [{"type": "text", "text": "日志滞后文本"}]},
                ],
                "draft": None,
                "draft_rev": 0,
            },
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "被截断的部分"
        assert body["truncated"] is True

    def test_empty_live_reply_session_still_running_marks_truncated(self, monkeypatch):
        """直播收集为空且异常收尾：回读时会话仍在 running，日志内容同样未收全，需标记截断态。"""
        self._patch_truncated_service(
            monkeypatch,
            live_reply="",
            live_status="error",
            entries_payload={
                "session_id": "sess-1",
                "status": "running",
                "entries": [
                    {"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]},
                    {"seq": 5, "type": "assistant", "content": [{"type": "text", "text": "日志滞后文本"}]},
                ],
                "draft": None,
                "draft_rev": 0,
            },
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "日志滞后文本"
        assert body["truncated"] is True

    def test_backfill_failure_keeps_partial_reply_marked_truncated(self, monkeypatch):
        """事件日志回读失败时，保留部分回复但显式标记截断态，不静默放行。"""
        self._patch_truncated_service(
            monkeypatch,
            live_reply="被截断的部分",
            live_status="error",
            entries_exc=RuntimeError("db unavailable"),
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "被截断的部分"
        assert body["status"] == "error"
        assert body["truncated"] is True

    def test_empty_log_extraction_keeps_live_reply(self, monkeypatch):
        """日志暂无主线回复条目时保留直播部分文本；会话仍 running 则标记截断。"""
        self._patch_truncated_service(
            monkeypatch,
            live_reply="被截断的部分",
            live_status="error",
            entries_payload={
                "session_id": "sess-1",
                "status": "running",
                "entries": [{"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]}],
                "draft": None,
                "draft_rev": 0,
            },
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "被截断的部分"
        assert body["truncated"] is True

    def test_empty_live_reply_and_empty_log_stays_untruncated(self, monkeypatch):
        """直播与日志均为空：没有任何内容可言截断，即使会话仍在 running 也不误标。"""
        self._patch_truncated_service(
            monkeypatch,
            live_reply="",
            live_status="error",
            entries_payload={
                "session_id": "sess-1",
                "status": "running",
                "entries": [{"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]}],
                "draft": None,
                "draft_rev": 0,
            },
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == ""
        assert body["truncated"] is False

    def test_interrupted_status_also_backfills(self, monkeypatch):
        """interrupted 收尾同属流异常路径：非空部分回复同样从终态日志补齐。"""
        self._patch_truncated_service(
            monkeypatch,
            live_reply="被截断的部分",
            live_status="interrupted",
            entries_payload={
                "session_id": "sess-1",
                "status": "interrupted",
                "entries": [
                    {"seq": 4, "type": "user", "content": [{"type": "text", "text": "问题"}]},
                    {"seq": 5, "type": "assistant", "content": [{"type": "text", "text": "中断前完整文本"}]},
                ],
                "draft": None,
                "draft_rev": 0,
            },
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "中断前完整文本"
        assert body["truncated"] is False

    def test_completed_nonempty_reply_skips_backfill(self, monkeypatch):
        """正常完成的非空回复不触发日志回读，truncated 恒为 False。"""
        mock_service = self._patch_truncated_service(
            monkeypatch,
            live_reply="完整回复",
            live_status="completed",
            entries_payload={"session_id": "sess-1", "status": "completed", "entries": []},
        )
        with _make_client() as client:
            resp = client.post("/api/v1/agent/chat", json={"project_name": "demo", "message": "问题"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "完整回复"
        assert body["truncated"] is False
        mock_service.list_session_entries.assert_not_awaited()


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
