import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

import lib.generation_queue as generation_queue_module
from lib.generation_queue import GenerationQueue
from webui.server.routers import tasks as tasks_router


class TestTaskRouterAndEvents(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmpdir.cleanup)

        self.db_path = Path(self.tmpdir.name) / "task_queue.db"
        generation_queue_module._QUEUE_INSTANCE = GenerationQueue(db_path=self.db_path)
        self.addCleanup(self._reset_queue_singleton)

    def _reset_queue_singleton(self):
        generation_queue_module._QUEUE_INSTANCE = None

    def _build_app(self):
        app = FastAPI()
        app.include_router(tasks_router.router, prefix="/api/v1")
        return app

    def test_task_router_endpoints_and_incremental_events(self):
        queue = generation_queue_module.get_generation_queue()
        task = queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "p"},
            script_file="episode_01.json",
            source="webui",
        )
        queue.claim_next_task(media_type="image")
        queue.mark_task_failed(task["task_id"], "mock fail")

        app = self._build_app()
        with TestClient(app) as client:
            task_resp = client.get(f"/api/v1/tasks/{task['task_id']}")
            self.assertEqual(task_resp.status_code, 200)
            self.assertEqual(task_resp.json()["task"]["status"], "failed")

            list_resp = client.get("/api/v1/tasks?project_name=demo")
            self.assertEqual(list_resp.status_code, 200)
            self.assertGreaterEqual(list_resp.json()["total"], 1)

            stats_resp = client.get("/api/v1/tasks/stats?project_name=demo")
            self.assertEqual(stats_resp.status_code, 200)
            stats = stats_resp.json()["stats"]
            self.assertEqual(stats["failed"], 1)

        events = queue.get_events_since(last_event_id=0, project_name="demo")
        self.assertGreaterEqual(len(events), 3)

        last_running_id = events[1]["id"]
        incremental = queue.get_events_since(last_event_id=last_running_id, project_name="demo")
        self.assertTrue(all(event["id"] > last_running_id for event in incremental))
        self.assertTrue(any(event["event_type"] == "failed" for event in incremental))


if __name__ == "__main__":
    unittest.main()
