"""Queue history with a live owner and an already accepted provider job."""

import asyncio
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import time_machine
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from lib.db.repositories.task_repo import TaskNotCancellableError, TaskRepository
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import GenerationWorker
from lib.infra.app_data_dir import reset_for_tests as reset_data_root
from server.services.tasks.generation_context import reset_backend_cache_for_tests
from server.services.tasks.generation_tasks import execute_generation_task
from server.services.tasks.resume_executor import execute_resume_video_task
from tests.factories import custom_endpoint_definition
from tests.http_capture import capture_http
from tests.integration.server.services.tasks.video_recovery_support import (
    configure_chain_project,
    seed_custom_video_rows,
    seed_running_video_task,
    storyboard_checkpoint_json,
)
from tests.invariant_profiles import state_machine_settings

pytestmark = pytest.mark.invariant
PAID = "accepted-video"


@dataclass
class ExpectedTask:
    status: str = "queued"
    parent: str | None = None
    owned: bool = False
    cancelled_by: str | None = None


class TaskHistory(RuleBasedStateMachine):
    def __init__(self, open_database, directory=None):
        super().__init__()
        self.stack = ExitStack()
        try:
            root = Path(self.stack.enter_context(TemporaryDirectory(dir=directory)))
            self.run = self.stack.enter_context(asyncio.Runner()).run
            database = open_database(root / "history.sqlite")
            self.factory = self.run(database.__aenter__())
            self.stack.callback(lambda: self.run(database.__aexit__(None, None, None)))
            self.clock = self.stack.enter_context(time_machine.travel(datetime(2026, 1, 1, tzinfo=UTC), tick=False))
            self.patch = self.stack.enter_context(pytest.MonkeyPatch.context())
            configure_chain_project(self.factory, root, self.patch)
            reset_data_root()
            self.stack.callback(reset_data_root)
            reset_backend_cache_for_tests()
            self.stack.callback(reset_backend_cache_for_tests)
            http = self.stack.enter_context(capture_http())
            self.post = http.route(method="POST").respond(409)
            self.poll = http.get("https://relay.test/v1/video/fetch/job-old").respond(
                json={"status": "completed", "video_url": "https://relay.test/file.mp4"}
            )
            self.download = http.get("https://relay.test/file.mp4").respond(content=b"paid-video")
            definition = custom_endpoint_definition(enum_maps={})
            definition["submit"]["body"] = {"prompt": "{{ prompt }}", "duration": "{{ duration_seconds }}"}
            rows = self.run(seed_custom_video_rows(self.factory, definition=definition))
            provider = rows["provider_id"]
            self.run(
                seed_running_video_task(
                    self.factory,
                    task_id=PAID,
                    provider_id=provider,
                    checkpoint_json=storyboard_checkpoint_json(
                        PAID, provider_id=provider, endpoint_guard=rows["endpoint_keys"][0]
                    ),
                )
            )
            self.tasks = {PAID: ExpectedTask("running")}
            self.leases = {}
            self.drop_ack = False
            self.worker = self.new_worker()
            self.enqueue(False)
            self.claim()
        except BaseException:
            self.stack.close()
            raise

    @initialize(event=st.sampled_from(("lost_requeue_ack", "restart", "continue")))
    def first_interruption(self, event):
        """Start short histories at either interruption boundary or in normal operation."""
        if event == "lost_requeue_ack":
            self.requeue(True)
        elif event == "restart":
            self.restart()

    def teardown(self):
        self.stack.close()

    @asynccontextmanager
    async def session(self):
        async with self.factory() as session:
            if self.drop_ack:
                commit = session.commit

                async def commit_then_disconnect():
                    await commit()
                    self.drop_ack = False
                    raise OSError("connection lost after commit")

                self.patch.setattr(session, "commit", commit_then_disconnect)
            yield session

    def new_worker(self):
        return GenerationWorker(
            queue=GenerationQueue(session_factory=self.session),
            executor=execute_generation_task,
            resume_executor=execute_resume_video_task,
            write_backoff=self.on_backoff,
            lanes=("image",),
        )

    async def on_backoff(self, delay):
        self.check()
        await self.claim_image()
        if self.next_claimable() is None:
            async with asyncio.timeout(2):
                assert not await self.worker._claim_tasks()

    def candidates(self, status):
        return [key for key, task in self.tasks.items() if task.status == status and key != PAID]

    @rule(dependent=st.booleans())
    def enqueue(self, dependent):
        parents = self.candidates("queued") + self.candidates("running")
        parent = parents[-1] if parents and dependent else None
        self.clock.shift(timedelta(seconds=1))

        async def add():
            async with self.factory() as session:
                return await TaskRepository(session).enqueue(
                    project_name="demo",
                    media_type="image",
                    task_type="storyboard",
                    resource_id=f"image-{len(self.tasks)}",
                    dependency_task_id=parent,
                )

        row = self.run(add())
        assert row["task_id"] not in self.tasks
        self.tasks[row["task_id"]] = ExpectedTask(parent=parent)

    def next_claimable(self):
        return next(
            (
                key
                for key in self.candidates("queued")
                if not self.tasks[key].owned
                and (self.tasks[key].parent is None or self.tasks[self.tasks[key].parent].status == "succeeded")
            ),
            None,
        )

    async def claim_image(self):
        expected = self.next_claimable()
        owner = self.worker._ownership
        row = await self.worker.queue.claim_next_task("image", owned_task_ids=owner.owned_task_ids())
        assert (row["task_id"] if row else None) == expected
        if expected:
            lease = owner.try_acquire(expected)
            assert lease is not None
            self.leases[expected] = lease
            self.tasks[expected].owned = True
            self.tasks[expected].status = "running"

    @rule()
    def claim(self):
        self.run(self.claim_image())

    def release(self, key):
        self.leases.pop(key).release()
        self.tasks[key].owned = False

    @precondition(lambda self: bool(self.candidates("running")))
    @rule(lost_ack=st.booleans())
    def requeue(self, lost_ack):
        key = self.candidates("running")[0]
        self.tasks[key].status = "queued"
        self.drop_ack = lost_ack
        result = self.run(self.worker._requeue_single_task(key))
        assert result is (not lost_ack)
        self.release(key)

    @precondition(lambda self: bool(self.candidates("running")))
    @rule()
    def complete(self):
        key = self.candidates("running")[0]
        assert self.run(self.worker.queue.mark_task_succeeded(key, {"ok": True})) == 1
        self.tasks[key].status = "succeeded"
        self.release(key)

    @rule(index=st.integers(0, 20))
    def cancel(self, index):
        key = list(self.tasks)[1 + index % (len(self.tasks) - 1)]
        task = self.tasks[key]

        async def cancel():
            async with self.factory() as session:
                return await TaskRepository(session).cancel_task(key)

        if task.status == "running":
            with pytest.raises(TaskNotCancellableError):
                self.run(cancel())
            return
        result = self.run(cancel())
        cancelled = set()
        if task.status == "queued":
            for identity, entry in self.tasks.items():
                if entry.status == "queued" and (identity == key or entry.parent in cancelled):
                    entry.status = "cancelled"
                    entry.cancelled_by = "user" if identity == key else "cascade"
                    cancelled.add(identity)
        assert {row["task_id"]: row["cancelled_by"] for row in result["cancelled"]} == {
            identity: self.tasks[identity].cancelled_by for identity in cancelled
        }
        assert len(result["cancelled"]) == len(cancelled)
        assert [row["task_id"] for row in result["skipped_terminal"]] == ([] if cancelled else [key])

    @rule()
    def restart(self):
        self.worker = self.new_worker()
        self.leases.clear()
        for task in self.tasks.values():
            task.owned = False
        for key, task in self.tasks.items():
            if key == PAID:
                task.status = "succeeded"
            elif task.status == "running" or (
                task.status == "queued" and task.parent and self.tasks[task.parent].status == "failed"
            ):
                task.status = "failed"

        async def recover():
            await self.worker._recovery.handle_orphans()
            await self.worker._recovery.wait_dispatchers()

        self.run(recover())

    def check(self):
        assert self.worker._ownership.owned_task_ids() == {k for k, task in self.tasks.items() if task.owned}
        assert self.post.call_count == 0
        assert self.poll.call_count == self.download.call_count == int(self.tasks[PAID].status == "succeeded")

    @invariant()
    def matches_history(self):
        self.check()

        async def snapshot():
            async with self.factory() as session:
                return await TaskRepository(session).list_tasks(page_size=len(self.tasks))

        page = self.run(snapshot())
        assert page["total"] == len(self.tasks)
        assert {row["task_id"] for row in page["items"]} == self.tasks.keys()
        for row in page["items"]:
            task = self.tasks[row["task_id"]]
            assert (row["status"], row["dependency_task_id"], row["cancelled_by"]) == (
                task.status,
                task.parent,
                task.cancelled_by,
            )
            assert row["provider_job_id"] == ("job-old" if row["task_id"] == PAID else None)


@pytest.mark.timeout(600)
def test_task_history(example_database, tmp_path):
    run_state_machine_as_test(lambda: TaskHistory(example_database, tmp_path), settings=state_machine_settings())
    assert list(tmp_path.iterdir()) == []


def test_replay_paid_restart(example_database):
    history = TaskHistory(example_database)
    try:
        history.restart()
        row = history.run(history.worker.queue.get_task(PAID))
        assert row["status"] == "succeeded"
        assert history.post.call_count == 0
        history.matches_history()
    finally:
        history.teardown()


def test_replay_lost_ack(example_database):
    history = TaskHistory(example_database)
    try:
        (original,) = history.worker._ownership.owned_task_ids()
        history.requeue(True)
        history.matches_history()
        history.claim()
        assert history.worker._ownership.owned_task_ids() == {original}
        history.matches_history()
    finally:
        history.teardown()
