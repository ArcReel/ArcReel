import asyncio
import contextlib
from typing import Any

import pytest

from lib.db.models.api_call import ApiCall
from lib.generation.generation_worker import GenerationWorker, SlotTable
from tests.integration.lib.generation.worker_support import (
    FakeWorkerQueue,
    capacity_table,
    db_queue,
    reference_checkpoint_json,
    seed_running_task,
    stage_task_dir,
    storyboard_resume_task,
    stub_executors,
    task_status,
)


def _phase_ids(slots: SlotTable, provider: str, media: str) -> tuple[set[str], set[str]]:
    """Return inflight and pending task IDs for one capacity bucket."""
    bucket = slots._slots.get((provider, media), {})
    inflight = {tid for tid, occ in bucket.items() if not occ.pending}
    pending = {tid for tid, occ in bucket.items() if occ.pending}
    return inflight, pending


def _worker_storyboard_checkpoint(task_id: str, *, provider_id: str = "ark") -> str:
    return str(storyboard_resume_task(task_id, provider_id=provider_id)["execution_checkpoint_json"])


def _storyboard_orphan(task_id: str, *, provider_id: str = "ark", job_id: str = "job-1") -> dict[str, Any]:
    return {**storyboard_resume_task(task_id, provider_id=provider_id, job_id=job_id), "status": "running"}


async def _task_error(factory, task_id: str) -> str | None:
    from lib.db.models.task import Task

    async with factory() as session:
        row = await session.get(Task, task_id)
        return None if row is None else row.error_message


def _active_task_named(slots: SlotTable, name: str) -> asyncio.Future[Any]:
    """Read running tasks from occupancy records by executor name."""
    matches = [task for task in slots.all_active_tasks() if isinstance(task, asyncio.Task) and task.get_name() == name]
    assert len(matches) == 1, f"占用台账里没有唯一名为 {name} 的执行体"
    return matches[0]


