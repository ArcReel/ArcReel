"""Tests for GenerationQueue (async wrapper over TaskRepository)."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.generation_queue import GenerationQueue


@pytest.fixture
async def queue():
    """Create a GenerationQueue backed by in-memory SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    q = GenerationQueue(session_factory=factory)
    yield q
    await engine.dispose()


class TestGenerationQueue:
    async def test_enqueue_dedupe_claim_and_succeed(self, queue):
        first = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test"},
            script_file="episode_01.json",
            source="webui",
        )
        assert not first["deduped"]

        deduped = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test2"},
            script_file="episode_01.json",
            source="webui",
        )
        assert deduped["deduped"]
        assert deduped["task_id"] == first["task_id"]

        running = await queue.claim_next_task(media_type="image")
        assert running is not None
        assert running["task_id"] == first["task_id"]
        assert running["status"] == "running"

        rows = await queue.mark_task_succeeded(first["task_id"], {"file_path": "storyboards/scene_E1S01.png"})
        assert rows == 1
        done = await queue.get_task(first["task_id"])
        assert done is not None
        assert done["status"] == "succeeded"
        assert done["result"]["file_path"] == "storyboards/scene_E1S01.png"

        # 终态后允许再次入队
        second = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test3"},
            script_file="episode_01.json",
            source="webui",
        )
        assert not second["deduped"]
        assert second["task_id"] != first["task_id"]

    async def test_event_sequence_and_incremental_read(self, queue):
        task = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={"prompt": "video"},
            script_file="episode_01.json",
            source="skill",
        )
        await queue.claim_next_task(media_type="video")
        await queue.mark_task_failed(task["task_id"], "mock error")

        all_events = await queue.get_events_since(last_event_id=0)
        assert len(all_events) >= 3
        assert all_events[0]["event_type"] == "queued"
        assert all_events[1]["event_type"] == "running"
        assert all_events[2]["event_type"] == "failed"

        last_seen_id = all_events[1]["id"]
        incremental = await queue.get_events_since(last_event_id=last_seen_id)
        assert all(event["id"] > last_seen_id for event in incremental)
        assert any(event["event_type"] == "failed" for event in incremental)

        latest_id = await queue.get_latest_event_id()
        assert latest_id == all_events[-1]["id"]

    async def test_worker_lease_takeover(self, queue):
        first_ok = await queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-a",
            ttl_seconds=1,
        )
        assert first_ok

        second_ok = await queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-b",
            ttl_seconds=1,
        )
        assert not second_ok

        await asyncio.sleep(1.2)

        takeover_ok = await queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-b",
            ttl_seconds=1,
        )
        assert takeover_ok

    async def test_claim_next_task_respects_dependencies_without_blocking_other_heads(self, queue):
        head_one = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "p1"},
            script_file="episode_01.json",
            source="skill",
            dependency_group="episode_01.json:group:1",
            dependency_index=0,
        )
        await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S02",
            payload={"prompt": "p2"},
            script_file="episode_01.json",
            source="skill",
            dependency_task_id=head_one["task_id"],
            dependency_group="episode_01.json:group:1",
            dependency_index=1,
        )
        head_two = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S03",
            payload={"prompt": "p3"},
            script_file="episode_01.json",
            source="skill",
            dependency_group="episode_01.json:group:2",
            dependency_index=0,
        )

        first_claim = await queue.claim_next_task(media_type="image")
        second_claim = await queue.claim_next_task(media_type="image")
        blocked_claim = await queue.claim_next_task(media_type="image")

        assert first_claim is not None
        assert second_claim is not None
        assert {first_claim["task_id"], second_claim["task_id"]} == {
            head_one["task_id"],
            head_two["task_id"],
        }
        assert blocked_claim is None

        await queue.mark_task_succeeded(
            head_one["task_id"],
            {"file_path": "storyboards/scene_E1S01.png"},
        )
        unblocked_claim = await queue.claim_next_task(media_type="image")
        assert unblocked_claim is not None
        assert unblocked_claim["resource_id"] == "E1S02"

    async def test_mark_task_failed_cascades_to_queued_dependents(self, queue):
        first = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "p1"},
            script_file="episode_01.json",
            source="skill",
            dependency_group="episode_01.json:group:1",
            dependency_index=0,
        )
        second = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S02",
            payload={"prompt": "p2"},
            script_file="episode_01.json",
            source="skill",
            dependency_task_id=first["task_id"],
            dependency_group="episode_01.json:group:1",
            dependency_index=1,
        )
        third = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S03",
            payload={"prompt": "p3"},
            script_file="episode_01.json",
            source="skill",
            dependency_task_id=second["task_id"],
            dependency_group="episode_01.json:group:1",
            dependency_index=2,
        )

        running = await queue.claim_next_task(media_type="image")
        assert running is not None
        assert running["task_id"] == first["task_id"]

        await queue.mark_task_failed(first["task_id"], "boom")

        second_task = await queue.get_task(second["task_id"])
        third_task = await queue.get_task(third["task_id"])
        assert second_task is not None
        assert third_task is not None
        assert second_task["status"] == "failed"
        assert third_task["status"] == "failed"
        assert "blocked by failed dependency" in second_task["error_message"]
        assert first["task_id"] in second_task["error_message"]
        assert second["task_id"] in third_task["error_message"]

    async def test_requeue_running_tasks(self, queue):
        task = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={"prompt": "video"},
            script_file="episode_01.json",
            source="webui",
        )
        running = await queue.claim_next_task(media_type="video")
        assert running is not None
        assert running["status"] == "running"

        recovered = await queue.requeue_running_tasks()
        assert recovered == 1

        queued = await queue.get_task(task["task_id"])
        assert queued is not None
        assert queued["status"] == "queued"
        assert queued["started_at"] is None

        claimed_again = await queue.claim_next_task(media_type="video")
        assert claimed_again is not None
        assert claimed_again["task_id"] == task["task_id"]

        events = await queue.get_events_since(last_event_id=0)
        assert any(event["event_type"] == "requeued" for event in events)

    async def test_cancel_task(self, queue):
        result = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )

        cancel_result = await queue.cancel_task(result["task_id"])
        assert len(cancel_result["cancelled"]) == 1
        assert cancel_result["cancelled"][0]["status"] == "cancelled"

    async def test_cancel_all_queued(self, queue):
        await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="ep1.json",
        )

        result = await queue.cancel_all_queued("demo")
        assert result["cancelled_count"] == 2

        stats = await queue.get_task_stats(project_name="demo")
        assert stats["cancelled"] == 2
        assert stats["queued"] == 0

    async def test_persist_provider_job_id_wrapper(self, queue):
        """persist_provider_job_id 是 wrapper,只验证不抛(行为细节在 repo 层测过)。"""
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # 入队的 task 此时是 queued,但 persist 不校验 status(独立 commit)
        await queue.persist_provider_job_id(enqueued["task_id"], "job-abc-123")
        task = await queue.get_task(enqueued["task_id"])
        assert task is not None
        assert task["provider_job_id"] == "job-abc-123"

    async def test_mark_task_cancelled_wrapper(self, queue):
        """mark_task_cancelled wrapper → repo.finalize_cancelled,SQL 守卫接住 queued/cancelling/running。"""
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # 从 queued 直接落 cancelled(进程级 cancel 兜底路径)
        rows = await queue.mark_task_cancelled(enqueued["task_id"], cancelled_by="restart")
        assert rows == 1
        task = await queue.get_task(enqueued["task_id"])
        assert task is not None
        assert task["status"] == "cancelled"
        # 终态再调一次返回 0(SQL 守卫排除终态)
        rows = await queue.mark_task_cancelled(enqueued["task_id"])
        assert rows == 0

    async def test_cancel_task_dispatches_worker_callback(self, queue):
        """cancel_task 把 cancelling 列表派发给 worker_cancel_callback(秒级响应)。"""
        # 先把任务推到 running,这样 cancel 走 cancelling 中间态
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await queue.claim_next_task("video")

        signaled: list[str] = []

        def _fake_cancel(task_id: str) -> bool:
            signaled.append(task_id)
            return True

        queue.set_worker_cancel_callback(_fake_cancel)
        result = await queue.cancel_task(enqueued["task_id"])
        # running task 应进入 cancelling
        assert signaled == [enqueued["task_id"]]
        assert result["cancelling"] == [enqueued["task_id"]]

    async def test_cancel_task_callback_exception_does_not_break(self, queue):
        """callback 抛异常不影响 cancel_task 返回(best-effort 信号)。"""
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await queue.claim_next_task("video")

        def _bad_cancel(_task_id: str) -> bool:
            raise RuntimeError("worker not responding")

        queue.set_worker_cancel_callback(_bad_cancel)
        # 不应抛
        result = await queue.cancel_task(enqueued["task_id"])
        assert result["cancelling"] == [enqueued["task_id"]]

    async def test_get_cancel_preview_wrapper(self, queue):
        """get_cancel_preview wrapper → repo.get_cancel_preview。"""
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        preview = await queue.get_cancel_preview(enqueued["task_id"])
        assert preview["task"]["task_id"] == enqueued["task_id"]
