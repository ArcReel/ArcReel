"""解析模型失败时，任务记录仍属于用户指定的供应商。"""

import asyncio

import pytest

from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import GenerationWorker
from lib.generation.task_failure import parse_failure
from tests.catalog_support import install_catalog, with_entry_changes
from tests.fakes import bind_session_factory
from tests.integration.lib.generation.worker_support import (
    capacity_table,
    seed_running_task,
    stub_executors,
    use_worker_project,
)


@pytest.mark.parametrize("legacy", [False, True], ids=["enqueue", "legacy-null"])
@pytest.mark.parametrize("image_capacity", [0, 1], ids=["claim-failure", "execution-failure"])
async def test_bare_provider_without_default_keeps_failure_provider(db_factory, monkeypatch, legacy, image_capacity):
    snapshot = catalog.factory_snapshot()
    default_model = snapshot.builtin_providers["ark"].defaults["image"]
    install_catalog(monkeypatch, with_entry_changes(snapshot, "ark", default_model, discontinued=True))
    project = {"image_provider_t2i": "ark"}
    use_worker_project(monkeypatch, project)
    bind_session_factory(monkeypatch, db_factory)
    monkeypatch.setattr("lib.generation.generation_worker.async_session_factory", db_factory)
    queue = GenerationQueue(session_factory=db_factory)
    if legacy:
        task_id = "legacy-null"
        await seed_running_task(
            db_factory,
            task_id,
            task_type="storyboard",
            media_type="image",
            status="queued",
            started_at=None,
            provider_id=None,
        )
    else:
        submitted = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
        )
        task_id = submitted["task_id"]
        queued = await queue.get_task(task_id)
        assert queued is not None
        assert queued["provider_id"] == "ark"

    execute = asyncio.Event()

    async def resolve_before_generation(_task, *, claimed_provider_id):
        await execute.wait()
        await ConfigResolver(db_factory).resolve_image_backend(project, {}, generation_type="t2i")
        raise AssertionError("unavailable default must fail before generation")

    worker = GenerationWorker(
        queue=queue,
        capacity=capacity_table({"ark": {"image": image_capacity}, "gemini-aistudio": {"image": 3}}),
        lanes=("image",),
        executor=resolve_before_generation,
        resume_executor=stub_executors.execute_resume,
    )
    await worker._claim_tasks()
    execute.set()
    await worker._wait_inflight_completion()
    failed = await queue.get_task(task_id)
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["provider_id"] == "ark"
    # 认领时供应商没有图片容量，按不支持该媒体类型失败；执行时解析到只写了供应商的项目字段，提示选定模型
    expected = (
        ("model_not_selected", {"provider": "ark"})
        if image_capacity
        else ("provider_unsupported_media", {"provider_id": "ark", "media_type": "image"})
    )
    assert parse_failure(failed["error_message"]) == expected
