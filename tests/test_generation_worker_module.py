import asyncio

import pytest

from lib.generation_worker import (
    DEFAULT_PROVIDER,
    GenerationWorker,
    ProviderPool,
    _build_default_pools,
    _extract_provider,
    _read_int_env,
)


class _FakeQueue:
    def __init__(self):
        self.released = False
        self.succeeded = []
        self.failed = []
        self._lease_calls = 0

    async def acquire_or_renew_worker_lease(self, name, owner_id, ttl_seconds):
        self._lease_calls += 1
        return True

    async def release_worker_lease(self, name, owner_id):
        self.released = True

    async def requeue_running_tasks(self):
        return 0

    async def claim_next_task(self, media_type):
        return None

    async def mark_task_succeeded(self, task_id, result):
        self.succeeded.append((task_id, result))

    async def mark_task_failed(self, task_id, error):
        self.failed.append((task_id, error))


class TestReadIntEnv:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ARCREEL_INT", raising=False)
        assert _read_int_env("ARCREEL_INT", 3, minimum=1) == 3

    def test_default_when_bad(self, monkeypatch):
        monkeypatch.setenv("ARCREEL_INT", "bad")
        assert _read_int_env("ARCREEL_INT", 3, minimum=1) == 3

    def test_minimum_enforced(self, monkeypatch):
        monkeypatch.setenv("ARCREEL_INT", "0")
        assert _read_int_env("ARCREEL_INT", 3, minimum=2) == 2


class TestProviderPool:
    def test_has_room(self):
        pool = ProviderPool(provider_id="test", image_max=2, video_max=1)
        assert pool.has_image_room()
        assert pool.has_video_room()

    def test_no_room_when_max_zero(self):
        pool = ProviderPool(provider_id="test", image_max=0, video_max=0)
        assert not pool.has_image_room()
        assert not pool.has_video_room()

    async def test_no_room_when_full(self):
        pool = ProviderPool(provider_id="test", image_max=1, video_max=1)
        # Simulate inflight tasks with a dummy future
        loop = asyncio.get_running_loop()
        dummy = loop.create_future()
        dummy.set_result(None)
        pool.image_inflight["t1"] = dummy
        pool.video_inflight["t2"] = dummy
        assert not pool.has_image_room()
        assert not pool.has_video_room()

    async def test_drain_finished(self):
        pool = ProviderPool(provider_id="test", image_max=2, video_max=2)
        loop = asyncio.get_running_loop()
        done = loop.create_future()
        done.set_result(None)
        pending = loop.create_future()
        pool.image_inflight["done1"] = done
        pool.image_inflight["pending1"] = pending
        pool.video_inflight["done2"] = done

        finished = pool.drain_finished()
        assert len(finished) == 2
        assert "done1" not in pool.image_inflight
        assert "pending1" in pool.image_inflight
        assert "done2" not in pool.video_inflight
        pending.cancel()


def _patch_pm(monkeypatch, project: dict | None):
    """让 worker 的 get_project_manager().load_project 返回给定 project dict。"""
    monkeypatch.setattr(
        "lib.config.resolver.get_project_manager",
        lambda: type("PM", (), {"load_project": lambda self, name: project or {}})(),
    )