class TestRestartRecovery:
    """Restart recovery: orphan routing (ADR 0007) and background resume dispatch."""

    @pytest.mark.asyncio
    async def test_handle_orphan_running_no_job_id_marks_restart_lost(self, monkeypatch):
        """ADR 0007: running without provider_job_id yields [restart_lost]."""
        queue = FakeWorkerQueue()
        queue._orphans = [
            {
                "task_id": "orphan-lost",
                "status": "running",
                "provider_id": None,
                "provider_job_id": None,
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        await worker._recovery.handle_orphans()
        assert queue.failed
        assert queue.failed[0][0] == "orphan-lost"
        assert "[restart_lost_no_job_id]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_reference_orphans_apply_strict_four_state_checkpoint_job_matrix(self, staged_project):
        queue = FakeWorkerQueue()
        queue._orphans = [
            {
                "task_id": "ref-neither",
                "status": "running",
                "task_type": "reference_video",
                "media_type": "video",
                "project_name": "demo",
                "resource_id": "E1U1",
                "script_file": "scripts/episode_1.json",
                "provider_job_id": None,
                "payload": {},
            },
            {
                "task_id": "ref-checkpoint-only",
                "status": "running",
                "task_type": "reference_video",
                "media_type": "video",
                "project_name": "demo",
                "resource_id": "E1U1",
                "script_file": "scripts/episode_1.json",
                "provider_job_id": None,
                "execution_checkpoint_json": reference_checkpoint_json("ref-checkpoint-only"),
                "payload": {},
            },
            {
                "task_id": "ref-job-only",
                "status": "running",
                "task_type": "reference_video",
                "media_type": "video",
                "project_name": "demo",
                "resource_id": "E1U1",
                "script_file": "scripts/episode_1.json",
                "provider_job_id": "job-1",
                "payload": {},
            },
            {
                "task_id": "ref-bad-checkpoint",
                "status": "running",
                "task_type": "reference_video",
                "media_type": "video",
                "project_name": "demo",
                "resource_id": "E1U1",
                "script_file": "scripts/episode_1.json",
                "provider_job_id": "job-2",
                "execution_checkpoint_json": "{broken",
                "payload": {},
            },
        ]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        staged = {task["task_id"]: stage_task_dir(staged_project, task["task_id"]) for task in queue._orphans}

        await worker._recovery.handle_orphans()

        failures = dict(queue.failed)
        assert "[restart_lost_no_job_id]" in failures["ref-neither"]
        assert "[restart_lost_checkpoint_no_job_id]" in failures["ref-checkpoint-only"]
        assert "[execution_identity_unrecoverable]" in failures["ref-job-only"]
        assert "[execution_identity_unrecoverable]" in failures["ref-bad-checkpoint"]
        assert [task_id for task_id, path in staged.items() if path.exists()] == []
        assert worker._recovery.orphan_dispatcher_task is None

    @pytest.mark.asyncio
    async def test_storyboard_orphans_apply_strict_four_state_checkpoint_job_matrix(self, staged_project):
        base = {
            "status": "running",
            "task_type": "video",
            "media_type": "video",
            "project_name": "demo",
            "resource_id": "E1S01",
            "script_file": "scripts/episode_1.json",
            "payload": {},
        }
        queue = FakeWorkerQueue()
        queue._orphans = [
            {**base, "task_id": "story-neither", "provider_job_id": None},
            {
                **base,
                "task_id": "story-checkpoint-only",
                "provider_job_id": None,
                "execution_checkpoint_json": _worker_storyboard_checkpoint("story-checkpoint-only"),
            },
            {**base, "task_id": "story-job-only", "provider_job_id": "job-1"},
            {
                **base,
                "task_id": "story-bad-checkpoint",
                "provider_job_id": "job-2",
                "execution_checkpoint_json": "{broken",
            },
        ]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        staged = {task["task_id"]: stage_task_dir(staged_project, task["task_id"]) for task in queue._orphans}

        await worker._recovery.handle_orphans()

        failures = dict(queue.failed)
        assert "[restart_lost_no_job_id]" in failures["story-neither"]
        assert "[restart_lost_checkpoint_no_job_id]" in failures["story-checkpoint-only"]
        assert "[execution_identity_unrecoverable]" in failures["story-job-only"]
        assert "[execution_identity_unrecoverable]" in failures["story-bad-checkpoint"]
        assert [task_id for task_id, path in staged.items() if path.exists()] == []
        assert worker._recovery.orphan_dispatcher_task is None

    @pytest.mark.asyncio
    async def test_reference_orphan_with_checkpoint_and_job_routes_by_checkpoint_provider(
        self, monkeypatch, staged_project
    ):
        from lib.backends.providers import PROVIDER_GROK

        queue = FakeWorkerQueue()
        task = {
            "task_id": "ref-ready",
            "status": "running",
            "task_type": "reference_video",
            "media_type": "video",
            "project_name": "demo",
            "resource_id": "E1U1",
            "script_file": "scripts/episode_1.json",
            "provider_id": PROVIDER_GROK,
            "provider_job_id": "job-ready",
            "execution_checkpoint_json": reference_checkpoint_json("ref-ready", provider_id="ark"),
            "payload": {"video_provider_r2v": "current/wrong"},
        }
        queue._orphans = [task]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        resumed: list[dict[str, Any]] = []

        async def _capture_resume(resume_task, *, job_id):
            resumed.append(resume_task)
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _capture_resume)

        await worker._recovery.handle_orphans()
        dispatcher_task = worker._recovery.orphan_dispatcher_task
        assert dispatcher_task is not None
        await asyncio.wait_for(dispatcher_task, timeout=5)

        # Dispatch uses checkpoint Ark identity, never advisory Grok columns or current payload configuration.
        assert [t["task_id"] for t in resumed] == ["ref-ready"]
        assert resumed[0]["provider_id"] == "ark"
        assert queue.failed == []

    # ------------------------------------------------------------------
    # handle_orphans: recovery routing
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_handle_orphan_image_running_marks_restart_lost(self, worker_db):
        """Image orphans have no resume path: [restart_lost], never automatic requeue with duplicate charges."""
        queue = FakeWorkerQueue()
        queue._orphans = [
            {
                "task_id": "img-orphan",
                "status": "running",
                "provider_id": "gemini-aistudio",
                "provider_job_id": "should-not-be-used",
                "media_type": "image",
                "task_type": "storyboard",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        await seed_running_task(worker_db, "img-orphan", task_type="storyboard", media_type="image")

        await worker._recovery.handle_orphans()

        assert await task_status(worker_db, "img-orphan") == "running", "image 孤儿绝不能被回队重跑"
        assert queue.failed
        assert queue.failed[0][0] == "img-orphan"
        assert "[restart_lost_image]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_render_running_marks_restart_lost(self, tmp_path, monkeypatch):
        """Render orphans fail without requeue; clean temporary files while retaining formal cuts/records."""
        from lib.project.project_manager import ProjectManager

        projects = ProjectManager(tmp_path)
        projects.create_project("demo")
        monkeypatch.setattr("lib.generation.restart_recovery.get_project_manager", lambda: projects)
        render_dir = projects.get_project_path("demo") / "renders" / "episode_1" / "tl-0000abcd"
        render_dir.mkdir(parents=True)
        formal = render_dir / "final_cut.mp4"
        record = render_dir / "final_cut.render.json"
        formal.write_bytes(b"previous")
        record.write_bytes(b"previous record")
        token = "a" * 32
        partial = render_dir / f".final_cut.{token}.partial.mp4"
        work = render_dir / f".final_cut.{token}.work"
        partial.write_bytes(b"unfinished")
        record_temp = render_dir / ".project.interrupted.tmp"
        record_temp.write_bytes(b"unfinished record")
        work.mkdir()
        (work / "segment_0000.mp4").write_bytes(b"intermediate")
        queue = FakeWorkerQueue()
        queue._orphans = [
            {
                "task_id": "render-orphan",
                "status": "running",
                "provider_id": "render",
                "provider_job_id": None,
                "media_type": "render",
                "task_type": "render_final_cut",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        await worker._recovery.handle_orphans()

        assert queue.failed == [("render-orphan", "[restart_lost_render]")]
        assert not partial.exists()
        assert not record_temp.exists()
        assert not work.exists()
        assert formal.read_bytes() == b"previous"
        assert record.read_bytes() == b"previous record"

    @pytest.mark.asyncio
    async def test_handle_orphan_non_resumable_video_marks_resume_unsupported(self, worker_db, staged_project):
        """Grok/Vidu video orphans yield [resume_unsupported]; backends lack resume, never rerun."""
        from lib.backends.providers import PROVIDER_GROK

        queue = FakeWorkerQueue()
        queue._orphans = [_storyboard_orphan("grok-orphan", provider_id=PROVIDER_GROK, job_id="some-job")]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        await seed_running_task(worker_db, "grok-orphan")

        await worker._recovery.handle_orphans()

        assert await task_status(worker_db, "grok-orphan") == "running", "不可 resume 的视频孤儿绝不能被回队重跑"
        assert queue.failed
        assert queue.failed[0][0] == "grok-orphan"
        assert "[resume_unsupported_provider]" in queue.failed[0][1]
        assert PROVIDER_GROK in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_running_routes_through_real_queue(self, worker_db, staged_project):
        """Recovery scans running only: image/nonresumable video orphans fail; other states stay untouched."""
        from lib.backends.providers import PROVIDER_GROK

        await seed_running_task(worker_db, "img-run", task_type="storyboard", media_type="image", resource_id="E1S02")
        await seed_running_task(
            worker_db,
            "grok-run",
            script_file="scripts/episode_1.json",
            provider_id=PROVIDER_GROK,
            provider_job_id="job",
            execution_checkpoint_json=_worker_storyboard_checkpoint("grok-run", provider_id=PROVIDER_GROK),
        )
        await seed_running_task(
            worker_db, "img-queued", task_type="storyboard", media_type="image", resource_id="E1S03", status="queued"
        )

        worker = GenerationWorker(
            queue=db_queue(worker_db), executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        await worker._recovery.handle_orphans()

        assert await task_status(worker_db, "img-run") == "failed"
        assert await _task_error(worker_db, "img-run") == "[restart_lost_image]"
        assert await task_status(worker_db, "grok-run") == "failed"
        assert "[resume_unsupported_provider]" in (await _task_error(worker_db, "grok-run") or "")
        assert await task_status(worker_db, "img-queued") == "queued"
        assert worker._recovery.orphan_dispatcher_task is None

    @pytest.mark.asyncio
    async def test_handle_orphan_discard_paths_zero_rows_leave_task_untouched(self, worker_db, staged_project):
        """If discard-path mark_failed affects zero rows because status left running after scanning, preserve state rather than changing it to cancelled."""
        from lib.backends.providers import PROVIDER_GROK
        from lib.generation.generation_queue import GenerationQueue

        stale_snapshot = [
            {
                "task_id": "img-raced",
                "status": "running",
                "provider_id": "gemini-aistudio",
                "provider_job_id": None,
                "media_type": "image",
                "task_type": "storyboard",
                "payload": {},
                "project_name": "demo",
            },
            _storyboard_orphan("grok-raced", provider_id=PROVIDER_GROK, job_id="job"),
        ]

        class _StaleScanQueue(GenerationQueue):
            async def list_orphan_tasks_on_start(self):
                return stale_snapshot

        # Row returns to queued after scanning: terminal writes accept running only; interruption fallback would wrongly flip queued to cancelled.
        await seed_running_task(
            worker_db, "img-raced", task_type="storyboard", media_type="image", status="queued", started_at=None
        )
        await seed_running_task(worker_db, "grok-raced", status="queued", started_at=None)

        worker = GenerationWorker(
            queue=_StaleScanQueue(session_factory=worker_db),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )
        await worker._recovery.handle_orphans()

        assert await task_status(worker_db, "img-raced") == "queued"
        assert await task_status(worker_db, "grok-raced") == "queued"

    @pytest.mark.asyncio
    async def test_handle_orphan_uses_checkpoint_provider_id(self, monkeypatch, worker_db, staged_project):
        """Checkpoint provider overrides advisory task columns and current-project resolution.

        Checkpoint Grok lacks resume even if advisory columns/current project switched to resumable Ark. Classify non_resumable → [resume_unsupported], never send old job_id to a new backend through VideoResumeRunner.run."""
        from lib.backends.providers import PROVIDER_GROK

        queue = FakeWorkerQueue()
        orphan = _storyboard_orphan("ghost-orphan", provider_id=PROVIDER_GROK, job_id="stale-job")
        orphan["provider_id"] = "ark"
        orphan["payload"] = {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"}
        queue._orphans = [orphan]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        await seed_running_task(worker_db, "ghost-orphan")
        resumed: list[str] = []

        async def _capture_resume(resume_task, *, job_id):
            resumed.append(resume_task["task_id"])
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _capture_resume)

        await worker._recovery.handle_orphans()

        # Checkpoint Grok yields [resume_unsupported]; incorrectly using advisory/payload Ark would dispatch resume.
        assert await task_status(worker_db, "ghost-orphan") == "running"
        assert resumed == []
        assert worker._recovery.orphan_dispatcher_task is None
        assert queue.failed
        assert queue.failed[0][0] == "ghost-orphan"
        assert "[resume_unsupported_provider]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_resumable_dispatches_resume_runner(self, monkeypatch, staged_project):
        """Resumable video providers with job_id dispatch VideoResumeRunner.run in background.

        Semaphore dispatcher registers inflight inside subtasks and pops in finally; assert target receipt in dispatched, since completion already cleans inflight."""
        queue = FakeWorkerQueue()
        queue._orphans = [_storyboard_orphan("ark-orphan", job_id="ark-job-1")]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        dispatched: list[dict] = []

        async def _capture_resume(task, *, job_id):
            dispatched.append(task)
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _capture_resume)
        await worker._recovery.handle_orphans()
        # Await background dispatcher, including orphan-dispatcher and provider-bucket subtasks.
        for _ in range(50):
            await asyncio.sleep(0)
            if dispatched:
                break
        # Await dispatcher task completion to avoid unawaited-task warnings.
        for t in list(asyncio.all_tasks()):
            name = t.get_name()
            if name in ("orphan-dispatcher",) or name.startswith(("orphan-dispatch-", "resume-video-")):
                with contextlib.suppress(Exception):
                    await t
        assert len(dispatched) == 1
        assert dispatched[0]["task_id"] == "ark-orphan"

    @pytest.mark.asyncio
    async def test_handle_orphan_fast_path_returns_immediately(self, monkeypatch, staged_project):
        """Fast path never blocks: five resumable orphans and video_max=2 with nonreturning resume still let `handle_orphans` return; background dispatcher owns execution."""
        queue = FakeWorkerQueue()
        queue._orphans = [_storyboard_orphan(f"orphan-{i}", job_id=f"job-{i}") for i in range(5)]
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 2}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        # Block resume entry to prove the fast path does not wait for completion.
        async def _block_forever(_task, *, job_id):
            await asyncio.Event().wait()

        monkeypatch.setattr(stub_executors, "resume", _block_forever)

        # Timeout prevents deadlock only: awaiting never-ending resume would hang forever, so this is not a timing assertion.
        await asyncio.wait_for(worker._recovery.handle_orphans(), timeout=10)
        assert worker._recovery.orphan_dispatcher_task is not None
        assert not worker._recovery.orphan_dispatcher_task.done()

        # Clean background dispatcher to avoid unawaited-task warnings.
        for t in list(asyncio.all_tasks()):
            if t.get_name() in ("orphan-dispatcher", "orphan-dispatch-ark"):
                t.cancel()
            if t.get_name().startswith("resume-video-"):
                t.cancel()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_handle_orphan_dispatcher_respects_pool_capacity(self, monkeypatch, staged_project):
        """Background dispatcher promotes in batches constrained by video capacity; in-flight occupancy never exceeds cap. Pending consumes no semaphore and does not count toward this limit."""
        queue = FakeWorkerQueue()
        queue._orphans = [_storyboard_orphan(f"orphan-{i}", job_id=f"job-{i}") for i in range(4)]
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 2}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        # Controlled events release resume tasks; record occupancy at every dispatch.
        snapshots: list[int] = []
        entered: list[str] = []
        gates: dict[str, asyncio.Event] = {f"orphan-{i}": asyncio.Event() for i in range(4)}

        def _inflight() -> int:
            return len(_phase_ids(worker._slots, "ark", "video")[0])

        async def _gated(task, *, job_id):
            snapshots.append(_inflight())
            entered.append(task["task_id"])
            await gates[task["task_id"]].wait()
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _gated)

        await worker._recovery.handle_orphans()
        # Let dispatcher promote first two to INFLIGHT, leaving two PENDING.
        for _ in range(20):
            await asyncio.sleep(0)
            if _inflight() >= 2:
                break
        assert _inflight() == 2, f"应只有 2 个 inflight，实际 {_inflight()}"

        # Release one in-flight task; _run_one finally releases occupancy and semaphore,
        # allowing promotion of the third. Production main-loop drain_finished handles cleanup each cycle;
        # call it once here (dispatcher subtasks no-op because finally releases their occupancy).
        gates[entered[0]].set()
        await asyncio.sleep(0)
        worker._slots.drain_finished()
        for _ in range(20):
            await asyncio.sleep(0)
            if _inflight() >= 2:
                break
        # Inflight never exceeds capacity two.
        assert _inflight() <= 2
        # Every VideoResumeRunner.run promotion snapshot stays within capacity, the core regression assertion.
        assert snapshots, "未采集到 inflight 快照"
        assert all(s <= 2 for s in snapshots), f"inflight 快照越过容量上限: {snapshots}"

        # Finalize: release every gate and await dispatcher.
        for gate in gates.values():
            gate.set()
        for _ in range(50):
            await asyncio.sleep(0)
        # Clean possible leftovers.
        for t in list(asyncio.all_tasks()):
            name = t.get_name()
            if name.startswith(("orphan-", "resume-video-")) and not t.done():
                t.cancel()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_handle_orphan_dispatcher_exits_on_stop_event(self, monkeypatch, staged_project):
        """Dispatcher exits cleanly on `_stop_event`, without dispatching remaining orphans."""
        queue = FakeWorkerQueue()
        queue._orphans = [_storyboard_orphan(f"orphan-{i}", job_id=f"job-{i}") for i in range(3)]
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 1}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        dispatched_count = 0
        first_dispatched = asyncio.Event()
        block_gate = asyncio.Event()

        async def _maybe_block(_task, *, job_id):
            nonlocal dispatched_count
            dispatched_count += 1
            first_dispatched.set()
            await block_gate.wait()
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _maybe_block)

        await worker._recovery.handle_orphans()
        # Await first orphan becoming in flight.
        await asyncio.wait_for(first_dispatched.wait(), timeout=1.0)
        assert dispatched_count == 1
        # Trigger shutdown.
        worker._stop_event.set()
        block_gate.set()
        # Let dispatcher see stop_event and exit without dispatching the remaining two.
        for _ in range(50):
            await asyncio.sleep(0)
            if dispatched_count == 1 and not any(
                t.get_name() == "orphan-dispatcher" and not t.done() for t in asyncio.all_tasks()
            ):
                break
        assert dispatched_count == 1, f"stop_event 后不应再 dispatch，实际 dispatched={dispatched_count}"


