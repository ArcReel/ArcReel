"""Tests for TaskRepository."""

import asyncio

import pytest
from sqlalchemy import event, select, update

from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.db.repositories.task_repo import TaskNotCancellableError, TaskRepository
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.generation.task_failure import encode_failure, render_failure
from lib.i18n import _ as translate_message


async def stored_calls(session) -> list[ApiCall]:
    """按 started_at 倒序读回 api_calls 行。"""
    stmt = select(ApiCall).order_by(ApiCall.started_at.desc(), ApiCall.id.desc())
    return list((await session.execute(stmt)).scalars().all())


def _translator(locale: str):
    def translate(key: str, **kwargs):
        return translate_message(key, locale=locale, **kwargs)

    return translate


class TestTaskRepository:
    async def test_retry_artifact_download_reopens_same_api_call(self, db_session):
        usage = UsageRepository(db_session)
        repo = TaskRepository(db_session)
        task = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            provider_id="custom-1",
        )
        older_call_id = await usage.start_call(
            project_name="demo", call_type="video", model="old", task_id=task["task_id"]
        )
        await usage.finish_call(
            older_call_id,
            status="failed",
            settlement=SettlementInput(cost_amount=0),
            error_message="older download failed",
        )
        call_id = await usage.start_call(project_name="demo", call_type="video", model="m", task_id=task["task_id"])
        await usage.finish_call(
            call_id,
            status="failed",
            settlement=SettlementInput(cost_amount=0),
            error_message="download failed",
            error_code="download_failed",
            error_params={"status": 403},
        )
        await repo.claim_next("video")
        await repo.persist_provider_job_id(task["task_id"], "job-42", endpoint="ce-1")
        await repo.mark_failed(
            task["task_id"],
            encode_failure("artifact_download_failed", detail="403"),
        )

        retried = await repo.retry_artifact_download(task["task_id"])

        assert retried["status"] == "running"
        calls = await stored_calls(db_session)
        assert [(row.id, row.status) for row in calls] == [
            (call_id, "pending"),
            (older_call_id, "failed"),
        ]
        # 重开的行不再带上一次下载的失败原文与机器码：重试成功后 resume 结算只翻状态，
        # 留着会让这条 success 行一直挂着 download_failed。
        reopened = (
            await db_session.execute(
                select(ApiCall.error_message, ApiCall.error_code, ApiCall.error_params).where(ApiCall.id == call_id)
            )
        ).one()
        assert tuple(reopened) == (None, None, None)

    async def test_retry_artifact_download_reopens_api_call_left_pending_by_resume(self, db_session):
        # 落 artifact_download_failed 的任务，其 ApiCall 停在 pending 时同样受理重试；
        # 不受理就意味着产物只能整单重跑、重扣一次费。
        usage = UsageRepository(db_session)
        repo = TaskRepository(db_session)
        task = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S03",
            payload={},
            provider_id="custom-1",
        )
        call_id = await usage.start_call(project_name="demo", call_type="video", model="m", task_id=task["task_id"])
        await repo.claim_next("video")
        await repo.persist_provider_job_id(task["task_id"], "job-44", endpoint="ce-1")
        await repo.mark_failed(task["task_id"], encode_failure("artifact_download_failed", detail="403"))

        retried = await repo.retry_artifact_download(task["task_id"])

        assert retried["status"] == "running"
        calls = await stored_calls(db_session)
        assert [(row.id, row.status) for row in calls] == [(call_id, "pending")]

    async def test_retry_artifact_download_leaves_undispatchable_task_failed(self, db_session):
        usage = UsageRepository(db_session)
        repo = TaskRepository(db_session)
        # provider_id 缺席的任务没有派发目标：翻成 running 后无人接手，故资格判定就该拒绝。
        task = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
        )
        call_id = await usage.start_call(project_name="demo", call_type="video", model="m", task_id=task["task_id"])
        await usage.finish_call(call_id, status="failed", settlement=SettlementInput(cost_amount=0))
        await repo.claim_next("video")
        await repo.persist_provider_job_id(task["task_id"], "job-43", endpoint="ce-1")
        await repo.mark_failed(task["task_id"], encode_failure("artifact_download_failed", detail="403"))

        with pytest.raises(ValueError, match=r"task is not eligible for artifact download retry"):
            await repo.retry_artifact_download(task["task_id"])

        assert (await repo.get(task["task_id"]))["status"] == "failed"
        assert (await stored_calls(db_session))[0].status == "failed"

    async def test_retry_artifact_download_rejects_task_without_linked_call(self, db_session):
        """调用行按 api_calls.task_id 反查；历史任务的调用没有这层关联时拒绝重试，不新开计费行。"""
        usage = UsageRepository(db_session)
        repo = TaskRepository(db_session)
        task = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S04",
            payload={},
            provider_id="custom-1",
        )
        call_id = await usage.start_call(project_name="demo", call_type="video", model="m")
        await usage.finish_call(call_id, status="failed", settlement=SettlementInput(cost_amount=0))
        await repo.claim_next("video")
        await repo.persist_provider_job_id(task["task_id"], "job-45", endpoint="ce-1")
        await repo.mark_failed(task["task_id"], encode_failure("artifact_download_failed", detail="403"))

        with pytest.raises(ValueError, match=r"task has no settleable api call"):
            await repo.retry_artifact_download(task["task_id"])

        assert (await repo.get(task["task_id"]))["status"] == "failed"

    async def test_enqueue_dedupe_claim_succeed(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test"},
            script_file="ep1.json",
        )
        assert not first["deduped"]

        deduped = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={"prompt": "test2"},
            script_file="ep1.json",
        )
        assert deduped["deduped"]
        assert deduped["task_id"] == first["task_id"]

        running = await repo.claim_next("image")
        assert running is not None
        assert running["status"] == "running"

        affected = await repo.mark_succeeded(first["task_id"], {"file": "test.png"})
        assert affected == 1
        done = await repo.get(first["task_id"])
        assert done["status"] == "succeeded"

    async def test_enqueue_dedupe_respects_resource_type(self, db_session):
        """同名不同资产类型（如角色和道具都叫「玉佩」）不应互相去重。"""
        repo = TaskRepository(db_session)

        character_edit = await repo.enqueue(
            project_name="demo",
            task_type="image_edit",
            media_type="image",
            resource_id="玉佩",
            resource_type="character",
            payload={"prompt": "edit character"},
        )
        assert not character_edit["deduped"]

        prop_edit = await repo.enqueue(
            project_name="demo",
            task_type="image_edit",
            media_type="image",
            resource_id="玉佩",
            resource_type="prop",
            payload={"prompt": "edit prop"},
        )
        assert not prop_edit["deduped"]
        assert prop_edit["task_id"] != character_edit["task_id"]

        # 同一 resource_type 再次入队仍应正常去重（回归防护）。
        character_edit_again = await repo.enqueue(
            project_name="demo",
            task_type="image_edit",
            media_type="image",
            resource_id="玉佩",
            resource_type="character",
            payload={"prompt": "edit character again"},
        )
        assert character_edit_again["deduped"]
        assert character_edit_again["task_id"] == character_edit["task_id"]

    async def test_get_active_tasks_for_resources_matches_dedupe_key(self, db_session):
        repo = TaskRepository(db_session)

        finished = await repo.enqueue(
            project_name="demo",
            task_type="reference_video",
            media_type="video",
            resource_id="E1U2",
            payload={"prompt": "unit2"},
            script_file="ep1.json",
        )
        claimed = await repo.claim_next("video")
        assert claimed is not None
        assert claimed["task_id"] == finished["task_id"]
        await repo.mark_succeeded(finished["task_id"], {"file": "unit2.mp4"})

        active = await repo.enqueue(
            project_name="demo",
            task_type="reference_video",
            media_type="video",
            resource_id="E1U1",
            payload={"prompt": "unit1"},
            script_file="ep1.json",
        )
        # 去重键的每个维度各放一个同名 resource_id 的活动干扰任务：任一维度从查询条件里
        # 漏掉，它对应的干扰任务就会混进结果，把别的项目/任务类型的在途状态错算成本次冲突。
        decoys = [
            {"project_name": "other-demo"},
            {"task_type": "video"},
            {"resource_type": "unit"},
            {"script_file": "ep2.json"},
            {"resource_id": "E1U9"},
        ]
        for override in decoys:
            enqueued = await repo.enqueue(
                **{
                    "project_name": "demo",
                    "task_type": "reference_video",
                    "media_type": "video",
                    "resource_id": "E1U1",
                    "payload": {"prompt": "decoy"},
                    "script_file": "ep1.json",
                    **override,
                }
            )
            assert not enqueued["deduped"], f"干扰任务被去重，无法验证维度 {override}"

        found = await repo.get_active_tasks_for_resources(
            project_name="demo",
            task_type="reference_video",
            resource_ids=["E1U1", "E1U2"],
            script_file="ep1.json",
        )

        assert [t["resource_id"] for t in found] == ["E1U1"]
        assert found[0]["task_id"] == active["task_id"]
        assert found[0]["status"] == "queued"

    async def test_get_active_tasks_for_resources_covers_every_active_status(self, db_session):
        """queued / running 两个活动态都算冲突，终态不算。

        探测的状态维度直接取自 ``ACTIVE_TASK_STATUSES``，与 ``idx_tasks_dedupe_active``
        同口径；少一个状态就会让该状态下的 unit 被判为"空闲"而放行重复入队。
        """

        async def _enqueue(resource_id: str):
            return await repo.enqueue(
                project_name="demo",
                task_type="reference_video",
                media_type="video",
                resource_id=resource_id,
                payload={"prompt": resource_id},
                script_file="ep1.json",
            )

        repo = TaskRepository(db_session)

        # 逐个入队并立刻推进到目标状态：claim_next 取的是队首，前一个离开 queued 后
        # 下一次 claim 才会落到新入队的那个。
        done = await _enqueue("E1U0")
        await repo.claim_next("video")
        await repo.mark_succeeded(done["task_id"], {"file": "unit0.mp4"})

        cancelled = await _enqueue("E1U3")
        await repo.cancel_task(cancelled["task_id"])
        assert (await repo.get(cancelled["task_id"]))["status"] == "cancelled"

        running = await _enqueue("E1U2")
        await repo.claim_next("video")
        assert (await repo.get(running["task_id"]))["status"] == "running"

        await _enqueue("E1U1")

        found = await repo.get_active_tasks_for_resources(
            project_name="demo",
            task_type="reference_video",
            resource_ids=["E1U0", "E1U1", "E1U2", "E1U3"],
            script_file="ep1.json",
        )

        assert {t["resource_id"]: t["status"] for t in found} == {
            "E1U1": "queued",
            "E1U2": "running",
        }

    async def test_get_active_tasks_for_resources_empty_ids_short_circuits(self, db_session):
        repo = TaskRepository(db_session)

        found = await repo.get_active_tasks_for_resources(
            project_name="demo",
            task_type="reference_video",
            resource_ids=[],
            script_file="ep1.json",
        )

        assert found == []

    async def test_dependency_cascade_failure(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        second = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S02",
            payload={},
            script_file="ep1.json",
            dependency_task_id=first["task_id"],
        )

        await repo.claim_next("image")
        await repo.mark_failed(first["task_id"], "boom")

        dep_task = await repo.get(second["task_id"])
        assert dep_task["status"] == "failed"
        assert dep_task["error_message"] == encode_failure(
            "cascade_blocked_dependency", dependency_task_id=first["task_id"], reason="boom"
        )

    async def test_cascade_with_long_structured_root_reason_stays_renderable(self, db_session):
        """根任务的失败原因本身是带长 detail 的结构化编码时，级联包裹后仍须是合法可解析的 JSON。"""
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        second = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S02",
            payload={},
            script_file="ep1.json",
            dependency_task_id=first["task_id"],
        )

        await repo.claim_next("image")
        long_reason = encode_failure("resume_expired_detail", detail="x" * 1900)
        await repo.mark_failed(first["task_id"], long_reason)

        dep_task = await repo.get(second["task_id"])
        assert dep_task["status"] == "failed"
        error_message = dep_task["error_message"]
        assert error_message is not None
        assert len(error_message) <= 2000

        for locale in ("zh", "en", "vi"):
            rendered = render_failure(error_message, _translator(locale))
            assert rendered is not None
            assert "[" not in rendered
            assert "resume_expired_detail" not in rendered

    async def test_deep_dependency_cascade_stays_renderable(self, db_session):
        """10 层依赖链级联失败：编码不应因深度嵌套被截断成非法 JSON。"""
        repo = TaskRepository(db_session)

        chain_length = 10
        tasks = []
        dependency_task_id = None
        for i in range(chain_length):
            task = await repo.enqueue(
                project_name="demo",
                task_type="storyboard",
                media_type="image",
                resource_id=f"E1S{i:02d}",
                payload={},
                script_file="ep1.json",
                dependency_task_id=dependency_task_id,
            )
            tasks.append(task)
            dependency_task_id = task["task_id"]

        await repo.claim_next("image")
        await repo.mark_failed(tasks[0]["task_id"], "boom" * 50)

        deepest = await repo.get(tasks[-1]["task_id"])
        assert deepest["status"] == "failed"
        error_message = deepest["error_message"]
        assert error_message is not None
        assert len(error_message) <= 2000

        for locale in ("zh", "en", "vi"):
            rendered = render_failure(error_message, _translator(locale))
            assert rendered is not None
            assert "[" not in rendered
            assert "cascade_blocked_dependency" not in rendered

    async def test_requeue_running_tasks(self, db_session):
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("video")
        count = await repo.requeue_running()
        assert count == 1

        queued = await repo.get(task["task_id"])
        assert queued["status"] == "queued"

    async def test_worker_lease(self, db_session):
        repo = TaskRepository(db_session)

        assert await repo.acquire_or_renew_lease(name="default", owner_id="a", ttl=2)
        assert not await repo.acquire_or_renew_lease(name="default", owner_id="b", ttl=2)
        assert await repo.is_worker_online(name="default")

        await repo.release_lease(name="default", owner_id="a")
        assert not await repo.is_worker_online(name="default")

    async def test_worker_lease_concurrent_first_acquire(self, file_db_factory):
        factory = file_db_factory
        start = asyncio.Event()

        async def _attempt(owner_id: str) -> bool:
            await start.wait()
            async with factory() as db_session:
                repo = TaskRepository(db_session)
                return await repo.acquire_or_renew_lease(
                    name="default",
                    owner_id=owner_id,
                    ttl=2,
                )

        first = asyncio.create_task(_attempt("worker-a"))
        second = asyncio.create_task(_attempt("worker-b"))
        start.set()

        a_ok, b_ok = await asyncio.gather(first, second)
        assert sorted([a_ok, b_ok]) == [False, True]

        async with factory() as db_session:
            repo = TaskRepository(db_session)
            lease = await repo.get_worker_lease(name="default")
            assert lease is not None
            assert lease["owner_id"] in {"worker-a", "worker-b"}

    async def test_list_tasks_with_filters(self, db_session):
        repo = TaskRepository(db_session)

        await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await repo.enqueue(
            project_name="other",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="ep2.json",
        )

        result = await repo.list_tasks(project_name="demo")
        assert result["total"] == 1

        result = await repo.list_tasks()
        assert result["total"] == 2

    async def test_task_has_cancelled_by_field(self, db_session):
        repo = TaskRepository(db_session)
        task = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        fetched = await repo.get(task["task_id"])
        assert fetched["cancelled_by"] is None

    async def test_cancel_single_queued_task(self, db_session):
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )

        result = await repo.cancel_task(task["task_id"])
        assert len(result["cancelled"]) == 1
        assert result["cancelled"][0]["task_id"] == task["task_id"]
        assert result["cancelled"][0]["cancelled_by"] == "user"
        assert result["skipped_terminal"] == []

        cancelled = await repo.get(task["task_id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["cancelled_by"] == "user"

    async def test_get_stats(self, db_session):
        repo = TaskRepository(db_session)

        await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        stats = await repo.get_stats()
        assert stats["queued"] == 1
        assert stats["total"] == 1

    async def test_cancel_task_cascades_to_dependents(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        second = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
            dependency_task_id=first["task_id"],
        )

        result = await repo.cancel_task(first["task_id"])
        assert len(result["cancelled"]) == 2
        assert result["cancelled"][0]["task_id"] == first["task_id"]
        assert result["cancelled"][0]["cancelled_by"] == "user"
        assert result["cancelled"][1]["task_id"] == second["task_id"]
        assert result["cancelled"][1]["cancelled_by"] == "cascade"

        dep_task = await repo.get(second["task_id"])
        assert dep_task["status"] == "cancelled"
        assert dep_task["cancelled_by"] == "cascade"

    async def test_cancel_running_task_is_rejected(self, db_session):
        """取消只对 queued 开放：执行中的任务被拒绝，状态不变。"""
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await repo.claim_next("image")

        with pytest.raises(TaskNotCancellableError):
            await repo.cancel_task(task["task_id"])
        with pytest.raises(TaskNotCancellableError):
            await repo.get_cancel_preview(task["task_id"])

        refreshed = await repo.get(task["task_id"])
        assert refreshed["status"] == "running"
        assert refreshed["cancelled_by"] is None

    async def test_cancel_preview(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        second = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
            dependency_task_id=first["task_id"],
        )

        preview = await repo.get_cancel_preview(first["task_id"])
        assert preview["task"]["task_id"] == first["task_id"]
        assert len(preview["cascaded"]) == 1
        assert preview["cascaded"][0]["task_id"] == second["task_id"]

    async def test_cancel_all_queued(self, db_session):
        repo = TaskRepository(db_session)

        await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        t2 = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="ep1.json",
        )
        # Claim one task so it becomes running
        await repo.claim_next("image")

        result = await repo.cancel_all_queued("demo")
        assert result["cancelled_count"] == 1  # only the queued video task
        assert result["skipped_running_count"] == 0  # running 任务在查询 queued 前已被 claim，不算 skipped

        task = await repo.get(t2["task_id"])
        assert task["status"] == "cancelled"

    async def test_get_stats_includes_cancelled(self, db_session):
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        await repo.cancel_task(task["task_id"])

        stats = await repo.get_stats()
        assert stats["cancelled"] == 1
        assert stats["queued"] == 0


class TestCancelCascade:
    """A→B→C 依赖链：取消只作用于排队中的节点，级联只涉及排队中的下游。"""

    async def _chain_3(self, repo: TaskRepository) -> tuple[str, str, str]:
        a = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
        )
        b = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S01",
            payload={},
            script_file="ep1.json",
            dependency_task_id=a["task_id"],
        )
        c = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="ep1.json",
            dependency_task_id=b["task_id"],
        )
        return a["task_id"], b["task_id"], c["task_id"]

    async def test_cancel_running_head_is_rejected_and_chain_stays_queued(self, db_session):
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)
        await repo.claim_next("image")  # A 拉成 running

        with pytest.raises(TaskNotCancellableError):
            await repo.cancel_task(a)

        assert (await repo.get(a))["status"] == "running"
        assert (await repo.get(b))["status"] == "queued"
        assert (await repo.get(c))["status"] == "queued"

    async def test_cancel_queued_head_cascades_to_grandchildren(self, db_session):
        """取消排队中的 A → A/B/C 全 cancelled；下游与发起方在 cancelled_by 上可区分。"""
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)

        result = await repo.cancel_task(a)

        assert [t["task_id"] for t in result["cancelled"]] == [a, b, c]
        for tid, expected in [(a, "user"), (b, "cascade"), (c, "cascade")]:
            t = await repo.get(tid)
            assert t["status"] == "cancelled", f"{tid} expected cancelled, got {t['status']}"
            assert t["cancelled_by"] == expected

    async def test_interrupted_running_head_cascades_to_queued_dependents(self, db_session):
        """进程级打断把 running 的 A 落 cancelled，排队中的下游随之级联取消。"""
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)
        await repo.claim_next("image")

        assert await repo.finalize_interrupted(a) == 1

        for tid, expected in [(a, "user"), (b, "cascade"), (c, "cascade")]:
            t = await repo.get(tid)
            assert t["status"] == "cancelled"
            assert t["cancelled_by"] == expected

    async def test_cancel_records_each_terminal_event_at_most_once(self, db_session):
        """每个 task 的终态推送（project_events SSE 依赖的 terminal_events）不重复记账。"""
        repo = TaskRepository(db_session)
        a, b, c = await self._chain_3(repo)
        await repo.cancel_task(a)
        await repo.finalize_interrupted(a)

        cancelled_events = [e for e in repo.terminal_events if e["status"] == "cancelled"]
        by_task: dict[str, int] = {}
        for e in cancelled_events:
            tid = e["task_id"]
            by_task[tid] = by_task.get(tid, 0) + 1
        assert by_task == {a: 1, b: 1, c: 1}


