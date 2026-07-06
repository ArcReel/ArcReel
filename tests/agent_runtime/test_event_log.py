"""会话事件日志：写入点定型、seq 单调、幂等键、懒生成。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from server.agent_runtime.event_log import (
    REPLAYED_USER_ECHO_KEY,
    EventLogService,
    EventLogStore,
    SdkMessageNormalizer,
    build_user_entry,
    normalize_sdk_message_to_entries,
)


@pytest.fixture()
async def log_store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield EventLogStore(session_factory=factory)
    await engine.dispose()


@pytest.fixture()
async def file_log_store(tmp_path):
    """文件 SQLite + NullPool：并发测试需要独立连接（内存库 StaticPool 会串扰）。"""
    from sqlalchemy import event, pool

    db_path = tmp_path / "event-log.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=pool.NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield EventLogStore(session_factory=factory)
    await engine.dispose()


# ---------------------------------------------------------------------------
# normalize_sdk_message_to_entries — 写入点定型纯函数
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_assistant_message_becomes_single_entry_with_message_id(self):
        entries = normalize_sdk_message_to_entries(
            {
                "type": "assistant",
                "message_id": "msg_01",
                "uuid": "u-1",
                "content": [{"type": "text", "text": "你好"}],
            }
        )
        assert len(entries) == 1
        entry = entries[0]
        assert entry["type"] == "assistant"
        assert entry["message_id"] == "msg_01"
        assert entry["uuid"] == "u-1"
        assert entry["content"] == [{"type": "text", "text": "你好"}]
        assert entry["timestamp"]

    def test_assistant_infers_untyped_blocks(self):
        entries = normalize_sdk_message_to_entries(
            {
                "type": "assistant",
                "content": [{"id": "tu-1", "name": "Bash", "input": {"command": "ls"}}],
            }
        )
        assert entries[0]["content"][0]["type"] == "tool_use"

    def test_tool_result_blocks_become_independent_entries(self):
        entries = normalize_sdk_message_to_entries(
            {
                "type": "user",
                "uuid": "u-2",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu-1", "content": "ok", "is_error": False},
                    {"tool_use_id": "tu-2", "content": [{"type": "text", "text": "boom"}], "is_error": True},
                ],
            }
        )
        assert [e["type"] for e in entries] == ["tool_result", "tool_result"]
        assert entries[0]["tool_use_id"] == "tu-1"
        assert entries[0]["content"] == "ok"
        assert entries[1]["tool_use_id"] == "tu-2"
        assert entries[1]["content"] == "boom"
        assert entries[1]["is_error"] is True
        # 独立条目、不同 uuid
        assert entries[0]["uuid"] != entries[1]["uuid"]

    def test_mixed_user_content_splits_tool_results_from_text(self):
        entries = normalize_sdk_message_to_entries(
            {
                "type": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu-1", "content": "ok"},
                    {"type": "text", "text": "继续"},
                ],
            }
        )
        assert [e["type"] for e in entries] == ["tool_result", "user"]
        assert entries[1]["content"] == [{"type": "text", "text": "继续"}]

    def test_subagent_message_carries_parent_tool_use_id(self):
        entries = normalize_sdk_message_to_entries(
            {
                "type": "assistant",
                "parent_tool_use_id": "tu-parent",
                "content": [{"type": "text", "text": "sub"}],
            }
        )
        assert entries[0]["parent_tool_use_id"] == "tu-parent"

        entries = normalize_sdk_message_to_entries(
            {
                "type": "user",
                "parent_tool_use_id": "tu-parent",
                "content": [{"type": "tool_result", "tool_use_id": "tu-sub", "content": "x"}],
            }
        )
        assert entries[0]["parent_tool_use_id"] == "tu-parent"

    def test_local_echo_and_replayed_echo_are_skipped(self):
        assert normalize_sdk_message_to_entries({"type": "user", "content": "hi", "local_echo": True}) == []
        assert normalize_sdk_message_to_entries({"type": "user", "content": "hi", REPLAYED_USER_ECHO_KEY: True}) == []

    def test_stream_event_and_result_are_not_logged(self):
        assert normalize_sdk_message_to_entries({"type": "stream_event", "event": {"type": "message_start"}}) == []
        assert normalize_sdk_message_to_entries({"type": "result", "subtype": "success"}) == []
        assert normalize_sdk_message_to_entries({"type": "runtime_status", "status": "idle"}) == []

    def test_task_system_message_becomes_generic_system_entry(self):
        entries = normalize_sdk_message_to_entries(
            {
                "type": "system",
                "subtype": "task_notification",
                "task_id": "t1",
                "status": "completed",
                "summary": "done",
                "tool_use_id": "tu-1",
            }
        )
        assert len(entries) == 1
        assert entries[0]["type"] == "system"
        assert entries[0]["subtype"] == "task_notification"
        assert entries[0]["task_id"] == "t1"
        assert entries[0]["task_status"] == "completed"

    def test_other_system_subtypes_are_ignored(self):
        assert normalize_sdk_message_to_entries({"type": "system", "subtype": "init", "session_id": "s"}) == []
        assert normalize_sdk_message_to_entries({"type": "system", "subtype": "compact_boundary"}) == []

    def test_plain_string_user_content(self):
        entries = normalize_sdk_message_to_entries({"type": "user", "content": "[Request interrupted by user]"})
        assert len(entries) == 1
        assert entries[0]["type"] == "user"
        assert entries[0]["content"] == [{"type": "text", "text": "[Request interrupted by user]"}]


# ---------------------------------------------------------------------------
# SdkMessageNormalizer — skill 调用定型（跨消息状态）
# ---------------------------------------------------------------------------


_SKILL_INJECTION_TEXT = (
    "Base directory for this skill: /proj/.claude/skills/generate-storyboard\n\n# 生成分镜图\n\n完整注入正文……"
)


class TestSkillInvocationTyping:
    def test_injection_becomes_typed_entry_with_name_and_args_from_tool_use(self):
        normalizer = SdkMessageNormalizer()
        normalizer.normalize(
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu-skill",
                        "name": "Skill",
                        "input": {"skill": "generate-storyboard", "args": "第一集所有场景"},
                    }
                ],
            }
        )
        entries = normalizer.normalize(
            {
                "type": "user",
                "uuid": "u-inject",
                "content": [{"type": "text", "text": _SKILL_INJECTION_TEXT}],
            }
        )

        assert len(entries) == 1
        entry = entries[0]
        assert entry["type"] == "system"
        assert entry["subtype"] == "skill_invocation"
        assert entry["skill_name"] == "generate-storyboard"
        assert entry["skill_args"] == "第一集所有场景"
        assert entry["tool_use_id"] == "tu-skill"
        # 注入全文不进日志：条目任何字段都不携带正文
        import json

        assert "完整注入正文" not in json.dumps(entries, ensure_ascii=False)

    def test_injection_without_prior_tool_use_parses_name_from_path(self):
        normalizer = SdkMessageNormalizer()
        entries = normalizer.normalize(
            {
                "type": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Base directory for this skill: /tmp/.claude/skills/commit/SKILL.md\n\n正文",
                    }
                ],
            }
        )
        assert len(entries) == 1
        assert entries[0]["subtype"] == "skill_invocation"
        assert entries[0]["skill_name"] == "commit"
        assert entries[0]["skill_args"] is None
        assert entries[0]["tool_use_id"] is None

    def test_skill_content_prefix_also_recognized(self):
        normalizer = SdkMessageNormalizer()
        entries = normalizer.normalize({"type": "user", "content": [{"type": "text", "text": "Skill content: 正文"}]})
        assert len(entries) == 1
        assert entries[0]["subtype"] == "skill_invocation"
        assert entries[0]["skill_name"] is None

    def test_pending_skill_consumed_once(self):
        normalizer = SdkMessageNormalizer()
        normalizer.normalize(
            {
                "type": "assistant",
                "content": [{"type": "tool_use", "id": "tu-1", "name": "Skill", "input": {"skill": "commit"}}],
            }
        )
        first = normalizer.normalize({"type": "user", "content": [{"type": "text", "text": "Skill content: A"}]})
        second = normalizer.normalize({"type": "user", "content": [{"type": "text", "text": "Skill content: B"}]})
        assert first[0]["tool_use_id"] == "tu-1"
        assert second[0]["tool_use_id"] is None

    def test_concurrent_skill_calls_in_one_message_consumed_in_order(self):
        """同一 assistant 消息内并发发起两个 Skill 调用：按调用顺序逐一消费，不覆盖。"""
        normalizer = SdkMessageNormalizer()
        normalizer.normalize(
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu-a", "name": "Skill", "input": {"skill": "skill-a"}},
                    {"type": "tool_use", "id": "tu-b", "name": "Skill", "input": {"skill": "skill-b"}},
                ],
            }
        )
        first = normalizer.normalize({"type": "user", "content": [{"type": "text", "text": "Skill content: A"}]})
        second = normalizer.normalize({"type": "user", "content": [{"type": "text", "text": "Skill content: B"}]})
        assert first[0]["skill_name"] == "skill-a"
        assert first[0]["tool_use_id"] == "tu-a"
        assert second[0]["skill_name"] == "skill-b"
        assert second[0]["tool_use_id"] == "tu-b"

    def test_skill_state_keyed_by_parent_context(self):
        """主线与 subagent 消息在 live 流中交错：skill 关联互不串扰。"""
        normalizer = SdkMessageNormalizer()
        normalizer.normalize(
            {
                "type": "assistant",
                "content": [{"type": "tool_use", "id": "tu-main", "name": "Skill", "input": {"skill": "main-skill"}}],
            }
        )
        normalizer.normalize(
            {
                "type": "assistant",
                "parent_tool_use_id": "tu-agent",
                "content": [{"type": "tool_use", "id": "tu-sub", "name": "Skill", "input": {"skill": "sub-skill"}}],
            }
        )
        sub_entries = normalizer.normalize(
            {
                "type": "user",
                "parent_tool_use_id": "tu-agent",
                "content": [{"type": "text", "text": "Skill content: sub"}],
            }
        )
        main_entries = normalizer.normalize(
            {"type": "user", "content": [{"type": "text", "text": "Skill content: main"}]}
        )
        assert sub_entries[0]["skill_name"] == "sub-skill"
        assert sub_entries[0]["tool_use_id"] == "tu-sub"
        assert sub_entries[0]["parent_tool_use_id"] == "tu-agent"
        assert main_entries[0]["skill_name"] == "main-skill"
        assert main_entries[0]["tool_use_id"] == "tu-main"

    def test_mixed_user_message_splits_tool_result_skill_and_text(self):
        normalizer = SdkMessageNormalizer()
        entries = normalizer.normalize(
            {
                "type": "user",
                "uuid": "u-mixed",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu-r", "content": "Launching skill: commit"},
                    {"type": "text", "text": "Skill content: 正文"},
                    {"type": "text", "text": "普通文本"},
                ],
            }
        )
        assert [e["type"] for e in entries] == ["tool_result", "system", "user"]
        assert entries[1]["subtype"] == "skill_invocation"
        assert entries[2]["content"] == [{"type": "text", "text": "普通文本"}]

    def test_camel_case_parent_variants_normalized_at_write_point(self):
        """三种大小写 key 变体在写入点归一化为 parent_tool_use_id。"""
        for key in ("parent_tool_use_id", "parentToolUseID", "parentToolUseId"):
            entries = normalize_sdk_message_to_entries(
                {"type": "assistant", key: "tu-p", "content": [{"type": "text", "text": "x"}]}
            )
            assert entries[0]["parent_tool_use_id"] == "tu-p", key
            assert "parentToolUseID" not in entries[0]
            assert "parentToolUseId" not in entries[0]

    def test_one_shot_wrapper_still_types_plain_messages(self):
        entries = normalize_sdk_message_to_entries({"type": "user", "content": "你好"})
        assert entries[0]["type"] == "user"


# ---------------------------------------------------------------------------
# EventLogStore — seq 单调 / 幂等键 / 游标
# ---------------------------------------------------------------------------


class TestEventLogStore:
    async def test_seq_is_monotonic_across_appends(self, log_store: EventLogStore):
        first = await log_store.append("s1", [{"type": "user", "uuid": "a"}])
        second = await log_store.append(
            "s1", [{"type": "assistant", "uuid": "b"}, {"type": "tool_result", "uuid": "c"}]
        )
        assert [e["seq"] for e in first] == [0]
        assert [e["seq"] for e in second] == [1, 2]

    async def test_seq_isolated_per_session(self, log_store: EventLogStore):
        await log_store.append("s1", [{"type": "user", "uuid": "a"}])
        other = await log_store.append("s2", [{"type": "user", "uuid": "b"}])
        assert other[0]["seq"] == 0

    async def test_list_after_returns_only_later_entries(self, log_store: EventLogStore):
        await log_store.append("s1", [{"type": "user", "uuid": "a"}, {"type": "assistant", "uuid": "b"}])
        await log_store.append("s1", [{"type": "assistant", "uuid": "c"}])
        entries = await log_store.list_after("s1", after_seq=0)
        assert [e["uuid"] for e in entries] == ["b", "c"]
        assert await log_store.list_after("s1", after_seq=2) == []

    async def test_append_user_entry_idempotent_by_client_key(self, log_store: EventLogStore):
        entry = build_user_entry([{"type": "text", "text": "hi"}])
        first, created_first = await log_store.append_user_entry("s1", entry, client_key="ck-1")
        retry = build_user_entry([{"type": "text", "text": "hi"}])
        second, created_second = await log_store.append_user_entry("s1", retry, client_key="ck-1")

        assert created_first is True
        assert created_second is False
        assert second["seq"] == first["seq"]
        assert second["uuid"] == first["uuid"]
        assert len(await log_store.list_after("s1")) == 1

    async def test_append_user_entry_without_client_key(self, log_store: EventLogStore):
        entry = build_user_entry([{"type": "text", "text": "hi"}])
        result, created = await log_store.append_user_entry("s1", entry)
        assert created is True
        assert result["seq"] == 0

    @pytest.mark.sqlite_only
    async def test_concurrent_appends_keep_seq_unique(self, file_log_store: EventLogStore):
        await asyncio.gather(*[file_log_store.append("s1", [{"type": "assistant", "uuid": f"u{i}"}]) for i in range(8)])
        entries = await file_log_store.list_after("s1")
        assert [e["seq"] for e in entries] == list(range(8))
        assert {e["uuid"] for e in entries} == {f"u{i}" for i in range(8)}

    async def test_append_retries_pk_conflict_even_without_literal_seq_in_message(
        self, log_store: EventLogStore, monkeypatch
    ):
        """seq 竞争判定不依赖错误信息字面包含 "seq"：驱动/配置不同,主键冲突的
        DETAIL 文案未必带这个词,只要不是 client_key 冲突就该按 seq 竞争重试
        （该表仅有 (session_id, seq) 主键与 client_key 唯一索引两个约束）。"""
        from sqlalchemy.exc import IntegrityError

        calls = {"n": 0}
        original_append_once = log_store._append_once  # pyright: ignore[reportPrivateUsage]

        async def _flaky_append_once(session_id, entries, client_key):
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError(
                    "INSERT INTO agent_session_event_log ...",
                    {},
                    Exception('duplicate key value violates unique constraint "agent_session_event_log_pkey"'),
                )
            return await original_append_once(session_id, entries, client_key)

        monkeypatch.setattr(log_store, "_append_once", _flaky_append_once)

        result = await log_store.append("s1", [{"type": "user", "uuid": "u1"}])

        assert calls["n"] == 2  # 首次撞主键冲突后重试一次即成功
        assert result[0]["uuid"] == "u1"

    async def test_has_entries(self, log_store: EventLogStore):
        assert await log_store.has_entries("s1") is False
        await log_store.append("s1", [{"type": "user", "uuid": "a"}])
        assert await log_store.has_entries("s1") is True

    async def test_delete_entry_rolls_back_accepted_user_entry(self, log_store: EventLogStore):
        """受理失败补偿删除：条目连同幂等键一起消失，重试可重新受理。"""
        entry = build_user_entry([{"type": "text", "text": "hi"}])
        appended, _created = await log_store.append_user_entry("s1", entry, client_key="ck-1")

        await log_store.delete_entry("s1", appended["seq"])

        assert await log_store.list_after("s1") == []
        assert await log_store.find_by_client_key("s1", "ck-1") is None
        retry = build_user_entry([{"type": "text", "text": "hi"}])
        again, created = await log_store.append_user_entry("s1", retry, client_key="ck-1")
        assert created is True
        assert again["seq"] == 0


# ---------------------------------------------------------------------------
# EventLogService — 懒生成
# ---------------------------------------------------------------------------


class _FakeAdapter:
    def __init__(self, messages, subagent_timelines=None):
        self._messages = messages
        self._subagent_timelines = subagent_timelines or {}
        self.read_count = 0

    async def read_raw_messages(self, sdk_session_id, project_cwd=None):
        self.read_count += 1
        return list(self._messages)

    async def read_subagent_timelines(self, sdk_session_id, project_cwd=None):
        return {k: list(v) for k, v in self._subagent_timelines.items()}


class TestLazyBackfill:
    async def test_backfills_from_transcript_once(self, log_store: EventLogStore):
        adapter = _FakeAdapter(
            [
                {"type": "user", "content": "写第一章", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z"},
                {
                    "type": "assistant",
                    "content": [{"type": "text", "text": "好的"}],
                    "uuid": "a1",
                    "timestamp": "2026-01-01T00:00:01Z",
                },
                {"type": "result", "subtype": "success", "uuid": "r1"},
            ]
        )
        service = EventLogService(log_store, adapter)

        entries = await service.list_entries("old-session", None)
        assert [e["type"] for e in entries] == ["user", "assistant"]
        assert [e["seq"] for e in entries] == [0, 1]
        assert entries[0]["timestamp"] == "2026-01-01T00:00:00Z"

        # 第二次访问不重复重放
        again = await service.list_entries("old-session", None)
        assert len(again) == 2
        assert adapter.read_count == 1

    async def test_concurrent_first_access_backfills_once(self, log_store: EventLogStore):
        adapter = _FakeAdapter([{"type": "user", "content": "hi", "uuid": "u1"}])
        service = EventLogService(log_store, adapter)

        results = await asyncio.gather(*[service.list_entries("old", None) for _ in range(5)])
        assert all(len(r) == 1 for r in results)
        assert len(await log_store.list_after("old")) == 1

    async def test_no_backfill_when_log_already_has_entries(self, log_store: EventLogStore):
        adapter = _FakeAdapter([{"type": "user", "content": "transcript", "uuid": "t1"}])
        service = EventLogService(log_store, adapter)
        await log_store.append("s1", [{"type": "user", "uuid": "live", "content": []}])

        entries = await service.list_entries("s1", None)
        assert [e["uuid"] for e in entries] == ["live"]
        assert adapter.read_count == 0

    async def test_cursor_filtering(self, log_store: EventLogStore):
        adapter = _FakeAdapter([])
        service = EventLogService(log_store, adapter)
        await log_store.append("s1", [{"type": "user", "uuid": "a"}, {"type": "assistant", "uuid": "b"}])

        entries = await service.list_entries("s1", None, after_seq=0)
        assert [e["uuid"] for e in entries] == ["b"]

    async def test_backfill_lock_not_leaked_when_transcript_empty(self, log_store: EventLogStore):
        """空 transcript 不写入：无协程持有/等待时锁对象随弱引用字典自动回收，
        不会为每个空/无效会话永久驻留内存；转为有内容后并发首访仍只灌入一次
        （互斥性质不受回收影响——同一 session_id 的并发等待者共享同一锁对象）。"""
        adapter = _FakeAdapter([])
        service = EventLogService(log_store, adapter)

        for i in range(50):
            await service.ensure_backfilled(f"empty-{i}", None)
        assert len(service._backfill_locks) == 0  # pyright: ignore[reportPrivateUsage]

        # transcript 补齐内容后，并发访问只灌入一次
        adapter._messages = [{"type": "user", "content": "hi", "uuid": "u1"}]  # pyright: ignore[reportPrivateUsage]
        await asyncio.gather(*[service.ensure_backfilled("old", None) for _ in range(5)])
        assert len(await log_store.list_after("old")) == 1
        # 写入成功后锁引用被清理
        assert "old" not in service._backfill_locks  # pyright: ignore[reportPrivateUsage]

    async def test_backfill_skips_message_that_fails_normalization(self, log_store: EventLogStore, monkeypatch):
        """历史消息规范化单条抛异常时容错跳过，不让整个懒生成因一条脏数据失败。"""
        adapter = _FakeAdapter(
            [
                {"type": "user", "content": "ok-1", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z"},
                {"type": "assistant", "content": [{"type": "text", "text": "poison"}], "uuid": "poison"},
                {"type": "user", "content": "ok-2", "uuid": "u2", "timestamp": "2026-01-01T00:00:02Z"},
            ]
        )
        service = EventLogService(log_store, adapter)

        original_normalize = SdkMessageNormalizer.normalize

        def _boom_on_poison(self, message):
            if message.get("uuid") == "poison":
                raise ValueError("boom")
            return original_normalize(self, message)

        monkeypatch.setattr(SdkMessageNormalizer, "normalize", _boom_on_poison)

        entries = await service.list_entries("session-with-poison", None)
        assert [e["uuid"] for e in entries] == ["u1", "u2"]


# ---------------------------------------------------------------------------
# 懒生成 — subagent subpath 合并
# ---------------------------------------------------------------------------


class TestSubagentBackfillMerge:
    @staticmethod
    def _main_messages():
        return [
            {"type": "user", "content": "调研一下", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z"},
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu-agent",
                        "name": "Agent",
                        "input": {"description": "探索代码", "prompt": "..."},
                    }
                ],
                "uuid": "a1",
            },
            {
                "type": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-agent", "content": "报告全文"}],
                "uuid": "u2",
            },
            {"type": "assistant", "content": [{"type": "text", "text": "总结"}], "uuid": "a2"},
        ]

    @staticmethod
    def _sub_messages():
        return [
            {"type": "user", "content": "内部 prompt", "uuid": "s-u1"},
            {
                "type": "assistant",
                "content": [{"type": "tool_use", "id": "tu-read", "name": "Read", "input": {"file_path": "/x"}}],
                "uuid": "s-a1",
            },
            {
                "type": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-read", "content": "内容"}],
                "uuid": "s-u2",
            },
        ]

    async def test_subagent_entries_spliced_at_task_tool_use_position(self, log_store: EventLogStore):
        adapter = _FakeAdapter(self._main_messages(), {"tu-agent": self._sub_messages()})
        service = EventLogService(log_store, adapter)

        entries = await service.list_entries("old-session", None)

        # 子条目紧跟携带 Task tool_use 的主线条目之后，先于后续主线条目
        uuids = [e["uuid"] for e in entries]
        assert uuids == ["u1", "a1", "s-u1", "s-a1", "s-u2-tr0", "u2-tr0", "a2"]
        # 子条目全部带 parent_tool_use_id，主线条目不带
        sub = [e for e in entries if e.get("parent_tool_use_id")]
        assert {e["parent_tool_use_id"] for e in sub} == {"tu-agent"}
        assert [e["uuid"] for e in sub] == ["s-u1", "s-a1", "s-u2-tr0"]

    async def test_unanchored_subagent_group_appended_at_end(self, log_store: EventLogStore):
        adapter = _FakeAdapter(
            [{"type": "user", "content": "hi", "uuid": "u1"}],
            {"tu-ghost": [{"type": "assistant", "content": [{"type": "text", "text": "孤儿"}], "uuid": "g1"}]},
        )
        service = EventLogService(log_store, adapter)

        entries = await service.list_entries("old-session", None)
        assert [e["uuid"] for e in entries] == ["u1", "g1"]
        assert entries[1]["parent_tool_use_id"] == "tu-ghost"

    async def test_skill_injection_inside_subagent_typed_with_parent(self, log_store: EventLogStore):
        sub = [
            {
                "type": "assistant",
                "content": [{"type": "tool_use", "id": "tu-s", "name": "Skill", "input": {"skill": "commit"}}],
                "uuid": "s-a1",
            },
            {"type": "user", "content": [{"type": "text", "text": "Skill content: 正文"}], "uuid": "s-u1"},
        ]
        adapter = _FakeAdapter(self._main_messages(), {"tu-agent": sub})
        service = EventLogService(log_store, adapter)

        entries = await service.list_entries("old-session", None)
        skill_entries = [e for e in entries if e.get("subtype") == "skill_invocation"]
        assert len(skill_entries) == 1
        assert skill_entries[0]["skill_name"] == "commit"
        assert skill_entries[0]["parent_tool_use_id"] == "tu-agent"
