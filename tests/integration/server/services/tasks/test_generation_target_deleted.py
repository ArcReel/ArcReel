"""生成完成时目标已删除：结果进项目回收站，原路径与版本历史不变，任务照常成功、费用照常记账。"""

import asyncio
import contextvars

import httpx

from lib.artifacts.version_manager import VersionManager
from lib.backends.image_backends.dashscope import DashScopeImageBackend
from lib.db.repositories.task_repo import TaskRepository
from lib.generation.deleted_target import TARGET_DELETED_NOTE
from lib.generation.generation_worker import GenerationWorker
from lib.project import project_trash
from lib.project.project_manager import get_project_manager
from server.services.tasks import generation_tasks
from tests.fakes import refuse_resume_execution, use_generation_context
from tests.http_capture import capture_http
from tests.integration.lib.generation.worker_support import db_queue
from tests.integration.server.derivative_sheet_support import (
    GENERATION_URL,
    RESULT_IMAGE_RGB,
    RESULT_URL,
    build_generator,
    solid_png_bytes,
)
from tests.integration.server.services.tasks.generation_tasks_support import fake_resolve_ctx


async def test_asset_deleted_while_generating_lands_the_result_in_project_trash(monkeypatch, db_factory):
    manager = get_project_manager()
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.add_character("demo", "阿岚", "少女")
    project_dir = manager.get_project_path("demo")
    generator = build_generator(project_dir, DashScopeImageBackend(api_key="sk", model="qwen-image-2.0"))
    use_generation_context(monkeypatch, fake_resolve_ctx(generator))

    async with db_factory() as session:
        repo = TaskRepository(session)
        await repo.enqueue(project_name="demo", task_type="character", media_type="image", resource_id="阿岚")
        task = await repo.claim_next("image")
    assert task is not None

    def _delete_character() -> None:
        manager.update_project("demo", lambda project: project["characters"].pop("阿岚"))

    async def _delete_while_generating(_request: httpx.Request) -> httpx.Response:
        # 删除来自别的请求，不在任务的执行上下文里。
        await asyncio.to_thread(contextvars.Context().run, _delete_character)
        return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [{"image": RESULT_URL}]}}]}})

    png = solid_png_bytes(RESULT_IMAGE_RGB)
    with capture_http() as router:
        router.post(GENERATION_URL).mock(side_effect=_delete_while_generating)
        router.get(RESULT_URL).mock(return_value=httpx.Response(200, content=png))
        worker = GenerationWorker(
            queue=db_queue(db_factory),
            executor=generation_tasks.execute_generation_task,
            resume_executor=refuse_resume_execution,
        )
        await worker._process_task(task, claimed_provider_id="openai")

    async with db_factory() as session:
        finished = await TaskRepository(session).get(task["task_id"])
    assert finished is not None
    assert finished["status"] == "succeeded"
    [record] = project_trash.list_records(project_dir)
    assert finished["result"] == {
        "resource_type": "characters",
        "resource_id": "阿岚",
        "file_path": None,
        "target_deleted": True,
        "trash_record_id": record.id,
    }
    assert record.object["note"] == TARGET_DELETED_NOTE
    assert record.files == ("characters/阿岚.png",)
    assert (project_trash.trash_dir(project_dir) / record.id / "files" / "characters" / "阿岚.png").read_bytes() == png
    # 原路径与版本历史不变，也不留暂存文件；被删的资产没有复活。
    assert list((project_dir / "characters").iterdir()) == []
    assert VersionManager(project_dir).get_versions("characters", "阿岚")["versions"] == []
    assert "阿岚" not in manager.load_project("demo")["characters"]
    # 费用照常记为成功。
    assert [outcome["status"] for outcome in generator.ledger.outcomes] == ["success"]
