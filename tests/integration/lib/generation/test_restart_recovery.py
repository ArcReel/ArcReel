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
    """返回某个容量桶的 inflight 与 pending task id。"""
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
    """按执行体名称从占用台账取得在跑任务。"""
    matches = [task for task in slots.all_active_tasks() if isinstance(task, asyncio.Task) and task.get_name() == name]
    assert len(matches) == 1, f"占用台账里没有唯一名为 {name} 的执行体"
    return matches[0]


class TestRestartRecovery:
    """重启自愈：孤儿分流（ADR 0007）与后台续跑派发。"""

    @pytest.mark.asyncio
    async def test_handle_orphan_running_no_job_id_marks_restart_lost(self, monkeypatch):
        """ADR 0007：running 但无 provider_job_id → [restart_lost]。"""
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
        await worker._recovery.wait_dispatchers()

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
        await worker._recovery.wait_dispatchers()

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
        await worker._recovery.wait_dispatchers()

        assert [t["task_id"] for t in resumed] == ["ref-ready"]
        assert resumed[0]["provider_id"] == "ark"
        assert queue.failed == []

    @pytest.mark.asyncio
    async def test_handle_orphan_image_running_marks_restart_lost(self, worker_db):
        """image 孤儿无 resume 入口 → [restart_lost]，绝不主动 requeue（避免重复扣费）。"""
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
        """render 孤儿判失败、不重排，清理临时文件并保留正式成片与记录。"""
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
        """Grok/Vidu video 孤儿 → [resume_unsupported]（backend 无 resume，绝不重跑）。"""
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
        """重启自愈只扫 running：image / 不可 resume 的视频孤儿落 failed，其余状态的行不被触碰。"""
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
        await worker._recovery.wait_dispatchers()

    @pytest.mark.asyncio
    async def test_handle_orphan_discard_paths_zero_rows_leave_task_untouched(self, worker_db, staged_project):
        """丢弃路径的 mark_failed 命中 0 行（扫描后行已离开 running）：任务保持原状，不被改判为 cancelled。"""
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
        """checkpoint provider 优先于 task 投影列和当前项目解析。

        如果 checkpoint provider 是 Grok（不支持 resume），即便 task advisory 列和当前项目
        已切换成 Ark（支持 resume），孤儿仍应被识别为 non_resumable → [resume_unsupported]，
        而不是去派发 VideoResumeRunner.run 拿旧 job_id 给新 backend 轮询。
        """
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

        assert await task_status(worker_db, "ghost-orphan") == "running"
        assert resumed == []
        await worker._recovery.wait_dispatchers()
        assert queue.failed
        assert queue.failed[0][0] == "ghost-orphan"
        assert "[resume_unsupported_provider]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_resumable_dispatches_resume_runner(self, monkeypatch, staged_project):
        """video resumable provider + 有 job_id → 后台 dispatcher 派发 VideoResumeRunner.run。

        Semaphore-based dispatcher 在 sub-task 内填 inflight、finally pop；本测验证
        dispatched 列表收到目标 task 即可（dispatcher 完成时 inflight 已被清理）。
        """
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
        for _ in range(50):
            await asyncio.sleep(0)
            if dispatched:
                break
        # 让 dispatcher 自身的 task 跑完（避免 unawaited task 警告）
        for t in list(asyncio.all_tasks()):
            name = t.get_name()
            if name in ("orphan-dispatcher",) or name.startswith(("orphan-dispatch-", "resume-video-")):
                with contextlib.suppress(Exception):
                    await t
        assert len(dispatched) == 1
        assert dispatched[0]["task_id"] == "ark-orphan"

    @pytest.mark.asyncio
    async def test_handle_orphan_fast_path_returns_immediately(self, monkeypatch, staged_project):
        """fast path 不阻塞——5 个可 resume orphan + video_max=2，
        resume 入口永不返回，`handle_orphans` 仍须返回，
        实际 dispatch 由后台 dispatcher 处理。"""
        queue = FakeWorkerQueue()
        queue._orphans = [_storyboard_orphan(f"orphan-{i}", job_id=f"job-{i}") for i in range(5)]
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 2}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        release = asyncio.Event()

        async def gated(task, *, job_id):
            await release.wait()
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", gated)
        await asyncio.wait_for(worker._recovery.handle_orphans(), timeout=10)
        release.set()
        await worker._recovery.wait_dispatchers()
        assert len(queue.succeeded) == 5

    @pytest.mark.asyncio
    async def test_handle_orphan_dispatcher_respects_pool_capacity(self, monkeypatch, staged_project):
        """后台 dispatcher 受 video 容量约束分批 promote 进 INFLIGHT，
        任一时刻 inflight 占用 ≤ cap（pending 不消耗 sem，不计入此上限）。"""
        queue = FakeWorkerQueue()
        queue._orphans = [_storyboard_orphan(f"orphan-{i}", job_id=f"job-{i}") for i in range(4)]
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 2}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        started: asyncio.Queue[str] = asyncio.Queue()
        gates = {task["task_id"]: asyncio.Event() for task in queue._orphans}
        active = 0
        peak = 0

        async def gated(task, *, job_id):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            started.put_nowait(task["task_id"])
            await gates[task["task_id"]].wait()
            active -= 1
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", gated)
        async with asyncio.timeout(5):
            await worker._recovery.handle_orphans()
            first = await started.get()
            await started.get()
            gates[first].set()
            await started.get()
            for gate in gates.values():
                gate.set()
            await worker._recovery.wait_dispatchers()
        assert peak == 2
        assert len(queue.succeeded) == 4

    @pytest.mark.asyncio
    async def test_handle_orphan_dispatcher_exits_on_stop_event(self, monkeypatch, staged_project):
        """`_stop_event` 触发时 dispatcher 干净退出，不再 dispatch 剩余 orphan。"""
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
        await asyncio.wait_for(first_dispatched.wait(), timeout=1.0)
        assert dispatched_count == 1
        worker._stop_event.set()
        block_gate.set()
        for _ in range(50):
            await asyncio.sleep(0)
            if dispatched_count == 1 and not any(
                t.get_name() == "orphan-dispatcher" and not t.done() for t in asyncio.all_tasks()
            ):
                break
        assert dispatched_count == 1, f"stop_event 后不应再 dispatch，实际 dispatched={dispatched_count}"


