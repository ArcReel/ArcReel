import asyncio

import pytest

from lib.generation_worker import (
    GenerationWorker,
    ProviderPool,
    _read_int_env,
    _extract_provider,
    _normalize_provider_id,
    DEFAULT_PROVIDER,
    _build_default_pools,
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

    def test_no_room_when_full(self):
        pool = ProviderPool(provider_id="test", image_max=1, video_max=1)
        # Simulate inflight tasks with a dummy future
        loop = asyncio.get_event_loop()
        dummy = loop.create_future()
        dummy.set_result(None)
        pool.image_inflight["t1"] = dummy
        pool.video_inflight["t2"] = dummy
        assert not pool.has_image_room()
        assert not pool.has_video_room()

    def test_drain_finished(self):
        pool = ProviderPool(provider_id="test", image_max=2, video_max=2)
        loop = asyncio.get_event_loop()
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


class TestExtractProvider:
    def test_video_provider_in_payload(self):
        task = {"payload": {"video_provider": "seedance"}}
        assert _extract_provider(task) == "seedance"

    def test_image_provider_in_payload(self):
        task = {"payload": {"image_provider": "gemini-vertex"}}
        assert _extract_provider(task) == "gemini-vertex"

    def test_default_when_no_provider(self):
        task = {"payload": {}}
        assert _extract_provider(task) == DEFAULT_PROVIDER

    def test_default_when_no_payload(self):
        task = {}
        assert _extract_provider(task) == DEFAULT_PROVIDER

    def test_normalize_old_name(self):
        task = {"payload": {"video_provider": "gemini"}}
        assert _extract_provider(task) == "gemini-aistudio"


class TestNormalizeProviderId:
    def test_old_to_new(self):
        assert _normalize_provider_id("gemini") == "gemini-aistudio"
        assert _normalize_provider_id("vertex") == "gemini-vertex"

    def test_already_new(self):
        assert _normalize_provider_id("seedance") == "seedance"
        assert _normalize_provider_id("grok") == "grok"


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
        assert pool.image_max == 1
        assert pool.video_max == 1
        assert "unknown-provider" in worker._pools

    def test_any_pool_has_room(self):
        pools = {
            "a": ProviderPool(provider_id="a", image_max=0, video_max=1),
            "b": ProviderPool(provider_id="b", image_max=1, video_max=0),
        }
        worker = GenerationWorker(queue=_FakeQueue(), pools=pools)
        assert worker._any_pool_has_room("image")
        assert worker._any_pool_has_room("video")
        # Fill them up
        loop = asyncio.get_event_loop()
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
                    {"task_id": "img1", "task_type": "gen_image", "media_type": "image",
                     "payload": {"image_provider": "gemini-aistudio"}},
                    {"task_id": "vid1", "task_type": "gen_video", "media_type": "video",
                     "payload": {"video_provider": "seedance"}},
                ]

            async def claim_next_task(self, media_type):
                for i, t in enumerate(self._tasks):
                    if t["media_type"] == media_type:
                        return self._tasks.pop(i)
                return None

        queue = _ClaimableQueue()
        pools = {
            "gemini-aistudio": ProviderPool(provider_id="gemini-aistudio", image_max=3, video_max=2),
            "seedance": ProviderPool(provider_id="seedance", image_max=0, video_max=2),
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
        assert "vid1" in pools["seedance"].video_inflight

        # Wait for tasks to complete
        await asyncio.gather(*[
            *pools["gemini-aistudio"].image_inflight.values(),
            *pools["seedance"].video_inflight.values(),
        ], return_exceptions=True)
