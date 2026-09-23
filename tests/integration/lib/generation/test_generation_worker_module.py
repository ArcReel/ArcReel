import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import pytest

from lib.db.models.api_call import ApiCall
from lib.generation.generation_worker import (
    _ORPHAN_RESCAN_LEASE_LOST_MULT,
    DEFAULT_PROVIDER,
    GenerationWorker,
    _extract_provider,
    _read_int_env,
)
from lib.script.script_editor import ScriptEditError
from tests.fakes import bind_safe_session_factory
from tests.integration.lib.generation.worker_support import (
    FakeWorkerQueue,
    capacity_table,
    db_queue,
    seed_running_task,
    stub_executors,
    task_status,
)


async def _fixed_projection(_task) -> str:
    return "test"


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
    pm = type("PM", (), {"load_project": lambda self, name: project or {}})()
    for target in ("lib.generation.generation_worker.get_project_manager", "lib.config.resolver.get_project_manager"):
        monkeypatch.setattr(target, lambda: pm)


@pytest.fixture
async def patch_empty_db(db_factory, monkeypatch):
    """把全局 async_session_factory 换成空内存库，隔离掉真实数据库。

    无 project_name 的 _extract_provider 会用 worker 模块级导入的 async_session_factory 经 ConfigResolver
    解析全局默认供应商，视频的入队派生与请求投影则在函数内晚导入 ``lib.db`` 上的全局名字；不隔离时
    它们读真实 dev 库，本机一旦配了其它 ready 供应商，回退断言就被污染。
    """
    monkeypatch.setattr("lib.db.async_session_factory", db_factory)
    monkeypatch.setattr("lib.generation.generation_worker.async_session_factory", db_factory)
    return db_factory