class TestDispatcherFailFastAndPendingTracking:
    """dispatcher fail-fast + pending/inflight 分集合精确容量与 cancel 跟踪。"""

    @pytest.mark.asyncio
    async def test_dispatch_provider_bucket_fail_fast_when_video_max_zero(self, worker_db):
        """video 容量=0 → 直接 mark_failed[resume_unsupported]，不进 Semaphore(0) 死锁。

        容量 0 会先触发一次 reload 兜底：库里这个自定义供应商一个启用模型都没有，
        video lane 因而被真实投影成 0，fail-fast 才落地。
        """
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
        """派发前就判死时那条 pending 的 ApiCall 也要翻 failed（零费用）。

        续跑不开新记账括号，只翻任务不结算调用会在用量报表里留一条永不终态的行；
        「重试下载」尤其明显——它刚把这条调用重开成 pending。
        """
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
        assert row.error_message == f"resume unsupported: provider {provider_key} has no video capacity"
        assert (row.error_code, row.error_params) == (None, None)

    @pytest.mark.asyncio
    async def test_sub_task_registered_in_pending_before_sem_acquire(self, monkeypatch, staged_project):
        """sem=1 + 2 task：第 2 个 sub-task sem 排队期间应以 PENDING 登记在台账。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"ark": {"image": 0, "video": 1}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        started = asyncio.Event()
        gate = asyncio.Event()

        async def _gated(_task, *, job_id):
            started.set()
            await gate.wait()
            return {"job_id": job_id}

        monkeypatch.setattr(stub_executors, "resume", _gated)

        tasks = [storyboard_resume_task(f"orphan-{i}", job_id=f"job-{i}") for i in range(2)]
        dispatcher = asyncio.create_task(worker._recovery.dispatch_provider_bucket("ark", tasks))

        await asyncio.wait_for(started.wait(), timeout=5)
        assert worker._slots.occupied("ark", "video") == 2

        gate.set()
        await dispatcher

    @pytest.mark.asyncio
    async def test_sem_queued_interruption_lands_terminal(self, monkeypatch, staged_project):
        """sem 排队期被打断：_run_one 显式落终态，任务不停在 running。"""
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

        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        _inflight, pending = _phase_ids(worker._slots, "ark", "video")
        assert "orphan-1" in pending

        _active_task_named(worker._slots, "resume-video-orphan-1").cancel()

        gate.set()
        await dispatcher

        assert queue.interrupted == ["orphan-1"]
        assert [tid for tid, _ in queue.succeeded] == ["orphan-0"]

    @pytest.mark.asyncio
    async def test_acquired_pre_process_interruption_lands_terminal(self, staged_project):
        """acquire 后、VideoResumeRunner.run 入 try 之前被打断：_run_one 兜底落终态。

        漏窗就是 VideoResumeRunner.run 内 try 块之前那段 provider 投影 await：打断落在
        那里时内部不会落终态，必须由 _run_one 兜底。任务不带 checkpoint 才会走到投影。
        """
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

        await asyncio.wait_for(acquired_event.wait(), timeout=1.0)

        _active_task_named(worker._slots, "resume-video-orphan-pre-try").cancel()

        pre_try_gate.set()
        await dispatcher

        assert queue.interrupted == ["orphan-pre-try"]


async def test_accepted_retry_survives_disconnect_and_shutdown_with_another_dispatcher(
    concurrent_session_factory, staged_project, monkeypatch
):
    from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
    from lib.generation.generation_queue import GenerationQueue
    from lib.generation.generation_worker import WorkerStoppingError
    from lib.generation.task_failure import encode_failure
    from tests.fakes import bind_safe_session_factory

    worker_db = concurrent_session_factory
    bind_safe_session_factory(monkeypatch, worker_db)
    committed = asyncio.Event()
    return_from_commit = asyncio.Event()
    entered = {tid: asyncio.Event() for tid in ("old", "retry")}
    finish = {tid: asyncio.Event() for tid in entered}
    executions = []

    class Queue(GenerationQueue):
        async def retry_artifact_download(self, task_id):
            row = await super().retry_artifact_download(task_id)
            committed.set()
            await return_from_commit.wait()
            return row

    async def resume(task, *, job_id):
        tid = task["task_id"]
        executions.append(tid)
        entered[tid].set()
        await finish[tid].wait()
        return {"job_id": job_id}

    for tid in entered:
        await seed_running_task(
            worker_db,
            tid,
            provider_id="ark",
            provider_job_id=tid,
            user_id=tid,
            script_file="scripts/episode_1.json",
            execution_checkpoint_json=_worker_storyboard_checkpoint(tid),
        )
    queue = Queue(session_factory=worker_db)
    await queue.mark_task_failed("retry", encode_failure("artifact_download_failed", detail="test"))
    async with worker_db() as session:
        usage = UsageRepository(session)
        call_id = await usage.start_call(
            project_name="demo", call_type="video", model="m", provider="ark", task_id="retry"
        )
        await usage.finish_call(
            call_id, status="failed", settlement=SettlementInput(cost_amount=0), error_message="download failed"
        )
        await session.commit()
    stopped = GenerationWorker(queue=queue, executor=stub_executors.execute, resume_executor=resume)
    stopped.stop_admission()
    with pytest.raises(WorkerStoppingError):
        await stopped.retry_artifact_download("retry")
    assert await task_status(worker_db, "retry") == "failed"
    async with worker_db() as session:
        call = await session.get(ApiCall, call_id)
        assert call is not None
        assert (call.status, call.error_message) == ("failed", "download failed")
    worker = GenerationWorker(queue=queue, executor=stub_executors.execute, resume_executor=resume)
    async with asyncio.timeout(5):
        await worker._recovery.handle_orphans()
        await entered["old"].wait()
        request = asyncio.create_task(worker.retry_artifact_download("retry"))
        await committed.wait()
        await worker._recovery.handle_orphans()
        worker.stop_admission()
        stopping = asyncio.create_task(worker.stop())
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        finish["old"].set()
        assert not stopping.done()
        return_from_commit.set()
        await entered["retry"].wait()
        assert not stopping.done()
        finish["retry"].set()
        await stopping
    assert executions == ["old", "retry"]
    assert await task_status(worker_db, "retry") == "succeeded"
    assert await task_status(worker_db, "old") == "succeeded"


async def test_retry_of_failure_waits_for_old_resume_to_settle(staged_project, worker_db):
    failed = asyncio.Event()
    release = asyncio.Event()
    retry_waiting = asyncio.Event()
    task = _storyboard_orphan("finishing")
    runs = []

    class Queue(FakeWorkerQueue):
        status = "running"

        async def get_task(self, task_id):
            if self.status == "failed":
                retry_waiting.set()
            return {"status": self.status}

        async def mark_task_failed(self, task_id, error):
            self.status = "failed"
            failed.set()
            await release.wait()
            return 1

        async def retry_artifact_download(self, task_id):
            assert release.is_set()
            self.status = "running"
            return task.copy()

    async def resume(task, *, job_id):
        runs.append(job_id)
        if len(runs) == 1:
            raise OSError("download failed")
        return {"job_id": job_id}

    queue = Queue()
    queue._orphans = [task]
    worker = GenerationWorker(queue=queue, executor=stub_executors.execute, resume_executor=resume)
    async with asyncio.timeout(5):
        await worker._recovery.handle_orphans()
        await failed.wait()
        request = asyncio.create_task(worker.retry_artifact_download("finishing"))
        await retry_waiting.wait()
        release.set()
        await request
        await worker.stop()
    assert runs == ["job-1", "job-1"]
    assert queue.succeeded == [("finishing", {"job_id": "job-1"})]


async def test_failed_recheck_waits_for_next_scan(staged_project, worker_db):
    rechecked = asyncio.Event()
    completed = asyncio.Event()
    polls = []
    scans = 0

    class Queue(FakeWorkerQueue):
        async def list_orphan_tasks_on_start(self):
            nonlocal scans
            scans += 1
            return [_storyboard_orphan("transient")]

        async def get_task(self, task_id):
            if scans == 1 and rechecked.is_set():
                raise TimeoutError("database unavailable")
            rechecked.set()
            return await super().get_task(task_id)

    async def resume(task, *, job_id):
        polls.append(scans)
        completed.set()
        return {"job_id": job_id}

    worker = GenerationWorker(queue=Queue(), executor=stub_executors.execute, resume_executor=resume)
    worker.poll_interval = 0.01
    await worker.start()
    try:
        await asyncio.wait_for(completed.wait(), timeout=5)
    finally:
        await worker.stop()
    assert len(polls) == 1
    assert polls[0] > 1
