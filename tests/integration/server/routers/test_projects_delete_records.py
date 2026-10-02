"""删除项目后，数据库里的旧记录与同名新项目断开关联，费用统计仍按已删除项目保留。"""

from datetime import UTC, datetime

from lib.backends.providers import CallStatus
from lib.db.models.api_call import ApiCall
from lib.db.repositories.session_repo import SessionRepository
from lib.db.repositories.task_repo import TaskRepository
from lib.db.repositories.usage_repo import UsageFilters, UsageRepository
from lib.project.project_manager import ProjectManager
from tests.integration.server.routers.projects_router_support import build_projects_client

BASE_TIME = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def _seed_old_project(db_factory) -> dict[str, str]:
    async with db_factory() as session:
        repo = TaskRepository(session)
        queued = await repo.enqueue(
            project_name="demo", task_type="storyboard", media_type="image", resource_id="E1S01"
        )
        running = await repo.enqueue(project_name="demo", task_type="video", media_type="video", resource_id="E1S02")
        claimed = await repo.claim_next("video")
        assert (claimed or {})["task_id"] == running["task_id"]
        session.add(
            ApiCall(
                project_name="demo",
                call_type="video",
                model="veo",
                provider="gemini",
                status=CallStatus.SUCCESS,
                started_at=BASE_TIME,
                cost_amount=1.5,
                currency="USD",
                segment_id="E1S02",
                task_id=running["task_id"],
            )
        )
        await session.commit()
        await SessionRepository(session).create("demo", "sdk-old-demo", title="旧会话")
    return {"queued": queued["task_id"], "running": running["task_id"]}


async def test_same_name_project_after_delete_inherits_no_records(tmp_path, monkeypatch, db_factory):
    manager = ProjectManager(tmp_path)
    manager.create_project("demo")
    old = await _seed_old_project(db_factory)

    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        response = client.delete("/api/v1/projects/demo")
    assert response.status_code == 200
    manager.create_project("demo")

    async with db_factory() as session:
        tasks = TaskRepository(session)
        assert (await tasks.list_tasks(project_name="demo"))["total"] == 0
        assert (await tasks.get(old["queued"]) or {})["status"] == "cancelled"
        # 删除时排队的任务不再被 worker 领走
        assert await tasks.claim_next("image") is None

        # 同一资源的新请求另起任务，不并到仍在执行的旧任务上
        fresh = await tasks.enqueue(project_name="demo", task_type="video", media_type="video", resource_id="E1S02")
        assert fresh["deduped"] is False
        assert fresh["task_id"] != old["running"]

        assert await SessionRepository(session).list(project_name="demo") == []

        usage = UsageRepository(session)
        assert await usage.fetch_summary_rows(filters=UsageFilters(project_name="demo")) == []
        [deleted_name] = [name for name in (await usage.fetch_usage_filter_options()).projects if name != "demo"]
        assert deleted_name.startswith("demo#deleted-")
        [row] = await usage.fetch_summary_rows(filters=UsageFilters(project_name=deleted_name))
        assert row.cost_amount == 1.5
        assert (await tasks.get(old["running"]) or {})["project_name"] == deleted_name


async def test_delete_missing_project_is_404_and_leaves_records(tmp_path, monkeypatch, db_factory):
    old = await _seed_old_project(db_factory)

    with build_projects_client(monkeypatch, ProjectManager(tmp_path), session_factory=db_factory) as client:
        response = client.delete("/api/v1/projects/demo")
    assert response.status_code == 404

    async with db_factory() as session:
        assert (await TaskRepository(session).get(old["queued"]) or {})["status"] == "queued"
