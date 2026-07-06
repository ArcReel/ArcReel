"""Live 写入点管道：draft 累积、log_entry/log_delta 广播、精确替换。"""

from __future__ import annotations

from typing import Any

from server.agent_runtime.entry_pipeline import DraftAccumulator, SessionEntryPipeline


class _RecordingStore:
    """记录 append 调用并模拟 seq 分配的假事件日志存储。"""

    def __init__(self):
        self.entries: list[dict[str, Any]] = []

    async def append(self, session_id: str, entries: list[dict], *, client_key=None) -> list[dict]:
        appended = []
        for entry in entries:
            appended.append({"seq": len(self.entries), **entry})
            self.entries.append(appended[-1])
        return appended


def _make_pipeline(session_id: str | None = "s1"):
    store = _RecordingStore()
    broadcasts: list[dict] = []
    pipeline = SessionEntryPipeline(
        store,  # type: ignore[arg-type]
        session_id_provider=lambda: session_id,
        broadcast=broadcasts.append,
    )
    return pipeline, store, broadcasts


def _stream_event(event: dict, *, parent: str | None = None) -> dict:
    msg: dict[str, Any] = {"type": "stream_event", "event": event}
    if parent:
        msg["parent_tool_use_id"] = parent
    return msg


def _message_start(message_id: str = "msg_01") -> dict:
    return _stream_event({"type": "message_start", "message": {"id": message_id}})


def _text_delta(text: str, index: int = 0) -> dict:
    return _stream_event({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}})


class TestDraftAccumulator:
    def test_message_start_captures_message_id(self):
        draft = DraftAccumulator()
        draft.apply_stream_event(_message_start("msg_42"))
        assert draft.message_id == "msg_42"

    def test_text_accumulation_and_snapshot(self):
        draft = DraftAccumulator()
        draft.apply_stream_event(_message_start())
        d1 = draft.apply_stream_event(_text_delta("你"))
        d2 = draft.apply_stream_event(_text_delta("好"))
        assert d1 is not None and d1["delta_type"] == "text_delta" and d1["text"] == "你"
        assert d2 is not None and d2["rev"] > d1["rev"]

        snapshot = draft.snapshot()
        assert snapshot is not None
        assert snapshot["message_id"] == "msg_01"
        assert snapshot["content"] == [{"type": "text", "text": "你好"}]
        assert snapshot["rev"] == d2["rev"]

    def test_block_start_broadcasts_normalized_block(self):
        draft = DraftAccumulator()
        draft.apply_stream_event(_message_start())
        delta = draft.apply_stream_event(
            _stream_event(
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {}},
                }
            )
        )
        assert delta is not None
        assert delta["delta_type"] == "block_start"
        assert delta["block_index"] == 1
        assert delta["block"]["name"] == "Bash"

    def test_input_json_delta_accumulates(self):
        draft = DraftAccumulator()
        draft.apply_stream_event(_message_start())
        draft.apply_stream_event(
            _stream_event(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {}},
                }
            )
        )
        draft.apply_stream_event(
            _stream_event(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": '{"comm'},
                }
            )
        )
        draft.apply_stream_event(
            _stream_event(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": 'and": "ls"}'},
                }
            )
        )
        snapshot = draft.snapshot()
        assert snapshot is not None
        assert snapshot["content"][0]["input"] == {"command": "ls"}

    def test_snapshot_carries_accumulated_tool_json(self):
        """快照携带原始 partial JSON，重连客户端以此为前缀续拼后续 delta。"""
        draft = DraftAccumulator()
        draft.apply_stream_event(_message_start())
        draft.apply_stream_event(
            _stream_event(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": '{"file": "a'},
                }
            )
        )
        snapshot = draft.snapshot()
        assert snapshot is not None
        assert snapshot["tool_json"] == {0: '{"file": "a'}

    def test_cross_parent_delta_filtered(self):
        """并行 subagent 流交错：非当前 parent 的增量被丢弃，不污染单槽草稿。"""
        draft = DraftAccumulator()
        draft.apply_stream_event(_stream_event({"type": "message_start", "message": {"id": "msg_b"}}, parent="tu-b"))
        draft.apply_stream_event(
            _stream_event(
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "B文本"}},
                parent="tu-b",
            )
        )
        # 另一条并行流（parent 不同）的增量不得拼入当前草稿
        stolen = draft.apply_stream_event(
            _stream_event(
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "A文本"}},
                parent="tu-a",
            )
        )
        assert stolen is None
        snapshot = draft.snapshot()
        assert snapshot is not None
        assert snapshot["content"] == [{"type": "text", "text": "B文本"}]
        assert snapshot["parent_tool_use_id"] == "tu-b"

    def test_orphan_delta_without_message_start_ignored(self):
        draft = DraftAccumulator()
        assert draft.apply_stream_event(_text_delta("x")) is None
        assert draft.snapshot() is None

    def test_clear_for_message_only_matches_same_id(self):
        draft = DraftAccumulator()
        draft.apply_stream_event(_message_start("msg_01"))
        draft.apply_stream_event(_text_delta("hi"))
        assert draft.clear_for_message("msg_other") is False
        assert draft.snapshot() is not None
        assert draft.clear_for_message("msg_01") is True
        assert draft.snapshot() is None

    def test_rev_is_monotonic_across_messages(self):
        draft = DraftAccumulator()
        draft.apply_stream_event(_message_start("msg_01"))
        d1 = draft.apply_stream_event(_text_delta("a"))
        draft.apply_stream_event(_message_start("msg_02"))
        d2 = draft.apply_stream_event(_text_delta("b"))
        assert d1 is not None and d2 is not None
        assert d2["rev"] > d1["rev"]


