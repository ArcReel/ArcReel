import asyncio
from typing import Any

import pytest

from lib.generation_worker import (
    _ORPHAN_RESCAN_LEASE_LOST_MULT,
    DEFAULT_PROVIDER,
    CapacityTable,
    GenerationWorker,
    SlotTable,
    _extract_provider,
    _read_int_env,
)


def _cap(limits: dict[str, dict[str, int]] | None = None, *, image: int = 5, video: int = 3) -> CapacityTable:
    """构造一张容量表：显式 per-provider 上限 + 懒默认（默认 image=5 / video=3）。"""
    return CapacityTable(_limits=limits or {}, _defaults={"image": image, "video": video})


def _phase_ids(slots: SlotTable, provider: str, media: str) -> tuple[set[str], set[str]]:
    """白盒辅助：返回某 (provider, media) bucket 的 (inflight_ids, pending_ids)。

    等价于旧测对 ``pool.video_inflight`` / ``pool.video_pending`` 的直接读取——
    SlotTable 用 ``_Occupant.pending`` 标志区分两相，对外不暴露按相计数。
    """
    bucket = slots._slots.get((provider, media), {})
    inflight = {tid for tid, occ in bucket.items() if not occ.pending}
    pending = {tid for tid, occ in bucket.items() if occ.pending}
    return inflight, pending


class _FakeQueue:
    def __init__(self, *, succeeded_rows: int = 1, failed_rows: int = 1):
        self.released = False
        self.succeeded = []
        self.failed = []
        self.cancelled = []
        self._lease_calls = 0
        self._succeeded_rows = succeeded_rows
        self._failed_rows = failed_rows
        self._orphans: list[dict] = []

    async def acquire_or_renew_worker_lease(self, name, owner_id, ttl_seconds):
        self._lease_calls += 1
        return True

    async def release_worker_lease(self, name, owner_id):
        self.released = True

    async def requeue_running_tasks(self):
        return 0

    async def list_orphan_tasks_on_start(self):
        return self._orphans

    async def claim_next_task(self, media_type, **_kwargs):
        return None

    async def mark_task_succeeded(self, task_id, result):
        self.succeeded.append((task_id, result))
        return self._succeeded_rows

    async def mark_task_failed(self, task_id, error):
        self.failed.append((task_id, error))
        return self._failed_rows

    async def mark_task_cancelled(self, task_id, *, cancelled_by="user"):
        self.cancelled.append((task_id, cancelled_by))
        return 1


class TestReadIntEnv:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ARCREEL_INT", raising=False)
        assert _read_int_env("ARCREEL_INT", 3, minimum=1) == 3

    def test_default_when_bad(self, monkeypatch):
        monkeypatch.setenv("ARCREEL_INT", "bad")
        assert _read_int_env("ARCREEL_INT", 3, minimum=1) == 3

    def test_minimum_enforced(self, monkeypatch):
        monkeypatch.setenv("ARCREEL_INT", "0")
        assert _read_int_env("ARCREEL_INT", 3, minimum=2) == 2


def _patch_pm(monkeypatch, project: dict | None):
    """让 worker 的 get_project_manager().load_project 返回给定 project dict。"""
    monkeypatch.setattr(
        "lib.config.resolver.get_project_manager",
        lambda: type("PM", (), {"load_project": lambda self, name: project or {}})(),
    )


class TestExtractProvider:
    """_extract_provider 是解析链的薄投影：按 task_type 派发，取 .provider_id。"""

    async def test_video_payload_provider(self):
        """payload 携带历史 video_provider → 投影直接取到（payload 层短路，无需 DB）。"""
        task = {"payload": {"video_provider": "ark"}, "task_type": "video"}
        assert await _extract_provider(task) == "ark"

    async def test_image_payload_provider(self):
        """payload 携带历史 image_provider → 投影取到。"""
        task = {"payload": {"image_provider": "gemini-vertex"}, "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    async def test_default_when_unresolvable(self):
        """无 project、无 payload、全局未配供应商 → 回退 DEFAULT_PROVIDER（仅供限流）。"""
        task = {"payload": {}}
        assert await _extract_provider(task) == DEFAULT_PROVIDER

    async def test_project_level_video_backend(self, monkeypatch):
        """项目级 video_backend 优先于全局默认。"""
        _patch_pm(monkeypatch, {"video_backend": "ark/seedance-1-0-pro"})
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}
        assert await _extract_provider(task) == "ark"

    async def test_project_level_image_t2i(self, monkeypatch):
        """image 投影按代表性 capability=t2i 取项目级 image_provider_t2i。"""
        _patch_pm(monkeypatch, {"image_provider_t2i": "gemini-vertex/imagen-3"})
        task = {"payload": {}, "project_name": "demo", "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    async def test_reference_video_routes_to_video_lane(self, monkeypatch):
        """reference_video task_type 必须按 video lane 解析 video_backend，而非 image 槽。

        项目同时配置了不同 provider 的 video_backend（ark）与 image_provider_t2i
        （gemini-vertex）。reference_video 属于 video lane，认领期 provider 投影须取 ark；
        若误判为 image lane（历史上 task_type != "video" 即读 image 槽），会取到纯图片
        供应商，导致 worker 在 video 通道以 video_max==0 直接把任务标记
        「供应商不支持 video 生成」。"""
        _patch_pm(
            monkeypatch,
            {
                "video_backend": "ark/seedance-1-0-pro",
                "image_provider_t2i": "gemini-vertex/imagen-3",
            },
        )
        task = {"payload": {}, "project_name": "demo", "task_type": "reference_video"}
        assert await _extract_provider(task) == "ark"

    async def test_payload_provider_takes_precedence_over_project(self, monkeypatch):
        """payload 历史 provider 优先于项目级。"""
        _patch_pm(monkeypatch, {"video_backend": "grok/grok-imagine-video"})
        task = {"payload": {"video_provider": "ark"}, "project_name": "demo", "task_type": "video"}
        assert await _extract_provider(task) == "ark"

    async def test_deleted_project_load_failure_falls_back_not_raises(self, monkeypatch):
        """指向已删除/不可读项目的历史任务：load_project 抛错也须回退 DEFAULT_PROVIDER，
        绝不冒泡阻断认领循环（否则一个坏任务会拖垮整个 worker）。"""

        def _raising_pm():
            def _load(self, name):
                raise FileNotFoundError(name)

            return type("PM", (), {"load_project": _load})()

        monkeypatch.setattr("lib.config.resolver.get_project_manager", _raising_pm)
        task = {"payload": {}, "project_name": "deleted-proj", "task_type": "video"}
        assert await _extract_provider(task) == DEFAULT_PROVIDER


class TestExtractProviderAlignsWithExecution:
    """M5 投影对齐：worker 取到的 provider_id 与执行层解析在同一 project/payload 下一致。"""

    async def test_image_alignment(self, monkeypatch):
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        project = {"image_provider_t2i": "openai/gen-1", "image_provider_i2i": "openai/edit-1"}
        _patch_pm(monkeypatch, project)
        task = {"payload": {}, "project_name": "demo", "task_type": "storyboard"}

        worker_provider = await _extract_provider(task)
        resolved = await ConfigResolver(async_session_factory).resolve_image_backend(project, {}, capability="t2i")
        assert worker_provider == resolved.provider_id == "openai"

    async def test_video_alignment(self, monkeypatch):
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        project = {"video_backend": "ark/seedance-1-0-pro"}
        _patch_pm(monkeypatch, project)
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}

        worker_provider = await _extract_provider(task)
        resolved = await ConfigResolver(async_session_factory).resolve_video_backend(project, {})
        assert worker_provider == resolved.provider_id == "ark"


