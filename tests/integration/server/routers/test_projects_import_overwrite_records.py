"""覆盖导入等同于删除现有项目再导入：现有项目的记录按删除收尾，覆盖失败时项目与记录原样保留。"""

from pathlib import Path

import pytest

from lib.db.repositories.session_repo import SessionRepository
from lib.db.repositories.task_repo import TaskRepository
from lib.db.repositories.usage_repo import UsageFilters, UsageRepository
from lib.project.project_manager import ProjectManager
from lib.project.task_project_claim import claim_task_project, ensure_task_project_claim
from server.services.project.project_archive import ProjectArchiveService
from tests.integration.server.routers.projects_router_support import build_projects_client, seed_project_records


def _create_demo_with_archive(manager: ProjectManager) -> bytes:
    """建好项目 demo，返回它的导出包，并给现有项目留下导出包里没有的项目记忆。"""
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    archive_path, _ = ProjectArchiveService(manager).export_project("demo")
    memory = manager.get_project_path("demo") / ".arcreel" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("index", encoding="utf-8")
    return archive_path.read_bytes()


def _overwrite_import(client, archive: bytes):
    return client.post(
        "/api/v1/projects/import",
        files={"file": ("demo.zip", archive, "application/zip")},
        data={"conflict_policy": "overwrite"},
    )


async def test_overwritten_project_inherits_no_records(tmp_path, monkeypatch, db_factory):
    manager = ProjectManager(tmp_path)
    archive = _create_demo_with_archive(manager)
    old = await seed_project_records(db_factory)

    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        response = _overwrite_import(client, archive)
    assert response.status_code == 200
    assert response.json()["conflict_resolution"] == "overwritten"
    assert not (manager.get_project_path("demo") / ".arcreel").exists()

    async with db_factory() as session:
        tasks = TaskRepository(session)
        assert (await tasks.list_tasks(project_name="demo"))["total"] == 0
        assert (await tasks.get(old["queued"]) or {})["status"] == "cancelled"
        assert await tasks.claim_next("image") is None
        assert await SessionRepository(session).list(project_name="demo") == []

        usage = UsageRepository(session)
        assert await usage.fetch_summary_rows(filters=UsageFilters(project_name="demo")) == []
        [deleted_name] = [name for name in (await usage.fetch_usage_filter_options()).projects if name != "demo"]
        assert deleted_name.startswith("demo#deleted-")
        [row] = await usage.fetch_summary_rows(filters=UsageFilters(project_name=deleted_name))
        assert row.cost_amount == 1.5


@pytest.mark.parametrize("failing_rename", ["existing-out", "import-in"])
async def test_failed_overwrite_leaves_the_project_and_its_records(tmp_path, monkeypatch, db_factory, failing_rename):
    manager = ProjectManager(tmp_path)
    archive = _create_demo_with_archive(manager)
    old = await seed_project_records(db_factory)
    project_dir = manager.get_project_path("demo")
    project_file = (project_dir / "project.json").read_bytes()

    rename = Path.rename
    refused: list[Path] = []

    def _failing_rename(self: Path, target):
        # existing-out：现有项目目录被占用、移不走（如 Windows 上有文件仍被打开）。
        # import-in：现有项目已移走，导入内容移入项目名时失败。
        if not refused and (self == project_dir if failing_rename == "existing-out" else Path(target) == project_dir):
            refused.append(self)
            raise PermissionError(f"in use: {self}")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", _failing_rename)
    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        response = _overwrite_import(client, archive)
    monkeypatch.undo()
    assert response.status_code == 500

    assert (project_dir / "project.json").read_bytes() == project_file
    assert (project_dir / ".arcreel" / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "index"
    assert [path.name for path in manager.projects_dir.iterdir()] == ["demo"]

    async with db_factory() as session:
        tasks = TaskRepository(session)
        assert (await tasks.get(old["queued"]) or {})["status"] == "queued"
        assert (await tasks.list_tasks(project_name="demo"))["total"] == 2
        assert len(await SessionRepository(session).list(project_name="demo")) == 1
        [row] = await UsageRepository(session).fetch_summary_rows(filters=UsageFilters(project_name="demo"))
        assert row.cost_amount == 1.5
    # 执行中的任务仍可落盘
    with claim_task_project(old["running"], "demo"):
        ensure_task_project_claim("demo")