class TestExtractProvider:
    """_extract_provider 是解析链的薄投影：按 task_type 派发，取 .provider_id。"""

    async def test_video_payload_provider(self):
        """payload 携带历史 video_provider → 投影直接取到（payload 层短路，无需 DB）。"""
        task = {"payload": {"video_provider": "ark"}, "task_type": "video"}
        assert await _extract_provider(task) == "ark"

    async def test_image_payload_provider(self):
        """payload 携带历史 image_provider → 投影取到。"""
        task = {"payload": {"image_provider": "gemini-vertex"}, "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    async def test_default_when_unresolvable(self):
        """无 project、无 payload、全局未配供应商 → 回退 DEFAULT_PROVIDER（仅供限流）。"""
        task = {"payload": {}}
        assert await _extract_provider(task) == DEFAULT_PROVIDER

    async def test_project_level_video_backend(self, monkeypatch):
        """项目级 video_backend 优先于全局默认。"""
        _patch_pm(monkeypatch, {"video_backend": "ark/seedance-1-0-pro"})
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}
        assert await _extract_provider(task) == "ark"

    async def test_project_level_image_t2i(self, monkeypatch):
        """image 投影按代表性 capability=t2i 取项目级 image_provider_t2i。"""
        _patch_pm(monkeypatch, {"image_provider_t2i": "gemini-vertex/imagen-3"})
        task = {"payload": {}, "project_name": "demo", "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    async def test_reference_video_routes_to_video_lane(self, monkeypatch):
        """reference_video task_type 必须按 video lane 解析 video_backend，而非 image 槽。

        项目同时配置了不同 provider 的 video_backend（ark）与 image_provider_t2i
        （gemini-vertex）。reference_video 属于 video lane，认领期 provider 投影须取 ark；
        若误判为 image lane（历史上 task_type != "video" 即读 image 槽），会取到纯图片
        供应商，导致 worker 在 video 通道以 video_max==0 直接把任务标记
        「供应商不支持 video 生成」。"""
        _patch_pm(
            monkeypatch,
            {
                "video_backend": "ark/seedance-1-0-pro",
                "image_provider_t2i": "gemini-vertex/imagen-3",
            },
        )
        task = {"payload": {}, "project_name": "demo", "task_type": "reference_video"}
        assert await _extract_provider(task) == "ark"

    async def test_payload_provider_takes_precedence_over_project(self, monkeypatch):
        """payload 历史 provider 优先于项目级。"""
        _patch_pm(monkeypatch, {"video_backend": "grok/grok-imagine-video"})
        task = {"payload": {"video_provider": "ark"}, "project_name": "demo", "task_type": "video"}
        assert await _extract_provider(task) == "ark"

    async def test_deleted_project_load_failure_falls_back_not_raises(self, monkeypatch):
        """指向已删除/不可读项目的历史任务：load_project 抛错也须回退 DEFAULT_PROVIDER，
        绝不冒泡阻断认领循环（否则一个坏任务会拖垮整个 worker）。"""

        def _raising_pm():
            def _load(self, name):
                raise FileNotFoundError(name)

            return type("PM", (), {"load_project": _load})()

        monkeypatch.setattr("lib.config.resolver.get_project_manager", _raising_pm)
        task = {"payload": {}, "project_name": "deleted-proj", "task_type": "video"}
        assert await _extract_provider(task) == DEFAULT_PROVIDER


class TestExtractProviderAlignsWithExecution:
    """M5 投影对齐：worker 取到的 provider_id 与执行层解析在同一 project/payload 下一致。"""

    async def test_image_alignment(self, monkeypatch):
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        project = {"image_provider_t2i": "openai/gen-1", "image_provider_i2i": "openai/edit-1"}
        _patch_pm(monkeypatch, project)
        task = {"payload": {}, "project_name": "demo", "task_type": "storyboard"}

        worker_provider = await _extract_provider(task)
        resolved = await ConfigResolver(async_session_factory).resolve_image_backend(project, {}, capability="t2i")
        assert worker_provider == resolved.provider_id == "openai"

    async def test_video_alignment(self, monkeypatch):
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        project = {"video_backend": "ark/seedance-1-0-pro"}
        _patch_pm(monkeypatch, project)
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}

        worker_provider = await _extract_provider(task)
        resolved = await ConfigResolver(async_session_factory).resolve_video_backend(project, {})
        assert worker_provider == resolved.provider_id == "ark"


class TestBuildDefaultPools:
    def test_builds_default_pool(self, monkeypatch):
        monkeypatch.delenv("IMAGE_MAX_WORKERS", raising=False)
        monkeypatch.delenv("VIDEO_MAX_WORKERS", raising=False)
        pools = _build_default_pools()
        assert DEFAULT_PROVIDER in pools
        assert pools[DEFAULT_PROVIDER].image_max == 5
        assert pools[DEFAULT_PROVIDER].video_max == 3

    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("IMAGE_MAX_WORKERS", "5")
        monkeypatch.setenv("VIDEO_MAX_WORKERS", "4")
        pools = _build_default_pools()
        assert pools[DEFAULT_PROVIDER].image_max == 5
        assert pools[DEFAULT_PROVIDER].video_max == 4


