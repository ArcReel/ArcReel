import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_store import SessionMetaStore
from tests.factories import make_session_meta


class _FakePM:
    def __init__(self, valid_project="demo"):
        self.valid_project = valid_project

    def get_project_path(self, project_name):
        if project_name != self.valid_project:
            raise FileNotFoundError(project_name)
        return Path("/tmp") / project_name


class _FakeMetaStore:
    def __init__(self, metas=None):
        self.metas = {m.id: m for m in (metas or [])}

    async def get(self, session_id):
        return self.metas.get(session_id)

    async def list(self, project_name=None, status=None, limit=50, offset=0):
        return list(self.metas.values())

    async def delete(self, session_id):
        return self.metas.pop(session_id, None) is not None


class _FakeEventLogService:
    """No-op 事件日志：单测聚焦 service 编排，不触达真实 DB。"""

    def __init__(self):
        self.backfilled = []

    async def ensure_backfilled(self, session_id, project_cwd):
        self.backfilled.append(session_id)


class _FakeSessionManager:
    def __init__(self):
        self.sessions = {}
        self.new_sessions = []
        self.sent = []
        self.sent_kwargs = []
        self.answered = []
        self.interrupted = []
        self.closed = []
        self.unsubscribed = []
        self.status = "running"
        self.buffer = []
        self.pending = []
        self.close_error = None

    async def send_new_session(self, project_name, prompt, **kwargs):
        self.new_sessions.append((project_name, prompt))
        return "sdk-new-id"

    async def get_status(self, session_id):
        return self.status

    async def get_pending_questions_snapshot(self, session_id):
        return list(self.pending)

    async def send_message(self, session_id, content, **kwargs):
        self.sent.append((session_id, content))
        self.sent_kwargs.append(kwargs)

    async def answer_user_question(self, session_id, question_id, answers):
        self.answered.append((session_id, question_id, answers))

    async def interrupt_session(self, session_id):
        self.interrupted.append(session_id)
        return "interrupted"

    async def close_session(self, session_id, *, reason="session closed"):
        self.closed.append((session_id, reason))
        if self.close_error is not None:
            raise self.close_error
        self.sessions.pop(session_id, None)

    async def shutdown_gracefully(self):
        return None


