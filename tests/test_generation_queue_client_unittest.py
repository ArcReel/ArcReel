import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import lib.generation_queue as generation_queue_module
from lib.generation_queue import GenerationQueue
from lib.generation_queue_client import (
    TaskWaitTimeoutError,
    WorkerOfflineError,
    wait_for_task,
)


class TestGenerationQueueClient(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmpdir.cleanup)

        self.db_path = Path(self.tmpdir.name) / "task_queue.db"
        generation_queue_module._QUEUE_INSTANCE = GenerationQueue(db_path=self.db_path)
        self.addCleanup(self._reset_queue_singleton)

    def _reset_queue_singleton(self):
        generation_queue_module._QUEUE_INSTANCE = None

    def test_wait_for_task_timeout(self):
        queue = generation_queue_module.get_generation_queue()
        task = queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="S01",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="skill",
        )

        with self.assertRaises(TaskWaitTimeoutError):
            wait_for_task(
                task["task_id"],
                poll_interval=0.05,
                timeout_seconds=0.2,
                worker_offline_grace_seconds=10.0,
            )

    def test_wait_for_task_raises_when_worker_offline(self):
        queue = generation_queue_module.get_generation_queue()
        task = queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="S02",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="skill",
        )

        with self.assertRaises(WorkerOfflineError):
            wait_for_task(
                task["task_id"],
                poll_interval=0.05,
                timeout_seconds=5.0,
                worker_offline_grace_seconds=0.2,
            )


if __name__ == "__main__":
    unittest.main()