class ResourceScopedTaskRepository(TaskRepository):
    """只看得到 resource_id 为 ``visible`` 的任务，用来验证取消与下载重试路径的目标行查询经过 ``_scope_query``。"""

    def _scope_query(self, stmt, model):
        return stmt.where(Task.resource_id == "visible")


class TestRetryDownloadRespectsScope:
    async def _seed_download_failed(self, db_session, resource_id: str) -> tuple[str, int]:
        """落一个满足全部重试资格的下载失败任务，返回任务 id 与其 failed 调用行 id。"""
        repo = TaskRepository(db_session)
        usage = UsageRepository(db_session)
        task = await repo.enqueue(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id=resource_id,
            payload={},
            provider_id="custom-1",
        )
        call_id = await usage.start_call(project_name="demo", call_type="video", model="m", task_id=task["task_id"])
        await usage.finish_call(
            call_id,
            status="failed",
            settlement=SettlementInput(cost_amount=0),
            error_message="download failed",
        )
        await repo.claim_next("video")
        await repo.persist_provider_job_id(task["task_id"], f"job-{resource_id}", endpoint="ce-1")
        await repo.mark_failed(task["task_id"], encode_failure("artifact_download_failed", detail="403"))
        return task["task_id"], call_id

    async def test_out_of_scope_task_is_rejected_like_missing_one(self, db_session):
        hidden, call_id = await self._seed_download_failed(db_session, "hidden")
        scoped = ResourceScopedTaskRepository(db_session)

        with pytest.raises(ValueError, match="task is not eligible for artifact download retry: no-such-task"):
            await scoped.retry_artifact_download("no-such-task")
        with pytest.raises(ValueError, match=f"task is not eligible for artifact download retry: {hidden}"):
            await scoped.retry_artifact_download(hidden)

        assert (await TaskRepository(db_session).get(hidden))["status"] == "failed"
        call = (
            await db_session.execute(select(ApiCall.status, ApiCall.error_message).where(ApiCall.id == call_id))
        ).one()
        assert tuple(call) == ("failed", "download failed")

    async def test_in_scope_task_is_retried(self, db_session):
        visible, call_id = await self._seed_download_failed(db_session, "visible")

        retried = await ResourceScopedTaskRepository(db_session).retry_artifact_download(visible)

        assert (retried["status"], retried["error_message"]) == ("running", None)
        calls = await stored_calls(db_session)
        assert [(row.id, row.status, row.error_message) for row in calls] == [(call_id, "pending", None)]


