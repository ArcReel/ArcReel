"""TaskRepository SQL WHERE 守卫状态机测试（ADR 0006）。

只验证外部可观察行为：合法源 → rows=1 + DB 变化；非法源 → rows=0 + DB 不变。
"""

from __future__ import annotations

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


@pytest.mark.asyncio
class TestRepoStateMachineGuards:
    async def test_mark_succeeded_only_from_running(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # queued → mark_succeeded 守卫 status='running' 拒绝
        rows = await repo.mark_succeeded(t["task_id"], {"file": "x"})
        assert rows == 0
        assert (await repo.get(t["task_id"]))["status"] == "queued"

        await repo.claim_next("image")
        rows = await repo.mark_succeeded(t["task_id"], {"file": "x"})
        assert rows == 1
        assert (await repo.get(t["task_id"]))["status"] == "succeeded"

        # 已 succeeded 再调 → 0 rows
        rows = await repo.mark_succeeded(t["task_id"], {"file": "y"})
        assert rows == 0

    async def test_mark_failed_only_from_running(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        rows = await repo.mark_failed(t["task_id"], "err")
        assert rows == 0

        await repo.claim_next("image")
        rows = await repo.mark_failed(t["task_id"], "err")
        assert rows == 1
        assert (await repo.get(t["task_id"]))["status"] == "failed"

    async def test_mark_cancelling_only_from_running(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # queued → 0 rows
        affected = await repo._mark_cancelling(t["task_id"])
        assert affected == 0

        await repo.claim_next("image")
        affected = await repo._mark_cancelling(t["task_id"])
        assert affected == 1
        assert (await repo.get(t["task_id"]))["status"] == "cancelling"

    async def test_finalize_cancelled_accepts_queued_or_cancelling(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # queued → cancelled
        result = await repo.finalize_cancelled(t["task_id"], cancelled_by="user")
        assert result["rows"] == 1
        assert result["cancelling"] == []
        assert (await repo.get(t["task_id"]))["status"] == "cancelled"
        # 重复调 → 0 rows
        result = await repo.finalize_cancelled(t["task_id"], cancelled_by="user")
        assert result["rows"] == 0

    async def test_finalize_cancelled_from_cancelling(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")
        await repo._mark_cancelling(t["task_id"])
        result = await repo.finalize_cancelled(t["task_id"], cancelled_by="user")
        assert result["rows"] == 1
        assert (await repo.get(t["task_id"]))["status"] == "cancelled"

    async def test_finalize_cancelled_rejects_terminal(self, db_session):
        """从 succeeded/failed 等终态不可转入 cancelled。"""
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")
        await repo.mark_succeeded(t["task_id"], {"x": 1})
        result = await repo.finalize_cancelled(t["task_id"], cancelled_by="user")
        assert result["rows"] == 0
        assert (await repo.get(t["task_id"]))["status"] == "succeeded"

    async def test_cancel_task_running_returns_cancelling_intent(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")
        result = await repo.cancel_task(t["task_id"])
        assert result["cancelling"] == [t["task_id"]]
        assert result["cancelled"] == []
        assert (await repo.get(t["task_id"]))["status"] == "cancelling"

    async def test_cancel_task_cancelling_is_idempotent(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")
        await repo._mark_cancelling(t["task_id"])
        # 已 cancelling → 幂等：不再加入 cancelling 列表
        result = await repo.cancel_task(t["task_id"])
        assert result["cancelling"] == []
        assert result["cancelled"] == []

    async def test_cancel_task_terminal_is_skipped(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")
        await repo.mark_succeeded(t["task_id"], {"x": 1})
        result = await repo.cancel_task(t["task_id"])
        assert len(result["skipped_terminal"]) == 1
        assert result["cancelling"] == []
        assert result["cancelled"] == []

    async def test_persist_provider_job_id_writes_column(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("video")
        await repo.persist_provider_job_id(t["task_id"], "provider-job-42")
        refreshed = await repo.get(t["task_id"])
        assert refreshed["provider_job_id"] == "provider-job-42"

    async def test_list_orphan_returns_running_and_cancelling(self, db_session):
        repo = TaskRepository(db_session)
        t1 = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        t2 = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r2",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")
        await repo.claim_next("video")
        await repo._mark_cancelling(t2["task_id"])

        orphans = await repo.list_orphan_tasks_on_start()
        statuses = {o["task_id"]: o["status"] for o in orphans}
        assert statuses == {t1["task_id"]: "running", t2["task_id"]: "cancelling"}

    async def test_claim_next_excludes_pool_full_providers(self, db_session):
        """claim_next 用 pool_full_providers 黑名单：排除池满 provider；NULL 和未知 provider 不受影响。"""
        repo = TaskRepository(db_session)
        # provider_id 显式 set 为 'gemini-aistudio'
        t1 = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
            provider_id="gemini-aistudio",
        )
        # 不设 provider_id（老数据 IS NULL 兜底）
        t2 = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r2",
            payload={},
            script_file="ep1.json",
        )
        # 未知 provider（例如自定义 provider 已被删除，DB 仍留有 provider_id）
        t3 = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r3",
            payload={},
            script_file="ep1.json",
            provider_id="custom-deleted",
        )
        # gemini 池满 → 黑名单含 gemini-aistudio，应跳过 t1，FIFO 领 t2（NULL）
        first = await repo.claim_next("image", pool_full_providers=frozenset({"gemini-aistudio"}))
        assert first is not None
        assert first["task_id"] == t2["task_id"]

        # 继续：仍然只 gemini 池满，t1 不能领，t3（custom-deleted 不在黑名单）应被领
        await repo.mark_succeeded(t2["task_id"], {})
        second = await repo.claim_next("image", pool_full_providers=frozenset({"gemini-aistudio"}))
        assert second is not None
        assert second["task_id"] == t3["task_id"], "未知 provider（已删除的自定义 provider）不应被白名单/黑名单误过滤"

        # 黑名单清空 → t1 可领
        await repo.mark_succeeded(t3["task_id"], {})
        third = await repo.claim_next("image", pool_full_providers=None)
        assert third is not None
        assert third["task_id"] == t1["task_id"]

    async def test_finalize_cancelled_from_running(self, db_session):
        """finalize_cancelled 也能从 running 直接落 cancelled。

        进程级 cancel（SIGTERM / asyncio.Task.cancel 直接打到 running）跳过 cancelling
        中间态，靠 finalize_cancelled 的 SQL 守卫 IN ('queued','cancelling','running') 兜底。
        守卫被改动时这条用例先红。
        """
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # 状态推到 running
        claimed = await repo.claim_next("image")
        assert claimed is not None
        assert claimed["task_id"] == t["task_id"]

        # running → finalize_cancelled 直接落 cancelled，不需要走 cancelling
        result = await repo.finalize_cancelled(t["task_id"], cancelled_by="user")
        assert result["rows"] == 1
        final = await repo.get(t["task_id"])
        assert final["status"] == "cancelled"
        assert final["cancelled_by"] == "user"