class TestCapacityTable:
    """容量表：provider × media_type → 上限，三态 get + reload 只换数字。"""

    def test_get_three_states(self):
        table = CapacityTable(
            _limits={"known": {"image": 4, "video": 0}},
            _defaults={"image": 5, "video": 3},
        )
        # 已知 + lane 在表 → 登记值（含 0=不支持该 lane）
        assert table.get("known", "image") == 4
        assert table.get("known", "video") == 0
        # 已知 + lane 不在表 → 0
        assert table.get("known", "audio") == 0
        # 完全未知 → 默认，且纯查询不写回
        assert table.get("unknown", "image") == 5
        assert table.get("unknown", "video") == 3
        assert "unknown" not in table._limits

    def test_replace_only_swaps_numbers(self):
        table = CapacityTable(_limits={"p": {"image": 3, "video": 3}}, _defaults={"image": 5, "video": 3})
        table.replace({"p": {"image": 9, "video": 1}})
        assert table.get("p", "image") == 9
        assert table.get("p", "video") == 1
        # 默认不受 replace 影响
        assert table.get("unknown", "image") == 5

    def test_from_env_derives_support_and_defaults(self, monkeypatch):
        from lib.config.registry import PROVIDER_REGISTRY

        monkeypatch.delenv("IMAGE_MAX_WORKERS", raising=False)
        monkeypatch.delenv("VIDEO_MAX_WORKERS", raising=False)
        table = CapacityTable.from_env()
        for pid, meta in PROVIDER_REGISTRY.items():
            assert pid in table._limits
            assert table.get(pid, "image") == (5 if "image" in meta.media_types else 0)
            assert table.get(pid, "video") == (3 if "video" in meta.media_types else 0)

    def test_from_env_reads_env(self, monkeypatch):
        monkeypatch.setenv("IMAGE_MAX_WORKERS", "7")
        monkeypatch.setenv("VIDEO_MAX_WORKERS", "2")
        table = CapacityTable.from_env()
        # 未知 provider 的懒默认跟随 env
        assert table.get("unknown", "image") == 7
        assert table.get("unknown", "video") == 2

    async def test_from_db_known_providers_and_unsupported_lanes_zero(self):
        """from_db：所有 registry provider 已知，不支持的 lane 强制 0。

        只断言与 config 数值无关的确定不变量（已知 + 不支持→0），避免本地 dev DB
        的 config 覆盖导致 env 耦合。
        """
        from lib.config.registry import PROVIDER_REGISTRY

        table = await CapacityTable.from_db()
        for pid, meta in PROVIDER_REGISTRY.items():
            assert pid in table._limits
            if "image" not in meta.media_types:
                assert table.get(pid, "image") == 0
            if "video" not in meta.media_types:
                assert table.get(pid, "video") == 0

    @staticmethod
    def _stub_from_db_sources(monkeypatch, configs: dict[str, dict[str, str]]) -> None:
        """把 from_db 的两个数据源（provider config / 自定义供应商）替换为内存数据。"""
        from unittest.mock import AsyncMock, MagicMock

        for env in ("IMAGE_MAX_WORKERS", "VIDEO_MAX_WORKERS", "AUDIO_MAX_WORKERS"):
            monkeypatch.delenv(env, raising=False)

        class _FakeSessionCtx:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, *exc_info):
                return False

        monkeypatch.setattr("lib.db.safe_session_factory", lambda: _FakeSessionCtx())
        monkeypatch.setattr(
            "lib.config.service.ConfigService.get_all_provider_configs",
            AsyncMock(return_value=configs),
        )
        monkeypatch.setattr(
            "lib.db.repositories.custom_provider_repo.CustomProviderRepository.list_providers_with_models",
            AsyncMock(return_value=[]),
        )

    @pytest.mark.parametrize("dirty", ["", "3.7", "abc"])
    async def test_from_db_dirty_value_falls_back_per_key(self, monkeypatch, caplog, dirty):
        """单个 key 的存量脏值只回退该 key 默认值并告警，不拖垮整表加载。"""
        import logging

        self._stub_from_db_sources(
            monkeypatch,
            {
                "ark": {"image_max_workers": dirty, "video_max_workers": "2"},
                "gemini-aistudio": {"image_max_workers": "7"},
            },
        )

        with caplog.at_level(logging.WARNING, logger="lib.generation_worker"):
            table = await CapacityTable.from_db()

        # 脏 key 回退 env 默认值（image=5）并告警
        assert table.get("ark", "image") == 5
        assert "image_max_workers" in caplog.text
        # 同 provider 其余合法 key、其他 provider 的配置正常生效
        assert table.get("ark", "video") == 2
        assert table.get("gemini-aistudio", "image") == 7

    async def test_from_db_negative_value_clamps_to_zero(self, monkeypatch, caplog):
        """可解析的负数沿用 clamp 语义（→0），不视为脏值回退默认，但留告警可观测。"""
        import logging

        self._stub_from_db_sources(monkeypatch, {"ark": {"video_max_workers": "-1"}})

        with caplog.at_level(logging.WARNING, logger="lib.generation_worker"):
            table = await CapacityTable.from_db()

        assert table.get("ark", "video") == 0
        assert "video_max_workers" in caplog.text


