"""TaskRepository SQL WHERE state-guard tests.

Observe public behavior only: valid sources → rows=1 + DB changes; invalid → rows=0 + unchanged DB.
"""

from __future__ import annotations

import pytest

from lib.db.repositories.task_repo import TaskNotCancellableError, TaskRepository


@pytest.mark.asyncio
class TestRepoStateMachineGuards:
    async def test_mark_succeeded_only_from_running(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # queued → mark_succeeded rejected by status='running' guard.
        rows = await repo.mark_succeeded(t["task_id"], {"file": "x"})
        assert rows == 0
        assert (await repo.get(t["task_id"]))["status"] == "queued"

        await repo.claim_next("image")
        rows = await repo.mark_succeeded(t["task_id"], {"file": "x"})
        assert rows == 1
        assert (await repo.get(t["task_id"]))["status"] == "succeeded"

        # Already succeeded → 0 rows.
        rows = await repo.mark_succeeded(t["task_id"], {"file": "y"})
        assert rows == 0

    async def test_mark_failed_only_from_running(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        rows = await repo.mark_failed(t["task_id"], "err")
        assert rows == 0

        await repo.claim_next("image")
        rows = await repo.mark_failed(t["task_id"], "err")
        assert rows == 1
        assert (await repo.get(t["task_id"]))["status"] == "failed"

    async def test_finalize_interrupted_accepts_queued(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # queued → cancelled
        assert await repo.finalize_interrupted(t["task_id"]) == 1
        assert (await repo.get(t["task_id"]))["status"] == "cancelled"
        # Repeated call → 0 rows.
        assert await repo.finalize_interrupted(t["task_id"]) == 0

    async def test_finalize_interrupted_rejects_terminal(self, db_session):
        """Terminal succeeded/failed states cannot transition to cancelled."""
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")
        await repo.mark_succeeded(t["task_id"], {"x": 1})
        assert await repo.finalize_interrupted(t["task_id"]) == 0
        assert (await repo.get(t["task_id"]))["status"] == "succeeded"

    async def test_cancel_task_rejects_running_and_leaves_it_running(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")
        with pytest.raises(TaskNotCancellableError) as exc_info:
            await repo.cancel_task(t["task_id"])
        assert exc_info.value.task_id == t["task_id"]
        assert (await repo.get(t["task_id"]))["status"] == "running"

        # Rejected cancellation leaves execution unchanged; task finishes succeeded.
        assert await repo.mark_succeeded(t["task_id"], {"x": 1}) == 1
        assert (await repo.get(t["task_id"]))["status"] == "succeeded"

    async def test_cancel_task_terminal_is_skipped(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")
        await repo.mark_succeeded(t["task_id"], {"x": 1})
        result = await repo.cancel_task(t["task_id"])
        assert result == {"cancelled": [], "skipped_terminal": [result["skipped_terminal"][0]]}
        assert result["skipped_terminal"][0]["task_id"] == t["task_id"]

    async def test_persist_provider_job_id_writes_column(self, db_session):
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("video")
        await repo.persist_provider_job_id(t["task_id"], "provider-job-42")
        refreshed = await repo.get(t["task_id"])
        assert refreshed["provider_job_id"] == "provider-job-42"
        # Built-ins have no endpoint dimension; leave NULL.
        assert refreshed["provider_endpoint"] is None

    async def test_persist_provider_job_id_writes_endpoint_alongside(self, db_session):
        """Custom endpoint and job_id persist together and must be simultaneously visible; otherwise resume
        has job_id but cannot determine whether the protocol changed."""
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r-ep",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("video")
        await repo.persist_provider_job_id(t["task_id"], "provider-job-43", endpoint="openai-video")
        refreshed = await repo.get(t["task_id"])
        assert refreshed["provider_job_id"] == "provider-job-43"
        assert refreshed["provider_endpoint"] == "openai-video"

    async def test_persist_provider_job_id_without_endpoint_keeps_existing(self, db_session):
        """None endpoint does not clear existing values; clearing abandons comparison and is riskier than preserving."""
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r-keep",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("video")
        await repo.persist_provider_job_id(t["task_id"], "job-a", endpoint="openai-video")
        await repo.persist_provider_job_id(t["task_id"], "job-b")
        refreshed = await repo.get(t["task_id"])
        assert refreshed["provider_endpoint"] == "openai-video"

    async def test_persist_provider_job_id_writes_base_url_alongside_endpoint(self, db_session):
        """Custom dimensions persist separately: protocol ID in endpoint, domain in submitted_base_url."""
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r-both",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("video")
        await repo.persist_provider_job_id(
            t["task_id"],
            "provider-job-44",
            endpoint="dashscope-async-video",
            base_url="https://custom-a.example.com/api/v1",
        )
        refreshed = await repo.get(t["task_id"])
        assert refreshed["provider_job_id"] == "provider-job-44"
        assert refreshed["provider_endpoint"] == "dashscope-async-video"
        assert refreshed["submitted_base_url"] == "https://custom-a.example.com/api/v1"

    async def test_persist_provider_job_id_without_base_url_keeps_existing(self, db_session):
        """None base_url preserves existing values; clearing abandons replay and resumes polling against current configuration."""
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r-keep-url",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("video")
        await repo.persist_provider_job_id(
            t["task_id"], "job-a", endpoint="dashscope-async-video", base_url="https://custom-a.example.com/api/v1"
        )
        await repo.persist_provider_job_id(t["task_id"], "job-b")
        refreshed = await repo.get(t["task_id"])
        assert refreshed["submitted_base_url"] == "https://custom-a.example.com/api/v1"

    @pytest.mark.parametrize("task_type", ["video", "reference_video"])
    async def test_persist_video_checkpoint_atomically_locks_actual_provider(self, db_session, task_type):
        repo = TaskRepository(db_session)
        task = await repo.enqueue(
            project_name="demo",
            task_type=task_type,
            media_type="video",
            resource_id="E1U1",
            payload={"script_file": "scripts/episode_1.json"},
            script_file="scripts/episode_1.json",
            provider_id="enqueue-advisory",
        )
        await repo.claim_next("video")

        await repo.persist_execution_checkpoint(task["task_id"], '{"schema_version":1}', "actual-provider")

        refreshed = await repo.get(task["task_id"])
        assert refreshed["execution_checkpoint_json"] == '{"schema_version":1}'
        assert refreshed["provider_id"] == "actual-provider"
        assert refreshed["provider_job_id"] is None
        assert refreshed["payload"] == {"script_file": "scripts/episode_1.json"}

    @pytest.mark.parametrize("task_type", ["video", "reference_video"])
    async def test_persist_video_checkpoint_is_once_only_and_requires_running_before_job(self, db_session, task_type):
        repo = TaskRepository(db_session)
        task = await repo.enqueue(
            project_name="demo",
            task_type=task_type,
            media_type="video",
            resource_id="E1U1",
            payload={},
            script_file="scripts/episode_1.json",
        )

        with pytest.raises(ValueError, match="checkpoint persistence guard"):
            await repo.persist_execution_checkpoint(task["task_id"], "first", "provider-a")

        await repo.claim_next("video")
        await repo.persist_execution_checkpoint(task["task_id"], "first", "provider-a")
        with pytest.raises(ValueError, match="checkpoint persistence guard"):
            await repo.persist_execution_checkpoint(task["task_id"], "second", "provider-b")

        refreshed = await repo.get(task["task_id"])
        assert refreshed["execution_checkpoint_json"] == "first"
        assert refreshed["provider_id"] == "provider-a"

    async def test_list_orphan_returns_running_only(self, db_session):
        repo = TaskRepository(db_session)
        t1 = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        t2 = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="r2",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")

        orphans = await repo.list_orphan_tasks_on_start()
        statuses = {o["task_id"]: o["status"] for o in orphans}
        assert statuses == {t1["task_id"]: "running"}
        assert (await repo.get(t2["task_id"]))["status"] == "queued"

    async def test_claim_next_excludes_pool_full_providers(self, db_session):
        """claim_next excludes pool_full_providers; NULL and unknown providers remain eligible."""
        repo = TaskRepository(db_session)
        # Explicit provider_id='gemini-aistudio'.
        t1 = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
            provider_id="gemini-aistudio",
        )
        # Unset provider_id, legacy IS NULL fallback.
        t2 = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r2",
            payload={},
            script_file="ep1.json",
        )
        # Unknown provider, e.g. deleted custom provider with persisted provider_id.
        t3 = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r3",
            payload={},
            script_file="ep1.json",
            provider_id="custom-deleted",
        )
        # Full gemini pool blacklists gemini-aistudio; skip t1, FIFO-claim t2 (NULL).
        first = await repo.claim_next("image", pool_full_providers=frozenset({"gemini-aistudio"}))
        assert first is not None
        assert first["task_id"] == t2["task_id"]

        # Still only gemini full: t1 ineligible; claim t3, whose custom-deleted provider is not blacklisted.
        await repo.mark_succeeded(t2["task_id"], {})
        second = await repo.claim_next("image", pool_full_providers=frozenset({"gemini-aistudio"}))
        assert second is not None
        assert second["task_id"] == t3["task_id"], "未知 provider（已删除的自定义 provider）不应被白名单/黑名单误过滤"

        # Empty blacklist makes t1 eligible.
        await repo.mark_succeeded(t3["task_id"], {})
        third = await repo.claim_next("image", pool_full_providers=None)
        assert third is not None
        assert third["task_id"] == t1["task_id"]

    async def test_claim_next_filters_reference_task_by_recorded_provider(self, db_session):
        """Reference-video tasks filter the queued provider like others, without inferred-current-state exemptions."""
        repo = TaskRepository(db_session)
        await repo.enqueue(
            project_name="demo",
            task_type="reference_video",
            media_type="video",
            resource_id="E1U1",
            payload={"script_file": "ep1.json"},
            script_file="ep1.json",
            provider_id="provider-full",
        )
        ready = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S1",
            payload={},
            script_file="ep1.json",
            provider_id="provider-ready",
        )

        claimed = await repo.claim_next(
            "video",
            pool_full_providers=frozenset({"provider-full"}),
        )

        assert claimed is not None
        assert claimed["task_id"] == ready["task_id"]

    async def test_finalize_interrupted_from_running(self, db_session):
        """Process interruption settles running directly to cancelled, preventing repeated startup recovery of stuck running tasks."""
        repo = TaskRepository(db_session)
        t = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="r1",
            payload={},
            script_file="ep1.json",
        )
        # Advance to running.
        claimed = await repo.claim_next("image")
        assert claimed is not None
        assert claimed["task_id"] == t["task_id"]

        assert await repo.finalize_interrupted(t["task_id"]) == 1
        final = await repo.get(t["task_id"])
        assert final["status"] == "cancelled"
        assert final["cancelled_by"] == "interrupted"
