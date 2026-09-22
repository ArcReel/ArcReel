import asyncio

import pytest
from sqlalchemy import select

from lib.db.models.api_call import ApiCall
from lib.generation.generation_worker import GenerationWorker
from lib.generation.video_resume import cleanup_video_staging
from lib.script.script_editor import ScriptEditError
from tests.integration.lib.generation.worker_support import (
    FakeWorkerQueue,
    reference_checkpoint_json,
    stage_task_dir,
    storyboard_resume_task,
    stub_executors,
)


async def _stored_calls(session) -> list[ApiCall]:
    """按 started_at 倒序读回 api_calls 行。"""
    stmt = select(ApiCall).order_by(ApiCall.started_at.desc(), ApiCall.id.desc())
    return list((await session.execute(stmt)).scalars().all())


class TestVideoResumeRunner:
    """续跑：分流、provider 锁定、调用行结算与 staging 清理。"""

    @pytest.mark.asyncio
    async def test_video_orphan_cleanup_removes_provider_media_and_task_output(self, tmp_path, monkeypatch):
        from lib.generation.media_generator import task_video_staging_path

        project_path = tmp_path / "demo"
        output_path = project_path / "videos" / "scene_E1S01.mp4"
        output_path.parent.mkdir(parents=True)
        staged_output = task_video_staging_path(output_path, "orphan-cleanup")
        staged_output.write_bytes(b"interrupted-download")
        provider_media = project_path / ".arcreel" / "tasks" / "orphan-cleanup" / "provider_media"
        provider_media.mkdir(parents=True)
        (provider_media / "000-start_image.png").write_bytes(b"staged-input")

        class _ProjectManager:
            def get_project_path(self, _project_name):
                return project_path

        monkeypatch.setattr("lib.generation.video_resume.get_project_manager", lambda: _ProjectManager())
        await cleanup_video_staging(
            {
                "task_id": "orphan-cleanup",
                "task_type": "video",
                "project_name": "demo",
                "resource_id": "E1S01",
            }
        )

        assert not staged_output.exists()
        assert not provider_media.exists()

    # ------------------------------------------------------------------
    # VideoResumeRunner.run：分流 + provider 锁定
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_process_resume_task_uses_storyboard_checkpoint_identity(self, monkeypatch):
        """分镜视频只有 checkpoint + job 齐备才续跑；enqueue payload 不再承担身份锁定。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        captured: list[tuple[dict, str]] = []

        async def _fake_resume(task, *, job_id):
            captured.append((task, job_id))
            return {"ok": True}

        monkeypatch.setattr(stub_executors, "resume", _fake_resume)

        task = storyboard_resume_task("resume-locked", provider_id="openai", job_id="openai-job")
        task["payload"] = {"video_provider_i2v": "gemini-aistudio/veo-3.1-fast-generate-preview"}
        await worker._resume.run(task)
        assert len(captured) == 1
        captured_task, captured_job_id = captured[0]
        # Resume executor receives the row unchanged and loads its immutable checkpoint itself.
        assert captured_task["payload"] == {"video_provider_i2v": "gemini-aistudio/veo-3.1-fast-generate-preview"}
        assert captured_job_id == "openai-job"
        assert queue.succeeded == [("resume-locked", {"ok": True})]

    @pytest.mark.asyncio
    async def test_process_resume_task_resume_expired(self, monkeypatch):
        """ResumeExpiredError → mark_failed [resume_expired]。"""
        from lib.backends.video_backend_contract import ResumeExpiredError

        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _expire(_task, *, job_id):
            raise ResumeExpiredError(job_id=job_id, provider="ark")

        monkeypatch.setattr(stub_executors, "resume", _expire)
        task = storyboard_resume_task("exp", job_id="x")
        await worker._resume.run(task)
        assert queue.failed
        assert queue.failed[0][0] == "exp"
        assert "[resume_expired_detail]" in queue.failed[0][1]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failed_rows", [1, 0])
    async def test_process_resume_task_settles_the_call_row_with_the_failure_text(
        self, monkeypatch, worker_db, failed_rows: int
    ):
        """派发侧终态失败：判死这次续跑的异常要随补账落到调用行，不能只翻任务。

        任务侧落的是任务失败码（``[resume_expired_detail]``），记录表读的是调用行的
        error_message / error_code——两张表各有自己的失败登记，调用行那份只能从这里落。
        任务终态写入命中 0 行时调用行照样按失败结算，不改判为 cancelled。
        """
        from lib.backends.video_backend_contract import ResumeExpiredError
        from lib.db.repositories.usage_repo import UsageRepository

        async with worker_db() as session:
            call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="video", model="m", task_id="exp-row"
            )

        queue = FakeWorkerQueue(failed_rows=failed_rows)
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _expire(_task, *, job_id):
            raise ResumeExpiredError(job_id=job_id, provider="ark")

        monkeypatch.setattr(stub_executors, "resume", _expire)
        await worker._resume.run(storyboard_resume_task("exp-row", job_id="x"))
        assert queue.interrupted == []

        async with worker_db() as session:
            row = await session.get(ApiCall, call_id)
        assert (row.status, row.cost_amount) == ("failed", 0)
        assert row.error_message == "resume job x expired or not found on provider ark"
        # 过期异常不带 HTTP 状态也不带上游错误码，分类落空——读侧按原文显示
        assert (row.error_code, row.error_params) == (None, None)

    @pytest.mark.asyncio
    async def test_process_resume_task_endpoint_changed(self, monkeypatch):
        """ResumeEndpointChangedError → mark_failed [resume_endpoint_changed]，错误可归因。"""
        from lib.backends.video_backend_contract import ResumeEndpointChangedError

        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _changed(_task, *, job_id):
            raise ResumeEndpointChangedError(
                job_id=job_id,
                provider="custom-7",
                submitted_endpoint="openai-video",
                current_endpoint="minimax-video",
            )

        monkeypatch.setattr(stub_executors, "resume", _changed)
        task = storyboard_resume_task("ep", provider_id="custom-7", job_id="x")
        await worker._resume.run(task)
        assert queue.failed
        assert queue.failed[0][0] == "ep"
        assert "[resume_endpoint_changed_detail]" in queue.failed[0][1]
        # 两侧 endpoint 都进错误详情，用户能归因到「接口被换过」而非泛化失败
        assert "openai-video" in queue.failed[0][1]
        assert "minimax-video" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_process_resume_task_resume_unsupported(self, monkeypatch):
        """NotImplementedError → mark_failed [resume_unsupported]。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _unsup(_task, *, job_id):
            raise NotImplementedError("no resume_video")

        monkeypatch.setattr(stub_executors, "resume", _unsup)
        task = storyboard_resume_task("uns", provider_id="vidu", job_id="x")
        await worker._resume.run(task)
        assert queue.failed
        assert queue.failed[0][0] == "uns"
        assert "[resume_unsupported_detail]" in queue.failed[0][1]

    @pytest.mark.asyncio
    async def test_process_resume_task_generic_exception(self, monkeypatch):
        """通用 Exception → mark_failed（无前缀，与运行期 backend 失败同款）。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _boom(_task, *, job_id):
            raise RuntimeError("transient backend error")

        monkeypatch.setattr(stub_executors, "resume", _boom)
        task = storyboard_resume_task("boom", job_id="x")
        await worker._resume.run(task)
        assert queue.failed
        assert queue.failed[0][0] == "boom"
        # 无 [resume_*] 前缀
        assert not queue.failed[0][1].startswith("[resume_")

    @pytest.mark.asyncio
    async def test_process_resume_task_script_edit_error_encodes_key(self, monkeypatch, staged_project):
        """resume_executor 复用 finalize_reference_video_unit 等 finalize helper，同样会抛
        ScriptEditError；resume 路径与常规 _process_task 走同一份 encode_task_failure_message，
        不能因为是重启自愈这条独立调用链就退回 str(exc) 的固定中文。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _raise_script_edit_error(_task, *, job_id):
            raise ScriptEditError("generated_assets 必须是 dict", key="script_edit_generated_assets_invalid")

        monkeypatch.setattr(stub_executors, "resume", _raise_script_edit_error)
        staged = stage_task_dir(staged_project, "resume_script_edit")
        task = {
            "task_id": "resume_script_edit",
            "task_type": "reference_video",
            "media_type": "video",
            "provider_id": "ark",
            "provider_job_id": "x",
            "execution_checkpoint_json": reference_checkpoint_json("resume_script_edit"),
            "payload": {},
            "project_name": "demo",
            "resource_id": "E1U1",
            "script_file": "scripts/episode_1.json",
        }
        await worker._resume.run(task)
        assert queue.failed
        assert queue.failed[0] == (
            "resume_script_edit",
            "[script_edit_generated_assets_invalid]",
        )
        assert not staged.exists(), "终态落定后必须清掉该任务的 provider media staging"

    @pytest.mark.asyncio
    async def test_process_resume_task_cancelled_error(self, monkeypatch, worker_db):
        """进程级打断（CancelledError）→ task / ApiCall 都结算 cancelled，再重新抛出。"""
        from lib.db.repositories.usage_repo import UsageRepository

        async with worker_db() as session:
            older_call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="video", model="old", task_id="rc"
            )
            call_id = await UsageRepository(session).start_call(
                project_name="demo", call_type="video", model="m", task_id="rc"
            )
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )

        async def _cancel(_task, *, job_id):
            raise asyncio.CancelledError

        monkeypatch.setattr(stub_executors, "resume", _cancel)
        task = storyboard_resume_task("rc", job_id="x")
        with pytest.raises(asyncio.CancelledError):
            await worker._resume.run(task)
        assert queue.interrupted == ["rc"]
        async with worker_db() as session:
            stored = await _stored_calls(session)
        assert [(row.id, row.status) for row in stored] == [
            (call_id, "cancelled"),
            (older_call_id, "pending"),
        ]
        assert stored[0].cost_amount == 0

    @pytest.mark.asyncio
    async def test_process_resume_task_no_job_id_fails_fast(self):
        """无 provider_job_id 的 task 被派发到 VideoResumeRunner.run 时直接 mark_failed。"""
        queue = FakeWorkerQueue()
        worker = GenerationWorker(
            queue=queue, executor=stub_executors.execute, resume_executor=stub_executors.execute_resume
        )
        task = {
            "task_id": "no-job",
            "task_type": "video",
            "media_type": "video",
            "provider_job_id": "",
            "payload": {},
            "project_name": "demo",
        }
        await worker._resume.run(task)
        assert queue.failed
        assert queue.failed[0][0] == "no-job"
        assert "[restart_lost_no_job_id]" in queue.failed[0][1]
