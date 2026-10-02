"""删除或覆盖项目时仍在执行的任务放弃落盘：不写进同名的新项目，也不在已删除项目下留下残余目录。"""

import contextvars
from pathlib import Path

import httpx
import pytest

from lib.backends.image_backends.dashscope import DashScopeImageBackend
from lib.db.repositories.task_repo import TaskRepository
from lib.generation.generation_worker import GenerationWorker
from lib.generation.task_failure import encode_failure
from lib.project.project_manager import ProjectManager, get_project_manager
from server.services.project.project_archive import ProjectArchiveService
from server.services.tasks import generation_tasks
from tests.fakes import refuse_resume_execution
from tests.http_capture import capture_http
from tests.integration.lib.generation.worker_support import db_queue
from tests.integration.server.derivative_sheet_support import (
    GENERATION_URL,
    RESULT_IMAGE_RGB,
    RESULT_URL,
    build_generator,
    solid_png_bytes,
)
from tests.integration.server.routers.projects_router_support import build_projects_client
from tests.integration.server.services.tasks.generation_tasks_support import fake_resolve_ctx


def _create_demo(manager: ProjectManager) -> None:
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.add_character("demo", "阿岚", "少女")


def _tree(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


@pytest.mark.parametrize("replacement", ["recreated", "deleted-only", "overwritten"])
async def test_task_running_when_project_replaced_leaves_no_trace(monkeypatch, db_factory, replacement):
    manager = get_project_manager()
    _create_demo(manager)
    project_dir = manager.get_project_path("demo")
    archive_path, _ = ProjectArchiveService(manager).export_project("demo")
    generator = build_generator(project_dir, DashScopeImageBackend(api_key="sk", model="qwen-image-2.0"))
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(generator))

    async with db_factory() as session:
        repo = TaskRepository(session)
        await repo.enqueue(project_name="demo", task_type="character", media_type="image", resource_id="阿岚")
        task = await repo.claim_next("image")
    assert task is not None

    new_project: dict[str, bytes | None] = {}
    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:

        def _replace() -> None:
            if replacement == "overwritten":
                response = client.post(
                    "/api/v1/projects/import",
                    files={"file": ("demo.zip", archive_path.read_bytes(), "application/zip")},
                    data={"conflict_policy": "overwrite"},
                )
                assert response.json()["conflict_resolution"] == "overwritten"
            else:
                assert client.delete("/api/v1/projects/demo").status_code == 200
            if replacement == "recreated":
                _create_demo(manager)
            if replacement != "deleted-only":
                new_project.update(_tree(project_dir))

        def _delete_while_generating(_request: httpx.Request) -> httpx.Response:
            # 删除、重建与覆盖导入来自别的请求，不在任务的执行上下文里。
            contextvars.Context().run(_replace)
            return httpx.Response(
                200, json={"output": {"choices": [{"message": {"content": [{"image": RESULT_URL}]}}]}}
            )

        with capture_http() as router:
            router.post(GENERATION_URL).mock(side_effect=_delete_while_generating)
            router.get(RESULT_URL).mock(return_value=httpx.Response(200, content=solid_png_bytes(RESULT_IMAGE_RGB)))
            worker = GenerationWorker(
                queue=db_queue(db_factory),
                executor=generation_tasks.execute_generation_task,
                resume_executor=refuse_resume_execution,
            )
            await worker._process_task(task, claimed_provider_id="dashscope")

    async with db_factory() as session:
        finished = await TaskRepository(session).get(task["task_id"])
    assert finished is not None
    assert finished["status"] == "failed"
    assert finished["error_message"] == encode_failure("project_deleted_during_task")
    if replacement != "deleted-only":
        assert _tree(project_dir) == new_project
    else:
        assert not project_dir.exists()
