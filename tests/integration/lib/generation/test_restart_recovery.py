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

        # 派发身份取 checkpoint 的 ark，而不是 task 投影列的 Grok 或 payload 里的当前配置
        assert [t["task_id"] for t in resumed] == ["ref-ready"]
        assert resumed[0]["provider_id"] == "ark"
        assert queue.failed == []

    # ------------------------------------------------------------------
    # handle_orphans：分流补全
    # ------------------------------------------------------------------
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
        assert worker._recovery.orphan_dispatcher_task is None

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

        # 行在扫描之后回到 queued：终态写入的守卫只放行 running，而打断兜底会把 queued 翻成 cancelled
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

        # 用 checkpoint 的 Grok → [resume_unsupported]；若误用 advisory/payload 的 ark → 会派发 resume
        assert await task_status(worker_db, "ghost-orphan") == "running"
        assert resumed == []
        assert worker._recovery.orphan_dispatcher_task is None
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
        # 等后台 dispatcher（含 orphan-dispatcher + provider 桶 sub-task）完成
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

        # 让 resume 执行入口 block 住——验证 fast path 不等它完成
        async def _block_forever(_task, *, job_id):
            await asyncio.Event().wait()

        monkeypatch.setattr(stub_executors, "resume", _block_forever)

        # 超时只是防死锁护栏：fast path 若等 resume 完成会永远挂住，不是耗时断言。
        await asyncio.wait_for(worker._recovery.handle_orphans(), timeout=10)
        assert worker._recovery.orphan_dispatcher_task is not None
        assert not worker._recovery.orphan_dispatcher_task.done()

        # 清理后台 dispatcher，避免 unawaited task 警告
        for t in list(asyncio.all_tasks()):
            if t.get_name() in ("orphan-dispatcher", "orphan-dispatch-ark"):
                t.cancel()
            if t.get_name().startswith("resume-video-"):
                t.cancel()
        await asyncio.sleep(0)

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

        # 用 controlled event 让 resume 任务可控完成；同时记录每次 dispatch 时的占用
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
        # 让 dispatcher 把前 2 个 promote 进 INFLIGHT（其余 2 个 PENDING 排队）
        for _ in range(20):
            await asyncio.sleep(0)
            if _inflight() >= 2:
                break
        assert _inflight() == 2, f"应只有 2 个 inflight，实际 {_inflight()}"

        # 释放一个已 inflight 的 task：其 _run_one finally 会 release 占用 + sem，
        # 腾出名额让 dispatcher 把第 3 个 promote 进来；主循环每 cycle 的 drain_finished
        # 在生产中清理，这里调一次模拟（对 dispatcher sub-task 是 no-op，占用由 finally 释放）。
        gates[entered[0]].set()
        await asyncio.sleep(0)
        worker._slots.drain_finished()
        for _ in range(20):
            await asyncio.sleep(0)
            if _inflight() >= 2:
                break
        # 任一时刻 inflight 都不超过容量 2
        assert _inflight() <= 2
        # 每次 promote 进 VideoResumeRunner.run 时的 inflight 快照都 ≤ 容量（核心回归点）
        assert snapshots, "未采集到 inflight 快照"
        assert all(s <= 2 for s in snapshots), f"inflight 快照越过容量上限: {snapshots}"

        # 收尾：释放所有 gate，等 dispatcher 结束
        for gate in gates.values():
            gate.set()
        for _ in range(50):
            await asyncio.sleep(0)
        # 清理可能的残余
        for t in list(asyncio.all_tasks()):
            name = t.get_name()
            if name.startswith(("orphan-", "resume-video-")) and not t.done():
                t.cancel()
        await asyncio.sleep(0)

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
        # 等第一个 orphan 进 inflight
        await asyncio.wait_for(first_dispatched.wait(), timeout=1.0)
        assert dispatched_count == 1
        # 触发停机
        worker._stop_event.set()
        block_gate.set()
        # 让 dispatcher 看到 stop_event 退出（不再 dispatch 剩余 2 个）
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
                project_name="demo", call_type="video", model="m", task_id="retry-1"
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
        # 判死时没有异常对象可分类，只有一段原文：落 error_message，不落码
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
        # pending 计入容量：占用=2 ≥ cap=1 → 无空位，主循环不会超额 claim
        assert worker._slots.has_room("ark", "video", 1) is False

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

        # 等第 1 个 task 进入 VideoResumeRunner.run（占住 sem），第 2 个还在 sem 排队
        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        _inflight, pending = _phase_ids(worker._slots, "ark", "video")
        assert "orphan-1" in pending

        # 打断 sem 排队中的 orphan-1
        _active_task_named(worker._slots, "resume-video-orphan-1").cancel()

        # 让 dispatcher 跑完
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

        # 等 VideoResumeRunner.run 进入空窗（await pre_try_gate.wait() 期间）
        await asyncio.wait_for(acquired_event.wait(), timeout=1.0)

        # 现在 task 在 acquired=True（已 promote 为 INFLIGHT）状态，但内部还没接管终态
        _active_task_named(worker._slots, "resume-video-orphan-pre-try").cancel()

        # gate 放开（CancelledError 已经在路上）
        pre_try_gate.set()
        await dispatcher

        # 必须落终态，不能停在 running
        assert queue.interrupted == ["orphan-pre-try"]

    @pytest.mark.asyncio
    async def test_dispatcher_handle_set_after_handle_orphan(self, monkeypatch, staged_project):
        """handle_orphans 后 orphan_dispatcher_task 应被设置。"""
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
    """lease flap > 3×TTL 重夺时，本进程仍 inflight 的 task 不应被当孤儿处理。"""

    @pytest.mark.asyncio
    async def test_image_inflight_not_marked_restart_lost(self, monkeypatch):
        """本进程 image_inflight 含 task → 孤儿扫描应跳过，不标 [restart_lost]。"""
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

        # dispatcher 句柄不应被设置（本进程仍在跑，无 resumable 任务进 dispatcher）
        assert worker._recovery.orphan_dispatcher_task is None
        # 不应被标 [restart_lost] —— 本进程还在跑
        assert "img-active" not in {tid for tid, _ in queue.failed}
        # 清理
        fut.set_result(None)

    @pytest.mark.asyncio
    async def test_video_inflight_not_dispatched_to_resume(self):
        """本进程 video_inflight 含 task → 孤儿扫描应跳过，不启动重复 resume 流。"""
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

        # dispatcher 句柄从未被创建 → 本进程 inflight 的 task 没有被重复 dispatch
        assert worker._recovery.orphan_dispatcher_task is None
        assert "vid-active" not in {tid for tid, _ in queue.failed}
        fut.set_result(None)

    @pytest.mark.asyncio
    async def test_video_pending_also_skipped(self):
        """本进程 video_pending（sem 排队中）含 task → 孤儿扫描应跳过。"""
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
        # dispatcher 句柄不应被设置（无 resumable 任务进 dispatcher）
        assert worker._recovery.orphan_dispatcher_task is None
        fut.set_result(None)


