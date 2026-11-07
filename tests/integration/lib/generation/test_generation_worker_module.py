import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
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
from lib.project.task_project_claim import revoke_task_project_claims
from lib.script.script_editor import ScriptEditError
from tests.factories import activate_reference_project
from tests.fakes import bind_safe_session_factory, bind_session_factory
from tests.integration.lib.generation.worker_support import (
    FakeWorkerQueue,
    capacity_table,
    db_queue,
    seed_running_task,
    stub_executors,
    task_status,
    use_worker_project,
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


@pytest.fixture
async def patch_empty_db(db_factory, monkeypatch):
    """Replace global async_session_factory with empty memory DB, isolating real development databases.

    Without project_name, _extract_provider uses worker-imported async_session_factory through ConfigResolver for global defaults. Video enqueue derivation/projection late-import globals from ``lib.db``. Without isolation, locally configured ready providers would contaminate fallback assertions."""
    bind_session_factory(monkeypatch, db_factory)
    monkeypatch.setattr("lib.generation.generation_worker.async_session_factory", db_factory)
    return db_factory


class TestExtractProvider:
    """_extract_provider thinly projects the resolution chain: dispatch by task_type and return .provider_id."""

    @pytest.mark.usefixtures("patch_empty_db")
    async def test_video_payload_identity_is_advisory_only(self):
        """Storyboard-video claims ignore legacy payload identity; absent current configuration uses rate-limit defaults only."""
        task = {"payload": {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"}, "task_type": "video"}
        assert await _extract_provider(task) == DEFAULT_PROVIDER

    async def test_image_payload_provider(self):
        """Historical payload image_provider supplies projection."""
        task = {"payload": {"image_provider": "gemini-vertex"}, "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    @pytest.mark.usefixtures("patch_empty_db")
    async def test_default_when_unresolvable(self):
        """Without project, payload or global configuration, use DEFAULT_PROVIDER for rate limiting only.

        Isolate global DB with patch_empty_db; otherwise ready providers in local development databases replace DEFAULT_PROVIDER via auto-resolution and contaminate assertions."""
        task = {"payload": {}}
        assert await _extract_provider(task) == DEFAULT_PROVIDER

    async def test_project_level_video_backend(self, monkeypatch):
        """Project video_backend outranks global defaults."""
        use_worker_project(monkeypatch, {"video_backend": "ark/doubao-seedance-2-0-260128"})
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}
        assert await _extract_provider(task) == "ark"

    async def test_project_level_image_t2i(self, monkeypatch):
        """Image projection uses representative generation_type=t2i and project image_provider_t2i."""
        use_worker_project(monkeypatch, {"image_provider_t2i": "gemini-vertex/imagen-3"})
        task = {"payload": {}, "project_name": "demo", "task_type": "storyboard"}
        assert await _extract_provider(task) == "gemini-vertex"

    async def test_reference_video_routes_to_video_lane(self, monkeypatch):
        """reference_video task_type resolves video_backend through video lane, never image slots.

        Project video_backend Ark differs from image_provider_t2i gemini-vertex. Claim-time projection must choose Ark; treating task_type != "video" as image would choose an image-only provider and immediately fail video_max==0 as unsupported video generation."""
        use_worker_project(
            monkeypatch,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "image_provider_t2i": "gemini-vertex/imagen-3",
            },
        )
        task = {"payload": {}, "project_name": "demo", "task_type": "reference_video"}
        assert await _extract_provider(task) == "ark"

    async def test_reference_video_prefers_r2v_bucket_provider(self, monkeypatch):
        """If unit-level video state cannot be read, use current r2v configuration, never payload enqueue-time pins."""
        use_worker_project(
            monkeypatch,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "video_provider_r2v": "dashscope/happyhorse-1.1-r2v",
            },
        )
        task = {
            "payload": {"video_provider_r2v": "ark/doubao-seedance-2-0-260128"},
            "project_name": "demo",
            "task_type": "reference_video",
        }
        assert await _extract_provider(task) == "dashscope"

    @pytest.mark.parametrize(
        ("text", "expected_provider"),
        [("@[A] 走进房间", "dashscope"), ("空镜头", "ark")],
    )
    async def test_reference_video_routes_by_hydrated_reference_bucket(
        self, tmp_path, monkeypatch, text, expected_provider
    ):
        """reference_video projection buckets actual usable references: images choose r2v provider, imageless units i2v, matching execution downgrade."""
        (tmp_path / "characters").mkdir()
        (tmp_path / "characters" / "A.png").write_bytes(b"image")
        project = activate_reference_project(
            tmp_path,
            {
                "video_provider_i2v": "ark/doubao-seedance-2-0-260128",
                "video_provider_r2v": "dashscope/happyhorse-1.1-r2v",
                "video_generate_audio": True,
                "characters": {"A": {"description": "x", "character_sheet": "characters/A.png"}},
            },
        )
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
            "video_provider_i2v": "ark/doubao-seedance-2-0-260128",
            "video_provider_r2v": "dashscope/happyhorse-1.1-r2v",
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

        assert await _extract_provider(task) == "dashscope"

    async def test_reference_video_script_read_failure_falls_back_to_r2v(self, monkeypatch):
        """Script-read failure uses representative r2v projection without bubbling out and blocking claims."""

        def _load_script(self, name, filename):
            raise ScriptEditError("broken")

        project = {
            "video_provider_i2v": "ark/doubao-seedance-2-0-260128",
            "video_provider_r2v": "dashscope/happyhorse-1.1-r2v",
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
        assert await _extract_provider(task) == "dashscope"

    async def test_deleted_project_load_failure_falls_back_not_raises(self, monkeypatch):
        """Residual tasks for deleted/unreadable projects fall back to DEFAULT_PROVIDER even if load_project raises; one bad task must not break the entire claim loop."""

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
    """M5 projection alignment: worker provider_id matches execution resolution for identical project/payload."""

    async def test_image_alignment(self, monkeypatch):
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        project = {"image_provider_t2i": "openai/gen-1", "image_provider_i2i": "openai/edit-1"}
        use_worker_project(monkeypatch, project)
        task = {"payload": {}, "project_name": "demo", "task_type": "storyboard"}

        worker_provider = await _extract_provider(task)
        resolved = await ConfigResolver(async_session_factory).resolve_image_backend(project, {}, generation_type="t2i")
        assert worker_provider == resolved.provider_id == "openai"

    async def test_video_alignment(self, monkeypatch):
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        project = {"video_backend": "ark/doubao-seedance-2-0-260128"}
        use_worker_project(monkeypatch, project)
        task = {"payload": {}, "project_name": "demo", "task_type": "video"}

        worker_provider = await _extract_provider(task)
        resolved = await ConfigResolver(async_session_factory).resolve_video_backend(project, {}, generation_type="i2v")
        assert worker_provider == resolved.provider_id == "ark"


class TestExecuteTaskPollTimeout:
    """Read global polling timeout once at dispatch and forward it in task dicts; only the two video lanes need it."""

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
        await worker._process_task({"task_id": "t1"}, claimed_provider_id="test")
        assert queue.succeeded == [("t1", {"ok": "t1"})]

        async def _raise(_task, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(stub_executors, "generation", _raise)
        await worker._process_task({"task_id": "t2"}, claimed_provider_id="test")
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

        await worker._process_task({"task_id": "task-reuse", "task_type": "video"}, claimed_provider_id="test")

        assert queue.succeeded == [("task-reuse", reused)]
        assert queue.failed == []

    @pytest.mark.asyncio
    async def test_process_reference_task_requeues_when_execution_provider_changes(self, monkeypatch, worker_db):
        """If execution resolves another provider, never submit under the old slot; refresh projection and requeue for reclaim."""

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
        # No running row exists; guarded requeue UPDATE affects zero rows, the premise of this case.

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
        # Zero-row terminal writes preserve task state rather than reclassify it interrupted.
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
        """If apply_unit_video_assets raises through async task queues rather than synchronous upload_unit_video, persist translatable [key] {params}, not fixed Chinese str(exc). Accept-Language rendering must serve en/vi correctly; retain params for complete placeholders such as resolve_items kind/type_name."""
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
        await worker._process_task({"task_id": "t_script_edit"}, claimed_provider_id="test")
        assert queue.failed
        assert queue.failed[0] == (
            "t_script_edit",
            '[script_edit_items_not_list] {"kind": "segments", "type_name": "dict"}',
        )

    async def test_task_whose_project_was_deleted_after_its_last_write_does_not_succeed(self, monkeypatch):
        """If projects are deleted after executor writes but before return, artifacts disappear with directories and tasks cannot report success."""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _project_deleted_before_return(task, **_kwargs):
            revoke_task_project_claims([task["task_id"]])
            return {"ok": True}

        monkeypatch.setattr(stub_executors, "generation", _project_deleted_before_return)
        await worker._process_task({"task_id": "t_deleted", "project_name": "demo"}, claimed_provider_id="test")
        assert queue.succeeded == []
        assert queue.failed == [("t_deleted", "[project_deleted_during_task]")]

    @pytest.mark.asyncio
    async def test_process_task_script_edit_error_unregistered_key_falls_back(self, monkeypatch):
        """Translation keys in errors.py and worker FAILURE_CODE_KEYS rely on coordinated registration, not runtime validation. Missing-code encode_failure KeyError must not interrupt mark_task_failed or strand tasks running."""
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
        await worker._process_task({"task_id": "t_unregistered"}, claimed_provider_id="test")
        assert queue.failed
        assert queue.failed[0] == ("t_unregistered", "[script_edit_error]")

    @pytest.mark.asyncio
    async def test_process_task_script_edit_error_circular_params_falls_back(self, monkeypatch):
        """Circular params make json.dumps raise ValueError, not TypeError. Handle the same fallback or serialization errors would interrupt mark_task_failed and strand running tasks."""
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
        await worker._process_task({"task_id": "t_circular"}, claimed_provider_id="test")
        assert queue.failed
        assert queue.failed[0] == ("t_circular", "[script_edit_error]")

    @pytest.mark.asyncio
    async def test_process_task_executor_cancelled_error_lands_cancelled(self, worker_db):
        """Executor CancelledError settles cancelled before reraising, never leaving running."""
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
            await worker._process_task({"task_id": "tc", "media_type": "image"}, claimed_provider_id="test")

        assert await task_status(worker_db, "tc") == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", ["succeeded", "failed"])
    async def test_process_task_zero_row_terminal_write_leaves_task_untouched(self, worker_db, outcome: str):
        """Zero-row terminal writes mean tasks already left running; leave state unchanged rather than reclassify cancelled."""
        # Row is queued; terminal guards accept only running, while interruption fallback would wrongly turn queued into cancelled.
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
        await worker._process_task({"task_id": "t0rows", "media_type": "video"}, claimed_provider_id="test")

        assert await task_status(worker_db, "t0rows") == "queued"

    @pytest.mark.asyncio
    async def test_stop_waits_for_running_task_to_finish_as_succeeded(self, concurrent_session_factory, monkeypatch):
        """Shutdown never cancels running tasks: stop awaits completion and succeeded, retaining running throughout shutdown."""
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
            worker._process_task({"task_id": "shutdown-run", "media_type": "image"}, claimed_provider_id="test"),
            name="generation-image-shutdown-run",
        )
        worker._slots.register("test", "image", "shutdown-run", running)

        # Occupancy probe fires when stop attaches completion callbacks to all running executors, indicating the waiting phase.
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
        """Drain interrupted executors without raising, remove occupancy and provide terminal-state fallback."""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _long():
            await asyncio.sleep(10)

        t = asyncio.create_task(_long())
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)  # Drive to done(cancelled), consuming cancellation.
        assert t.cancelled()
        worker._slots.register("test", "video", "tid", t)

        # Check .cancelled() synchronously, without awaiting/raising CancelledError; drain already removed the task.
        await worker._drain_finished_tasks()
        assert worker._slots.occupied("test", "video") == 0
        # If subtasks cannot finalize themselves, drain settles terminal state.
        assert queue.interrupted == ["tid"]

    @pytest.mark.asyncio
    async def test_drain_finished_tasks_drains_success_and_failure(self):
        """Noncancelled success reads .result(); failures enter except; neither invokes cancellation fallback."""
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

        await worker._drain_finished_tasks()  # Must not raise.
        assert worker._slots.occupied("test", "image") == 0
        assert worker._slots.occupied("test", "video") == 0
        assert queue.interrupted == []

    @pytest.mark.asyncio
    async def test_drain_fallback_terminal_write_failure_does_not_propagate(self):
        """If drain finalization fallback raises, warn only without breaking the main loop."""

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
        await asyncio.gather(t, return_exceptions=True)  # Drive to done(cancelled), consuming cancellation.
        assert t.cancelled()
        worker._slots.register("test", "video", "tid", t)

        # except absorbs fallback errors; drain neither raises nor retains the task.
        await worker._drain_finished_tasks()
        assert worker._slots.occupied("test", "video") == 0

    @pytest.mark.asyncio
    async def test_drain_lands_cancelled_when_interruption_hits_before_process_task_try(self, monkeypatch):
        """Cancellation before _process_task enters try, even before coroutine execution, receives drain terminal fallback."""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue,
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        async def _execute(_task, **_kwargs):
            raise AssertionError("execute 不应被调用：打断在协程开始执行之前就到")

        monkeypatch.setattr(stub_executors, "generation", _execute)

        t = asyncio.create_task(
            worker._process_task({"task_id": "tid", "media_type": "video"}, claimed_provider_id="test"),
            name="generation-video-tid",
        )
        worker._slots.register("test", "video", "tid", t)
        t.cancel()  # Coroutine has never been scheduled; cancellation precedes try.

        await worker.start()
        await asyncio.wait_for(queue.claim_after_interruption.wait(), timeout=2.0)

        # _process_task cannot finalize before try; drain provides fallback.
        assert queue.interrupted == ["tid"]
        # Main loop remains alive.
        assert worker._main_task is not None
        assert not worker._main_task.done()

        await asyncio.wait_for(worker.stop(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_run_loop_survives_inflight_task_interruption(self, monkeypatch):
        """Interrupting one executor finalizes its task while the main loop continues until explicit stop."""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        started = asyncio.Event()

        async def _block(_task, **_kwargs):
            started.set()
            await asyncio.sleep(10)  # Simulate long-running generation.

        monkeypatch.setattr(stub_executors, "generation", _block)

        t = asyncio.create_task(
            worker._process_task({"task_id": "tid", "media_type": "video"}, claimed_provider_id="test"),
            name="generation-video-tid",
        )
        worker._slots.register("test", "video", "tid", t)

        await worker.start()
        await started.wait()  # Ensure _process_task reached execute, after _extract_provider.

        t.cancel()
        await asyncio.wait_for(queue.claim_after_interruption.wait(), timeout=2.0)

        # Drain retries finalization after _process_task; guards make second attempts side-effect-free.
        assert set(queue.interrupted) == {"tid"}
        # Main loop survives absorbed CancelledError.
        assert worker._main_task is not None
        assert not worker._main_task.done()

        await asyncio.wait_for(worker.stop(), timeout=2.0)
        assert worker._main_task is None
        assert queue.released

    @pytest.mark.asyncio
    async def test_run_loop_survives_consecutive_interruptions_and_keeps_claiming(self, monkeypatch):
        """After multiple in-flight interruptions, the loop remains alive and can claim new tasks."""

        class _GatedQueue(FakeWorkerQueue):
            def __init__(self):
                super().__init__()
                self.allow_new = False
                self.new_dispatched = False

            async def claim_next_task(self, media_type, **_kwargs):
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
                worker._process_task({"task_id": tid, "media_type": "video"}, claimed_provider_id="test"),
                name=f"generation-video-{tid}",
            )
            worker._slots.register("gemini-aistudio", "video", tid, t)
            running.append(t)

        await worker.start()
        await all_entered.wait()  # Await all three tasks entering execute.

        for t in running:
            t.cancel()
        await asyncio.gather(*running, return_exceptions=True)

        # Main loop survives and all three tasks reach terminal state.
        assert worker._main_task is not None
        assert not worker._main_task.done()
        assert set(vid_ids) <= set(queue.interrupted)

        # After interruption, continue claiming/dispatching new tasks.
        queue.allow_new = True
        await asyncio.wait_for(fresh_started.wait(), timeout=2.0)
        assert queue.new_dispatched is True
        assert "fresh-img" in worker._slots.active_task_ids()

        # Finalize: release fresh-img, then stop normally.
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
    async def test_run_loop_survives_lease_renewal_error_and_keeps_claiming(self):
        """Lease-renewal errors such as pool timeouts fail only that cycle; keep renewing and claiming."""

        class _FlakyLeaseQueue(FakeWorkerQueue):
            def __init__(self):
                super().__init__()
                self.renewals = 0
                self.claims = 0
                self.claimed_after_error = asyncio.Event()

            async def acquire_or_renew_worker_lease(self, name, owner_id, ttl_seconds):
                self.renewals += 1
                if self.renewals == 2:
                    raise TimeoutError("QueuePool limit reached")
                return True

            async def claim_next_task(self, media_type, **_kwargs):
                self.claims += 1
                if self.renewals > 2:
                    self.claimed_after_error.set()
                return

        queue = _FlakyLeaseQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        await worker.start()
        await asyncio.wait_for(queue.claimed_after_error.wait(), timeout=2.0)
        assert worker._main_task is not None
        assert not worker._main_task.done()
        assert not queue.released
        await worker.stop()

        assert queue.released

    @pytest.mark.asyncio
    async def test_cycle_error_after_claim_rescans_orphans_and_settles_claimed_task(self):
        """Errors after claim but before slot registration leave running rows for orphan rescanning on the next lease ownership."""

        class _ClaimOnceQueue(FakeWorkerQueue):
            def __init__(self):
                super().__init__()
                self.orphan_scans = 0
                self.settled = asyncio.Event()
                self._pending = [{"task_id": "img-stuck", "task_type": "gen_image", "media_type": "image"}]

            async def claim_next_task(self, media_type, **_kwargs):
                if not self._pending:
                    return None
                task = self._pending.pop()
                # Claim immediately marks running; orphan scans subsequently see it in DB.
                self._orphans.append({**task, "status": "running"})
                return task

            async def list_orphan_tasks_on_start(self):
                self.orphan_scans += 1
                return [t for t in self._orphans if t["task_id"] not in {tid for tid, _ in self.failed}]

            async def mark_task_failed(self, task_id, error):
                result = await super().mark_task_failed(task_id, error)
                self.settled.set()
                return result

        projections = 0

        async def _flaky_projection(_task) -> str:
            nonlocal projections
            projections += 1
            if projections == 1:
                raise TimeoutError("QueuePool limit reached")
            return "test"

        async def _settle(*, taskless_started_before: datetime | None) -> int:
            return 0

        queue = _ClaimOnceQueue()
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"test": {"image": 1}}),
            provider_projection=_flaky_projection,
            lanes=("image",),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
            settle_interrupted_calls=_settle,
        )
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        await worker.start()
        try:
            await asyncio.wait_for(queue.settled.wait(), timeout=2.0)
        finally:
            await worker.stop()

        assert queue.orphan_scans >= 2, "出错后下一次持有 lease 时应重扫孤儿"
        assert [tid for tid, _ in queue.failed] == ["img-stuck"]
        assert "[restart_lost_image]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_orphan_rescan_after_cycle_error_skips_tasks_still_active_in_slots(self):
        """Post-error rescans exclude tasks still executing in this process, without failure or duplicate resume."""

        class _FlakyLeaseQueue(FakeWorkerQueue):
            def __init__(self):
                super().__init__()
                self.renewals = 0
                self.orphan_scans = 0
                self.rescanned = asyncio.Event()

            async def acquire_or_renew_worker_lease(self, name, owner_id, ttl_seconds):
                self.renewals += 1
                if self.renewals == 2:
                    raise TimeoutError("QueuePool limit reached")
                return True

            async def list_orphan_tasks_on_start(self):
                self.orphan_scans += 1
                if self.orphan_scans >= 2:
                    self.rescanned.set()
                return self._orphans

        async def _settle(*, taskless_started_before: datetime | None) -> int:
            return 0

        queue = _FlakyLeaseQueue()
        worker = GenerationWorker(
            queue=queue,
            capacity=capacity_table({"test": {"image": 1, "video": 1}}),
            provider_projection=_fixed_projection,
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
            settle_interrupted_calls=_settle,
        )
        worker.heartbeat_interval = 0.01
        worker.poll_interval = 0.01

        dispatched: list[Any] = []

        async def _spy_dispatch(resumable_by_provider):
            dispatched.append(resumable_by_provider)

        worker._recovery.dispatch_resumable = _spy_dispatch

        # Both tasks executing in this process are also running in DB.
        busy = asyncio.get_running_loop().create_future()
        worker._slots.register("test", "image", "img-busy", busy)
        worker._slots.register("test", "video", "vid-busy", busy)
        queue._orphans = [
            {"task_id": "img-busy", "status": "running", "task_type": "gen_image", "media_type": "image"},
            {
                "task_id": "vid-busy",
                "status": "running",
                "task_type": "video",
                "media_type": "video",
                "provider_id": "test",
                "provider_job_id": "job-1",
            },
        ]

        await worker.start()
        try:
            await asyncio.wait_for(queue.rescanned.wait(), timeout=2.0)
        finally:
            busy.set_result(None)
            await worker.stop()

        assert queue.failed == []
        assert dispatched == []

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

            async def claim_next_task(self, media_type, **_kwargs):
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

    # ------------------------------------------------------------------
    # _pool_full_providers
    # ------------------------------------------------------------------
    def test_pool_full_providers_sources_from_occupancy_with_cap_guard(self):
        """Full-pool blacklists start from occupied providers; cap==0 skips downgraded lanes' running occupancy.

        Only ``occupied_providers`` reach ``cap > 0``, so the guard's reachable case is occupied + zero capacity after reload disables a lane. Exclude such providers from blacklists: drain existing tasks normally; new tasks fail fast through ``_claim_tasks`` cap==0 rather than silently disappearing through SQL filters."""
        loop = asyncio.new_event_loop()
        dummy = loop.create_future()
        dummy.set_result(None)

        worker = GenerationWorker(
            queue=FakeWorkerQueue(),
            capacity=capacity_table(
                {
                    "full": {"image": 1, "video": 0},  # Capacity one with occupancy one is full.
                    "roomy": {"image": 2, "video": 0},  # Capacity two with occupancy one has room.
                    "downgraded": {"image": 0, "video": 0},  # Occupancy one but capacity zero after reload downgrade.
                }
            ),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )
        # All three providers occupy image lanes, enter occupied_providers and reach the guard.
        worker._slots.register("full", "image", "t-full", dummy)
        worker._slots.register("roomy", "image", "t-roomy", dummy)
        worker._slots.register("downgraded", "image", "t-down", dummy)

        full_image = worker._pool_full_providers("image")
        assert "full" in full_image, "cap>0 且占满应进黑名单"
        assert "roomy" not in full_image, "cap>0 但有空不进黑名单"
        # Crucial: downgraded occupancy actually reaches cap>0 and is excluded.
        # Repeating the old case with unoccupied cap==0 would never enter occupied_providers,
        # execute no guard line and falsely pass.
        assert "downgraded" not in full_image, "cap==0 守卫必须短路，不让降级 lane 进黑名单"

        # No video occupancy means empty blacklist: unoccupied lanes cannot be full.
        assert worker._pool_full_providers("video") == frozenset()
        loop.close()


