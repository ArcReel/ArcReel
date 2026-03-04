"""Tests for generation_queue_client async functions."""

import pytest

from lib.generation_queue_client import (
    TaskWaitTimeoutError,
    WorkerOfflineError,
    enqueue_task_only,
    wait_for_task,
)


class TestGenerationQueueClient:
    async def test_enqueue_task_only_requires_online_worker(self, generation_queue):
        with pytest.raises(WorkerOfflineError):
            await enqueue_task_only(
                project_name="demo",
                task_type="storyboard",
                media_type="image",
                resource_id="S00",
                payload={"prompt": "p"},
                script_file="episode_01.json",
            )

    async def test_enqueue_task_only_enqueues_when_worker_online(self, generation_queue):
        await generation_queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-a",
            ttl_seconds=30,
        )

        result = await enqueue_task_only(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="S01",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            dependency_group="episode_01.json:group:1",
            dependency_index=0,
        )

        task = await generation_queue.get_task(result["task_id"])
        assert task is not None
        assert task["status"] == "queued"
        assert task["dependency_group"] == "episode_01.json:group:1"
        assert task["dependency_index"] == 0

    async def test_wait_for_task_timeout(self, generation_queue):
        task = await generation_queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="S01",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="skill",
        )

        with pytest.raises(TaskWaitTimeoutError):
            await wait_for_task(
                task["task_id"],
                poll_interval=0.05,
                timeout_seconds=0.2,
                worker_offline_grace_seconds=10.0,
            )

    async def test_wait_for_task_raises_when_worker_offline(self, generation_queue):
        task = await generation_queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="S02",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="skill",
        )

        with pytest.raises(WorkerOfflineError):
            await wait_for_task(
                task["task_id"],
                poll_interval=0.05,
                timeout_seconds=5.0,
                worker_offline_grace_seconds=0.2,
            )