class TestOrphanDispatcherNonBlockingOverride:
    """lease 重夺时旧 dispatcher 仍在跑：本轮直接覆盖句柄，不 await（liveness）也不 cancel
    （避免错误中断 in-flight resume）。"""

    @pytest.mark.asyncio
    async def test_old_dispatcher_not_awaited_on_re_scan(self, monkeypatch, staged_project):
        """旧 dispatcher 跑 5s 时，再次进 handle_orphans 应秒级返回（不阻塞）。"""
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

        # 第 1 次扫描：启动旧 dispatcher
        await worker._recovery.handle_orphans()
        old_dispatcher = worker._recovery.orphan_dispatcher_task
        assert old_dispatcher is not None
        assert not old_dispatcher.done()

        # 第 2 次扫描（模拟 lease flap 超阈值后重夺）：应直接覆盖句柄、不阻塞
        start = asyncio.get_event_loop().time()
        await worker._recovery.handle_orphans()
        elapsed = asyncio.get_event_loop().time() - start
        # 应在 1s 内完成；旧 dispatcher 仍未 done 但句柄已被新的覆盖
        assert elapsed < 1.0, f"重夺时不应阻塞：elapsed={elapsed:.3f}s"
        assert worker._recovery.orphan_dispatcher_task is not old_dispatcher
        # 旧 dispatcher 未被 cancel——in-flight resume 不应被错误中断
        assert not old_dispatcher.cancelled()
        assert not old_dispatcher.done()

        # 清理：放开 gate 让 dispatcher 完成
        block.set()
        new_dispatcher = worker._recovery.orphan_dispatcher_task
        assert new_dispatcher is not None
        await asyncio.gather(old_dispatcher, new_dispatcher, return_exceptions=True)