class TestGenerationWorker:
    @pytest.mark.asyncio
    async def test_process_task_success_and_failure(self, monkeypatch):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _fake_execute(task):
            return {"ok": task["task_id"]}

        monkeypatch.setattr(
            "server.services.generation_tasks.execute_generation_task",
            _fake_execute,
        )
        await worker._process_task({"task_id": "t1"})
        assert queue.succeeded == [("t1", {"ok": "t1"})]

        async def _raise(_task):
            raise RuntimeError("boom")

        monkeypatch.setattr("server.services.generation_tasks.execute_generation_task", _raise)
        await worker._process_task({"task_id": "t2"})
        assert queue.failed and queue.failed[0][0] == "t2"

    @pytest.mark.asyncio
    async def test_process_task_cancelled_error_marks_cancelled(self, monkeypatch):
        """ADR 0006: asyncio.CancelledError 走 finally → mark_cancelled。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _cancelled(_task):
            raise asyncio.CancelledError

        monkeypatch.setattr("server.services.generation_tasks.execute_generation_task", _cancelled)
        with pytest.raises(asyncio.CancelledError):
            await worker._process_task({"task_id": "tc"})
        assert queue.cancelled and queue.cancelled[0][0] == "tc"

    @pytest.mark.asyncio
    async def test_process_task_zero_rows_succeeded_falls_through_to_cancelled(self, monkeypatch):
        """ADR 0006 0-rows-cancelled 协议：mark_succeeded 返回 0 时 finally 调 mark_cancelled。"""
        queue = _FakeQueue(succeeded_rows=0)
        worker = GenerationWorker(queue=queue)

        async def _ok(_task):
            return {"result": "ok"}

        monkeypatch.setattr("server.services.generation_tasks.execute_generation_task", _ok)
        await worker._process_task({"task_id": "t0rows"})
        # mark_succeeded 调过但返回 0 rows → mark_cancelled 兜底
        assert queue.succeeded == [("t0rows", {"result": "ok"})]
        assert queue.cancelled and queue.cancelled[0][0] == "t0rows"

    @pytest.mark.asyncio
    async def test_request_cancel_signals_inflight_task(self):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _long():
            await asyncio.sleep(10)

        t = asyncio.create_task(_long())
        worker._slots.register("test", "video", "tid", t)

        assert worker.request_cancel("tid") is True
        # asyncio 会在下次调度时 cancel
        await asyncio.sleep(0)
        assert t.cancelled() or t.done()

        # 不在 inflight → False
        assert worker.request_cancel("ghost") is False

    @pytest.mark.asyncio
    async def test_drain_finished_tasks_absorbs_cancelled_error(self):
        """取消的 inflight task 被 drain：不抛、从台账移除，并 drain 端兜底 mark_cancelled。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _long():
            await asyncio.sleep(10)

        t = asyncio.create_task(_long())
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)  # 驱动到 done(cancelled)，吞掉取消结果
        assert t.cancelled()
        worker._slots.register("test", "video", "tid", t)

        # 同步判定 .cancelled()：不 await，不抛 CancelledError，task 已被 drain 移除。
        await worker._drain_finished_tasks()
        assert worker._slots.occupied("test", "video") == 0
        # 子任务来不及自落终态时，drain 端兜底 mark_cancelled。
        assert queue.cancelled and queue.cancelled[0][0] == "tid"

    @pytest.mark.asyncio
    async def test_drain_finished_tasks_drains_success_and_failure(self):
        """非取消路径：成功 task 走 .result() 无异常，失败 task 走 except 分支，均不触发兜底取消。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _ok():
            return "done"

        async def _boom():
            raise RuntimeError("boom")

        ok_t = asyncio.create_task(_ok())
        boom_t = asyncio.create_task(_boom())
        await asyncio.gather(ok_t, boom_t, return_exceptions=True)
        worker._slots.register("test", "image", "ok", ok_t)
        worker._slots.register("test", "video", "boom", boom_t)

        await worker._drain_finished_tasks()  # 不抛
        assert worker._slots.occupied("test", "image") == 0
        assert worker._slots.occupied("test", "video") == 0
        # 非取消任务不应触发 drain 兜底 mark_cancelled
        assert queue.cancelled == []

    @pytest.mark.asyncio
    async def test_drain_fallback_mark_cancelled_failure_does_not_propagate(self):
        """drain 端兜底 mark_cancelled 自身抛错时只 warning，不冒泡（不挂掉主循环）。"""

        class _RaisingQueue(_FakeQueue):
            async def mark_task_cancelled(self, task_id, *, cancelled_by="user"):  # type: ignore[override]
                raise RuntimeError("db down")

        queue = _RaisingQueue()
        worker = GenerationWorker(queue=queue)

        async def _long():
            await asyncio.sleep(10)

        t = asyncio.create_task(_long())
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)  # 驱动到 done(cancelled)，吞掉取消结果
        assert t.cancelled()
        worker._slots.register("test", "video", "tid", t)

        # mark_cancelled 抛错被 except 吞掉，drain 不抛、task 仍被移除
        await worker._drain_finished_tasks()
        assert worker._slots.occupied("test", "video") == 0

    @pytest.mark.asyncio
    async def test_drain_marks_cancelled_when_cancel_hits_before_process_task_try(self, monkeypatch):
        """取消落在 _process_task 进 try 之前（_extract_provider await）：drain 端兜底落终态。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        in_extract = asyncio.Event()

        async def _blocking_extract(_task):
            in_extract.set()
            await asyncio.sleep(10)  # 停在入口解析，模拟 cancel 落在 _process_task 的 try 之前
            return "test"

        monkeypatch.setattr("lib.generation_worker._extract_provider", _blocking_extract)

        async def _execute(_task):
            raise AssertionError("execute 不应被调用：cancel 在 _extract_provider 阶段就到")

        monkeypatch.setattr("server.services.generation_tasks.execute_generation_task", _execute)

        t = asyncio.create_task(
            worker._process_task({"task_id": "tid", "media_type": "video"}),
            name="generation-video-tid",
        )
        worker._slots.register("test", "video", "tid", t)

        await worker.start()
        await in_extract.wait()  # 确保停在 _extract_provider（try 之前）
        assert worker.request_cancel("tid") is True
        await asyncio.sleep(0.1)

        # _process_task 没机会 mark（cancel 在 try 之前）→ drain 端兜底 mark_cancelled
        assert queue.cancelled and queue.cancelled[0][0] == "tid"
        # 主循环仍存活
        assert worker._main_task is not None and not worker._main_task.done()

        await asyncio.wait_for(worker.stop(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_run_loop_survives_inflight_task_cancellation(self, monkeypatch):
        """用户取消运行中的任务：任务 mark_cancelled，但 worker 主循环不退出。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        started = asyncio.Event()

        async def _block(_task):
            started.set()
            await asyncio.sleep(10)  # 模拟长时间运行的生成任务

        monkeypatch.setattr("server.services.generation_tasks.execute_generation_task", _block)

        t = asyncio.create_task(
            worker._process_task({"task_id": "tid", "media_type": "video"}),
            name="generation-video-tid",
        )
        worker._slots.register("test", "video", "tid", t)

        await worker.start()
        await started.wait()  # 确保 _process_task 已进入 execute（_extract_provider 已完成）

        assert worker.request_cancel("tid") is True
        await asyncio.sleep(0.1)  # 跨多个 loop tick：取消落地 + drain

        # 任务被正确 mark_cancelled
        assert queue.cancelled and queue.cancelled[0][0] == "tid"
        # 主循环吸收 CancelledError 后仍存活
        assert worker._main_task is not None and not worker._main_task.done()

        await asyncio.wait_for(worker.stop(), timeout=2.0)
        assert queue.released

    @pytest.mark.asyncio
    async def test_run_loop_survives_consecutive_cancellations_and_keeps_claiming(self, monkeypatch):
        """连续取消多个 inflight 任务后，主循环仍存活并能继续 claim 新任务。"""

        class _GatedQueue(_FakeQueue):
            def __init__(self):
                super().__init__()
                self.allow_new = False
                self.new_dispatched = False

            async def claim_next_task(self, media_type, **_kwargs):  # type: ignore[override]
                if self.allow_new and not self.new_dispatched and media_type == "image":
                    self.new_dispatched = True
                    return {
                        "task_id": "fresh-img",
                        "task_type": "gen_image",
                        "media_type": "image",
                        "payload": {"image_provider": "gemini-aistudio"},
                    }
                return None

        queue = _GatedQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({"gemini-aistudio": {"image": 5, "video": 3}}))
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        release = asyncio.Event()
        entered: set[str] = set()

        async def _block(task):
            entered.add(task["task_id"])
            await release.wait()
            return {"ok": True}

        monkeypatch.setattr("server.services.generation_tasks.execute_generation_task", _block)

        vid_ids = ["vid-0", "vid-1", "vid-2"]
        for tid in vid_ids:
            t = asyncio.create_task(
                worker._process_task({"task_id": tid, "media_type": "video"}),
                name=f"generation-video-{tid}",
            )
            worker._slots.register("gemini-aistudio", "video", tid, t)

        await worker.start()
        while not set(vid_ids) <= entered:  # 等三个任务都进入 execute
            await asyncio.sleep(0)

        for tid in vid_ids:
            assert worker.request_cancel(tid) is True
        await asyncio.sleep(0.1)

        # 主循环存活 + 三个任务都 mark_cancelled
        assert worker._main_task is not None and not worker._main_task.done()
        assert set(vid_ids) <= {c[0] for c in queue.cancelled}

        # 取消后仍能 claim 并 dispatch 新任务
        queue.allow_new = True
        await asyncio.sleep(0.1)
        assert queue.new_dispatched is True
        assert worker._slots.find_by_task("fresh-img") is not None

        # 收尾：放行 fresh-img 后正常停机
        release.set()
        await asyncio.wait_for(worker.stop(), timeout=2.0)
        assert queue.released

    @pytest.mark.asyncio
    async def test_stop_event_exits_loop_even_with_cancellations(self, monkeypatch):
        """语义对比：单任务取消不退出，但显式 stop（set stop event）必须让 worker 退出。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        started = asyncio.Event()

        async def _block(_task):
            started.set()
            await asyncio.sleep(10)

        monkeypatch.setattr("server.services.generation_tasks.execute_generation_task", _block)

        t = asyncio.create_task(
            worker._process_task({"task_id": "tid", "media_type": "video"}),
            name="generation-video-tid",
        )
        worker._slots.register("test", "video", "tid", t)

        await worker.start()
        await started.wait()
        assert worker.request_cancel("tid") is True
        await asyncio.sleep(0.05)
        # 取消阶段：主循环仍存活
        assert worker._main_task is not None and not worker._main_task.done()
        assert queue.cancelled and queue.cancelled[0][0] == "tid"

        # 显式 stop → worker 正常退出
        await asyncio.wait_for(worker.stop(), timeout=2.0)
        assert worker._main_task is None
        assert queue.released

    @pytest.mark.asyncio
    async def test_handle_orphan_cancelling_marks_cancelled(self, monkeypatch):
        """ADR 0007：orphan cancelling 状态 → mark_cancelled。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "orphan-cancelling",
                "status": "cancelling",
                "provider_id": None,
                "provider_job_id": None,
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue)
        await worker._handle_orphan_tasks_on_start()
        assert queue.cancelled and queue.cancelled[0][0] == "orphan-cancelling"

    @pytest.mark.asyncio
    async def test_handle_orphan_running_no_job_id_marks_restart_lost(self, monkeypatch):
        """ADR 0007：running 但无 provider_job_id → [restart_lost]。"""
        queue = _FakeQueue()
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
        worker = GenerationWorker(queue=queue)
        await worker._handle_orphan_tasks_on_start()
        assert queue.failed and queue.failed[0][0] == "orphan-lost"
        assert "[restart_lost_no_job_id]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_start_stop_run_loop_releases_lease(self):
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        await worker.start()
        await asyncio.sleep(0.05)
        await worker.stop()

        assert queue.released
        assert worker._main_task is None

    @pytest.mark.asyncio
    async def test_claim_tasks_dispatches_to_correct_pool(self, monkeypatch):
        """Tasks are dispatched to the correct provider slot."""

        class _ClaimableQueue(_FakeQueue):
            def __init__(self):
                super().__init__()
                self._tasks = [
                    {
                        "task_id": "img1",
                        "task_type": "gen_image",
                        "media_type": "image",
                        "payload": {"image_provider": "gemini-aistudio"},
                    },
                    {
                        "task_id": "vid1",
                        "task_type": "gen_video",
                        "media_type": "video",
                        "payload": {"video_provider": "ark"},
                    },
                ]

            async def claim_next_task(self, media_type, **_kwargs):  # type: ignore[override]
                for i, t in enumerate(self._tasks):
                    if t["media_type"] == media_type:
                        return self._tasks.pop(i)
                return None

        queue = _ClaimableQueue()
        worker = GenerationWorker(
            queue=queue,
            capacity=_cap({"gemini-aistudio": {"image": 3, "video": 2}, "ark": {"image": 0, "video": 2}}),
        )

        async def _fake_execute(task):
            return {"ok": True}

        monkeypatch.setattr(
            "server.services.generation_tasks.execute_generation_task",
            _fake_execute,
        )

        claimed = await worker._claim_tasks()
        assert claimed
        assert worker._slots.occupied("gemini-aistudio", "image") == 1
        assert worker._slots.find_by_task("img1") is not None
        assert worker._slots.occupied("ark", "video") == 1
        assert worker._slots.find_by_task("vid1") is not None

        # Wait for tasks to complete
        await asyncio.gather(*worker._slots.all_active_tasks(), return_exceptions=True)

    # ------------------------------------------------------------------
    # _pool_full_providers
    # ------------------------------------------------------------------
    def test_pool_full_providers_sources_from_occupancy_with_cap_guard(self):
        """池满黑名单源 = 有占用的 provider；cap==0 守卫短路降级 lane 的在跑占用。

        新设计下黑名单源是 ``occupied_providers``，无占用的 provider 根本不会到达
        ``cap > 0`` 守卫——所以守卫唯一可达场景是「有占用 + cap==0」（运行中 reload
        把某 lane 降级为不支持）。这类 provider 必须 *不* 进黑名单：其在跑任务靠主循环
        drain，新任务走 ``_claim_tasks`` 的 cap==0 fail-fast，而非被 SQL 静默 drop。
        """
        loop = asyncio.new_event_loop()
        dummy = loop.create_future()
        dummy.set_result(None)

        worker = GenerationWorker(
            queue=_FakeQueue(),
            capacity=_cap(
                {
                    "full": {"image": 1, "video": 0},  # cap=1 占 1 → 满
                    "roomy": {"image": 2, "video": 0},  # cap=2 占 1 → 有空
                    "downgraded": {"image": 0, "video": 0},  # 占 1 但 cap=0（被 reload 降级）
                }
            ),
        )
        # 三个 provider 在 image lane 都有占用 → 都会进入 occupied_providers，都到达守卫
        worker._slots.register("full", "image", "t-full", dummy)
        worker._slots.register("roomy", "image", "t-roomy", dummy)
        worker._slots.register("downgraded", "image", "t-down", dummy)

        full_image = worker._pool_full_providers("image")
        assert "full" in full_image, "cap>0 且占满应进黑名单"
        assert "roomy" not in full_image, "cap>0 但有空不进黑名单"
        # 关键：守卫真正被执行——downgraded 有占用 → 到达 cap>0 守卫 → 短路排除。
        # 若直译旧测（无占用的 cap==0 provider），它根本不在 occupied_providers 里，
        # 守卫一行都不会跑，测试会"假通过"。
        assert "downgraded" not in full_image, "cap==0 守卫必须短路，不让降级 lane 进黑名单"

        # video lane 无任何占用 → 黑名单为空（无占用就不可能"满"）
        assert worker._pool_full_providers("video") == frozenset()
        loop.close()

    # ------------------------------------------------------------------
    # _handle_orphan_tasks_on_start：分流补全
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_handle_orphan_image_running_marks_restart_lost(self, monkeypatch):
        """image 孤儿无 resume 入口 → [restart_lost]，绝不主动 requeue（避免重复扣费）。"""
        queue = _FakeQueue()
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
        worker = GenerationWorker(queue=queue)
        requeued: list[str] = []

        async def _capture_requeue(self, task_id):
            requeued.append(task_id)

        monkeypatch.setattr(GenerationWorker, "_requeue_single_task", _capture_requeue)
        await worker._handle_orphan_tasks_on_start()
        assert requeued == []
        assert queue.failed and queue.failed[0][0] == "img-orphan"
        assert "[restart_lost_image]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_non_resumable_video_marks_resume_unsupported(self, monkeypatch):
        """Grok/Vidu video 孤儿 → [resume_unsupported]（backend 无 resume，绝不重跑）。"""
        from lib.providers import PROVIDER_GROK

        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "grok-orphan",
                "status": "running",
                "provider_id": PROVIDER_GROK,
                "provider_job_id": "some-job",
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue)
        requeued: list[str] = []

        async def _capture_requeue(self, task_id):
            requeued.append(task_id)

        monkeypatch.setattr(GenerationWorker, "_requeue_single_task", _capture_requeue)
        await worker._handle_orphan_tasks_on_start()
        assert requeued == []
        assert queue.failed and queue.failed[0][0] == "grok-orphan"
        assert "[resume_unsupported_provider]" in queue.failed[0][1]
        assert PROVIDER_GROK in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_discard_paths_fallback_to_cancelled_on_zero_rows(self, monkeypatch):
        """非 resumable 路径 mark_failed 返 0 rows（race：被外部 cancel）→ 兜底 mark_cancelled。

        image / Grok / Vidu 三个丢弃路径都共用「mark_failed → 0 rows 时 mark_cancelled 兜底」协议；
        覆盖 image 一条即可代表（其它两路同源代码块）。
        """
        from lib.providers import PROVIDER_GROK

        queue = _FakeQueue(failed_rows=0)  # 模拟 SQL guard 拒绝（task 已被 cancel）
        queue._orphans = [
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
            {
                "task_id": "grok-raced",
                "status": "running",
                "provider_id": PROVIDER_GROK,
                "provider_job_id": "job",
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            },
        ]
        worker = GenerationWorker(queue=queue)
        await worker._handle_orphan_tasks_on_start()
        cancelled_ids = {tid for tid, _by in queue.cancelled}
        assert cancelled_ids == {"img-raced", "grok-raced"}

    @pytest.mark.asyncio
    async def test_handle_orphan_uses_persisted_provider_id(self, monkeypatch):
        """task.provider_id 优先于 _extract_provider 的当前项目解析（CR round-2 N2 回归）。

        如果 task 持久化的 provider_id 是 Grok（不支持 resume），即便当前项目配置
        已切换成 Ark（支持 resume），孤儿仍应被识别为 non_resumable → [resume_unsupported]，
        而不是去派发 _process_resume_task 拿旧 job_id 给新 backend 轮询。
        """
        from lib.providers import PROVIDER_GROK

        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "ghost-orphan",
                "status": "running",
                "provider_id": PROVIDER_GROK,  # 持久化的是 Grok
                "provider_job_id": "stale-job",
                "media_type": "video",
                "task_type": "video",
                # payload 显式写 video_provider=ark，模拟"项目已切换" → _extract_provider 会解析成 ark
                "payload": {"video_provider": "ark"},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue)
        requeued: list[str] = []
        resume_dispatched: list[str] = []

        async def _capture_requeue(self, task_id):
            requeued.append(task_id)

        async def _capture_resume(self, task):
            resume_dispatched.append(task["task_id"])

        monkeypatch.setattr(GenerationWorker, "_requeue_single_task", _capture_requeue)
        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _capture_resume)
        await worker._handle_orphan_tasks_on_start()
        # 用持久化的 Grok → [resume_unsupported]；若误用 payload 里的 ark → 会派发 _process_resume_task
        assert requeued == []
        assert resume_dispatched == []
        assert queue.failed and queue.failed[0][0] == "ghost-orphan"
        assert "[resume_unsupported_provider]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_handle_orphan_resumable_dispatches_process_resume_task(self, monkeypatch):
        """video resumable provider + 有 job_id → 后台 dispatcher 派发 _process_resume_task。

        Semaphore-based dispatcher 在 sub-task 内填 inflight、finally pop；本测验证
        dispatched 列表收到目标 task 即可（dispatcher 完成时 inflight 已被清理）。
        """
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "ark-orphan",
                "status": "running",
                "provider_id": "ark",
                "provider_job_id": "ark-job-1",
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue)
        dispatched: list[dict] = []

        async def _capture_resume(self, task):
            dispatched.append(task)

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _capture_resume)
        await worker._handle_orphan_tasks_on_start()
        # 等后台 dispatcher（含 orphan-dispatcher + provider 桶 sub-task）完成
        for _ in range(50):
            await asyncio.sleep(0)
            if dispatched:
                break
        # 让 dispatcher 自身的 task 跑完（避免 unawaited task 警告）
        for t in list(asyncio.all_tasks()):
            name = t.get_name()
            if (
                name in ("orphan-dispatcher",)
                or name.startswith("orphan-dispatch-")
                or name.startswith("resume-video-")
            ):
                try:
                    await t
                except Exception:
                    pass
        assert len(dispatched) == 1
        assert dispatched[0]["task_id"] == "ark-orphan"

    @pytest.mark.asyncio
    async def test_handle_orphan_fast_path_returns_immediately(self, monkeypatch):
        """fix #647 #1：fast path 不阻塞——5 个可 resume orphan + video_max=2，
        `_handle_orphan_tasks_on_start` 应几乎立刻返回（< 100ms），
        实际 dispatch 由后台 dispatcher 处理。"""
        import time

        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": f"orphan-{i}",
                "status": "running",
                "provider_id": "ark",
                "provider_job_id": f"job-{i}",
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
            for i in range(5)
        ]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 2}}))

        # 让 _process_resume_task block 住——验证 fast path 不等它完成
        async def _block_forever(self, task):
            await asyncio.Event().wait()

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _block_forever)

        start = time.monotonic()
        await worker._handle_orphan_tasks_on_start()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"fast path 阻塞了 {elapsed:.3f}s（应 < 100ms）"

        # 清理后台 dispatcher，避免 unawaited task 警告
        for t in list(asyncio.all_tasks()):
            if t.get_name() in ("orphan-dispatcher", "orphan-dispatch-ark"):
                t.cancel()
            if t.get_name().startswith("resume-video-"):
                t.cancel()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_handle_orphan_dispatcher_respects_pool_capacity(self, monkeypatch):
        """fix #647 #1：后台 dispatcher 受 video 容量约束分批 promote 进 INFLIGHT，
        任一时刻 inflight 占用 ≤ cap（pending 不消耗 sem，不计入此上限）。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": f"orphan-{i}",
                "status": "running",
                "provider_id": "ark",
                "provider_job_id": f"job-{i}",
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
            for i in range(4)
        ]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 2}}))

        # 用 controlled event 让 resume 任务可控完成；同时记录每次 dispatch 时的占用
        snapshots: list[int] = []
        entered: list[str] = []
        gates: dict[str, asyncio.Event] = {f"orphan-{i}": asyncio.Event() for i in range(4)}

        def _inflight() -> int:
            return len(_phase_ids(worker._slots, "ark", "video")[0])

        async def _gated(self, task):
            snapshots.append(_inflight())
            entered.append(task["task_id"])
            await gates[task["task_id"]].wait()

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _gated)

        await worker._handle_orphan_tasks_on_start()
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
        # 每次 promote 进 _process_resume_task 时的 inflight 快照都 ≤ 容量（核心回归点）
        assert snapshots and all(s <= 2 for s in snapshots), f"inflight 快照越过容量上限: {snapshots}"

        # 收尾：释放所有 gate，等 dispatcher 结束
        for gate in gates.values():
            gate.set()
        for _ in range(50):
            await asyncio.sleep(0)
        # 清理可能的残余
        for t in list(asyncio.all_tasks()):
            name = t.get_name()
            if name.startswith("orphan-") or name.startswith("resume-video-"):
                if not t.done():
                    t.cancel()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_handle_orphan_dispatcher_exits_on_stop_event(self, monkeypatch):
        """fix #647 #1：`_stop_event` 触发时 dispatcher 干净退出，不再 dispatch 剩余 orphan。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": f"orphan-{i}",
                "status": "running",
                "provider_id": "ark",
                "provider_job_id": f"job-{i}",
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
            for i in range(3)
        ]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        dispatched_count = 0
        first_dispatched = asyncio.Event()
        block_gate = asyncio.Event()

        async def _maybe_block(self, task):
            nonlocal dispatched_count
            dispatched_count += 1
            first_dispatched.set()
            await block_gate.wait()

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _maybe_block)

        await worker._handle_orphan_tasks_on_start()
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

    # ------------------------------------------------------------------
    # _process_resume_task：分流 + provider 锁定
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_process_resume_task_locks_persisted_provider_to_payload(self, monkeypatch):
        """C2 回归：persisted provider_id 应注入 payload.video_provider。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        captured_task: dict | None = None
        captured_job_id: str | None = None

        async def _fake_resume(task, *, job_id):
            nonlocal captured_task, captured_job_id
            captured_task = task
            captured_job_id = job_id
            return {"ok": True}

        monkeypatch.setattr("server.services.resume_executor.execute_resume_video_task", _fake_resume)

        task = {
            "task_id": "resume-locked",
            "task_type": "video",
            "media_type": "video",
            "provider_id": "openai",
            "provider_job_id": "openai-job",
            "payload": {"video_provider": "gemini-aistudio"},  # payload 原本指向另一个 provider
            "project_name": "demo",
        }
        await worker._process_resume_task(task)
        assert captured_task is not None
        # _process_resume_task 应覆写为持久化 provider_id (openai)
        assert captured_task["payload"]["video_provider"] == "openai"
        assert captured_job_id == "openai-job"
        assert queue.succeeded == [("resume-locked", {"ok": True})]

    @pytest.mark.asyncio
    async def test_process_resume_task_resume_expired(self, monkeypatch):
        """ResumeExpiredError → mark_failed [resume_expired]。"""
        from lib.video_backends.base import ResumeExpiredError

        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _expire(_task, *, job_id):
            raise ResumeExpiredError(job_id=job_id, provider="ark")

        monkeypatch.setattr("server.services.resume_executor.execute_resume_video_task", _expire)
        task = {
            "task_id": "exp",
            "task_type": "video",
            "media_type": "video",
            "provider_id": "ark",
            "provider_job_id": "x",
            "payload": {},
            "project_name": "demo",
        }
        await worker._process_resume_task(task)
        assert queue.failed and queue.failed[0][0] == "exp"
        assert "[resume_expired_detail]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_process_resume_task_resume_unsupported(self, monkeypatch):
        """NotImplementedError → mark_failed [resume_unsupported]。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _unsup(_task, *, job_id):
            raise NotImplementedError("no resume_video")

        monkeypatch.setattr("server.services.resume_executor.execute_resume_video_task", _unsup)
        task = {
            "task_id": "uns",
            "task_type": "video",
            "media_type": "video",
            "provider_id": "vidu",
            "provider_job_id": "x",
            "payload": {},
            "project_name": "demo",
        }
        await worker._process_resume_task(task)
        assert queue.failed and queue.failed[0][0] == "uns"
        assert "[resume_unsupported_detail]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_process_resume_task_generic_exception(self, monkeypatch):
        """通用 Exception → mark_failed（无前缀，与运行期 backend 失败同款）。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _boom(_task, *, job_id):
            raise RuntimeError("transient backend error")

        monkeypatch.setattr("server.services.resume_executor.execute_resume_video_task", _boom)
        task = {
            "task_id": "boom",
            "task_type": "video",
            "media_type": "video",
            "provider_id": "ark",
            "provider_job_id": "x",
            "payload": {},
            "project_name": "demo",
        }
        await worker._process_resume_task(task)
        assert queue.failed and queue.failed[0][0] == "boom"
        # 无 [resume_*] 前缀
        assert not queue.failed[0][1].startswith("[resume_")

    @pytest.mark.asyncio
    async def test_process_resume_task_cancelled_error(self, monkeypatch):
        """CancelledError → mark_cancelled + 重新抛出。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)

        async def _cancel(_task, *, job_id):
            raise asyncio.CancelledError

        monkeypatch.setattr("server.services.resume_executor.execute_resume_video_task", _cancel)
        task = {
            "task_id": "rc",
            "task_type": "video",
            "media_type": "video",
            "provider_id": "ark",
            "provider_job_id": "x",
            "payload": {},
            "project_name": "demo",
        }
        with pytest.raises(asyncio.CancelledError):
            await worker._process_resume_task(task)
        assert queue.cancelled and queue.cancelled[0][0] == "rc"

    @pytest.mark.asyncio
    async def test_process_resume_task_no_job_id_fails_fast(self):
        """无 provider_job_id 的 task 被派发到 _process_resume_task 时直接 mark_failed。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        task = {
            "task_id": "no-job",
            "task_type": "video",
            "media_type": "video",
            "provider_job_id": "",
            "payload": {},
            "project_name": "demo",
        }
        await worker._process_resume_task(task)
        assert queue.failed and queue.failed[0][0] == "no-job"
        assert "[restart_lost_resume_no_job_id]" in queue.failed[0][1]


