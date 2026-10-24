"""删除的项目成为已删除项目：整体恢复时记录改回项目名，原名被占用时换名；彻底删除后不可恢复。"""

from datetime import UTC, datetime
from urllib.parse import quote

from lib.db.repositories.task_repo import TaskRepository
from lib.db.repositories.usage_repo import UsageFilters, UsageRepository
from lib.project import deleted_projects
from lib.project.project_manager import ProjectManager
from server.auth import EMBEDDED_AGENT_SUB, CurrentUserInfo, get_current_user
from tests.integration.server.routers.projects_router_support import (
    build_projects_client,
    override,
    seed_project_records,
)

DELETED = "/api/v1/deleted-projects"


def _create(manager: ProjectManager, title: str) -> None:
    manager.create_project("demo")
    manager.create_project_metadata("demo", title, "Anime", "narration")


async def test_restored_project_takes_a_new_name_when_the_original_is_taken(tmp_path, monkeypatch, db_factory):
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")
    (manager.get_project_path("demo") / "source" / "chapter.txt").write_text("old text", encoding="utf-8")
    old = await seed_project_records(db_factory)

    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 200
        [entry] = client.get(DELETED).json()["projects"]
        assert entry["name"] == "demo"
        assert entry["title"] == "Old"

        _create(manager, "New")
        restored = client.post(f"{DELETED}/{quote(entry['id'], safe='')}/restore")
        assert restored.status_code == 200, restored.text
        assert restored.json()["name"] == "demo-2"

        assert client.get(DELETED).json()["projects"] == []
        names = sorted(p["name"] for p in client.get("/api/v1/projects").json()["projects"])
        assert names == ["demo", "demo-2"]

    assert manager.load_project("demo")["title"] == "New"
    assert manager.load_project("demo-2")["title"] == "Old"
    assert (manager.get_project_path("demo-2") / "source" / "chapter.txt").read_text(encoding="utf-8") == "old text"
    async with db_factory() as session:
        tasks = TaskRepository(session)
        # 记录随项目改回到恢复后的名字；删除时排队的任务仍是已取消
        assert (await tasks.get(old["queued"]) or {})["project_name"] == "demo-2"
        assert (await tasks.get(old["queued"]) or {})["status"] == "cancelled"
        assert (await tasks.list_tasks(project_name="demo"))["total"] == 0
        [row] = await UsageRepository(session).fetch_summary_rows(filters=UsageFilters(project_name="demo-2"))
        assert row.cost_amount == 1.5


async def test_restore_keeps_the_original_name_when_it_is_free(tmp_path, monkeypatch, db_factory):
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")
    old = await seed_project_records(db_factory)

    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 200
        assert not manager.project_exists("demo")
        [entry] = client.get(DELETED).json()["projects"]
        assert client.post(f"{DELETED}/{quote(entry['id'], safe='')}/restore").json()["name"] == "demo"

    assert manager.load_project("demo")["title"] == "Old"
    async with db_factory() as session:
        assert (await TaskRepository(session).get(old["running"]) or {})["project_name"] == "demo"


async def test_purged_project_cannot_be_restored(tmp_path, monkeypatch, db_factory):
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")

    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 200
        [entry] = client.get(DELETED).json()["projects"]
        assert client.delete(f"{DELETED}/{quote(entry['id'], safe='')}").status_code == 200
        assert client.get(DELETED).json()["projects"] == []
        missing = client.post(f"{DELETED}/{quote(entry['id'], safe='')}/restore")
        assert missing.status_code == 404
        assert missing.json()["detail"].startswith("已删除项目里没有这个项目")
        # 不是已删除项目 ID 的输入一律按不存在处理，不会解析到数据根里的其他位置
        assert client.delete(f"{DELETED}/projects").status_code == 404

    assert list(manager.layout.deleted_projects_dir.iterdir()) == []


async def test_embedded_agent_token_cannot_purge_a_deleted_project(tmp_path, monkeypatch, db_factory):
    """内嵌 Agent 持有本地服务的令牌：彻底删除已删除项目在服务端拒绝它，恢复照常可用。"""
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")

    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 200
        [entry] = client.get(DELETED).json()["projects"]
        override(client, get_current_user, lambda: CurrentUserInfo(id="default", sub=EMBEDDED_AGENT_SUB, role="admin"))

        assert client.delete(f"{DELETED}/{quote(entry['id'], safe='')}").status_code == 403
        assert [p["id"] for p in client.get(DELETED).json()["projects"]] == [entry["id"]]


def test_deleted_projects_expire_after_the_retention_period(tmp_path):
    manager = ProjectManager(tmp_path)
    for name in ("old", "fresh"):
        manager.create_project(name)
    layout = manager.layout
    deleted_projects.move_in(layout, manager.get_project_path("old"), "old#deleted-20261001T000000Z")
    deleted_projects.move_in(layout, manager.get_project_path("fresh"), "fresh#deleted-20261020T000000Z")

    now = datetime(2026, 11, 1, tzinfo=UTC)
    assert deleted_projects.purge_expired(layout, now=now) == 1
    assert [entry.name for entry in deleted_projects.list_entries(layout)] == ["fresh"]
