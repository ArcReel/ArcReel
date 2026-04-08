"""Tests for generation_queue_client async functions."""

from unittest.mock import AsyncMock, patch

import pytest

from lib.generation_queue_client import (
    BatchTaskResult,
    BatchTaskSpec,
    TaskWaitTimeoutError,
    WorkerOfflineError,
    batch_enqueue_and_wait_sync,
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


class TestBatchEnqueueAndWaitSync:
    """Tests for batch_enqueue_and_wait_sync (mocked async functions)."""

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_empty_specs(self, mock_enqueue, mock_wait):
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=[],
        )
        assert successes == []
        assert failures == []
        mock_enqueue.assert_not_called()
        mock_wait.assert_not_called()

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_basic_success(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [
            {"task_id": "t1"},
            {"task_id": "t2"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {"file_path": "a.png"}},
            {"status": "succeeded", "result": {"file_path": "b.png"}},
        ]

        specs = [
            BatchTaskSpec(task_type="character", media_type="image", resource_id="Alice"),
            BatchTaskSpec(task_type="character", media_type="image", resource_id="Bob"),
        ]
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
        )

        assert len(successes) == 2
        assert len(failures) == 0
        assert {s.resource_id for s in successes} == {"Alice", "Bob"}
        assert mock_enqueue.call_count == 2
        assert mock_wait.call_count == 2

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_partial_failure(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [
            {"task_id": "t1"},
            {"task_id": "t2"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {"file_path": "a.png"}},
            {"status": "failed", "error_message": "API error"},
        ]

        specs = [
            BatchTaskSpec(task_type="clue", media_type="image", resource_id="jade-pendant"),
            BatchTaskSpec(task_type="clue", media_type="image", resource_id="old-tree"),
        ]
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
        )

        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].resource_id in ("jade-pendant", "old-tree")
        assert failures[0].status == "failed"

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_wait_exception_becomes_failure(self, mock_enqueue, mock_wait):
        mock_enqueue.return_value = {"task_id": "t1"}
        mock_wait.side_effect = RuntimeError("connection lost")

        specs = [
            BatchTaskSpec(task_type="storyboard", media_type="image", resource_id="S01"),
        ]
        successes, failures = batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
        )

        assert len(successes) == 0
        assert len(failures) == 1
        assert "connection lost" in failures[0].error

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_dependency_resource_id_resolution(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [
            {"task_id": "t-first"},
            {"task_id": "t-second"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {}},
            {"status": "succeeded", "result": {}},
        ]

        specs = [
            BatchTaskSpec(
                task_type="storyboard",
                media_type="image",
                resource_id="S01",
            ),
            BatchTaskSpec(
                task_type="storyboard",
                media_type="image",
                resource_id="S02",
                dependency_resource_id="S01",
                dependency_group="ep1:group:1",
                dependency_index=1,
            ),
        ]
        batch_enqueue_and_wait_sync(project_name="demo", specs=specs)

        # First enqueue: no dependency
        first_call = mock_enqueue.call_args_list[0]
        assert first_call.kwargs.get("dependency_task_id") is None

        # Second enqueue: dependency_task_id resolved to "t-first"
        second_call = mock_enqueue.call_args_list[1]
        assert second_call.kwargs["dependency_task_id"] == "t-first"
        assert second_call.kwargs["dependency_group"] == "ep1:group:1"
        assert second_call.kwargs["dependency_index"] == 1

    @patch("lib.generation_queue_client.wait_for_task", new_callable=AsyncMock)
    @patch("lib.generation_queue_client.enqueue_task_only", new_callable=AsyncMock)
    def test_callbacks_invoked(self, mock_enqueue, mock_wait):
        mock_enqueue.side_effect = [
            {"task_id": "t1"},
            {"task_id": "t2"},
        ]
        mock_wait.side_effect = [
            {"status": "succeeded", "result": {}},
            {"status": "failed", "error_message": "boom"},
        ]

        success_ids = []
        failure_ids = []

        def on_success(br: BatchTaskResult):
            success_ids.append(br.resource_id)

        def on_failure(br: BatchTaskResult):
            failure_ids.append(br.resource_id)

        specs = [
            BatchTaskSpec(task_type="character", media_type="image", resource_id="A"),
            BatchTaskSpec(task_type="character", media_type="image", resource_id="B"),
        ]
        batch_enqueue_and_wait_sync(
            project_name="demo",
            specs=specs,
            on_success=on_success,
            on_failure=on_failure,
        )

        assert len(success_ids) == 1
        assert len(failure_ids) == 1