class TestDispatcherFailFastAndPendingTracking:
    """dispatcher fail-fast + pending/inflight 分集合精确容量与 cancel 跟踪。"""

    @pytest.mark.asyncio
    async def test_dispatch_provider_bucket_fail_fast_when_video_max_zero(self, monkeypatch):
        """video 容量=0 → 直接 mark_failed[resume_unsupported]，不进 Semaphore(0) 死锁。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 0}}))

        async def _no_reload(self):
            return None

        monkeypatch.setattr(GenerationWorker, "reload_limits", _no_reload)

        tasks = [{"task_id": f"orphan-{i}", "provider_id": "ark"} for i in range(3)]
        await worker._dispatch_provider_bucket("ark", tasks)

        assert {tid for tid, _ in queue.failed} == {"orphan-0", "orphan-1", "orphan-2"}
        assert all("[resume_unsupported_capacity_zero]" in msg for _, msg in queue.failed)

    @pytest.mark.asyncio
    async def test_sub_task_registered_in_pending_before_sem_acquire(self, monkeypatch):
        """sem=1 + 2 task：第 2 个 sub-task sem 排队期间应以 PENDING 登记在台账。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        gate = asyncio.Event()

        async def _gated(self, task):
            await gate.wait()

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _gated)

        tasks = [{"task_id": f"orphan-{i}", "provider_id": "ark"} for i in range(2)]
        dispatcher = asyncio.create_task(worker._dispatch_provider_bucket("ark", tasks))

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
    async def test_request_cancel_finds_sem_queued_task_in_pending(self, monkeypatch):
        """cancel sem 排队中的 task → request_cancel 命中并触发 cancel。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        gate = asyncio.Event()
        process_started: asyncio.Event = asyncio.Event()

        async def _gated(self, task):
            process_started.set()
            await gate.wait()

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _gated)

        tasks = [{"task_id": f"orphan-{i}", "provider_id": "ark"} for i in range(2)]
        dispatcher = asyncio.create_task(worker._dispatch_provider_bucket("ark", tasks))

        await asyncio.wait_for(process_started.wait(), timeout=1.0)
        _inflight, pending = _phase_ids(worker._slots, "ark", "video")
        assert "orphan-1" in pending

        ok = worker.request_cancel("orphan-1")
        assert ok is True

        gate.set()
        await dispatcher

    @pytest.mark.asyncio
    async def test_sem_queued_cancel_marks_task_cancelled(self, monkeypatch):
        """sem 排队期被 cancel：_run_one 应显式 mark_task_cancelled，DB 不留 cancelling。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        gate = asyncio.Event()
        first_started = asyncio.Event()

        async def _gated(self, task):
            first_started.set()
            await gate.wait()

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _gated)

        tasks = [{"task_id": f"orphan-{i}", "provider_id": "ark"} for i in range(2)]
        dispatcher = asyncio.create_task(worker._dispatch_provider_bucket("ark", tasks))

        # 等第 1 个 task 进入 _process_resume_task（占住 sem），第 2 个还在 sem 排队
        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        _inflight, pending = _phase_ids(worker._slots, "ark", "video")
        assert "orphan-1" in pending

        # 取消 sem 排队中的 orphan-1
        queued_task = worker._slots.find_by_task("orphan-1")
        assert queued_task is not None
        queued_task.cancel()

        # 让 dispatcher 跑完
        gate.set()
        await dispatcher

        # orphan-1 应被显式 mark_task_cancelled（sem 排队期 cancel 路径），不能停在 cancelling
        cancelled_ids = {tid for tid, _ in queue.cancelled}
        assert "orphan-1" in cancelled_ids

    @pytest.mark.asyncio
    async def test_acquired_pre_process_cancel_marks_task_cancelled(self, monkeypatch):
        """acquire 后、_process_resume_task 入 try 之前 cancel：_run_one 应兜底 mark cancelled。

        模拟场景：_process_resume_task 内部 try 块之前还有 await（如 _extract_provider），
        cancel 在那段 await 抛 CancelledError，内部不会调 mark，必须由 _run_one 兜底。
        """
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        acquired_event = asyncio.Event()
        pre_try_gate = asyncio.Event()

        async def _fake_resume_task(self, task):
            # 模拟 acquired=True 后、try 块之前的 await（即漏窗）
            acquired_event.set()
            await pre_try_gate.wait()
            # 一般不会走到这里——测试通过 cancel queued_task 中断

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _fake_resume_task)

        tasks = [{"task_id": "orphan-pre-try", "provider_id": "ark"}]
        dispatcher = asyncio.create_task(worker._dispatch_provider_bucket("ark", tasks))

        # 等 _process_resume_task 进入空窗（await pre_try_gate.wait() 期间）
        await asyncio.wait_for(acquired_event.wait(), timeout=1.0)

        # 现在 task 在 acquired=True（已 promote 为 INFLIGHT）状态，但内部还没接管终态
        sub_task = worker._slots.find_by_task("orphan-pre-try")
        assert sub_task is not None
        sub_task.cancel()

        # gate 放开（CancelledError 已经在路上）
        pre_try_gate.set()
        await dispatcher

        # 必须落 cancelled 终态，不能停在 cancelling
        assert "orphan-pre-try" in {tid for tid, _ in queue.cancelled}

    @pytest.mark.asyncio
    async def test_dispatcher_handle_set_after_handle_orphan(self, monkeypatch):
        """_handle_orphan_tasks_on_start 后 self._orphan_dispatcher_task 应被设置。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "orphan-x",
                "status": "running",
                "provider_id": "ark",
                "provider_job_id": "job-x",
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        block = asyncio.Event()

        async def _gated(self, task):
            await block.wait()

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _gated)

        await worker._handle_orphan_tasks_on_start()
        assert worker._orphan_dispatcher_task is not None
        assert not worker._orphan_dispatcher_task.done()

        block.set()
        await worker._orphan_dispatcher_task


class TestOrphanScanSelfPreemption:
    """lease flap > 3×TTL 重夺时，本进程仍 inflight 的 task 不应被当孤儿处理。"""

    @pytest.mark.asyncio
    async def test_image_inflight_not_marked_restart_lost(self, monkeypatch):
        """本进程 image_inflight 含 task → 孤儿扫描应跳过，不标 [restart_lost]。"""
        queue = _FakeQueue()
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
        worker = GenerationWorker(queue=queue)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        worker._slots.register("ark", "image", "img-active", fut)

        await worker._handle_orphan_tasks_on_start()

        # dispatcher 句柄不应被设置（本进程仍在跑，无 resumable 任务进 dispatcher）
        assert worker._orphan_dispatcher_task is None
        # 不应被标 [restart_lost] —— 本进程还在跑
        assert "img-active" not in {tid for tid, _ in queue.failed}
        # 清理
        fut.set_result(None)

    @pytest.mark.asyncio
    async def test_video_inflight_not_dispatched_to_resume(self, monkeypatch):
        """本进程 video_inflight 含 task → 孤儿扫描应跳过，不启动重复 resume 流。"""
        queue = _FakeQueue()
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
        worker = GenerationWorker(queue=queue)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        worker._slots.register("ark", "video", "vid-active", fut)

        captured: list[dict[str, Any]] = []

        async def _spy_dispatch(self, mapping):
            captured.append(dict(mapping))

        monkeypatch.setattr(GenerationWorker, "_dispatch_resume_orphans_background", _spy_dispatch)

        await worker._handle_orphan_tasks_on_start()

        # 主断言：dispatcher 句柄从未被创建（同步检查，不受 spy 调度时机影响）
        assert worker._orphan_dispatcher_task is None
        # 兜底：spy 也确认未被调用（dispatcher 即便创建也会因 mapping 为空跳过）
        assert captured == [], f"本进程 inflight 的 task 不应被重复 dispatch: {captured}"
        assert "vid-active" not in {tid for tid, _ in queue.failed}
        fut.set_result(None)

    @pytest.mark.asyncio
    async def test_video_pending_also_skipped(self):
        """本进程 video_pending（sem 排队中）含 task → 孤儿扫描应跳过。"""
        queue = _FakeQueue()
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
        worker = GenerationWorker(queue=queue)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        worker._slots.register("ark", "video", "vid-pending", fut, pending=True)

        await worker._handle_orphan_tasks_on_start()

        assert "vid-pending" not in {tid for tid, _ in queue.failed}
        # dispatcher 句柄不应被设置（无 resumable 任务进 dispatcher）
        assert worker._orphan_dispatcher_task is None
        fut.set_result(None)


class TestOrphanDispatcherNonBlockingOverride:
    """lease 重夺时旧 dispatcher 仍在跑：本轮直接覆盖句柄，不 await（liveness）也不 cancel
    （避免错误中断 in-flight resume）。"""

    @pytest.mark.asyncio
    async def test_old_dispatcher_not_awaited_on_re_scan(self, monkeypatch):
        """旧 dispatcher 跑 5s 时，再次进 _handle_orphan_tasks_on_start 应秒级返回（不阻塞）。"""
        queue = _FakeQueue()
        queue._orphans = [
            {
                "task_id": "orphan-x",
                "status": "running",
                "provider_id": "ark",
                "provider_job_id": "job-x",
                "media_type": "video",
                "task_type": "video",
                "payload": {},
                "project_name": "demo",
            }
        ]
        worker = GenerationWorker(queue=queue, capacity=_cap({"ark": {"image": 0, "video": 1}}))

        block = asyncio.Event()

        async def _gated(self, task):
            await block.wait()

        monkeypatch.setattr(GenerationWorker, "_process_resume_task", _gated)

        # 第 1 次扫描：启动旧 dispatcher
        await worker._handle_orphan_tasks_on_start()
        old_dispatcher = worker._orphan_dispatcher_task
        assert old_dispatcher is not None
        assert not old_dispatcher.done()

        # 第 2 次扫描（模拟 lease flap 超阈值后重夺）：应直接覆盖句柄、不阻塞
        start = asyncio.get_event_loop().time()
        await worker._handle_orphan_tasks_on_start()
        elapsed = asyncio.get_event_loop().time() - start
        # 应在 1s 内完成；旧 dispatcher 仍未 done 但句柄已被新的覆盖
        assert elapsed < 1.0, f"重夺时不应阻塞：elapsed={elapsed:.3f}s"
        assert worker._orphan_dispatcher_task is not old_dispatcher
        # 旧 dispatcher 未被 cancel——in-flight resume 不应被错误中断
        assert not old_dispatcher.cancelled()
        assert not old_dispatcher.done()

        # 清理：放开 gate 让 dispatcher 完成
        block.set()
        new_dispatcher = worker._orphan_dispatcher_task
        assert new_dispatcher is not None
        await asyncio.gather(old_dispatcher, new_dispatcher, return_exceptions=True)


class TestOrphanOnceAndLeaseFlap:
    """orphan 一次性扫描 + lease flap 阈值。"""

    def _build_worker(self) -> tuple[GenerationWorker, _FakeQueue, list[int]]:
        """构造 worker；返回 (worker, queue, scan_count)。scan_count 记录扫描次数。"""
        queue = _FakeQueue()
        worker = GenerationWorker(queue=queue)
        scan_count: list[int] = []

        async def _spy_scan():
            scan_count.append(1)

        # 替换 _handle_orphan_tasks_on_start 为 spy，便于精确断言扫描次数
        worker._handle_orphan_tasks_on_start = _spy_scan  # type: ignore[assignment]
        return worker, queue, scan_count

    @pytest.mark.asyncio
    async def test_orphan_scanned_once_on_first_lease_acquire(self):
        worker, _, scan_count = self._build_worker()
        worker._owns_lease = True
        worker._orphan_handled_once = False

        # 模拟主循环里那段守卫
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._handle_orphan_tasks_on_start()
            worker._orphan_handled_once = True

        assert len(scan_count) == 1
        assert worker._orphan_handled_once is True

    @pytest.mark.asyncio
    async def test_orphan_not_rescanned_in_steady_state(self):
        """稳定持 lease 多拍主循环：扫描仅 1 次。"""
        worker, _, scan_count = self._build_worker()
        worker._owns_lease = True

        for _ in range(5):
            if worker._owns_lease and not worker._orphan_handled_once:
                await worker._handle_orphan_tasks_on_start()
                worker._orphan_handled_once = True

        assert len(scan_count) == 1

    @pytest.mark.asyncio
    async def test_orphan_does_not_rescan_on_short_lease_flap(self):
        """lease flap < lease_ttl：不重扫。"""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        # 首次获得 lease 扫一次
        worker._owns_lease = True
        await worker._handle_orphan_tasks_on_start()
        worker._orphan_handled_once = True

        # 模拟丢 lease 1 秒后又夺回（短 flap）
        import time as _time

        worker._lease_lost_monotonic = _time.monotonic() - 1.0
        # 应用 _run_loop 中的逻辑片段
        lost_duration = _time.monotonic() - worker._lease_lost_monotonic
        if lost_duration > worker.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
            worker._orphan_handled_once = False
        worker._lease_lost_monotonic = None

        # flap 时长远小于 30s，仍是 handled_once
        assert worker._orphan_handled_once is True
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._handle_orphan_tasks_on_start()
        assert len(scan_count) == 1, "短 flap 不应重扫"

    @pytest.mark.asyncio
    async def test_orphan_does_not_rescan_below_3x_ttl(self):
        """lease_ttl < lost < 3×lease_ttl：仍不重扫（边界）。"""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        worker._owns_lease = True
        await worker._handle_orphan_tasks_on_start()
        worker._orphan_handled_once = True

        import time as _time

        # lost = 15s（介于 ttl=10s 与 3×ttl=30s 之间）
        worker._lease_lost_monotonic = _time.monotonic() - 15.0
        lost_duration = _time.monotonic() - worker._lease_lost_monotonic
        if lost_duration > worker.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
            worker._orphan_handled_once = False
        worker._lease_lost_monotonic = None

        assert worker._orphan_handled_once is True
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._handle_orphan_tasks_on_start()
        assert len(scan_count) == 1, "15s 仍 < 3×ttl=30s，不应重扫"

    @pytest.mark.asyncio
    async def test_orphan_rescans_after_real_lease_handoff(self):
        """lost > 3×lease_ttl：清零开关，下次重扫。"""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        worker._owns_lease = True
        await worker._handle_orphan_tasks_on_start()
        worker._orphan_handled_once = True

        import time as _time

        # lost = 40s > 30s 阈值，认为另一进程曾持过 lease
        worker._lease_lost_monotonic = _time.monotonic() - 40.0
        lost_duration = _time.monotonic() - worker._lease_lost_monotonic
        if lost_duration > worker.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
            worker._orphan_handled_once = False
        worker._lease_lost_monotonic = None

        assert worker._orphan_handled_once is False
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._handle_orphan_tasks_on_start()
            worker._orphan_handled_once = True
        assert len(scan_count) == 2, "lost 超过 3×ttl 应重扫一次"