class TestAssistantServiceMore:
    @pytest.mark.asyncio
    async def test_service_init_interrupts_stale_running_sessions(self, tmp_path):
        # Create an in-memory async store and seed data
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = SessionMetaStore(session_factory=factory)

        running = await store.create("demo", "sdk-running-1")
        completed = await store.create("demo", "sdk-completed-1")
        await store.update_status(running.id, "running")
        await store.update_status(completed.id, "completed")

        service = AssistantService(project_root=tmp_path)
        # Replace the service's meta_store with our test store
        service.meta_store = store
        service.session_manager.meta_store = store

        # Manually run the interrupt logic (normally done in startup())
        await service._interrupt_stale_running_sessions()

        refreshed_running = await service.meta_store.get(running.id)
        refreshed_completed = await service.meta_store.get(completed.id)
        assert refreshed_running is not None
        assert refreshed_running.status == "interrupted"
        assert refreshed_completed is not None
        assert refreshed_completed.status == "completed"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_startup_waits_cleanup_and_is_idempotent(self, tmp_path, monkeypatch):
        service = AssistantService(project_root=tmp_path)
        calls = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fake_interrupt():
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()

        monkeypatch.setattr(service, "_interrupt_stale_running_sessions", fake_interrupt)

        startup_task = asyncio.create_task(service.startup())
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        assert not startup_task.done()

        release.set()
        await asyncio.wait_for(startup_task, timeout=0.2)
        assert calls == 1

        await service.startup()
        assert calls == 1

    @pytest.mark.asyncio
    async def test_crud_and_message_validation(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1", status="idle")

        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([meta])
        service.event_log = _FakeEventLogService()

        listed = await service.list_sessions()
        assert len(listed) == 1

        fetched = await service.get_session("s1")
        assert fetched.status == "idle"
        sm.sessions["s1"] = SimpleNamespace(status="running")
        fetched_live = await service.get_session("s1")
        assert fetched_live.status == "running"

        # send_or_create — new session (no session_id)
        new_result = await service.send_or_create("demo", "hello")
        assert new_result == {"status": "accepted", "session_id": "sdk-new-id", "entry": None}
        assert len(sm.new_sessions) == 1
        assert sm.new_sessions[0][0] == "demo"

        # send_or_create — existing session
        existing_result = await service.send_or_create("demo", "world", session_id="s1")
        assert existing_result == {"status": "accepted", "session_id": "s1", "entry": None}
        assert sm.sent == [("s1", "world")]

        # send_or_create — empty message raises ValueError
        with pytest.raises(ValueError):
            await service.send_or_create("demo", "   ")

        # send_or_create — missing session raises FileNotFoundError
        with pytest.raises(FileNotFoundError):
            await service.send_or_create("demo", "hello", session_id="missing")

        # send_or_create — project mismatch raises FileNotFoundError
        with pytest.raises(FileNotFoundError):
            await service.send_or_create("other_project", "hello", session_id="s1")

        with pytest.raises(FileNotFoundError):
            await service.answer_user_question("missing", "q1", {"a": "b"})
        await service.answer_user_question("s1", "q1", {"a": "b"})
        assert sm.answered == [("s1", "q1", {"a": "b"})]

        with pytest.raises(FileNotFoundError):
            await service.interrupt_session("missing")
        interrupted = await service.interrupt_session("s1")
        assert interrupted["session_status"] == "interrupted"

    @pytest.mark.asyncio
    async def test_send_or_create_threads_locale_into_continuation(self, tmp_path):
        """Continuation (existing session) must forward the request locale so a
        cold-recovered session rebuilds its language regulation correctly."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1", status="idle")

        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([meta])
        service.event_log = _FakeEventLogService()

        await service.send_or_create("demo", "world", session_id="s1", locale="en")

        assert sm.sent == [("s1", "world")]
        assert sm.sent_kwargs[0]["locale"] == "en"

    @pytest.mark.asyncio
    async def test_send_or_create_threads_locale_into_multimodal_continuation(self, tmp_path):
        """The image-bearing continuation branch (sdk_prompt is not None) must also
        forward the request locale, not just the text branch."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1", status="idle")

        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([meta])
        service.event_log = _FakeEventLogService()

        image = SimpleNamespace(data="ZmFrZQ==", media_type="image/png")
        await service.send_or_create("demo", "hello", session_id="s1", images=[image], locale="vi")

        assert sm.sent_kwargs[0]["locale"] == "vi"
        assert sm.sent_kwargs[0]["echo_content"] is not None

    @pytest.mark.asyncio
    async def test_send_or_create_concurrent_same_client_key_creates_one_session(self, tmp_path):
        """同一 client_key 的并发新建请求应在 send_new_session 完成前互斥等待，
        不因在途窗口各自建会话、重复执行同一 prompt。"""
        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()

        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_send_new_session(project_name, prompt, **kwargs):
            entered.set()
            await release.wait()
            sm.new_sessions.append((project_name, prompt))
            return "sdk-new-id"

        sm.send_new_session = slow_send_new_session

        async def fake_find_by_client_key(session_id, client_key):
            return {"seq": 0, "type": "user", "content": []}

        async def fake_find_new_session_by_client_key(client_key):
            return None

        service.event_log_store.find_by_client_key = fake_find_by_client_key
        service.event_log_store.find_new_session_by_client_key = fake_find_new_session_by_client_key

        task1 = asyncio.create_task(service.send_or_create("demo", "hello", client_key="ck-race"))
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        task2 = asyncio.create_task(service.send_or_create("demo", "hello", client_key="ck-race"))
        await asyncio.sleep(0)  # 让 task2 跑到锁等待处挂起
        release.set()

        result1 = await task1
        result2 = await task2

        assert len(sm.new_sessions) == 1  # 只建了一个会话，未因在途窗口重复投递
        assert result1["session_id"] == result2["session_id"] == "sdk-new-id"

    @pytest.mark.asyncio
    async def test_send_or_create_recovers_client_key_mapping_from_db_after_restart(self, tmp_path):
        """进程内幂等映射重启丢失后，受理已落库（新会话首条条目 seq 0 携带
        client_key）的重试应经 DB 兜底命中既有会话，不再重复建会话。"""
        from server.agent_runtime.event_log import EventLogStore, build_user_entry

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = EventLogStore(session_factory=factory)

        # 首次受理（重启前）：新会话首条用户条目已随 client_key 落库
        accepted_entry = build_user_entry([{"type": "text", "text": "hello"}])
        await store.append("sdk-prev", [accepted_entry], client_key="ck-restart")

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store
        assert service._new_session_client_keys == {}  # 重启后进程内映射为空

        result = await service.send_or_create("demo", "hello", client_key="ck-restart")

        assert sm.new_sessions == []  # 未重复建会话
        assert result["session_id"] == "sdk-prev"
        assert result["entry"] is not None
        assert result["entry"]["uuid"] == accepted_entry["uuid"]
        # 命中后回填进程内映射，后续重试走快路径
        assert service._new_session_client_keys["ck-restart"] == "sdk-prev"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_send_or_create_recovers_client_key_after_lru_eviction(self, tmp_path):
        """进程内 LRU 淘汰旧键后，同键重试经 DB 兜底命中原会话。"""
        from server.agent_runtime.event_log import EventLogStore

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = EventLogStore(session_factory=factory)

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store
        service._new_session_client_keys_max = 1

        # 模拟 SessionManager 的受理落库：新会话首条条目随 client_key 写入
        async def send_new_session(project_name, prompt, *, user_entry=None, client_key=None, **kwargs):
            sid = f"sdk-{len(sm.new_sessions)}"
            sm.new_sessions.append((project_name, prompt))
            if user_entry is not None:
                await store.append_user_entry(sid, user_entry, client_key=client_key)
            return sid

        sm.send_new_session = send_new_session

        first = await service.send_or_create("demo", "hello", client_key="ck-a")
        await service.send_or_create("demo", "another", client_key="ck-b")
        assert "ck-a" not in service._new_session_client_keys  # LRU 已淘汰

        retry = await service.send_or_create("demo", "hello", client_key="ck-a")

        assert len(sm.new_sessions) == 2  # 重试未新建第三个会话
        assert retry["session_id"] == first["session_id"] == "sdk-0"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_send_or_create_hit_refreshes_lru_position(self, tmp_path):
        """命中进程内映射时刷新 LRU 位置：被频繁重试命中的 key 不因插入顺序
        在先，被之后新到达但只访问一次的 key 挤出缓存（退化为 FIFO）。"""
        from server.agent_runtime.event_log import EventLogStore

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = EventLogStore(session_factory=factory)

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store
        service._new_session_client_keys_max = 2

        async def send_new_session(project_name, prompt, *, user_entry=None, client_key=None, **kwargs):
            sid = f"sdk-{len(sm.new_sessions)}"
            sm.new_sessions.append((project_name, prompt))
            if user_entry is not None:
                await store.append_user_entry(sid, user_entry, client_key=client_key)
            return sid

        sm.send_new_session = send_new_session

        await service.send_or_create("demo", "hello-a", client_key="ck-a")
        await service.send_or_create("demo", "hello-b", client_key="ck-b")
        assert list(service._new_session_client_keys) == ["ck-a", "ck-b"]

        # 命中 ck-a（幂等重试，不新建会话）：应把 ck-a 刷新到最近使用端
        hit = await service.send_or_create("demo", "hello-a", client_key="ck-a")
        assert hit["session_id"] == "sdk-0"
        assert list(service._new_session_client_keys) == ["ck-b", "ck-a"]

        # 第三个 key 到达挤出缓存：应淘汰最久未使用的 ck-b，而非刚被命中的 ck-a
        await service.send_or_create("demo", "hello-c", client_key="ck-c")

        assert "ck-a" in service._new_session_client_keys
        assert "ck-b" not in service._new_session_client_keys
        assert len(sm.new_sessions) == 3  # a/b/c 三次真正新建，命中未重复建会话

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_send_or_create_hit_survives_concurrent_eviction_during_db_lookup(self, tmp_path, monkeypatch):
        """命中路径在 `await find_by_client_key(...)` 期间，该 key 被其他并发
        请求的淘汰逻辑移除：刷新 LRU 位置的收尾步骤须安全处理键已不存在的
        情形（不能直接 move_to_end 抛 KeyError 把幂等命中打成未处理异常）。"""
        from server.agent_runtime.event_log import EventLogStore

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = EventLogStore(session_factory=factory)

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store

        async def send_new_session(project_name, prompt, *, user_entry=None, client_key=None, **kwargs):
            sid = f"sdk-{len(sm.new_sessions)}"
            sm.new_sessions.append((project_name, prompt))
            if user_entry is not None:
                await store.append_user_entry(sid, user_entry, client_key=client_key)
            return sid

        sm.send_new_session = send_new_session

        first = await service.send_or_create("demo", "hello", client_key="ck-a")
        assert "ck-a" in service._new_session_client_keys

        original_find_by_client_key = store.find_by_client_key

        async def find_by_client_key_with_concurrent_eviction(session_id, client_key):
            # 模拟另一并发请求在本次 DB 查询期间把该 key 挤出缓存。
            service._new_session_client_keys.pop(client_key, None)
            return await original_find_by_client_key(session_id, client_key)

        monkeypatch.setattr(store, "find_by_client_key", find_by_client_key_with_concurrent_eviction)

        retry = await service.send_or_create("demo", "hello", client_key="ck-a")

        assert retry["session_id"] == first["session_id"] == "sdk-0"
        assert len(sm.new_sessions) == 1  # 未因异常或键缺失而重复建会话
        assert service._new_session_client_keys["ck-a"] == "sdk-0"  # 命中后已安全重新记入

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_send_or_create_falls_back_when_cached_mapping_points_to_deleted_session(self, tmp_path):
        """进程内映射指向的会话条目已不存在（如会话被删除后映射未清）时，
        不应把幽灵 "accepted" 响应（entry=None）返回给调用方——应清掉失效
        映射并按正常路径新建会话，而不是让调用方连接一个不存在的会话。"""
        from server.agent_runtime.event_log import EventLogStore

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = EventLogStore(session_factory=factory)

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store
        # 模拟会话已被删除但进程内映射未清（delete_session 不感知该映射）。
        service._new_session_client_keys["ck-stale"] = "sdk-deleted"

        result = await service.send_or_create("demo", "hello", client_key="ck-stale")

        assert len(sm.new_sessions) == 1  # 未拿到幽灵响应，走了正常新建路径
        assert result["session_id"] == "sdk-new-id"
        assert result["session_id"] != "sdk-deleted"
        # 映射已刷新为新会话，不再指向已删除的会话
        assert service._new_session_client_keys["ck-stale"] == "sdk-new-id"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_ghost_mapping_cleanup_does_not_clobber_concurrent_fresh_mapping(self, tmp_path):
        """幽灵映射清理在 `await find_by_client_key(...)` 期间该 key 被其他
        并发请求写入了新的有效映射：清理不应无条件 pop，否则会误删并发
        写入的新映射（即便 DB 兜底之后仍能查回正确会话，也不应制造这层
        可避免的抖动窗口）。"""
        service = AssistantService(project_root=tmp_path)
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = _FakeSessionManager()
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()

        async def find_by_client_key(session_id, client_key):
            # 模拟另一并发请求在本次查询期间把该 key 更新为新的有效映射。
            service._new_session_client_keys[client_key] = "sdk-fresh"
            return None  # 旧会话（sdk-deleted）条目已不存在

        async def find_new_session_by_client_key(client_key):
            return None  # 本测试只关心清理分支，不需要真的兜底恢复

        service.event_log_store = SimpleNamespace(
            find_by_client_key=find_by_client_key,
            find_new_session_by_client_key=find_new_session_by_client_key,
        )
        service._new_session_client_keys["ck-a"] = "sdk-deleted"

        result = await service._find_accepted_new_session("ck-a")  # pyright: ignore[reportPrivateUsage]

        assert result is None  # 本测试的兜底查询返回 None，不影响本次断言重点
        assert service._new_session_client_keys["ck-a"] == "sdk-fresh"  # 未被误删

    @pytest.mark.asyncio
    async def test_recovered_mapping_does_not_overwrite_concurrent_fresher_mapping(self, tmp_path):
        """DB 兜底恢复在 `await find_new_session_by_client_key(...)` 期间该 key
        已被其他并发请求记入更新的映射：不应用本次查到的（较旧）session_id
        覆盖并发写入的映射。"""
        service = AssistantService(project_root=tmp_path)
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = _FakeSessionManager()
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()

        async def find_by_client_key(session_id, client_key):
            return None

        async def find_new_session_by_client_key(client_key):
            # 模拟另一并发请求在本次查询期间已经建好新会话并记入映射。
            service._new_session_client_keys[client_key] = "sdk-fresh"
            return "sdk-old", {"seq": 0, "type": "user"}

        service.event_log_store = SimpleNamespace(
            find_by_client_key=find_by_client_key,
            find_new_session_by_client_key=find_new_session_by_client_key,
        )

        result = await service._find_accepted_new_session("ck-b")  # pyright: ignore[reportPrivateUsage]

        assert result is not None
        assert result["session_id"] == "sdk-old"  # 本次调用仍返回自己查到的权威条目
        # 但不应覆盖并发写入的更新映射
        assert service._new_session_client_keys["ck-b"] == "sdk-fresh"

    @pytest.mark.asyncio
    async def test_delete_session_closes_active_session_before_delete(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1")
        service.meta_store = _FakeMetaStore([meta])
        sm = _FakeSessionManager()
        sm.sessions["s1"] = SimpleNamespace()
        service.session_manager = sm

        ok = await service.delete_session("s1")
        assert ok is True
        assert sm.closed == [("s1", "session deleted")]
        assert "s1" not in sm.sessions

        missing = await service.delete_session("missing")
        assert missing is False

    @pytest.mark.asyncio
    async def test_delete_session_propagates_close_error(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1")
        service.meta_store = _FakeMetaStore([meta])
        sm = _FakeSessionManager()
        sm.sessions["s1"] = SimpleNamespace()
        sm.close_error = RuntimeError("close failed")
        service.session_manager = sm

        with pytest.raises(RuntimeError, match="close failed"):
            await service.delete_session("s1")

        assert "s1" in sm.sessions

    def test_status_event_helpers(self, tmp_path):
        service = AssistantService(project_root=tmp_path)

        assert service._check_runtime_status_terminal({"status": "???."}, "s1") is None
        terminal_event = service._check_runtime_status_terminal({"status": "interrupted"}, "s1")
        assert terminal_event is not None
        assert terminal_event.event == "status"

        sse_event = service._sse_event("status", {"x": 1})
        assert sse_event.event == "status"
        assert sse_event.data == {"x": 1}

        assert service._resolve_result_status({"session_status": "interrupted"}) == "interrupted"
        assert service._resolve_result_status({"subtype": "error_x", "is_error": True}) == "error"
        payload = service._build_status_event_payload("error", "s1", None)
        assert payload["status"] == "error"
        assert payload["subtype"] == "error"
        assert payload["is_error"] is True

    def test_skill_listing_and_metadata_parsing(self, tmp_path, monkeypatch):
        service = AssistantService(project_root=tmp_path)
        service.pm = _FakePM(valid_project="demo")

        agent_skill = tmp_path / "agent_runtime_profile" / ".claude" / "skills" / "s1"
        agent_skill.mkdir(parents=True)
        (agent_skill / "SKILL.md").write_text(
            "---\nname: project-skill\ndescription: from frontmatter\n---\n# body\n",
            encoding="utf-8",
        )

        # Create a fallback skill for metadata parsing test
        fallback_skill_dir = tmp_path / "agent_runtime_profile" / ".claude" / "skills" / "s2"
        fallback_skill_dir.mkdir(parents=True)
        (fallback_skill_dir / "SKILL.md").write_text(
            "first non heading line\n# heading\n",
            encoding="utf-8",
        )

        all_skills = service.list_available_skills()
        names = {item["name"] for item in all_skills}
        assert "project-skill" in names
        assert "s2" in names

        for_project = service.list_available_skills(project_name="demo")
        assert len(for_project) >= 1

        fallback = service._load_skill_metadata(fallback_skill_dir / "SKILL.md", "fallback")
        assert fallback["name"] == "fallback"
        assert fallback["description"] == "first non heading line"
