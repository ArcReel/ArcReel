import asyncio

import pytest

from lib.generation_worker import GenerationWorker, _read_int_env


class _FakeQueue:
    def __init__(self):
        self.released = False
        self.succeeded = []
        self.failed = []
        self._lease_calls = 0

    def acquire_or_renew_worker_lease(self, name, owner_id, ttl_seconds):
        self._lease_calls += 1
        return True

    def release_worker_lease(self, name, owner_id):
        self.released = True

    def requeue_running_tasks(self):
        return 0

    def claim_next_task(self, media_type):
        return None

    def mark_task_succeeded(self, task_id, result):
        self.succeeded.append((task_id, result))

    def mark_task_failed(self, task_id, error):
        self.failed.append((task_id, error))


class TestGenerationWorker:
    def test_read_int_env(self, monkeypatch):
        monkeypatch.delenv("ARCREEL_INT", raising=False)
        assert _read_int_env("ARCREEL_INT", 3, minimum=1) == 3

        monkeypatch.setenv("ARCREEL_INT", "bad")
        assert _read_int_env("ARCREEL_INT", 3, minimum=1) == 3

        monkeypatch.setenv("ARCREEL_INT", "0")
        assert _read_int_env("ARCREEL_INT", 3, minimum=2) == 2

    @pytest.mark.asyncio
    async def test_process_task_success_and_failure(self, monkeypatch):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        monkeypatch.setattr(
            "webui.server.services.generation_tasks.execute_generation_task",
            lambda task: {"ok": task["task_id"]},
        )
        await worker._process_task({"task_id": "t1"})
        assert queue.succeeded == [("t1", {"ok": "t1"})]

        def _raise(_task):
            raise RuntimeError("boom")

        monkeypatch.setattr("webui.server.services.generation_tasks.execute_generation_task", _raise)
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
