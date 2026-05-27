"""Tests for TaskRepository."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.repositories.task_repo import TaskRepository


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestTaskRepository:
    async def test_enqueue_dedupe_claim_succeed(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test"},
            script_file="ep1.json",
        )
        assert not first["deduped"]

        deduped = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test2"},
            script_file="ep1.json",
        )
        assert deduped["deduped"]
        assert deduped["task_id"] == first["task_id"]

        running = await repo.claim_next("image")
        assert running is not None
        assert running["status"] == "running"

        affected = await repo.mark_succeeded(first["task_id"], {"file": "test.png"})
        assert affected == 1
        done = await repo.get(first["task_id"])
        assert done["status"] == "succeeded"

    async def test_event_sequence(self, db_session):
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("video")
        await repo.mark_failed(task["task_id"], "mock error")

        events = await repo.get_events_since(last_event_id=0)
        assert len(events) >= 3
        types = [e["event_type"] for e in events]
        assert types == ["queued", "running", "failed"]

    async def test_dependency_cascade_failure(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        second = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S02",
            payload={},
            script_file="ep1.json",
            dependency_task_id=first["task_id"],
        )

        await repo.claim_next("image")
        await repo.mark_failed(first["task_id"], "boom")

        dep_task = await repo.get(second["task_id"])
        assert dep_task["status"] == "failed"
        assert "blocked by failed dependency" in dep_task["error_message"]

    async def test_requeue_running_tasks(self, db_session):
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("video")
        count = await repo.requeue_running()
        assert count == 1

        queued = await repo.get(task["task_id"])
        assert queued["status"] == "queued"

    async def test_worker_lease(self, db_session):
        repo = TaskRepository(db_session)

        assert await repo.acquire_or_renew_lease(name="default", owner_id="a", ttl=2)
        assert not await repo.acquire_or_renew_lease(name="default", owner_id="b", ttl=2)
        assert await repo.is_worker_online(name="default")

        await repo.release_lease(name="default", owner_id="a")
        assert not await repo.is_worker_online(name="default")

    async def test_worker_lease_concurrent_first_acquire(self, tmp_path):
        db_path = tmp_path / "lease-race.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        start = asyncio.Event()

        async def _attempt(owner_id: str) -> bool:
            await start.wait()
            async with factory() as session:
                repo = TaskRepository(session)
                return await repo.acquire_or_renew_lease(
                    name="default",
                    owner_id=owner_id,
                    ttl=2,
                )

        first = asyncio.create_task(_attempt("worker-a"))
        second = asyncio.create_task(_attempt("worker-b"))
        start.set()

        a_ok, b_ok = await asyncio.gather(first, second)
        assert sorted([a_ok, b_ok]) == [False, True]

        async with factory() as session:
            repo = TaskRepository(session)
            lease = await repo.get_worker_lease(name="default")
            assert lease is not None
            assert lease["owner_id"] in {"worker-a", "worker-b"}

        await engine.dispose()

    async def test_list_tasks_with_filters(self, db_session):
        repo = TaskRepository(db_session)

        await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await repo.enqueue(
            project_name="other",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="ep2.json",
        )

        result = await repo.list_tasks(project_name="demo")
        assert result["total"] == 1

        result = await repo.list_tasks()
        assert result["total"] == 2

    async def test_task_has_cancelled_by_field(self, db_session):
        repo = TaskRepository(db_session)
        task = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        fetched = await repo.get(task["task_id"])
        assert fetched["cancelled_by"] is None

    async def test_cancel_single_queued_task(self, db_session):
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )

        result = await repo.cancel_task(task["task_id"])
        assert len(result["cancelled"]) == 1
        assert result["cancelled"][0]["task_id"] == task["task_id"]
        assert result["cancelled"][0]["cancelled_by"] == "user"
        assert result["cancelling"] == []
        assert result["skipped_terminal"] == []

        cancelled = await repo.get(task["task_id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["cancelled_by"] == "user"

    async def test_get_stats(self, db_session):
        repo = TaskRepository(db_session)

        await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        stats = await repo.get_stats()
        assert stats["queued"] == 1
        assert stats["total"] == 1

    async def test_cancel_task_cascades_to_dependents(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        second = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
            dependency_task_id=first["task_id"],
        )

        result = await repo.cancel_task(first["task_id"])
        assert len(result["cancelled"]) == 2
        assert result["cancelled"][0]["task_id"] == first["task_id"]
        assert result["cancelled"][0]["cancelled_by"] == "user"
        assert result["cancelled"][1]["task_id"] == second["task_id"]
        assert result["cancelled"][1]["cancelled_by"] == "cascade"

        dep_task = await repo.get(second["task_id"])
        assert dep_task["status"] == "cancelled"
        assert dep_task["cancelled_by"] == "cascade"

    async def test_cancel_running_task_marks_cancelling(self, db_session):
        """ADR 0006: 取消 running task 转入 cancelling 中间态；
        Repository 不再 raise，由上层 GenerationQueue 分发 worker 信号。"""
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")

        result = await repo.cancel_task(task["task_id"])
        assert result["cancelled"] == []
        assert result["cancelling"] == [task["task_id"]]
        assert result["skipped_terminal"] == []

        refreshed = await repo.get(task["task_id"])
        assert refreshed["status"] == "cancelling"

    async def test_cancel_preview(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        second = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
            dependency_task_id=first["task_id"],
        )

        preview = await repo.get_cancel_preview(first["task_id"])
        assert preview["task"]["task_id"] == first["task_id"]
        assert len(preview["cascaded"]) == 1
        assert preview["cascaded"][0]["task_id"] == second["task_id"]

    async def test_cancel_all_queued(self, db_session):
        repo = TaskRepository(db_session)

        await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        t2 = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="ep1.json",
        )
        # Claim one task so it becomes running
        await repo.claim_next("image")

        result = await repo.cancel_all_queued("demo")
        assert result["cancelled_count"] == 1  # only the queued video task
        assert result["skipped_running_count"] == 0  # running 任务在查询 queued 前已被 claim，不算 skipped

        task = await repo.get(t2["task_id"])
        assert task["status"] == "cancelled"

    async def test_get_stats_includes_cancelled(self, db_session):
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await repo.cancel_task(task["task_id"])

        stats = await repo.get_stats()
        assert stats["cancelled"] == 1
        assert stats["queued"] == 0


class TestPersistApiCallId:
    """persist_api_call_id：read-modify-write 写入 task.payload["api_call_id"]。"""

    async def _enqueue(self, repo: TaskRepository, *, payload=None) -> str:
        # 不用 `payload or {...}`——空 dict {} 会被 falsy 回退为默认值
        if payload is None:
            payload = {"prompt": "p"}
        result = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload=payload,
            script_file="ep1.json",
        )
        return result["task_id"]

    async def test_persist_writes_api_call_id_into_payload(self, db_session):
        repo = TaskRepository(db_session)
        task_id = await self._enqueue(repo, payload={"prompt": "p"})

        await repo.persist_api_call_id(task_id, 42)

        task = await repo.get(task_id)
        assert task is not None
        assert task["payload"]["api_call_id"] == 42
        assert task["payload"]["prompt"] == "p", "其它 payload 字段不应被覆盖"

    async def test_persist_overwrites_existing_api_call_id(self, db_session):
        """重试场景：同一 task 第二次走 generate 写新 call_id 应覆盖。"""
        repo = TaskRepository(db_session)
        task_id = await self._enqueue(repo)

        await repo.persist_api_call_id(task_id, 10)
        await repo.persist_api_call_id(task_id, 20)

        task = await repo.get(task_id)
        assert task is not None
        assert task["payload"]["api_call_id"] == 20

    async def test_persist_handles_empty_payload(self, db_session):
        """Payload 为空 JSON 也能正常写。"""
        repo = TaskRepository(db_session)
        task_id = await self._enqueue(repo, payload={})

        await repo.persist_api_call_id(task_id, 7)

        task = await repo.get(task_id)
        assert task is not None
        assert task["payload"] == {"api_call_id": 7}

    async def test_persist_raises_when_task_not_found(self, db_session):
        """task_id 不存在 → 显式 ValueError，避免静默 commit 让上层以为已持久化。"""
        repo = TaskRepository(db_session)
        with pytest.raises(ValueError, match="task not found"):
            await repo.persist_api_call_id("nonexistent-task-id", 42)

    async def test_persist_handles_null_payload_json_row(self, db_session):
        """task 存在但 payload_json IS NULL（迁移历史/旧任务）→ 走 first() 判存在性，不应误判 task not found。"""
        from sqlalchemy import update as sql_update

        from lib.db.models.task import Task

        repo = TaskRepository(db_session)
        task_id = await self._enqueue(repo, payload={"prompt": "p"})
        # 模拟历史/迁移数据 payload_json IS NULL（Task.payload_json 是 Mapped[str | None]）
        await db_session.execute(sql_update(Task).where(Task.task_id == task_id).values(payload_json=None))
        await db_session.commit()

        # 不应抛 ValueError——行存在
        await repo.persist_api_call_id(task_id, 99)

        task = await repo.get(task_id)
        assert task is not None
        assert task["payload"] == {"api_call_id": 99}


class TestCancelCascadeAcrossCancelling:
    """fix #647 #4：cancel 级联跨过 cancelling 节点，A(running)→B(queued)→C(queued)
    在 A 落 cancelled 时通过 finalize_cancelled 自动级联到 B/C。"""

    async def _chain_3(self, repo: TaskRepository) -> tuple[str, str, str]:
        a = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        b = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
            dependency_task_id=a["task_id"],
        )
        c = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="ep1.json",
            dependency_task_id=b["task_id"],
        )
        return a["task_id"], b["task_id"], c["task_id"]

    async def test_cancel_running_task_returns_only_cancelling(self, db_session):
        """A running → cancel_task 响应体 cancelled=[]、cancelling=[A]、下游变动靠后续 SSE/finalize。"""
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)
        await repo.claim_next("image")  # A 拉成 running

        result = await repo.cancel_task(a)
        assert result["cancelled"] == []
        assert result["cancelling"] == [a]
        assert result["skipped_terminal"] == []

        # B/C 当前仍 queued，因为父 A 还没落 cancelled
        assert (await repo.get(b))["status"] == "queued"
        assert (await repo.get(c))["status"] == "queued"

    async def test_cancel_link_running_to_queued(self, db_session):
        """A(running)→B(queued)→C(queued)：finalize_cancelled(A) → A/B/C 全 cancelled，
        B/C 的 cancelled_by="cascade"。"""
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)
        await repo.claim_next("image")
        await repo.cancel_task(a)  # A → cancelling

        await repo.finalize_cancelled(a)

        for tid, expected in [(a, "user"), (b, "cascade"), (c, "cascade")]:
            t = await repo.get(tid)
            assert t["status"] == "cancelled", f"{tid} expected cancelled, got {t['status']}"
            assert t["cancelled_by"] == expected

    async def test_finalize_cancelled_returns_cascading_cancelling_ids(self, db_session):
        """finalize_cancelled 应返回级联出来的 running 子任务 task_id 列表，
        让上层 GenerationQueue 同步分发 in-process cancel 信号给 worker。
        """
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)
        await repo.claim_next("image")
        # 直接把 B set 成 running 绕开依赖守卫
        from sqlalchemy import update

        from lib.db.models.task import Task

        await db_session.execute(update(Task).where(Task.task_id == b).values(status="running"))
        await db_session.commit()

        # finalize_cancelled(a) 级联：A → cancelled、B(running) → cancelling、C(queued, dep on B) 留 queued
        result = await repo.finalize_cancelled(a)

        assert result["rows"] == 1
        # B 是 running 下游，cascade 把它转 cancelling 应进入 cancelling 列表
        assert b in result["cancelling"], "running 下游 task_id 必须返回，让上层分发 cancel"
        # C 是 queued（依赖 B 还没结束），不进 cancelling 列表
        assert c not in result["cancelling"]

    async def test_cancel_link_running_running_queued(self, db_session):
        """A(running)→B(running)→C(queued)：finalize(A) → A cancelled、B cancelling、C queued；
        finalize(B) → B cancelled、C cancelled。"""
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)
        # A、B 都拉到 running（B 实际还卡 dep，但模拟单元；用 mark 跳过 claim 守卫）
        await repo.claim_next("image")
        # 直接把 B set 成 running 绕开依赖守卫
        from sqlalchemy import update

        from lib.db.models.task import Task

        await db_session.execute(update(Task).where(Task.task_id == b).values(status="running"))
        await db_session.commit()

        await repo.cancel_task(a)
        await repo.cancel_task(b)
        await repo.finalize_cancelled(a)

        assert (await repo.get(a))["status"] == "cancelled"
        assert (await repo.get(b))["status"] == "cancelling"
        assert (await repo.get(c))["status"] == "queued"

        await repo.finalize_cancelled(b)
        assert (await repo.get(b))["status"] == "cancelled"
        assert (await repo.get(c))["status"] == "cancelled"
        assert (await repo.get(c))["cancelled_by"] == "cascade"

    async def test_cancel_cascade_event_data_carries_cancelled_by(self, db_session):
        """前端通过 SSE 事件 data 中的 cancelled_by 字段区分级联取消。

        本测试断言：finalize_cancelled(A) 触发的 B/C cancelled 事件 data 中
        cancelled_by="cascade"；A 自己的事件 data cancelled_by="user"。
        """
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)
        await repo.claim_next("image")
        await repo.cancel_task(a)
        await repo.finalize_cancelled(a)

        events = await repo.get_events_since(last_event_id=0)
        cancelled_events = [e for e in events if e["event_type"] == "cancelled"]
        by_task = {e["task_id"]: e for e in cancelled_events}

        assert by_task[a]["data"]["cancelled_by"] == "user"
        assert by_task[b]["data"]["cancelled_by"] == "cascade"
        assert by_task[c]["data"]["cancelled_by"] == "cascade"

    async def test_cancel_emits_each_event_at_most_twice(self, db_session):
        """每个 task 的 cancelling/cancelled 事件不超过 1 次/各 1 次（不重复 emit）。"""
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)
        await repo.claim_next("image")
        await repo.cancel_task(a)
        await repo.finalize_cancelled(a)

        events = await repo.get_events_since(last_event_id=0)
        cancel_events = [e for e in events if e["event_type"] in ("cancelling", "cancelled")]
        # 计数每个 task 的 cancel-related events
        by_task: dict[str, int] = {}
        for e in cancel_events:
            tid = e["task_id"]
            by_task[tid] = by_task.get(tid, 0) + 1
        for tid in (a, b, c):
            assert by_task.get(tid, 0) <= 2, f"{tid} 有重复 cancel 事件: {by_task.get(tid)}"