class TestDispatcherFailFastAndPendingTracking:
    """Dispatcher fail-fast behavior, separate pending/inflight sets, exact capacity and cancellation tracking."""

    @pytest.mark.asyncio
    async def test_dispatch_provider_bucket_fail_fast_when_video_max_zero(self, worker_db):
        """Zero video capacity immediately marks failed[resume_unsupported], avoiding Semaphore(0) deadlock.

        Capacity zero first triggers a reload fallback: no enabled custom-provider models in DB makes real video-lane projection zero, exercising fail-fast."""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with worker_db() as session:
            provider = await CustomProviderRepository(session).create_provider(
                display_name="no-models",
                discovery_format="openai",
                base_url="https://example.invalid",
                api_key="k",
            )
            await session.commit()
        provider_key = f"custom-{provider.id}"

        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({provider_key: {"image": 0, "video": 0}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        tasks = [{"task_id": f"orphan-{i}", "provider_id": provider_key} for i in range(3)]
        await worker._recovery.dispatch_provider_bucket(provider_key, tasks)

        assert {tid for tid, _ in queue.failed} == {"orphan-0", "orphan-1", "orphan-2"}
        assert all("[resume_unsupported_capacity_zero]" in msg for _, msg in queue.failed)

    @pytest.mark.asyncio
    async def test_capacity_zero_settles_the_pending_call(self, worker_db):
        """Early dispatch failure also marks the pending ApiCall failed at zero cost.

        Resume opens no new ledger bracket; changing tasks without settling calls would leave permanently pending usage rows, especially after retry-download reopens one as pending."""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository
        from lib.db.repositories.usage_repo import UsageRepository

        async with worker_db() as session:
            provider = await CustomProviderRepository(session).create_provider(
                display_name="no-models",
                discovery_format="openai",
                base_url="https://example.invalid",
                api_key="k",
            )
            call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="video", model="m", provider="gemini", task_id="retry-1"
            )
            await session.commit()
        provider_key = f"custom-{provider.id}"

        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({provider_key: {"image": 0, "video": 0}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        await worker._recovery.dispatch_provider_bucket(
            provider_key,
            [{"task_id": "retry-1", "provider_id": provider_key, "payload": {}}],
        )

        async with worker_db() as session:
            row = await session.get(ApiCall, call_id)
        assert (row.status, row.cost_amount) == ("failed", 0)
        # Early failure has no exception object to classify, only original text: store error_message without a code.
        assert row.error_message == f"resume unsupported: provider {provider_key} has no video capacity"
        assert (row.error_code, row.error_params) == (None, None)

    @pytest.mark.asyncio
    async def test_sub_task_registered_in_pending_before_sem_acquire(self, monkeypatch, staged_project):
        """With semaphore one and two tasks, register the second as PENDING while it waits."""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 1}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        gate = asyncio.Event()

        async def _gated(_task, *, job_id):
            await gate.wait()
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _gated)

        tasks = [storyboard_resume_task(f"orphan-{i}", job_id=f"job-{i}") for i in range(2)]
        dispatcher = asyncio.create_task(worker._recovery.dispatch_provider_bucket("ark", tasks))

        for _ in range(20):
            await asyncio.sleep(0)
            inflight, _pending = _phase_ids(worker._slots, "ark", "video")
            if len(inflight) == 1:
                break
        inflight, pending = _phase_ids(worker._slots, "ark", "video")
        assert len(inflight) == 1
        assert len(pending) == 1
        # Pending counts toward capacity: occupancy two ≥ capacity one leaves no room for overclaiming.
        assert worker._slots.has_room("ark", "video", 1) is False

        gate.set()
        await dispatcher

    @pytest.mark.asyncio
    async def test_sem_queued_interruption_lands_terminal(self, monkeypatch, staged_project):
        """Interrupting semaphore wait makes _run_one explicitly settle terminal state, never leave running."""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 1}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        gate = asyncio.Event()
        first_started = asyncio.Event()

        async def _gated(_task, *, job_id):
            first_started.set()
            await gate.wait()
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _gated)

        tasks = [storyboard_resume_task(f"orphan-{i}", job_id=f"job-{i}") for i in range(2)]
        dispatcher = asyncio.create_task(worker._recovery.dispatch_provider_bucket("ark", tasks))

        # First task enters VideoResumeRunner.run holding semaphore; second still awaits it.
        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        _inflight, pending = _phase_ids(worker._slots, "ark", "video")
        assert "orphan-1" in pending

        # Interrupt semaphore-queued orphan-1.
        _active_task_named(worker._slots, "resume-video-orphan-1").cancel()

        # Await dispatcher completion.
        gate.set()
        await dispatcher

        assert queue.interrupted == ["orphan-1"]
        assert [tid for tid, _ in queue.succeeded] == ["orphan-0"]

    @pytest.mark.asyncio
    async def test_acquired_pre_process_interruption_lands_terminal(self, staged_project):
        """Interrupting after acquisition but before VideoResumeRunner.run enters try makes _run_one settle terminal state.

        The gap is the provider-projection await before the internal try: interruption there bypasses internal finalization, requiring outer fallback. Only tasks lacking checkpoints enter projection."""
        queue = FakeWorkerQueue()

        acquired_event = asyncio.Event()
        pre_try_gate = asyncio.Event()

        async def _blocked_projection(_task):
            acquired_event.set()
            await pre_try_gate.wait()
            return "ark"

        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 1}}),
            provider_projection=_blocked_projection,
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        tasks = [
            {
                "task_id": "orphan-pre-try",
                "provider_id": "ark",
                "media_type": "video",
                "provider_job_id": "job-pre-try",
                "project_name": "demo",
            }
        ]
        dispatcher = asyncio.create_task(worker._recovery.dispatch_provider_bucket("ark", tasks))

        # Wait for VideoResumeRunner.run pre-try window at await pre_try_gate.wait().
        await asyncio.wait_for(acquired_event.wait(), timeout=1.0)

        # Task has acquired=True, promoted INFLIGHT, but internal handling does not yet own terminal state.
        _active_task_named(worker._slots, "resume-video-orphan-pre-try").cancel()

        # Release gate with CancelledError already on its way.
        pre_try_gate.set()
        await dispatcher

        # Must settle terminal state, never leave running.
        assert queue.interrupted == ["orphan-pre-try"]

    @pytest.mark.asyncio
    async def test_dispatcher_handle_set_after_handle_orphan(self, monkeypatch, staged_project):
        """handle_orphans sets orphan_dispatcher_task afterward."""
        queue = FakeWorkerQueue()
        queue._orphans = [_storyboard_orphan("orphan-x", job_id="job-x")]
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 1}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        block = asyncio.Event()

        async def _gated(_task, *, job_id):
            await block.wait()
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _gated)

        await worker._recovery.handle_orphans()
        assert worker._recovery.orphan_dispatcher_task is not None
        assert not worker._recovery.orphan_dispatcher_task.done()

        block.set()
        await worker._recovery.orphan_dispatcher_task


