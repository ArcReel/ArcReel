"""Task terminal events through project_events.

Cover pure `build_task_terminal_change` conversion and GenerationQueue terminal entry points (success / failure / cancellation / batch cancellation / cascade failure) publishing batch events after persistence."""

from contextlib import asynccontextmanager

import pytest

from lib.generation.generation_queue import GenerationQueue
from lib.generation.task_terminal_events import build_task_terminal_change
from lib.project.project_change_hints import register_project_change_batch_listener


@pytest.fixture
async def queue(db_factory):
    return GenerationQueue(session_factory=db_factory)


@pytest.fixture
def captured_batches():
    """Collect (project_name, changes) published on the batch bus during this test."""
    batches: list[tuple[str, list[dict]]] = []

    def listener(project_name, _source, changes):
        batches.append((project_name, [dict(c) for c in changes]))

    unregister = register_project_change_batch_listener(listener)
    yield batches
    unregister()


def _commit_failing_factory(factory):
    """Proxy real session factories, replacing only ``commit`` with failure while reads/writes use the real DB.

    Fail exactly after terminal events are collected but before transaction commit; real session exit rolls back normally."""

    class _CommitFails:
        def __init__(self, session):
            self._session = session

        def __getattr__(self, name):
            return getattr(self._session, name)

        async def commit(self):
            raise RuntimeError("commit 炸了")

    @asynccontextmanager
    async def _open():
        async with factory() as session:
            yield _CommitFails(session)

    return _open


def _task_changes(batches):
    """Select only terminal-task changes, ignoring concurrent unrelated project changes."""
    return [c for _, changes in batches for c in changes if c["entity_type"] == "task"]


class TestBuildTaskTerminalChange:
    @pytest.mark.parametrize(
        ("status", "action"),
        [("succeeded", "task_succeeded"), ("failed", "task_failed"), ("cancelled", "task_cancelled")],
    )
    def test_terminal_statuses_map_to_actions(self, status, action):
        change = build_task_terminal_change({"task_id": "t1", "status": status, "task_type": "video"})
        assert change is not None
        assert change["action"] == action
        assert change["entity_type"] == "task"
        assert change["entity_id"] == "t1"
        assert change["task_type"] == "video"

    def test_refresh_signal_not_user_facing_notification(self):
        """Terminal changes refresh only: never show notifications or navigate/focus."""
        change = build_task_terminal_change({"task_id": "t1", "status": "succeeded"})
        assert change is not None
        assert change["important"] is False
        assert change["focus"] is None

    @pytest.mark.parametrize(
        "event",
        [
            {"task_id": "t1", "status": "queued"},
            {"task_id": "t1", "status": "running"},
            {"task_id": "", "status": "succeeded"},
        ],
    )
    def test_non_terminal_or_invalid_yields_none(self, event):
        assert build_task_terminal_change(event) is None


class TestQueueEmitsTerminalEvents:
    async def _enqueue_running(self, queue, resource_id="E1S01", task_type="video"):
        task = await queue.enqueue_task(
            project_name="demo",
            task_type=task_type,
            media_type="video",
            resource_id=resource_id,
            payload={},
            script_file="episode_01.json",
            source="webui",
        )
        await queue.claim_next_task(media_type="video")
        return task["task_id"]

    async def test_success_emits_task_succeeded(self, queue, captured_batches):
        task_id = await self._enqueue_running(queue)
        captured_batches.clear()

        await queue.mark_task_succeeded(task_id, {"file_path": "videos/E1S01.mp4"})

        changes = _task_changes(captured_batches)
        assert [c["action"] for c in changes] == ["task_succeeded"]
        assert changes[0]["entity_id"] == task_id
        assert changes[0]["task_type"] == "video"
        assert captured_batches[0][0] == "demo"

    async def test_failure_emits_task_failed(self, queue, captured_batches):
        task_id = await self._enqueue_running(queue)
        captured_batches.clear()

        await queue.mark_task_failed(task_id, "provider timeout")

        assert [c["action"] for c in _task_changes(captured_batches)] == ["task_failed"]

    async def test_cancel_emits_task_cancelled(self, queue, captured_batches):
        task = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="episode_01.json",
            source="webui",
        )
        captured_batches.clear()

        await queue.cancel_task(task["task_id"])

        changes = _task_changes(captured_batches)
        assert [c["action"] for c in changes] == ["task_cancelled"]
        assert changes[0]["entity_id"] == task["task_id"]

    async def test_cancel_all_queued_emits_one_batch_per_project(self, queue, captured_batches):
        for idx in range(3):
            await queue.enqueue_task(
                project_name="demo",
                task_type="video",
                media_type="video",
                resource_id=f"E1S1{idx}",
                payload={},
                script_file="episode_01.json",
                source="webui",
            )
        captured_batches.clear()

        await queue.cancel_all_queued("demo")

        # Combine three terminal changes into one batch to avoid rebuilding snapshots per task.
        assert len(captured_batches) == 1
        changes = _task_changes(captured_batches)
        assert len(changes) == 3
        assert {c["action"] for c in changes} == {"task_cancelled"}

    async def test_non_terminal_transitions_emit_nothing(self, queue, captured_batches):
        """Enqueue and claim are intermediate states and emit no terminal events."""
        await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S03",
            payload={},
            script_file="episode_01.json",
            source="webui",
        )
        await queue.claim_next_task(media_type="video")

        assert _task_changes(captured_batches) == []

    async def test_no_publish_when_commit_fails(self, queue, db_factory, captured_batches):
        """Never publish collected terminal events if commit fails; unpersisted terminal state cannot be announced.

        `_task_repo` publishes after `async with` for this reason. Moving publication inside repositories or before commit would announce state never persisted. Fail the session collaborator's actual commit seam: collect normally, then rollback, preserving absence of both persistence and publication."""
        task_id = await self._enqueue_running(queue)
        captured_batches.clear()

        failing_queue = GenerationQueue(session_factory=_commit_failing_factory(db_factory))
        with pytest.raises(RuntimeError, match="commit 炸了"):
            await failing_queue.mark_task_succeeded(task_id, {"file_path": "videos/E1S01.mp4"})

        assert _task_changes(captured_batches) == []
        assert (await queue.get_task(task_id))["status"] == "running"

    async def test_cascade_failure_emits_event_for_dependent(self, queue, captured_batches):
        """Cascade-failed downstream tasks emit terminal events too; _record_terminal_event centralizes every path."""
        parent = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S04",
            payload={},
            script_file="episode_01.json",
            source="webui",
        )
        child = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S04",
            payload={},
            script_file="episode_01.json",
            source="webui",
            dependency_task_id=parent["task_id"],
        )
        await queue.claim_next_task(media_type="image")
        captured_batches.clear()

        await queue.mark_task_failed(parent["task_id"], "boom")

        failed_ids = {c["entity_id"] for c in _task_changes(captured_batches) if c["action"] == "task_failed"}
        assert failed_ids == {parent["task_id"], child["task_id"]}