class TestGenerationWorker:
    @pytest.mark.asyncio
    async def test_process_task_success_and_failure(self, monkeypatch):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _fake_execute(task):
            return {"ok": task["task_id"]}

        monkeypatch.setattr(
            "server.services.generation_tasks.execute_generation_task",
            _fake_execute,
        )
        await worker._process_task({"task_id": "t1"})
        assert queue.succeeded == [("t1", {"ok": "t1"})]

        async def _raise(_task):
            raise RuntimeError("boom")

        monkeypatch.setattr("server.services.generation_tasks.execute_generation_task", _raise)
        await worker._process_task({"task_id": "t2"})
        assert queue.failed and queue.failed[0][0] == "t2"

    @pytest.mark.asyncio
    async def test_start_stop_run_loop_releases_lease(self):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        await worker.start()
        await asyncio.sleep(0.05)
        await worker.stop()

        assert queue.released
        assert worker._main_task is None

    def test_backward_compat_image_video_workers(self):
        pools = {
            "a": ProviderPool(provider_id="a", image_max=3, video_max=2),
            "b": ProviderPool(provider_id="b", image_max=1, video_max=0),
        }
        worker = GenerationWorker(queue=_FakeQueue(), pools=pools)
        assert worker.image_workers == 4
        assert worker.video_workers == 2

    def test_reload_limits_from_env(self, monkeypatch):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        monkeypatch.setenv("IMAGE_MAX_WORKERS", "10")
        monkeypatch.setenv("VIDEO_MAX_WORKERS", "8")
        worker.reload_limits_from_env()
        assert worker._pools[DEFAULT_PROVIDER].image_max == 10
        assert worker._pools[DEFAULT_PROVIDER].video_max == 8

    def test_get_or_create_pool_unknown(self):
        worker = GenerationWorker(queue=_FakeQueue())
        pool = worker._get_or_create_pool("unknown-provider")
        assert pool.provider_id == "unknown-provider"
        assert pool.image_max == 5
        assert pool.video_max == 3
        assert "unknown-provider" in worker._pools

    async def test_any_pool_has_room(self):
        pools = {
            "a": ProviderPool(provider_id="a", image_max=0, video_max=1),
            "b": ProviderPool(provider_id="b", image_max=1, video_max=0),
        }
        worker = GenerationWorker(queue=_FakeQueue(), pools=pools)
        assert worker._any_pool_has_room("image")
        assert worker._any_pool_has_room("video")
        # Fill them up
        loop = asyncio.get_running_loop()
        dummy = loop.create_future()
        dummy.set_result(None)
        pools["b"].image_inflight["t1"] = dummy
        assert not worker._any_pool_has_room("image")

    @pytest.mark.asyncio
    async def test_claim_tasks_dispatches_to_correct_pool(self, monkeypatch):
        """Tasks are dispatched to the correct provider pool."""

        class _ClaimableQueue(_FakeQueue):
            def __init__(self):
                super().__init__()
                self._tasks = [
                    {
                        "task_id": "img1",
                        "task_type": "gen_image",
                        "media_type": "image",
                        "payload": {"image_provider": "gemini-aistudio"},
                    },
                    {
                        "task_id": "vid1",
                        "task_type": "gen_video",
                        "media_type": "video",
                        "payload": {"video_provider": "ark"},
                    },
                ]

            async def claim_next_task(self, media_type):  # type: ignore[override]
                for i, t in enumerate(self._tasks):
                    if t["media_type"] == media_type:
                        return self._tasks.pop(i)
                return None

        queue = _ClaimableQueue()
        pools = {
            "gemini-aistudio": ProviderPool(provider_id="gemini-aistudio", image_max=3, video_max=2),
            "ark": ProviderPool(provider_id="ark", image_max=0, video_max=2),
        }
        worker = GenerationWorker(queue=queue, pools=pools)

        async def _fake_execute(task):
            return {"ok": True}

        monkeypatch.setattr(
            "server.services.generation_tasks.execute_generation_task",
            _fake_execute,
        )

        claimed = await worker._claim_tasks()
        assert claimed
        assert "img1" in pools["gemini-aistudio"].image_inflight
        assert "vid1" in pools["ark"].video_inflight

        # Wait for tasks to complete
        await asyncio.gather(
            *[
                *pools["gemini-aistudio"].image_inflight.values(),
                *pools["ark"].video_inflight.values(),
            ],
            return_exceptions=True,
        )
