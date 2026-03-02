import time
import sqlite3
from pathlib import Path

from lib.generation_queue import GenerationQueue


def _create_queue(tmp_path: Path) -> GenerationQueue:
    db_path = tmp_path / "queue.db"
    return GenerationQueue(db_path=db_path)


class TestGenerationQueue:
    def test_enqueue_dedupe_claim_and_succeed(self, tmp_path):
        queue = _create_queue(tmp_path)

        first = queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test"},
            script_file="episode_01.json",
            source="webui",
        )
        assert not first["deduped"]

        deduped = queue.enqueue_task(
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

        running = queue.claim_next_task(media_type="image")
        assert running is not None
        assert running["task_id"] == first["task_id"]
        assert running["status"] == "running"

        done = queue.mark_task_succeeded(first["task_id"], {"file_path": "storyboards/scene_E1S01.png"})
        assert done is not None
        assert done["status"] == "succeeded"
        assert done["result"]["file_path"] == "storyboards/scene_E1S01.png"

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
        assert not second["deduped"]
        assert second["task_id"] != first["task_id"]

    def test_event_sequence_and_incremental_read(self, tmp_path):
        queue = _create_queue(tmp_path)

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
        assert len(all_events) >= 3
        assert all_events[0]["event_type"] == "queued"
        assert all_events[1]["event_type"] == "running"
        assert all_events[2]["event_type"] == "failed"

        last_seen_id = all_events[1]["id"]
        incremental = queue.get_events_since(last_event_id=last_seen_id)
        assert all(event["id"] > last_seen_id for event in incremental)
        assert any(event["event_type"] == "failed" for event in incremental)

        latest_id = queue.get_latest_event_id()
        assert latest_id == all_events[-1]["id"]

    def test_worker_lease_takeover(self, tmp_path):
        queue = _create_queue(tmp_path)

        first_ok = queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-a",
            ttl_seconds=1,
        )
        assert first_ok

        second_ok = queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-b",
            ttl_seconds=1,
        )
        assert not second_ok

        time.sleep(1.2)

        takeover_ok = queue.acquire_or_renew_worker_lease(
            name="default",
            owner_id="worker-b",
            ttl_seconds=1,
        )
        assert takeover_ok

    def test_claim_next_task_respects_dependencies_without_blocking_other_heads(self, tmp_path):
        queue = _create_queue(tmp_path)

        head_one = queue.enqueue_task(
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
        queue.enqueue_task(
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
        head_two = queue.enqueue_task(
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

        first_claim = queue.claim_next_task(media_type="image")
        second_claim = queue.claim_next_task(media_type="image")
        blocked_claim = queue.claim_next_task(media_type="image")

        assert first_claim is not None
        assert second_claim is not None
        assert {first_claim["task_id"], second_claim["task_id"]} == {
            head_one["task_id"],
            head_two["task_id"],
        }
        assert blocked_claim is None

        queue.mark_task_succeeded(head_one["task_id"], {"file_path": "storyboards/scene_E1S01.png"})
        unblocked_claim = queue.claim_next_task(media_type="image")
        assert unblocked_claim is not None
        assert unblocked_claim["resource_id"] == "E1S02"

    def test_mark_task_failed_cascades_to_queued_dependents(self, tmp_path):
        queue = _create_queue(tmp_path)

        first = queue.enqueue_task(
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
        second = queue.enqueue_task(
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
        third = queue.enqueue_task(
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

        running = queue.claim_next_task(media_type="image")
        assert running is not None
        assert running["task_id"] == first["task_id"]

        queue.mark_task_failed(first["task_id"], "boom")

        second_task = queue.get_task(second["task_id"])
        third_task = queue.get_task(third["task_id"])
        assert second_task is not None
        assert third_task is not None
        assert second_task["status"] == "failed"
        assert third_task["status"] == "failed"
        assert "blocked by failed dependency" in second_task["error_message"]
        assert first["task_id"] in second_task["error_message"]
        assert second["task_id"] in third_task["error_message"]

    def test_requeue_running_tasks(self, tmp_path):
        queue = _create_queue(tmp_path)

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
        assert running is not None
        assert running["status"] == "running"

        recovered = queue.requeue_running_tasks()
        assert recovered == 1

        queued = queue.get_task(task["task_id"])
        assert queued is not None
        assert queued["status"] == "queued"
        assert queued["started_at"] is None

        claimed_again = queue.claim_next_task(media_type="video")
        assert claimed_again is not None
        assert claimed_again["task_id"] == task["task_id"]

        events = queue.get_events_since(last_event_id=0)
        assert any(event["event_type"] == "requeued" for event in events)

    def test_get_events_since_does_not_leak_sqlite_file_descriptors(self, tmp_path, fd_count):
        queue = _create_queue(tmp_path)

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

        baseline = fd_count()
        for _ in range(120):
            queue.get_events_since(last_event_id=0, limit=10)
        after = fd_count()

        if baseline >= 0 and after >= 0:
            # Allow small runtime fluctuations, but prevent linear FD growth.
            assert after <= baseline + 10, f"FD count grew unexpectedly: baseline={baseline}, after={after}"

    def test_init_db_adds_dependency_columns_for_existing_database(self, tmp_path):
        db_path = tmp_path / "queue.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE tasks (
                    task_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    script_file TEXT,
                    payload_json TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_message TEXT,
                    source TEXT NOT NULL DEFAULT 'webui',
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE worker_lease (
                    name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    lease_until REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        queue = GenerationQueue(db_path=db_path)
        assert queue is not None

        conn = sqlite3.connect(db_path)
        try:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
        finally:
            conn.close()

        assert "dependency_task_id" in columns
        assert "dependency_group" in columns
        assert "dependency_index" in columns