class _CountingProjection:
    """Current-project provider projection substitute returning ``current`` and recording projected tasks."""

    def __init__(self, current: str) -> None:
        self.current = current
        self.calls: list[str] = []

    async def __call__(self, task: dict[str, Any]) -> str:
        self.calls.append(task["task_id"])
        return self.current


async def _seed_queued(
    factory, task_id: str, *, media_type: str, task_type: str, provider_id: str | None, order: int
) -> None:
    await seed_running_task(
        factory,
        task_id,
        task_type=task_type,
        media_type=media_type,
        resource_id=f"E1U{order}",
        status="queued",
        started_at=None,
        queued_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=order),
        provider_id=provider_id,
    )


async def _task_row(factory, task_id: str) -> dict[str, Any]:
    from lib.db.models.task import Task

    async with factory() as session:
        row = await session.get(Task, task_id)
        assert row is not None
        return {"status": row.status, "provider_id": row.provider_id, "queued_at": row.queued_at}


# (media_type, task_type) representatives for image/audio/video, plus reference-to-video coverage.
_MEDIA_TASKS = [
    pytest.param("image", "storyboard", id="image"),
    pytest.param("audio", "tts", id="audio"),
    pytest.param("video", "video", id="video"),
    pytest.param("video", "reference_video", id="reference_video"),
]


