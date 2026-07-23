"""Unit tests for SdkTranscriptAdapter."""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter


class TestSdkTranscriptAdapterLegacyPath:
    """Tests for the filesystem fallback path (store=None)."""

    async def test_read_raw_messages_returns_adapted_messages(self):
        """SDK messages are adapted to the internal dict format."""
        mock_msg = MagicMock()
        mock_msg.type = "user"
        mock_msg.message = {"content": "Hello"}
        mock_msg.uuid = "uuid-123"
        mock_msg.parent_tool_use_id = None
        mock_msg.timestamp = "2026-03-05T00:00:00Z"

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            result = await adapter.read_raw_messages("sdk-session-123")

        assert len(result) == 1
        assert result[0]["type"] == "user"
        assert result[0]["content"] == "Hello"
        assert result[0]["uuid"] == "uuid-123"
        assert result[0]["timestamp"] == "2026-03-05T00:00:00Z"

    async def test_read_raw_messages_empty_session_id(self):
        """Empty session ID returns empty list."""
        adapter = SdkTranscriptAdapter()
        assert await adapter.read_raw_messages("") == []
        assert await adapter.read_raw_messages(None) == []

    async def test_read_raw_messages_sdk_error_returns_empty(self):
        """SDK exceptions are caught and return empty list."""
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            side_effect=RuntimeError("SDK error"),
        ):
            adapter = SdkTranscriptAdapter()
            assert await adapter.read_raw_messages("sdk-session-123") == []

    async def test_parent_tool_use_id_preserved(self):
        """parent_tool_use_id is included when present."""
        mock_msg = MagicMock()
        mock_msg.type = "user"
        mock_msg.message = {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}
        mock_msg.uuid = "uuid-456"
        mock_msg.parent_tool_use_id = "task-1"
        mock_msg.timestamp = None

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            result = await adapter.read_raw_messages("sdk-session-123")

        assert result[0]["parent_tool_use_id"] == "task-1"

    async def test_assistant_message_content_is_list(self):
        """Assistant messages preserve content as-is (list of blocks)."""
        mock_msg = MagicMock()
        mock_msg.type = "assistant"
        mock_msg.message = {"content": [{"type": "text", "text": "Hello"}]}
        mock_msg.uuid = "uuid-789"
        mock_msg.parent_tool_use_id = None
        mock_msg.timestamp = "2026-03-05T00:00:01Z"

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages",
            return_value=[mock_msg],
        ):
            adapter = SdkTranscriptAdapter()
            result = await adapter.read_raw_messages("sdk-session-123")

        assert result[0]["type"] == "assistant"
        assert result[0]["content"] == [{"type": "text", "text": "Hello"}]

    async def test_legacy_result_does_not_backfill_failure_fields(self):
        mock_msg = MagicMock(
            spec=[
                "type",
                "message",
                "uuid",
                "parent_tool_use_id",
                "timestamp",
                "subtype",
                "is_error",
                "api_error_status",
                "errors",
                "result",
            ]
        )
        mock_msg.type = "result"
        mock_msg.message = {}
        mock_msg.uuid = "legacy-result"
        mock_msg.parent_tool_use_id = None
        mock_msg.timestamp = None
        mock_msg.subtype = "error_during_execution"
        mock_msg.is_error = True
        mock_msg.api_error_status = 429
        mock_msg.errors = ["rate limited"]
        mock_msg.result = "failed"

        with patch("server.agent_runtime.sdk_transcript_adapter.get_session_messages", return_value=[mock_msg]):
            result = await SdkTranscriptAdapter().read_raw_messages("sdk-session")

        assert result[0] == {
            "type": "result",
            "content": "",
            "uuid": "legacy-result",
            "timestamp": None,
        }


