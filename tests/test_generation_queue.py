"""Tests for GenerationQueue (async wrapper over TaskRepository)."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.generation_queue import GenerationQueue
from lib.task_failure import encode_failure

pytestmark = pytest.mark.unit


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
        # 每层只记直接阻塞方 task_id，reason 沿链条原样传递根因（不随层数重新嵌套）——
        # 避免深层依赖链把上一层的完整编码串再嵌套进新一层 JSON 造成的近指数增长。
        expected_second = encode_failure(
            "cascade_blocked_dependency", dependency_task_id=first["task_id"], reason="boom"
        )
        expected_third = encode_failure(
            "cascade_blocked_dependency", dependency_task_id=second["task_id"], reason="boom"
        )
        assert second_task["error_message"] == expected_second
        assert third_task["error_message"] == expected_third

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

    async def test_finalize_cancelled_dispatches_cascade_callback(self, queue):
        """mark_task_cancelled(finalize 入口) 把级联出的 running 子任务派发给 callback。

        A(running)→B(running)→C(queued)：worker finally 调 finalize_cancelled(A)，
        cascade 把 B 标 cancelling，须同步调 callback(B) 让 worker request_cancel(B)
        立刻发 in-process cancel，而非等 B 跑完 provider 调用。
        """
        from sqlalchemy import update as sql_update

        from lib.db.models.task import Task

        a_task = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        b_task = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
            dependency_task_id=a_task["task_id"],
        )

        # 把 A 拉到 running、B 也直接 set 成 running（跳过 dep 守卫）
        await queue.claim_next_task("image")
        async with queue._session_factory() as session:
            await session.execute(sql_update(Task).where(Task.task_id == b_task["task_id"]).values(status="running"))
            await session.commit()

        signaled: list[str] = []

        def _fake_cancel(task_id: str) -> bool:
            signaled.append(task_id)
            return True

        queue.set_worker_cancel_callback(_fake_cancel)
        # finalize_cancelled(A) 级联：A → cancelled、B(running) → cancelling
        rows = await queue.mark_task_cancelled(a_task["task_id"], cancelled_by="user")
        assert rows == 1
        # B 必须被分发 callback —— Repository 返回意图、Queue 上层分发
        assert b_task["task_id"] in signaled

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


@pytest.fixture
def stub_enqueue_resolution(monkeypatch):
    """把入队解析链（视频 / 图片 / 音频三条）换成固定身份，返回一个可改写解析结果的 holder。"""
    from lib.config.resolver import ProviderModel

    holder = {"resolved": ProviderModel("custom-7", "pinned-video-model")}

    class _FakeResolver:
        def __init__(self, factory):
            pass

        async def resolve_video_backend(self, project, payload, *, capability=None):
            return holder["resolved"]

        async def resolve_image_backend(self, project, payload, *, capability):
            return holder["resolved"]

        async def resolve_audio_backend(self, project, payload):
            return holder["resolved"]

    class _FakeProjectManager:
        def load_project(self, project_name):
            return {}

    monkeypatch.setattr("lib.config.resolver.ConfigResolver", _FakeResolver)
    monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: _FakeProjectManager())
    return holder


class TestPinExecutionModelOnEnqueue:
    """入队把解析出的执行 model 钉进视频任务 payload 的能力桶键。"""

    async def test_video_task_pins_bucket_key(self, queue, stub_enqueue_resolution):
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={"prompt": "p"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"]["video_provider_i2v"] == "custom-7/pinned-video-model"
        assert task["provider_id"] == "custom-7"

    async def test_reference_video_task_pins_r2v_bucket_key(self, queue, stub_enqueue_resolution):
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="reference_video",
            media_type="video",
            resource_id="r1",
            payload={"prompt": "p"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"]["video_provider_r2v"] == "custom-7/pinned-video-model"

    async def test_non_video_task_pins_nothing(self, queue, stub_enqueue_resolution):
        """图片任务的 capability 执行时才定，入队不钉——只落 provider_id。"""
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={"prompt": "p"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"] == {"prompt": "p"}
        assert task["provider_id"] == "custom-7"

    async def test_unresolvable_model_leaves_payload_untouched(self, queue, stub_enqueue_resolution):
        """解析补不出 model → 不钉半截身份，payload 与 provider_id 均按原有兜底。"""
        from lib.config.resolver import ProviderModel

        stub_enqueue_resolution["resolved"] = ProviderModel("", "")
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={"prompt": "p"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"] == {"prompt": "p"}
        assert task["provider_id"] is None

    async def test_provider_without_model_pins_nothing(self, queue, stub_enqueue_resolution):
        """解析出 provider 但补不出 model → 只落 provider_id，不钉半截桶键。"""
        from lib.config.resolver import ProviderModel

        stub_enqueue_resolution["resolved"] = ProviderModel("custom-7", "")
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={"prompt": "p"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task["payload"] == {"prompt": "p"}
        assert task["provider_id"] == "custom-7"

    async def test_caller_payload_not_mutated(self, queue, stub_enqueue_resolution):
        """钉入走新 dict：调用方常复用同一份 payload 批量入队。"""
        payload = {"prompt": "p"}
        await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload=payload,
            script_file="ep1.json",
        )
        assert payload == {"prompt": "p"}