class TestOrphanScanSelfPreemption:
    """Lease reacquisition after >3×TTL must not orphan tasks still in flight in this process."""

    @pytest.mark.asyncio
    async def test_image_inflight_not_marked_restart_lost(self, monkeypatch):
        """Skip orphans present in this process's image_inflight without [restart_lost]."""
        queue = FakeWorkerQueue()
        queue._orphans = [
            {
                "task_id": "img-active",
                "status": "running",
                "media_type": "image",
                "task_type": "storyboard",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        worker._slots.register("ark", "image", "img-active", fut)

        await worker._recovery.handle_orphans()

        # No dispatcher handle: this process still owns tasks and sends no resumable orphan to dispatcher.
        assert worker._recovery.orphan_dispatcher_task is None
        # Never mark [restart_lost] while this process is still executing it.
        assert "img-active" not in {tid for tid, _ in queue.failed}
        # Cleanup.
        fut.set_result(None)

    @pytest.mark.asyncio
    async def test_video_inflight_not_dispatched_to_resume(self):
        """Skip this process's video_inflight tasks without duplicate resume streams."""
        queue = FakeWorkerQueue()
        queue._orphans = [
            {
                "task_id": "vid-active",
                "status": "running",
                "media_type": "video",
                "task_type": "video",
                "provider_id": "ark",
                "provider_job_id": "ark-job-1",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        worker._slots.register("ark", "video", "vid-active", fut)

        await worker._recovery.handle_orphans()

        # Dispatcher handle was never created: no duplicate dispatch of this process's in-flight task.
        assert worker._recovery.orphan_dispatcher_task is None
        assert "vid-active" not in {tid for tid, _ in queue.failed}
        fut.set_result(None)

    @pytest.mark.asyncio
    async def test_video_pending_also_skipped(self):
        """Skip this process's video_pending tasks waiting on semaphores."""
        queue = FakeWorkerQueue()
        queue._orphans = [
            {
                "task_id": "vid-pending",
                "status": "running",
                "media_type": "video",
                "task_type": "video",
                "provider_id": "ark",
                "provider_job_id": "ark-job-2",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        worker._slots.register("ark", "video", "vid-pending", fut, pending=True)

        await worker._recovery.handle_orphans()

        assert "vid-pending" not in {tid for tid, _ in queue.failed}
        # No dispatcher handle without resumable tasks.
        assert worker._recovery.orphan_dispatcher_task is None
        fut.set_result(None)


class TestOrphanDispatcherNonBlockingOverride:
    """If the old dispatcher runs on lease reacquisition, replace its handle without awaiting (liveness) or cancelling (would interrupt in-flight resume)."""

    @pytest.mark.asyncio
    async def test_old_dispatcher_not_awaited_on_re_scan(self, monkeypatch, staged_project):
        """If an old dispatcher runs for five seconds, another handle_orphans still returns within seconds without blocking."""
        queue = FakeWorkerQueue()
        queue._orphans = [_storyboard_orphan("orphan-x", job_id="job-x")]
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 1}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        block = asyncio.Event()

        async def _gated(_task, *, job_id):
            await block.wait()
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _gated)

        # First scan starts the old dispatcher.
        await worker._recovery.handle_orphans()
        old_dispatcher = worker._recovery.orphan_dispatcher_task
        assert old_dispatcher is not None
        assert not old_dispatcher.done()

        # Second scan simulates lease reacquisition after threshold: replace handle without blocking.
        start = asyncio.get_event_loop().time()
        await worker._recovery.handle_orphans()
        elapsed = asyncio.get_event_loop().time() - start
        # Finish within one second; old dispatcher is unfinished but its handle has been replaced.
        assert elapsed < 1.0, f"重夺时不应阻塞：elapsed={elapsed:.3f}s"
        assert worker._recovery.orphan_dispatcher_task is not old_dispatcher
        # Never cancel old dispatcher and incorrectly interrupt in-flight resume.
        assert not old_dispatcher.cancelled()
        assert not old_dispatcher.done()

        # Cleanup: release gate and await dispatcher.
        block.set()
        new_dispatcher = worker._recovery.orphan_dispatcher_task
        assert new_dispatcher is not None
        await asyncio.gather(old_dispatcher, new_dispatcher, return_exceptions=True)