class TestSdkTranscriptAdapterStorePath:
    """Tests for the SessionStore-backed read path."""

    @pytest.mark.asyncio
    async def test_read_via_store_returns_adapted_messages(self):
        """Store path uses get_session_messages_from_store and inherits timestamp from SessionMessage.

        SessionMessage.timestamp is round-tripped from the payload.timestamp we
        persist in DbSessionStore (Task 4), so no JSONL backfill is required.
        """
        mock_msg = MagicMock()
        mock_msg.type = "user"
        mock_msg.message = {"content": "Hello"}
        mock_msg.uuid = "uuid-store"
        mock_msg.parent_tool_use_id = None
        mock_msg.timestamp = "2026-05-01T00:00:00Z"

        fake_store = object()
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=AsyncMock(return_value=[mock_msg]),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_raw_messages("sdk-session-store", project_cwd="/tmp/proj")

        assert len(result) == 1
        assert result[0]["timestamp"] == "2026-05-01T00:00:00Z"
        assert result[0]["type"] == "user"
        assert result[0]["content"] == "Hello"
        assert result[0]["uuid"] == "uuid-store"

    @pytest.mark.asyncio
    async def test_read_via_store_passes_directory(self):
        """The store helper receives the project_cwd as `directory=`."""
        fake_store = object()
        helper = AsyncMock(return_value=[])
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=helper,
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            await adapter.read_raw_messages("sdk-session-x", project_cwd="/tmp/proj")
        helper.assert_awaited_once()
        args, kwargs = helper.call_args
        assert args[0] is fake_store
        assert args[1] == "sdk-session-x"
        assert kwargs.get("directory") == "/tmp/proj"

    @pytest.mark.asyncio
    async def test_read_via_store_returns_empty_on_error(self):
        """Store helper exceptions are swallowed and returned as an empty list."""
        fake_store = object()
        helper = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=helper,
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_raw_messages("sdk-session-x", project_cwd="/tmp/proj")
        assert result == []

    @pytest.mark.asyncio
    async def test_read_via_store_backfills_timestamp_from_store_payload(self):
        """SessionMessage from SDK has no timestamp; adapter backfills via store.load()."""
        mock_msg = MagicMock(spec=["type", "message", "uuid", "parent_tool_use_id"])
        mock_msg.type = "user"
        mock_msg.message = {"content": "Hello"}
        mock_msg.uuid = "uuid-789"
        mock_msg.parent_tool_use_id = None
        # Note: do NOT set mock_msg.timestamp — to mimic real SDK that omits the field

        fake_store = MagicMock()
        fake_store.load = AsyncMock(
            return_value=[
                {
                    "type": "user",
                    "uuid": "uuid-789",
                    "timestamp": "2026-05-01T01:00:00Z",
                    "message": {"content": "Hello"},
                },
            ]
        )

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=AsyncMock(return_value=[mock_msg]),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_raw_messages(
                "sdk-session", project_cwd=os.path.join(tempfile.gettempdir(), "proj")
            )

        assert result[0]["timestamp"] == "2026-05-01T01:00:00Z"
        fake_store.load.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_read_via_store_backfills_tool_use_result_from_store_payload(self):
        """AskUserQuestion 等工具的结构化结果（toolUseResult）从 store payload 回填，
        懒生成重放据此产出与 live 相同的 typed 答复条目。"""
        mock_msg = MagicMock(spec=["type", "message", "uuid", "parent_tool_use_id"])
        mock_msg.type = "user"
        mock_msg.message = {"content": [{"type": "tool_result", "tool_use_id": "tu-q", "content": "answered"}]}
        mock_msg.uuid = "uuid-ans"
        mock_msg.parent_tool_use_id = None

        fake_store = MagicMock()
        fake_store.load = AsyncMock(
            return_value=[
                {
                    "type": "user",
                    "uuid": "uuid-ans",
                    "timestamp": "2026-05-01T01:00:00Z",
                    "message": {"content": []},
                    "toolUseResult": {"questions": [], "answers": {"继续吗?": "继续"}, "annotations": {}},
                },
            ]
        )

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=AsyncMock(return_value=[mock_msg]),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_raw_messages("sdk-session", project_cwd="/tmp/proj")

        assert result[0]["tool_use_result"] == {"questions": [], "answers": {"继续吗?": "继续"}, "annotations": {}}

    @pytest.mark.asyncio
    async def test_read_via_store_omits_tool_use_result_when_absent(self):
        mock_msg = MagicMock(spec=["type", "message", "uuid", "parent_tool_use_id"])
        mock_msg.type = "user"
        mock_msg.message = {"content": "x"}
        mock_msg.uuid = "uuid-plain"
        mock_msg.parent_tool_use_id = None

        fake_store = MagicMock()
        fake_store.load = AsyncMock(return_value=[{"type": "user", "uuid": "uuid-plain", "message": {"content": "x"}}])

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=AsyncMock(return_value=[mock_msg]),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_raw_messages("sdk-session", project_cwd="/tmp/proj")

        assert "tool_use_result" not in result[0]

    @pytest.mark.asyncio
    async def test_read_via_store_handles_missing_payload_timestamp(self):
        """When the store entry has no timestamp, output stays None — no crash."""
        mock_msg = MagicMock(spec=["type", "message", "uuid", "parent_tool_use_id"])
        mock_msg.type = "user"
        mock_msg.message = {"content": "x"}
        mock_msg.uuid = "uuid-xyz"
        mock_msg.parent_tool_use_id = None

        fake_store = MagicMock()
        fake_store.load = AsyncMock(
            return_value=[
                {"type": "user", "uuid": "uuid-xyz", "message": {"content": "x"}},  # no timestamp
            ]
        )

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.get_session_messages_from_store",
            new=AsyncMock(return_value=[mock_msg]),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_raw_messages("sdk-session", project_cwd="/tmp/proj")

        assert result[0]["timestamp"] is None