class _GatedExecution:
    """Execution substitute released only after claim cycles, recording every received claim slot.

    Memory DB sessions share a connection; execution writes interleaved with the next claim in that cycle could be undone by its rollback. Release handshake orders them. If ``drift_to`` is supplied, differing slots raise execution-entry drift."""

    def __init__(self, *, drift_to: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._drift_to = drift_to
        self._released = asyncio.Event()

    async def __call__(self, task: dict[str, Any], *, claimed_provider_id: str) -> dict[str, Any]:
        from lib.generation.generation_queue import ensure_dispatch_provider

        await self._released.wait()
        self.calls.append((task["task_id"], claimed_provider_id))
        if self._drift_to is not None:
            ensure_dispatch_provider(claimed_provider_id, self._drift_to)
        return {"ok": True}

    async def cycle(self, worker: GenerationWorker, *, keep: tuple[asyncio.Future[Any], ...] = ()) -> None:
        """Run one claim cycle, release and await its executors; ``keep`` is test-registered placeholder occupancy."""
        await worker._claim_tasks()
        self._released.set()
        await asyncio.gather(*[t for t in worker._slots.all_active_tasks() if t not in keep])
        await worker._drain_finished_tasks()
        self._released.clear()


class TestClaimByRecordedProvider:
    """Image/audio/video claiming uses queued provider records alone for full-pool filtering and slot ownership; execution checks current projects."""

    @staticmethod
    def _worker(queue, projection, limits: dict[str, dict[str, int]], media_type: str) -> GenerationWorker:
        return GenerationWorker(
            queue=queue,
            capacity=capacity_table(limits),
            provider_projection=projection,
            lanes=(media_type,),
            executor=stub_executors.execute,
            resume_executor=stub_executors.execute_resume,
        )

    @pytest.mark.parametrize(("media_type", "task_type"), _MEDIA_TASKS)
    @pytest.mark.parametrize("queued", [5, 50])
    async def test_full_pool_claim_cycle_projects_no_queued_task(
        self, worker_db, media_type: str, task_type: str, queued: int
    ):
        projection = _CountingProjection("ark")
        worker = self._worker(db_queue(worker_db), projection, {"ark": {media_type: 1}}, media_type)
        occupier = asyncio.get_running_loop().create_future()
        worker._slots.register("ark", media_type, "running", occupier)
        for n in range(queued):
            await _seed_queued(
                worker_db, f"task-{n}", media_type=media_type, task_type=task_type, provider_id="ark", order=n
            )
        try:
            claimed = await worker._claim_tasks()
        finally:
            occupier.cancel()

        assert claimed is False
        assert projection.calls == []
        assert {(await _task_row(worker_db, f"task-{n}"))["status"] for n in range(queued)} == {"queued"}

    @pytest.mark.parametrize(("media_type", "task_type"), _MEDIA_TASKS)
    async def test_claim_runs_recorded_provider_without_projecting(
        self, monkeypatch, worker_db, media_type: str, task_type: str
    ):
        execution = _GatedExecution()
        monkeypatch.setattr(stub_executors, "generation", execution)
        projection = _CountingProjection("minimax")
        worker = self._worker(db_queue(worker_db), projection, {"ark": {media_type: 1}}, media_type)
        await _seed_queued(
            worker_db, "task-recorded", media_type=media_type, task_type=task_type, provider_id="ark", order=0
        )

        await execution.cycle(worker)

        assert projection.calls == []
        assert execution.calls == [("task-recorded", "ark")]
        assert (await _task_row(worker_db, "task-recorded"))["status"] == "succeeded"

    @pytest.mark.parametrize(("media_type", "task_type"), _MEDIA_TASKS)
    async def test_legacy_task_without_recorded_provider_is_projected_once_and_recorded(
        self, monkeypatch, worker_db, media_type: str, task_type: str
    ):
        execution = _GatedExecution()
        monkeypatch.setattr(stub_executors, "generation", execution)
        projection = _CountingProjection("minimax")
        worker = self._worker(db_queue(worker_db), projection, {"minimax": {media_type: 1}}, media_type)
        await _seed_queued(
            worker_db, "task-legacy", media_type=media_type, task_type=task_type, provider_id=None, order=0
        )

        await execution.cycle(worker)

        assert projection.calls == ["task-legacy"]
        assert execution.calls == [("task-legacy", "minimax")]
        row = await _task_row(worker_db, "task-legacy")
        assert (row["status"], row["provider_id"]) == ("succeeded", "minimax")

    @pytest.mark.parametrize(("media_type", "task_type"), _MEDIA_TASKS)
    async def test_legacy_task_projected_into_full_pool_is_recorded_and_not_projected_again(
        self, monkeypatch, worker_db, media_type: str, task_type: str
    ):
        execution = _GatedExecution()
        monkeypatch.setattr(stub_executors, "generation", execution)
        projection = _CountingProjection("minimax")
        worker = self._worker(
            db_queue(worker_db), projection, {"minimax": {media_type: 1}, "ark": {media_type: 1}}, media_type
        )
        occupier = asyncio.get_running_loop().create_future()
        worker._slots.register("minimax", media_type, "running", occupier)
        await _seed_queued(
            worker_db, "task-legacy", media_type=media_type, task_type=task_type, provider_id=None, order=0
        )
        await _seed_queued(
            worker_db, "task-ready", media_type=media_type, task_type=task_type, provider_id="ark", order=1
        )
        try:
            for _cycle in range(2):
                await execution.cycle(worker, keep=(occupier,))
        finally:
            occupier.cancel()

        assert projection.calls == ["task-legacy"]
        legacy = await _task_row(worker_db, "task-legacy")
        assert (legacy["status"], legacy["provider_id"]) == ("queued", "minimax")
        assert execution.calls == [("task-ready", "ark")]

    @pytest.mark.parametrize(("media_type", "task_type"), _MEDIA_TASKS)
    async def test_recorded_provider_that_no_longer_supports_the_media_is_projected_once_and_recorded(
        self, monkeypatch, worker_db, media_type: str, task_type: str
    ):
        """If recorded providers lose media support while queued, project current state once and persist it; do not fail provider_unsupported_media."""
        execution = _GatedExecution()
        monkeypatch.setattr(stub_executors, "generation", execution)
        projection = _CountingProjection("minimax")
        worker = self._worker(
            db_queue(worker_db), projection, {"custom": {media_type: 0}, "minimax": {media_type: 1}}, media_type
        )
        await _seed_queued(
            worker_db, "task-stale", media_type=media_type, task_type=task_type, provider_id="custom", order=0
        )

        await execution.cycle(worker)

        assert projection.calls == ["task-stale"]
        assert execution.calls == [("task-stale", "minimax")]
        row = await _task_row(worker_db, "task-stale")
        assert (row["status"], row["provider_id"]) == ("succeeded", "minimax")

    @pytest.mark.parametrize(("media_type", "task_type"), _MEDIA_TASKS)
    async def test_provider_edited_while_queued_is_requeued_by_execution_entry_then_runs_on_new_slot(
        self, monkeypatch, worker_db, media_type: str, task_type: str
    ):
        """After project provider edits, claim under recorded slots; execution detects drift, updates records and requeues in place, taking new slots next cycle."""
        execution = _GatedExecution(drift_to="grok")
        monkeypatch.setattr(stub_executors, "generation", execution)
        projection = _CountingProjection("grok")
        worker = self._worker(
            db_queue(worker_db), projection, {"ark": {media_type: 1}, "grok": {media_type: 1}}, media_type
        )
        await _seed_queued(
            worker_db, "task-edited", media_type=media_type, task_type=task_type, provider_id="ark", order=1
        )
        # A later task already records the new provider; requeued edited tasks retain earlier priority and claim that new slot first.
        await _seed_queued(
            worker_db, "task-later", media_type=media_type, task_type=task_type, provider_id="grok", order=2
        )
        queued_at = (await _task_row(worker_db, "task-edited"))["queued_at"]
        occupier = asyncio.get_running_loop().create_future()
        worker._slots.register("grok", media_type, "running", occupier)

        try:
            await execution.cycle(worker, keep=(occupier,))
        finally:
            occupier.cancel()
        await worker._drain_finished_tasks()

        assert await _task_row(worker_db, "task-edited") == {
            "status": "queued",
            "provider_id": "grok",
            "queued_at": queued_at,
        }

        await execution.cycle(worker)

        assert execution.calls == [("task-edited", "ark"), ("task-edited", "grok")]
        assert projection.calls == []
        assert (await _task_row(worker_db, "task-edited"))["status"] == "succeeded"
        assert (await _task_row(worker_db, "task-later"))["status"] == "queued"


class TestOrphanOnceAndLeaseFlap:
    """One-time orphan scanning and lease-flap threshold."""

    def _build_worker(self) -> tuple[GenerationWorker, FakeWorkerQueue, list[int]]:
        """Construct workers and return (worker, queue, scan_count), counting scans."""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        scan_count: list[int] = []

        async def _spy_scan():
            scan_count.append(1)

        # Replace orphan scanning with a spy for exact call-count assertions.
        worker._recovery.handle_orphans = _spy_scan
        return worker, queue, scan_count

    @pytest.mark.asyncio
    async def test_orphan_scanned_once_on_first_lease_acquire(self):
        worker, _, scan_count = self._build_worker()
        worker._owns_lease = True
        worker._orphan_handled_once = False

        # Simulate the main-loop guard.
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._recovery.handle_orphans()
            worker._orphan_handled_once = True

        assert len(scan_count) == 1
        assert worker._orphan_handled_once is True

    @pytest.mark.asyncio
    async def test_orphan_not_rescanned_in_steady_state(self):
        """Stable lease across multiple cycles scans only once."""
        worker, _, scan_count = self._build_worker()
        worker._owns_lease = True

        for _ in range(5):
            if worker._owns_lease and not worker._orphan_handled_once:
                await worker._recovery.handle_orphans()
                worker._orphan_handled_once = True

        assert len(scan_count) == 1

    @pytest.mark.asyncio
    async def test_orphan_does_not_rescan_on_short_lease_flap(self):
        """Lease flap below lease_ttl never rescans."""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        # First lease acquisition scans once.
        worker._owns_lease = True
        await worker._recovery.handle_orphans()
        worker._orphan_handled_once = True

        # Simulate lease loss for one second and reacquisition (short flap).
        import time as _time

        worker._lease_lost_monotonic = _time.monotonic() - 1.0
        # Apply _run_loop logic.
        lost_duration = _time.monotonic() - worker._lease_lost_monotonic
        if lost_duration > worker.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
            worker._orphan_handled_once = False
        worker._lease_lost_monotonic = None

        # Flap is far below 30s, retaining handled_once.
        assert worker._orphan_handled_once is True
        if worker._owns_lease and not worker._orphan_handled_once:
            await worker._recovery.handle_orphans()
        assert len(scan_count) == 1, "短 flap 不应重扫"

    @pytest.mark.asyncio
    async def test_orphan_does_not_rescan_below_3x_ttl(self):
        """lease_ttl < lost < 3×lease_ttl still does not rescan (boundary)."""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        worker._owns_lease = True
        await worker._recovery.handle_orphans()
        worker._orphan_handled_once = True

        import time as _time

        # Lost 15s lies between TTL ten and 3×TTL thirty.
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
        """Lost >3×lease_ttl resets the switch and rescans next time."""
        worker, _, scan_count = self._build_worker()
        worker.lease_ttl = 10.0

        worker._owns_lease = True
        await worker._recovery.handle_orphans()
        worker._orphan_handled_once = True

        import time as _time

        # Lost 40s exceeds thirty, implying another process may have held the lease.
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
    """Startup convergence runs once per lease beside orphan handling, after orphan processing."""

    class _CyclingQueue(FakeWorkerQueue):
        """After several claim cycles, set an event so assertions wait for actual main-loop iterations."""

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
        """First convergence handles only taskless calls started before worker construction; later rescans handle task-bound calls only."""
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

        # Rescan after threshold lease loss: this process lives, and rows without task identities may still execute here.
        worker._orphan_handled_once = False
        queue._cycles = 0
        queue.cycled.clear()
        await self._run_until_settled(worker, queue)
        assert seen[1:] == [None]

    @pytest.mark.asyncio
    async def test_default_wiring_flips_orphan_pending_call_row(self, worker_db):
        """Without substitutes, real Ledger uses queue-wired DB and settles taskless pending rows failed[interrupted]."""
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