class TestSessionEntryPipeline:
    async def test_assistant_message_appends_entry_and_clears_matching_draft(self):
        pipeline, store, broadcasts = _make_pipeline()
        await pipeline.handle_message(_message_start("msg_01"))
        await pipeline.handle_message(_text_delta("你好"))
        assert pipeline.draft.snapshot() is not None

        await pipeline.handle_message(
            {
                "type": "assistant",
                "message_id": "msg_01",
                "uuid": "a-1",
                "content": [{"type": "text", "text": "你好"}],
            }
        )

        assert len(store.entries) == 1
        assert store.entries[0]["type"] == "assistant"
        assert store.entries[0]["message_id"] == "msg_01"
        # draft 被同 message_id 权威条目精确替换
        assert pipeline.draft.snapshot() is None

        entry_events = [b for b in broadcasts if b["type"] == "log_entry"]
        delta_events = [b for b in broadcasts if b["type"] == "log_delta"]
        assert len(entry_events) == 1
        assert entry_events[0]["entry"]["seq"] == 0
        assert len(delta_events) == 1

    async def test_assistant_with_other_message_id_keeps_draft(self):
        pipeline, _store, _broadcasts = _make_pipeline()
        await pipeline.handle_message(_message_start("msg_02"))
        await pipeline.handle_message(_text_delta("进行中"))

        await pipeline.handle_message({"type": "assistant", "message_id": "msg_01", "uuid": "a-0", "content": []})
        assert pipeline.draft.snapshot() is not None

    async def test_result_clears_draft_without_logging(self):
        pipeline, store, broadcasts = _make_pipeline()
        await pipeline.handle_message(_message_start("msg_01"))
        await pipeline.handle_message(_text_delta("部分内容"))

        await pipeline.handle_message({"type": "result", "subtype": "error_during_execution"})

        assert store.entries == []
        assert pipeline.draft.snapshot() is None
        # 轮次终结标记：entry 流以此产出终态，保证末条 log_entry 先送达
        assert broadcasts[-1] == {"type": "log_turn_complete", "session_id": "s1"}

    async def test_tool_result_user_message_becomes_independent_entries(self):
        pipeline, store, broadcasts = _make_pipeline()
        await pipeline.handle_message(
            {
                "type": "user",
                "uuid": "u-1",
                "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "ok"}],
            }
        )
        assert len(store.entries) == 1
        assert store.entries[0]["type"] == "tool_result"
        assert store.entries[0]["tool_use_id"] == "tu-1"
        assert broadcasts[0]["entry"]["seq"] == 0

    async def test_no_session_id_skips_everything(self):
        pipeline, store, broadcasts = _make_pipeline(session_id=None)
        await pipeline.handle_message({"type": "assistant", "content": [{"type": "text", "text": "x"}]})
        assert store.entries == []
        assert broadcasts == []

    async def test_store_failure_is_swallowed(self):
        class _BrokenStore:
            async def append(self, *args, **kwargs):
                raise RuntimeError("db down")

        broadcasts: list[dict] = []
        pipeline = SessionEntryPipeline(
            _BrokenStore(),  # type: ignore[arg-type]
            session_id_provider=lambda: "s1",
            broadcast=broadcasts.append,
        )
        # 重试耗尽后不抛出——不打断会话消费
        await pipeline.handle_message({"type": "assistant", "content": [{"type": "text", "text": "x"}]})
        assert broadcasts == []

    async def test_transient_store_failure_retried(self):
        """瞬时落库失败（SQLite busy 等）有界重试，避免时间线永久空洞。"""

        class _FlakyStore(_RecordingStore):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            async def append(self, session_id, entries, *, client_key=None):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("db busy")
                return await super().append(session_id, entries, client_key=client_key)

        store = _FlakyStore()
        broadcasts: list[dict] = []
        pipeline = SessionEntryPipeline(
            store,  # type: ignore[arg-type]
            session_id_provider=lambda: "s1",
            broadcast=broadcasts.append,
        )
        await pipeline.handle_message({"type": "assistant", "uuid": "a-1", "content": [{"type": "text", "text": "x"}]})
        assert store.attempts == 2
        assert len(store.entries) == 1
        assert [b["type"] for b in broadcasts] == ["log_entry"]