class TestSubagentTimelines:
    """read_subagent_timelines — subagent subpath 读取与 Task tool_use 锚定。"""

    @staticmethod
    def _main_payloads():
        """主线原始载荷：Task tool_result 携带 toolUseResult.agentId 锚定元数据。"""
        return [
            {
                "type": "user",
                "uuid": "uuid-anchor",
                "timestamp": "2026-05-01T00:00:10Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_agent", "content": "报告"}],
                },
                "toolUseResult": {"status": "completed", "agentId": "abc123", "agentType": "Explore"},
            },
        ]

    @staticmethod
    def _sub_message():
        mock_msg = MagicMock(spec=["type", "message", "uuid", "parent_tool_use_id"])
        mock_msg.type = "assistant"
        mock_msg.message = {"content": [{"type": "text", "text": "sub reply"}]}
        mock_msg.uuid = "sub-uuid-1"
        mock_msg.parent_tool_use_id = None
        return mock_msg

    async def test_store_path_groups_messages_by_anchored_tool_use_id(self):
        fake_store = MagicMock()

        async def _load(key):
            if key.get("subpath"):
                return [{"type": "assistant", "uuid": "sub-uuid-1", "timestamp": "2026-05-01T00:00:05Z"}]
            return self._main_payloads()

        fake_store.load = AsyncMock(side_effect=_load)

        with (
            patch(
                "server.agent_runtime.sdk_transcript_adapter.list_subagents_from_store",
                new=AsyncMock(return_value=["abc123"]),
            ),
            patch(
                "server.agent_runtime.sdk_transcript_adapter.get_subagent_messages_from_store",
                new=AsyncMock(return_value=[self._sub_message()]),
            ),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_subagent_timelines("sdk-session", project_cwd="/tmp/proj")

        assert set(result.keys()) == {"toolu_agent"}
        assert result["toolu_agent"][0]["type"] == "assistant"
        assert result["toolu_agent"][0]["uuid"] == "sub-uuid-1"
        # 子时间线时间戳从 subpath 载荷回填
        assert result["toolu_agent"][0]["timestamp"] == "2026-05-01T00:00:05Z"

    async def test_agent_without_anchor_is_skipped(self):
        fake_store = MagicMock()
        fake_store.load = AsyncMock(return_value=self._main_payloads())

        with (
            patch(
                "server.agent_runtime.sdk_transcript_adapter.list_subagents_from_store",
                new=AsyncMock(return_value=["ghost"]),
            ),
            patch(
                "server.agent_runtime.sdk_transcript_adapter.get_subagent_messages_from_store",
                new=AsyncMock(return_value=[self._sub_message()]),
            ),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_subagent_timelines("sdk-session", project_cwd="/tmp/proj")

        assert result == {}

    async def test_concurrent_agents_resolved_independently_one_failure_does_not_drop_others(self):
        """多个 subagent 并发读取：各自独立解析，一个读取失败不影响其余结果。"""
        payloads = [
            {
                "type": "user",
                "uuid": "uuid-anchor-1",
                "timestamp": "2026-05-01T00:00:10Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "报告1"}],
                },
                "toolUseResult": {"status": "completed", "agentId": "agent-1"},
            },
            {
                "type": "user",
                "uuid": "uuid-anchor-2",
                "timestamp": "2026-05-01T00:00:20Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "报告2"}],
                },
                "toolUseResult": {"status": "completed", "agentId": "agent-2"},
            },
        ]
        fake_store = MagicMock()
        fake_store.load = AsyncMock(return_value=payloads)

        def _sub_msg(uuid: str) -> MagicMock:
            mock_msg = MagicMock(spec=["type", "message", "uuid", "parent_tool_use_id"])
            mock_msg.type = "assistant"
            mock_msg.message = {"content": [{"type": "text", "text": uuid}]}
            mock_msg.uuid = uuid
            mock_msg.parent_tool_use_id = None
            return mock_msg

        async def _get_subagent_messages(store, session_id, agent_id, directory=None):
            if agent_id == "agent-1":
                return [_sub_msg("sub-1")]
            raise RuntimeError("boom")

        with (
            patch(
                "server.agent_runtime.sdk_transcript_adapter.list_subagents_from_store",
                new=AsyncMock(return_value=["agent-1", "agent-2"]),
            ),
            patch(
                "server.agent_runtime.sdk_transcript_adapter.get_subagent_messages_from_store",
                new=_get_subagent_messages,
            ),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            result = await adapter.read_subagent_timelines("sdk-session", project_cwd="/tmp/proj")

        assert set(result.keys()) == {"toolu_1"}
        assert result["toolu_1"][0]["uuid"] == "sub-1"

    async def test_legacy_filesystem_path_degrades_to_empty(self):
        """文件系统回退（无 store）：公开读取接口不携带锚定元数据，降级为不合并。"""
        adapter = SdkTranscriptAdapter()
        assert await adapter.read_subagent_timelines("sdk-session") == {}

    async def test_empty_session_id_returns_empty(self):
        adapter = SdkTranscriptAdapter(store=MagicMock())
        assert await adapter.read_subagent_timelines("") == {}
        assert await adapter.read_subagent_timelines(None) == {}

    async def test_list_subagents_error_returns_empty(self):
        fake_store = MagicMock()
        fake_store.load = AsyncMock(return_value=self._main_payloads())

        with patch(
            "server.agent_runtime.sdk_transcript_adapter.list_subagents_from_store",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            adapter = SdkTranscriptAdapter(store=fake_store)
            assert await adapter.read_subagent_timelines("sdk-session", project_cwd="/tmp/proj") == {}