class TestCancelRespectsScope:
    async def _enqueue(self, repo: TaskRepository, resource_id: str) -> str:
        task = await repo.enqueue(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id=resource_id,
            payload={},
            script_file="ep1.json",
        )
        return task["task_id"]

    @pytest.mark.parametrize("method", ["cancel_task", "get_cancel_preview"])
    async def test_out_of_scope_task_is_reported_as_missing(self, db_session, method):
        hidden = await self._enqueue(TaskRepository(db_session), "hidden")
        scoped = ResourceScopedTaskRepository(db_session)

        with pytest.raises(ValueError, match="任务 'no-such-task' 不存在"):
            await getattr(scoped, method)("no-such-task")
        with pytest.raises(ValueError, match=f"任务 '{hidden}' 不存在"):
            await getattr(scoped, method)(hidden)

        assert (await TaskRepository(db_session).get(hidden))["status"] == "queued"
        assert scoped.terminal_events == []

    async def test_cancel_all_queued_only_cancels_in_scope_tasks(self, db_session):
        seeder = TaskRepository(db_session)
        visible = await self._enqueue(seeder, "visible")
        hidden = await self._enqueue(seeder, "hidden")
        scoped = ResourceScopedTaskRepository(db_session)

        result = await scoped.cancel_all_queued("demo")

        assert result == {"cancelled_count": 1, "skipped_running_count": 0}
        assert (await seeder.get(visible))["status"] == "cancelled"
        assert (await seeder.get(hidden))["status"] == "queued"
        assert [e["task_id"] for e in scoped.terminal_events] == [visible]

    async def test_cancel_all_queued_without_in_scope_tasks_changes_nothing(self, db_session):
        seeder = TaskRepository(db_session)
        hidden = await self._enqueue(seeder, "hidden")
        scoped = ResourceScopedTaskRepository(db_session)

        result = await scoped.cancel_all_queued("demo")

        assert result == {"cancelled_count": 0, "skipped_running_count": 0}
        assert (await seeder.get(hidden))["status"] == "queued"
        assert scoped.terminal_events == []

    async def test_cancel_all_preview_counts_only_in_scope_queued_tasks(self, db_session):
        seeder = TaskRepository(db_session)
        await self._enqueue(seeder, "visible")
        await self._enqueue(seeder, "hidden")

        assert await seeder.get_cancel_all_preview("demo") == 2
        assert await ResourceScopedTaskRepository(db_session).get_cancel_all_preview("demo") == 1

    async def test_cancel_all_queued_counts_task_claimed_before_update_as_skipped(self, db_session):
        repo = TaskRepository(db_session)
        claimed = await self._enqueue(repo, "E1S01")
        remaining = await self._enqueue(repo, "E1S02")
        fired: list[bool] = []

        # 目标行查出之后、UPDATE 之前，worker 领走其中一个任务
        def claim_before_update(orm_execute_state):
            if orm_execute_state.is_update and not fired:
                fired.append(True)
                orm_execute_state.session.execute(update(Task).where(Task.task_id == claimed).values(status="running"))

        event.listen(db_session.sync_session, "do_orm_execute", claim_before_update)
        try:
            result = await repo.cancel_all_queued("demo")
        finally:
            event.remove(db_session.sync_session, "do_orm_execute", claim_before_update)

        assert fired == [True]
        assert result == {"cancelled_count": 1, "skipped_running_count": 1}
        assert (await repo.get(claimed))["status"] == "running"
        assert (await repo.get(remaining))["status"] == "cancelled"
