import time
import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from lib.generation_queue import GenerationQueue


class TestGenerationQueue(unittest.TestCase):
    @staticmethod
    def _fd_count() -> int:
        for fd_dir in ("/dev/fd", "/proc/self/fd"):
            try:
                return len(os.listdir(fd_dir))
            except OSError:
                continue
        return -1

    def _create_queue(self) -> GenerationQueue:
        tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "queue.db"
        return GenerationQueue(db_path=db_path)

    def test_enqueue_dedupe_claim_and_succeed(self):
        queue = self._create_queue()

        first = queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test"},
            script_file="episode_01.json",
            source="webui",
        )
        self.assertFalse(first["deduped"])

        deduped = queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test2"},
            script_file="episode_01.json",
            source="webui",
        )
        self.assertTrue(deduped["deduped"])
        self.assertEqual(deduped["task_id"], first["task_id"])

        running = queue.claim_next_task(media_type="image")
        self.assertIsNotNone(running)
        self.assertEqual(running["task_id"], first["task_id"])
        self.assertEqual(running["status"], "running")

        done = queue.mark_task_succeeded(first["task_id"], {"file_path": "storyboards/scene_E1S01.png"})
        self.assertIsNotNone(done)
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["file_path"], "storyboards/scene_E1S01.png")

        # 终态后允许再次入队
        second = queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test3"},
            script_file="episode_01.json",
            source="webui",
        )
        self.assertFalse(second["deduped"])
        self.assertNotEqual(second["task_id"], first["task_id"])

    def test_event_sequence_and_incremental_read(self):
        queue = self._create_queue()

        task = queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={"prompt": "video"},
            script_file="episode_01.json",
            source="skill",
        )
        queue.claim_next_task(media_type="video")
        queue.mark_task_failed(task["task_id"], "mock error")

        all_events = queue.get_events_since(last_event_id=0)
        self.assertGreaterEqual(len(all_events), 3)
        self.assertEqual(all_events[0]["event_type"], "queued")
        self.assertEqual(all_events[1]["event_type"], "running")
        self.assertEqual(all_events[2]["event_type"], "failed")

        last_seen_id = all_events[1]["id"]
        incremental = queue.get_events_since(last_event_id=last_seen_id)
        self.assertTrue(all(event["id"] > last_seen_id for event in incremental))
        self.assertTrue(any(event["event_type"] == "failed" for event in incremental))

        latest_id = queue.get_latest_event_id()
        self.assertEqual(latest_id, all_events[-1]["id"])

    def test_worker_lease_takeover(self):
        queue = self._create_queue()

        first_ok = queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-a",
            ttl_seconds=1,
        )
        self.assertTrue(first_ok)

        second_ok = queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-b",
            ttl_seconds=1,
        )
        self.assertFalse(second_ok)

        time.sleep(1.2)

        takeover_ok = queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-b",
            ttl_seconds=1,
        )
        self.assertTrue(takeover_ok)

    def test_requeue_running_tasks(self):
        queue = self._create_queue()

        task = queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={"prompt": "video"},
            script_file="episode_01.json",
            source="webui",
        )
        running = queue.claim_next_task(media_type="video")
        self.assertIsNotNone(running)
        self.assertEqual(running["status"], "running")

        recovered = queue.requeue_running_tasks()
        self.assertEqual(recovered, 1)

        queued = queue.get_task(task["task_id"])
        self.assertIsNotNone(queued)
        self.assertEqual(queued["status"], "queued")
        self.assertIsNone(queued["started_at"])

        claimed_again = queue.claim_next_task(media_type="video")
        self.assertIsNotNone(claimed_again)
        self.assertEqual(claimed_again["task_id"], task["task_id"])

        events = queue.get_events_since(last_event_id=0)
        self.assertTrue(any(event["event_type"] == "requeued" for event in events))

    def test_get_events_since_does_not_leak_sqlite_file_descriptors(self):
        queue = self._create_queue()

        # Ensure there is at least one event row.
        queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test"},
            script_file="episode_01.json",
            source="webui",
        )

        baseline = self._fd_count()
        for _ in range(120):
            queue.get_events_since(last_event_id=0, limit=10)
        after = self._fd_count()

        if baseline >= 0 and after >= 0:
            # Allow small runtime fluctuations, but prevent linear FD growth.
            self.assertLessEqual(
                after,
                baseline + 10,
                f"FD count grew unexpectedly: baseline={baseline}, after={after}",
            )


if __name__ == "__main__":
    unittest.main()