class TestExtractProvider:
    """_extract_provider 是解析链的薄投影：按 task_type 派发，取 .provider_id。"""

    @pytest.mark.usefixtures("patch_empty_db")
    async def test_video_payload_identity_is_advisory_only(self):
        """分镜视频认领忽略 enqueue payload 的旧身份；无当前配置时只回退限流默认。"""
        task = {"payload": {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"}, "task_type": "video"}
        assert await _extract_provider(task) == DEFAULT_PROVIDER

    async def test_image_payload_provider(self):
        """payload 携带历史 image_provider → 投影取到。"""
        task = {"payload": {"image_provider": "gemini-vertex"}, "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    @pytest.mark.usefixtures("patch_empty_db")
    async def test_default_when_unresolvable(self):
        """无 project、无 payload、全局未配供应商 → 回退 DEFAULT_PROVIDER（仅供限流）。

        必须隔离全局 DB（patch_empty_db）——否则会读真实 dev 库，本机配了其它 ready 供应商时
        auto-resolve 会返回该供应商而非 DEFAULT_PROVIDER，断言被本机环境污染。
        """
        task = {"payload": {}}
        assert await _extract_provider(task) == DEFAULT_PROVIDER

    async def test_project_level_video_backend(self, monkeypatch):
        """项目级 video_backend 优先于全局默认。"""
        _patch_pm(monkeypatch, {"video_backend": "ark/doubao-seedance-1-5-pro-251215"})
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}
        assert await _extract_provider(task) == "ark"

    async def test_project_level_image_t2i(self, monkeypatch):
        """image 投影按代表性 generation_type=t2i 取项目级 image_provider_t2i。"""
        _patch_pm(monkeypatch, {"image_provider_t2i": "gemini-vertex/imagen-3"})
        task = {"payload": {}, "project_name": "demo", "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    async def test_reference_video_routes_to_video_lane(self, monkeypatch):
        """reference_video task_type 必须按 video lane 解析 video_backend，而非 image 槽。

        项目同时配置了不同 provider 的 video_backend（ark）与 image_provider_t2i
        （gemini-vertex）。reference_video 属于 video lane，认领期 provider 投影须取 ark；
        若误判为 image lane（按 task_type != "video" 去读 image 槽），会取到纯图片
        供应商，导致 worker 在 video 通道以 video_max==0 直接把任务标记
        「供应商不支持 video 生成」。"""
        _patch_pm(
            monkeypatch,
            {
                "video_backend": "ark/doubao-seedance-1-5-pro-251215",
                "image_provider_t2i": "gemini-vertex/imagen-3",
            },
        )
        task = {"payload": {}, "project_name": "demo", "task_type": "reference_video"}
        assert await _extract_provider(task) == "ark"

    async def test_reference_video_prefers_r2v_bucket_provider(self, monkeypatch):
        """视频单元级状态读不到时回退当前 r2v 配置，不采用 payload 中的 enqueue-time pin。"""
        _patch_pm(
            monkeypatch,
            {
                "video_backend": "ark/doubao-seedance-1-5-pro-251215",
                "video_provider_r2v": "minimax/S2V-01",
            },
        )
        task = {
            "payload": {"video_provider_r2v": "ark/doubao-seedance-1-5-pro-251215"},
            "project_name": "demo",
            "task_type": "reference_video",
        }
        assert await _extract_provider(task) == "minimax"

    @pytest.mark.parametrize(
        ("text", "expected_provider"),
        [("@[A] 走进房间", "minimax"), ("空镜头", "ark")],
    )
    async def test_reference_video_routes_by_hydrated_reference_bucket(
        self, tmp_path, monkeypatch, text, expected_provider
    ):
        """reference_video 投影按 unit 当前实际可用参考图分桶：有图 → r2v 桶 provider，
        无参考图退化镜头 → i2v 桶 provider，与执行层降级定桶同口径。"""
        project = {
            "video_provider_i2v": "ark/doubao-seedance-1-5-pro-251215",
            "video_provider_r2v": "minimax/S2V-01",
            "video_generate_audio": True,
            "characters": {"A": {"character_sheet": "characters/A.png"}},
        }
        (tmp_path / "characters").mkdir()
        (tmp_path / "characters" / "A.png").write_bytes(b"image")
        script = {"video_units": [{"unit_id": "E1U1", "text": text}]}
        pm_cls = type(
            "PM",
            (),
            {
                "load_project": lambda self, name: project,
                "load_script": lambda self, name, filename: script,
                "get_project_path": lambda self, name: tmp_path,
            },
        )
        for target in (
            "lib.generation.generation_worker.get_project_manager",
            "lib.config.resolver.get_project_manager",
        ):
            monkeypatch.setattr(target, pm_cls)
        task = {
            "payload": {"script_file": "ep1.json"},
            "project_name": "demo",
            "task_type": "reference_video",
            "resource_id": "E1U1",
        }
        assert await _extract_provider(task) == expected_provider

    async def test_reference_video_claim_uses_frozen_task_script_locator(self, tmp_path, monkeypatch):
        project = {
            "video_provider_i2v": "ark/doubao-seedance-1-5-pro-251215",
            "video_provider_r2v": "minimax/S2V-01",
            "video_generate_audio": True,
            "characters": {"A": {"character_sheet": "characters/A.png"}},
        }
        (tmp_path / "characters").mkdir()
        (tmp_path / "characters" / "A.png").write_bytes(b"image")
        scripts = {
            "stale.json": {"video_units": [{"unit_id": "E1U1", "text": "空镜头"}]},
            "frozen.json": {"video_units": [{"unit_id": "E1U1", "text": "@[A] 走进房间"}]},
        }
        pm_cls = type(
            "PM",
            (),
            {
                "load_project": lambda self, name: project,
                "load_script": lambda self, name, filename: scripts[filename],
                "get_project_path": lambda self, name: tmp_path,
            },
        )
        for target in (
            "lib.generation.generation_worker.get_project_manager",
            "lib.config.resolver.get_project_manager",
        ):
            monkeypatch.setattr(target, pm_cls)
        task = {
            "payload": {"script_file": "stale.json"},
            "script_file": "frozen.json",
            "project_name": "demo",
            "task_type": "reference_video",
            "resource_id": "E1U1",
        }

        assert await _extract_provider(task) == "minimax"

    async def test_reference_video_script_read_failure_falls_back_to_r2v(self, monkeypatch):
        """剧本读取失败时投影回退 r2v 代表桶，不冒泡阻断认领。"""

        def _load_script(self, name, filename):
            raise ScriptEditError("broken")

        project = {
            "video_provider_i2v": "ark/doubao-seedance-1-5-pro-251215",
            "video_provider_r2v": "minimax/S2V-01",
        }
        pm_cls = type("PM", (), {"load_project": lambda self, name: project, "load_script": _load_script})
        for target in (
            "lib.generation.generation_worker.get_project_manager",
            "lib.config.resolver.get_project_manager",
        ):
            monkeypatch.setattr(target, pm_cls)
        task = {
            "payload": {"script_file": "ep1.json"},
            "project_name": "demo",
            "task_type": "reference_video",
            "resource_id": "E1U1",
        }
        assert await _extract_provider(task) == "minimax"

    async def test_queued_reference_video_reprojects_provider_after_project_edit(
        self, tmp_path, monkeypatch, patch_empty_db
    ):
        """队列行只保存 advisory provider；领取/执行前按项目最新 provider/model 重新投影。"""

        from lib.generation.generation_queue import GenerationQueue, reference_projection_for_queued_task

        holder = {"model": "ark/doubao-seedance-1-5-pro-251215"}
        script = {"video_units": [{"unit_id": "E1U1", "text": "空镜", "duration_seconds": 5}]}

        class _PM:
            def load_project(self, _name):
                return {
                    "generation_mode": "reference_video",
                    "video_provider_i2v": holder["model"],
                    "video_generate_audio": True,
                }

            def load_script(self, _name, _filename):
                return script

            def get_project_path(self, _name):
                return tmp_path

        for target in (
            "lib.generation.generation_worker.get_project_manager",
            "lib.config.resolver.get_project_manager",
        ):
            monkeypatch.setattr(target, _PM)
        queue = GenerationQueue(session_factory=patch_empty_db)
        enqueued = await queue.enqueue_task(
            project_name="demo",
            task_type="reference_video",
            media_type="video",
            resource_id="E1U1",
            payload={"script_file": "ep1.json"},
            script_file="ep1.json",
        )
        task = await queue.get_task(enqueued["task_id"])
        assert task is not None
        assert task["provider_id"] == "ark"
        assert "video_provider_i2v" not in task["payload"]
        assert "video_provider_r2v" not in task["payload"]

        first = await reference_projection_for_queued_task(
            project=_PM().load_project(task["project_name"]),
            project_name=task["project_name"],
            payload=task["payload"],
            resource_id=task["resource_id"],
        )
        assert first is not None
        assert (first.provider_id, first.model_id) == ("ark", "doubao-seedance-1-5-pro-251215")
        assert await _extract_provider(task) == "ark"

        holder["model"] = "ark/doubao-seedance-2-0-260128"
        second = await reference_projection_for_queued_task(
            project=_PM().load_project(task["project_name"]),
            project_name=task["project_name"],
            payload=task["payload"],
            resource_id=task["resource_id"],
        )
        assert second is not None
        assert (second.provider_id, second.model_id) == ("ark", "doubao-seedance-2-0-260128")

        holder["model"] = "grok/grok-imagine-video"
        third = await reference_projection_for_queued_task(
            project=_PM().load_project(task["project_name"]),
            project_name=task["project_name"],
            payload=task["payload"],
            resource_id=task["resource_id"],
        )
        assert third is not None
        assert (third.provider_id, third.model_id) == ("grok", "grok-imagine-video")
        assert await _extract_provider(task) == "grok"

    async def test_current_project_provider_takes_precedence_over_stale_payload(self, monkeypatch):
        """分镜视频在认领时重投影当前项目配置，不复用 enqueue payload 的旧 provider。"""
        _patch_pm(monkeypatch, {"video_backend": "grok/grok-imagine-video"})
        task = {
            "payload": {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"},
            "project_name": "demo",
            "task_type": "video",
        }
        assert await _extract_provider(task) == "grok"

    async def test_deleted_project_load_failure_falls_back_not_raises(self, monkeypatch):
        """指向已删除/不可读项目的残留任务：load_project 抛错也须回退 DEFAULT_PROVIDER，
        绝不冒泡阻断认领循环（否则一个坏任务会拖垮整个 worker）。"""

        def _raising_pm():
            def _load(self, name):
                raise FileNotFoundError(name)

            return type("PM", (), {"load_project": _load})()

        for target in (
            "lib.generation.generation_worker.get_project_manager",
            "lib.config.resolver.get_project_manager",
        ):
            monkeypatch.setattr(target, _raising_pm)
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
        resolved = await ConfigResolver(async_session_factory).resolve_image_backend(project, {}, generation_type="t2i")
        assert worker_provider == resolved.provider_id == "openai"

    async def test_video_alignment(self, monkeypatch):
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        project = {"video_backend": "ark/doubao-seedance-1-5-pro-251215"}
        _patch_pm(monkeypatch, project)
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}

        worker_provider = await _extract_provider(task)
        resolved = await ConfigResolver(async_session_factory).resolve_video_backend(project, {}, generation_type="i2v")
        assert worker_provider == resolved.provider_id == "ark"


class TestExecuteTaskPollTimeout:
    """派发时读一次全局轮询超时并写进任务字典下传；只有视频两条 lane 需要它。"""

    @pytest.fixture
    def patch_settings_db(self, db_factory, monkeypatch):
        @contextlib.asynccontextmanager
        async def _safe_session_factory():
            async with db_factory() as session:
                yield session

        bind_safe_session_factory(monkeypatch, _safe_session_factory)
        return db_factory

    @staticmethod
    def _capture_dispatch(monkeypatch) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        async def _fake_execute(task: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            captured.update(task)
            return {"ok": True}

        monkeypatch.setattr(stub_executors, "generation", _fake_execute)
        return captured

    @staticmethod
    async def _dispatch(task: dict[str, Any]) -> None:
        worker = GenerationWorker(
            queue=FakeWorkerQueue(), executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        await worker._process_task({"task_id": "poll-timeout", **task}, claimed_provider_id="ark")

    @pytest.mark.parametrize("task_type", ["video", "reference_video"])
    async def test_video_lanes_carry_configured_timeout(self, patch_settings_db, monkeypatch, task_type):
        from lib.config.service import ConfigService

        async with patch_settings_db() as session:
            await ConfigService(session).set_video_poll_timeout_seconds(7200)
            await session.commit()

        captured = self._capture_dispatch(monkeypatch)
        await self._dispatch({"task_type": task_type})

        assert captured["video_poll_timeout_seconds"] == 7200

    @pytest.mark.usefixtures("patch_settings_db")
    async def test_defaults_when_setting_absent(self, monkeypatch):
        captured = self._capture_dispatch(monkeypatch)
        await self._dispatch({"task_type": "video"})

        assert captured["video_poll_timeout_seconds"] == 3600

    @pytest.mark.usefixtures("patch_settings_db")
    async def test_non_video_lane_is_not_stamped(self, monkeypatch):
        captured = self._capture_dispatch(monkeypatch)
        await self._dispatch({"task_type": "storyboard"})

        assert "video_poll_timeout_seconds" not in captured


class TestGenerationWorker:
    @pytest.mark.asyncio
    async def test_process_task_success_and_failure(self, monkeypatch):
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _fake_execute(task, **_kwargs):
            return {"ok": task["task_id"]}

        monkeypatch.setattr(
            stub_executors,
            "generation",
            _fake_execute,
        )
        await worker._process_task({"task_id": "t1"})
        assert queue.succeeded == [("t1", {"ok": "t1"})]

        async def _raise(_task, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(stub_executors, "generation", _raise)
        await worker._process_task({"task_id": "t2"})
        assert queue.failed
        assert queue.failed[0][0] == "t2"

    @pytest.mark.asyncio
    async def test_reused_video_result_reaches_the_normal_succeeded_terminal_state(self, monkeypatch):
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        reused = {
            "version": 2,
            "file_path": "videos/scene_E1S01.mp4",
            "resource_type": "videos",
            "resource_id": "E1S01",
            "reused_existing": True,
        }

        async def _fake_execute(_task, *, claimed_provider_id):
            assert claimed_provider_id
            return reused

        monkeypatch.setattr(stub_executors, "generation", _fake_execute)

        await worker._process_task({"task_id": "task-reuse", "task_type": "video"})

        assert queue.succeeded == [("task-reuse", reused)]
        assert queue.failed == []

    @pytest.mark.asyncio
    async def test_process_reference_task_requeues_when_execution_provider_changes(self, monkeypatch, worker_db):
        """执行入口解析到别的 provider 时不占旧槽提交，而是刷新投影并回队重认领。"""

        from lib.generation.generation_queue import DispatchProviderChanged

        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _changed(_task, *, claimed_provider_id):
            assert claimed_provider_id == "ark"
            raise DispatchProviderChanged(claimed_provider_id="ark", actual_provider_id="minimax")

        monkeypatch.setattr(stub_executors, "generation", _changed)
        await seed_running_task(worker_db, "ref-provider-changed", task_type="reference_video", resource_id="E1U1")

        await worker._process_task(
            {"task_id": "ref-provider-changed", "task_type": "reference_video"},
            claimed_provider_id="ark",
        )

        assert queue.persisted_providers == [("ref-provider-changed", "minimax")]
        assert await task_status(worker_db, "ref-provider-changed") == "queued"
        assert queue.succeeded == []
        assert queue.failed == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failed_rows", [1, 0])
    async def test_process_reference_task_closes_terminal_state_when_provider_requeue_fails(
        self, monkeypatch, worker_db, failed_rows: int
    ):
        from lib.generation.generation_queue import DispatchProviderChanged

        queue = FakeWorkerQueue(failed_rows=failed_rows)
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _changed(_task, *, claimed_provider_id):
            raise DispatchProviderChanged(claimed_provider_id=claimed_provider_id, actual_provider_id="minimax")

        monkeypatch.setattr(stub_executors, "generation", _changed)
        # 库里没有这行 running 记录，回队的 guarded UPDATE 命中 0 行——回队失败正是本用例的前提

        await worker._process_task(
            {"task_id": "ref-provider-changed", "task_type": "reference_video"},
            claimed_provider_id="ark",
        )

        assert queue.succeeded == []
        assert queue.failed == [
            (
                "ref-provider-changed",
                '[dispatch_provider_requeue_failed] {"actual_provider_id": "minimax", "claimed_provider_id": "ark"}',
            )
        ]
        # 终态写入命中 0 行时任务保持原状，不被改判为打断
        assert queue.interrupted == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("affected", "expected"), [(1, True), (0, False)])
    async def test_requeue_single_task_reports_guarded_update_result(self, monkeypatch, affected: int, expected: bool):
        from contextlib import asynccontextmanager

        class _Result:
            rowcount = affected

        class _Session:
            async def execute(self, _statement):
                return _Result()

            async def commit(self):
                return None

        @asynccontextmanager
        async def _session_factory():
            yield _Session()

        bind_safe_session_factory(monkeypatch, _session_factory)

        assert (
            await GenerationWorker(
                queue=FakeWorkerQueue(), executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
            )._requeue_single_task("candidate")
            is expected
        )

    @pytest.mark.asyncio
    async def test_requeue_single_task_reports_database_failure(self, monkeypatch):
        def _session_factory() -> contextlib.AbstractAsyncContextManager[object]:
            raise RuntimeError("database unavailable")

        bind_safe_session_factory(monkeypatch, _session_factory)

        assert (
            await GenerationWorker(
                queue=FakeWorkerQueue(), executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
            )._requeue_single_task("running")
            is False
        )

    @pytest.mark.asyncio
    async def test_process_task_script_edit_error_encodes_key_and_params(self, monkeypatch):
        """apply_unit_video_assets 经异步任务队列（非 upload_unit_video 同步路由）抛出时，
        error_message 落成可翻译的 [key] {params} 结构而非 str(exc) 的固定中文，任务状态
        接口按 Accept-Language 渲染时才不会给 en/vi 用户漏出中文；params 一并落库才能在
        渲染侧还原成完整的翻译占位符替换（如 resolve_items 的 kind/type_name）。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _raise_script_edit_error(_task, **_kwargs):
            raise ScriptEditError(
                "segments 必须是列表，当前为 dict",
                key="script_edit_items_not_list",
                kind="segments",
                type_name="dict",
            )

        monkeypatch.setattr(
            stub_executors,
            "generation",
            _raise_script_edit_error,
        )
        await worker._process_task({"task_id": "t_script_edit"})
        assert queue.failed
        assert queue.failed[0] == (
            "t_script_edit",
            '[script_edit_items_not_list] {"kind": "segments", "type_name": "dict"}',
        )

    @pytest.mark.asyncio
    async def test_process_task_script_edit_error_unregistered_key_falls_back(self, monkeypatch):
        """两份登记（errors.py 的翻译 key、task_failure.FAILURE_CODE_KEYS 的 worker 编码表）
        靠约定同步，非运行时校验——新 raise 点忘了同步登记时，encode_failure 对未登记 key
        抛 KeyError 不能打断 mark_task_failed，否则任务会卡在 running 而非落终态。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _raise_unregistered(_task, **_kwargs):
            raise ScriptEditError("尚未登记的错误", key="script_edit_not_yet_registered")

        monkeypatch.setattr(
            stub_executors,
            "generation",
            _raise_unregistered,
        )
        await worker._process_task({"task_id": "t_unregistered"})
        assert queue.failed
        assert queue.failed[0] == ("t_unregistered", "[script_edit_error]")

    @pytest.mark.asyncio
    async def test_process_task_script_edit_error_circular_params_falls_back(self, monkeypatch):
        """params 含循环引用时 json.dumps 抛 ValueError（而非 TypeError）——同一条降级路径
        也要接住这个分支，否则序列化失败照样打断 mark_task_failed，任务卡在 running。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _raise_circular_params(_task, **_kwargs):
            circular: dict[str, Any] = {}
            circular["self"] = circular
            raise ScriptEditError(
                "generated_assets 必须是 dict",
                key="script_edit_generated_assets_invalid",
                circular=circular,
            )

        monkeypatch.setattr(
            stub_executors,
            "generation",
            _raise_circular_params,
        )
        await worker._process_task({"task_id": "t_circular"})
        assert queue.failed
        assert queue.failed[0] == ("t_circular", "[script_edit_error]")

    @pytest.mark.asyncio
    async def test_process_task_executor_cancelled_error_lands_cancelled(self, worker_db):
        """执行器抛 CancelledError（进程级打断）：任务不停在 running，落 cancelled 后重新抛出。"""
        await seed_running_task(worker_db, "tc", task_type="storyboard", media_type="image")

        async def _cancelled(_task, *, claimed_provider_id):
            raise asyncio.CancelledError

        worker = GenerationWorker(
            queue=db_queue(worker_db),
            provider_projection=_fixed_projection,
            executor=_cancelled,
            resume_executor=stub_executors.execute_resume,
        )
        with pytest.raises(asyncio.CancelledError):
            await worker._process_task({"task_id": "tc", "media_type": "image"})

        assert await task_status(worker_db, "tc") == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", ["succeeded", "failed"])
    async def test_process_task_zero_row_terminal_write_leaves_task_untouched(self, worker_db, outcome: str):
        """终态写入命中 0 行（任务已不在 running）：该写入不生效，任务不被改判为 cancelled。"""
        # 行处于 queued：终态写入的守卫只放行 running，而打断兜底会把 queued 翻成 cancelled
        await seed_running_task(worker_db, "t0rows", status="queued", started_at=None)

        async def _execute(_task, *, claimed_provider_id):
            if outcome == "failed":
                raise RuntimeError("boom")
            return {"result": "ok"}

        worker = GenerationWorker(
            queue=db_queue(worker_db),
            provider_projection=_fixed_projection,
            executor=_execute,
            resume_executor=stub_executors.execute_resume,
        )
        await worker._process_task({"task_id": "t0rows", "media_type": "video"})

        assert await task_status(worker_db, "t0rows") == "queued"

    @pytest.mark.asyncio
    async def test_stop_waits_for_running_task_to_finish_as_succeeded(self, concurrent_session_factory, monkeypatch):
        """服务关停不取消在跑任务：stop 等它跑完，任务落 succeeded；关停期间一直是 running。"""
        bind_safe_session_factory(monkeypatch, concurrent_session_factory)
        worker_db = concurrent_session_factory
        await seed_running_task(worker_db, "shutdown-run", task_type="storyboard", media_type="image")
        started = asyncio.Event()
        release = asyncio.Event()

        async def _execute(_task, *, claimed_provider_id):
            started.set()
            await release.wait()
            return {"ok": True}

        worker = GenerationWorker(
            queue=db_queue(worker_db),
            provider_projection=_fixed_projection,
            executor=_execute,
            resume_executor=stub_executors.execute_resume,
        )
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01
        running = asyncio.create_task(
            worker._process_task({"task_id": "shutdown-run", "media_type": "image"}),
            name="generation-image-shutdown-run",
        )
        worker._slots.register("test", "image", "shutdown-run", running)

        # 探针占用：关停对全部在跑执行体挂完成回调时置位，标志 stop 已进入「等在跑任务」
        awaiting_inflight = asyncio.Event()

        class _InflightProbe(asyncio.Future):
            def add_done_callback(self, fn, *, context=None):
                awaiting_inflight.set()
                super().add_done_callback(fn, context=context)

        probe = _InflightProbe()
        worker._slots.register("probe", "video", "stop-probe", probe)

        await worker.start()
        await started.wait()
        assert await task_status(worker_db, "shutdown-run") == "running"

        stopping = asyncio.create_task(worker.stop())
        probe_hit = asyncio.create_task(awaiting_inflight.wait())
        await asyncio.wait({stopping, probe_hit}, return_when=asyncio.FIRST_COMPLETED)

        assert probe_hit.done(), "stop 必须等在跑任务跑完"
        assert not stopping.done(), "stop 必须等在跑任务跑完"
        assert not running.done(), "stop 不得打断在跑任务"

        probe.set_result(None)
        release.set()
        await asyncio.wait_for(running, timeout=2.0)
        await asyncio.wait_for(stopping, timeout=2.0)

        assert not running.cancelled()
        assert await task_status(worker_db, "shutdown-run") == "succeeded"
        assert worker._main_task is None

    @pytest.mark.asyncio
    async def test_drain_finished_tasks_absorbs_cancelled_error(self):
        """被打断的执行体被 drain：不抛、从台账移除，并由 drain 端兜底落终态。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

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
        # 子任务来不及自落终态时，drain 端兜底落终态。
        assert queue.interrupted == ["tid"]

    @pytest.mark.asyncio
    async def test_drain_finished_tasks_drains_success_and_failure(self):
        """非打断路径：成功 task 走 .result() 无异常，失败 task 走 except 分支，均不触发兜底。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

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
        assert queue.interrupted == []

    @pytest.mark.asyncio
    async def test_drain_fallback_terminal_write_failure_does_not_propagate(self):
        """drain 端兜底落终态自身抛错时只 warning，不冒泡（不挂掉主循环）。"""

        class _RaisingQueue(FakeWorkerQueue):
            async def mark_task_interrupted(self, task_id):
                raise RuntimeError("db down")

        queue = _RaisingQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _long():
            await asyncio.sleep(10)

        t = asyncio.create_task(_long())
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)  # 驱动到 done(cancelled)，吞掉取消结果
        assert t.cancelled()
        worker._slots.register("test", "video", "tid", t)

        # 兜底抛错被 except 吞掉，drain 不抛、task 仍被移除
        await worker._drain_finished_tasks()
        assert worker._slots.occupied("test", "video") == 0

    @pytest.mark.asyncio
    async def test_drain_lands_cancelled_when_interruption_hits_before_process_task_try(self, monkeypatch):
        """打断落在 _process_task 进 try 之前（provider 投影 await）：drain 端兜底落终态。"""
        queue = FakeWorkerQueue()
        in_extract = asyncio.Event()

        async def _blocking_projection(_task):
            in_extract.set()
            await asyncio.sleep(10)  # 停在入口解析，模拟打断落在 _process_task 的 try 之前
            return "test"

        worker = GenerationWorker(
            queue=queue,
            provider_projection=_blocking_projection,
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        async def _execute(_task, **_kwargs):
            raise AssertionError("execute 不应被调用：打断在 provider 投影阶段就到")

        monkeypatch.setattr(stub_executors, "generation", _execute)

        t = asyncio.create_task(
            worker._process_task({"task_id": "tid", "media_type": "video"}),
            name="generation-video-tid",
        )
        worker._slots.register("test", "video", "tid", t)

        await worker.start()
        await in_extract.wait()  # 确保停在 provider 投影（try 之前）
        t.cancel()
        await asyncio.wait_for(queue.claim_after_interruption.wait(), timeout=2.0)

        # _process_task 没机会落终态（打断在 try 之前）→ drain 端兜底
        assert queue.interrupted == ["tid"]
        # 主循环仍存活
        assert worker._main_task is not None
        assert not worker._main_task.done()

        await asyncio.wait_for(worker.stop(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_run_loop_survives_inflight_task_interruption(self, monkeypatch):
        """单个执行体被打断：任务落终态，但 worker 主循环不退出，显式 stop 才退出。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        started = asyncio.Event()

        async def _block(_task, **_kwargs):
            started.set()
            await asyncio.sleep(10)  # 模拟长时间运行的生成任务

        monkeypatch.setattr(stub_executors, "generation", _block)

        t = asyncio.create_task(
            worker._process_task({"task_id": "tid", "media_type": "video"}),
            name="generation-video-tid",
        )
        worker._slots.register("test", "video", "tid", t)

        await worker.start()
        await started.wait()  # 确保 _process_task 已进入 execute（_extract_provider 已完成）

        t.cancel()
        await asyncio.wait_for(queue.claim_after_interruption.wait(), timeout=2.0)

        # _process_task 自落终态后 drain 端再兜底一次，守卫保证第二次无副作用
        assert set(queue.interrupted) == {"tid"}
        # 主循环吸收 CancelledError 后仍存活
        assert worker._main_task is not None
        assert not worker._main_task.done()

        await asyncio.wait_for(worker.stop(), timeout=2.0)
        assert worker._main_task is None
        assert queue.released

    @pytest.mark.asyncio
    async def test_run_loop_survives_consecutive_interruptions_and_keeps_claiming(self, monkeypatch):
        """连续打断多个 inflight 执行体后，主循环仍存活并能继续 claim 新任务。"""

        class _GatedQueue(FakeWorkerQueue):
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
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"gemini-aistudio": {"image": 5, "video": 3}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        release = asyncio.Event()
        fresh_started = asyncio.Event()
        entered: set[str] = set()
        all_entered = asyncio.Event()

        async def _block(task, **_kwargs):
            entered.add(task["task_id"])
            if task["task_id"] == "fresh-img":
                fresh_started.set()
            if set(vid_ids) <= entered:
                all_entered.set()
            await release.wait()
            return {"ok": True}

        monkeypatch.setattr(stub_executors, "generation", _block)

        vid_ids = ["vid-0", "vid-1", "vid-2"]
        running: list[asyncio.Task] = []
        for tid in vid_ids:
            t = asyncio.create_task(
                worker._process_task({"task_id": tid, "media_type": "video"}),
                name=f"generation-video-{tid}",
            )
            worker._slots.register("gemini-aistudio", "video", tid, t)
            running.append(t)

        await worker.start()
        await all_entered.wait()  # 等三个任务都进入 execute

        for t in running:
            t.cancel()
        await asyncio.gather(*running, return_exceptions=True)

        # 主循环存活 + 三个任务都落终态
        assert worker._main_task is not None
        assert not worker._main_task.done()
        assert set(vid_ids) <= set(queue.interrupted)

        # 打断后仍能 claim 并 dispatch 新任务
        queue.allow_new = True
        await asyncio.wait_for(fresh_started.wait(), timeout=2.0)
        assert queue.new_dispatched is True
        assert "fresh-img" in worker._slots.active_task_ids()

        # 收尾：放行 fresh-img 后正常停机
        release.set()
        await asyncio.wait_for(worker.stop(), timeout=2.0)
        assert queue.released

    @pytest.mark.asyncio
    async def test_start_stop_run_loop_releases_lease(self):
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
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

        class _ClaimableQueue(FakeWorkerQueue):
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
                        "payload": {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"},
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
            capacity=capacity_table({"gemini-aistudio": {"image": 3, "video": 2}, "ark": {"image": 0, "video": 2}}),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        async def _fake_execute(task, **_kwargs):
            return {"ok": True}

        monkeypatch.setattr(
            stub_executors,
            "generation",
            _fake_execute,
        )

        claimed = await worker._claim_tasks()
        assert claimed
        assert worker._slots.occupied("gemini-aistudio", "image") == 1
        assert worker._slots.occupied("ark", "video") == 1
        assert worker._slots.active_task_ids() == {"img1", "vid1"}

        # Wait for tasks to complete
        await asyncio.gather(*worker._slots.all_active_tasks(), return_exceptions=True)

    @pytest.mark.asyncio
    async def test_claim_requeue_on_full_pool_refreshes_provider_projection(self, monkeypatch, worker_db):
        """池满回队前把重派生的 provider 刷回投影列。

        走到二次校验池满这条路，说明存量投影与现值分裂（NULL 兜底，或入队后剧本参考集 /
        供应商配置被改）；不刷新的话存量值躲过 pool_full 的 SQL 过滤，满池期间每个 cycle
        都重复 claim → requeue → break，同 lane 其他可跑任务被持续排头阻塞。
        """

        class _StaleProjectionQueue(FakeWorkerQueue):
            def __init__(self):
                super().__init__()
                self._tasks = [
                    {
                        "task_id": "vid-ready",
                        "task_type": "video",
                        "media_type": "video",
                        "provider_id": "ark",
                        "payload": {"video_provider_i2v": "ark/model"},
                    },
                    {
                        "task_id": "vid-stale",
                        "task_type": "reference_video",
                        "media_type": "video",
                        "provider_id": "ark",  # 入队时的投影，与现值分裂
                        "payload": {},
                    },
                ]
                self.claim_kwargs: list[dict] = []

            async def claim_next_task(self, media_type, **kwargs):  # type: ignore[override]
                self.claim_kwargs.append(kwargs)
                if media_type == "video" and self._tasks:
                    return self._tasks.pop()
                return None

        queue = _StaleProjectionQueue()

        async def _current_provider(task):
            return "minimax" if task["task_id"] == "vid-stale" else "ark"

        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"minimax": {"video": 1}, "ark": {"video": 1}}),
            provider_projection=_current_provider,
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

        async def _execute(_task, *, claimed_provider_id):
            assert claimed_provider_id == "ark"
            return {"ok": True}

        monkeypatch.setattr(stub_executors, "generation", _execute)

        # 永不完成的占位 future 把 minimax 的 video 槽占满；不用 sleep，避免真实时间等待
        occupier = asyncio.get_running_loop().create_future()
        worker._slots.register("minimax", "video", "vid-running", occupier)
        await seed_running_task(worker_db, "vid-stale", task_type="reference_video", resource_id="E1U1")
        try:
            await worker._claim_tasks()
            await asyncio.gather(*[t for t in worker._slots.all_active_tasks() if t is not occupier])
        finally:
            occupier.cancel()

        assert await task_status(worker_db, "vid-stale") == "queued"
        assert queue.persisted_providers == [("vid-stale", "minimax")]
        assert queue.succeeded == [("vid-ready", {"ok": True})]
        assert any(kwargs.get("exclude_task_ids") == frozenset({"vid-stale"}) for kwargs in queue.claim_kwargs)

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
            queue=FakeWorkerQueue(),
            capacity=capacity_table(
                {
                    "full": {"image": 1, "video": 0},  # cap=1 占 1 → 满
                    "roomy": {"image": 2, "video": 0},  # cap=2 占 1 → 有空
                    "downgraded": {"image": 0, "video": 0},  # 占 1 但 cap=0（被 reload 降级）
                }
            ),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
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


class TestOrphanOnceAndLeaseFlap:
    """orphan 一次性扫描 + lease flap 阈值。"""

    def _build_worker(self) -> tuple[GenerationWorker, FakeWorkerQueue, list[int]]:
        """构造 worker；返回 (worker, queue, scan_count)。scan_count 记录扫描次数。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        scan_count: list[int] = []

        async def _spy_scan():
            scan_count.append(1)

        # 替换孤儿扫描入口为 spy，便于精确断言扫描次数
        worker._recovery.handle_orphans = _spy_scan
        return worker, queue, scan_count

    @pytest.mark.asyncio
    async def test_orphan_scanned_once_on_first_lease_acquire(self):
        worker, _, scan_count = self._build_worker()
        worker._owns_lease = True
        worker._orphan_handled_once = False

        # 模拟主循环里那段守卫
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._recovery.handle_orphans()
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
                await worker._recovery.handle_orphans()
                worker._orphan_handled_once = True

        assert len(scan_count) == 1

    @pytest.mark.asyncio
    async def test_orphan_does_not_rescan_on_short_lease_flap(self):
        """lease flap < lease_ttl：不重扫。"""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        # 首次获得 lease 扫一次
        worker._owns_lease = True
        await worker._recovery.handle_orphans()
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
            await worker._recovery.handle_orphans()
        assert len(scan_count) == 1, "短 flap 不应重扫"

    @pytest.mark.asyncio
    async def test_orphan_does_not_rescan_below_3x_ttl(self):
        """lease_ttl < lost < 3×lease_ttl：仍不重扫（边界）。"""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        worker._owns_lease = True
        await worker._recovery.handle_orphans()
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
            await worker._recovery.handle_orphans()
        assert len(scan_count) == 1, "15s 仍 < 3×ttl=30s，不应重扫"

    @pytest.mark.asyncio
    async def test_orphan_rescans_after_real_lease_handoff(self):
        """lost > 3×lease_ttl：清零开关，下次重扫。"""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        worker._owns_lease = True
        await worker._recovery.handle_orphans()
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
            await worker._recovery.handle_orphans()
            worker._orphan_handled_once = True
        assert len(scan_count) == 2, "lost 超过 3×ttl 应重扫一次"


class TestStartupInterruptedCallSettlement:
    """启动收口挂在孤儿处理旁：同一 lease 内只跑一次，且跑在孤儿处理之后。"""

    class _CyclingQueue(FakeWorkerQueue):
        """认领若干轮后置位事件，让测试等到主循环真的转了几圈再断言。"""

        def __init__(self, cycles: int = 3):
            super().__init__()
            self._target_cycles = cycles
            self._cycles = 0
            self.cycled = asyncio.Event()

        async def claim_next_task(self, media_type, **_kwargs):
            if media_type == "image":
                self._cycles += 1
                if self._cycles >= self._target_cycles:
                    self.cycled.set()
            return

    async def _run_until_settled(self, worker, queue) -> None:
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01
        await worker.start()
        try:
            await asyncio.wait_for(queue.cycled.wait(), timeout=5)
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_settles_once_and_after_orphan_handling(self):
        queue = self._CyclingQueue()
        queue._orphans = [
            {"task_id": "orphan-image", "status": "running", "task_type": "gen_image", "media_type": "image"}
        ]
        seen_failed_orphans: list[list[str]] = []

        async def _settle(*, taskless_started_before: datetime | None) -> int:
            seen_failed_orphans.append([task_id for task_id, _error in queue.failed])
            return 0

        worker = GenerationWorker(
            queue=queue,
            settle_interrupted_calls=_settle,
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )
        await self._run_until_settled(worker, queue)

        assert seen_failed_orphans == [["orphan-image"]], "收口只跑一次，且孤儿已先被处理"

    @pytest.mark.asyncio
    async def test_rescan_after_lease_loss_keeps_taskless_calls(self):
        """首次收口只收 worker 构造之前发起的无任务行；之后的重扫只收绑定任务的行。"""
        queue = self._CyclingQueue()
        seen: list[datetime | None] = []

        async def _settle(*, taskless_started_before: datetime | None) -> int:
            seen.append(taskless_started_before)
            return 0

        before = datetime.now(UTC)
        worker = GenerationWorker(
            queue=queue,
            settle_interrupted_calls=_settle,
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )
        after = datetime.now(UTC)
        await self._run_until_settled(worker, queue)
        assert len(seen) == 1
        assert seen[0] is not None
        assert before <= seen[0] <= after

        # lease 丢失超过阈值后的重扫：进程仍存活，无任务身份的行可能正在本进程里跑。
        worker._orphan_handled_once = False
        queue._cycles = 0
        queue.cycled.clear()
        await self._run_until_settled(worker, queue)
        assert seen[1:] == [None]

    @pytest.mark.asyncio
    async def test_default_wiring_flips_orphan_pending_call_row(self, worker_db):
        """不注入替身时走真实 Ledger，落在队列那处接线的库里：无任务身份的 pending 行翻成 failed[interrupted]。"""
        from lib.db.repositories.usage_repo import UsageRepository

        async with worker_db() as session:
            call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="text", model="m", provider="anthropic"
            )

        queue = self._CyclingQueue()
        await self._run_until_settled(
            GenerationWorker(
                queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
            ),
            queue,
        )

        async with worker_db() as session:
            row = await session.get(ApiCall, call_id)
        assert (row.status, row.error_code) == ("failed", "interrupted")
