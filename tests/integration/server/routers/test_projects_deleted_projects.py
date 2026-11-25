"""删除的项目成为已删除项目：整体恢复时记录改回项目名，原名被占用时换名；彻底删除后不可恢复。"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.repositories.task_repo import TaskRepository
from lib.db.repositories.usage_repo import UsageFilters, UsageRepository
from lib.project import deleted_projects
from lib.project.project_manager import ProjectManager
from server.auth import EMBEDDED_AGENT_SUB, CurrentUserInfo, get_current_user
from server.routers import projects
from server.services.project import project_retirement
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
        assert (await tasks.get(old["queued"]) or {})["project_name"] == "demo-2"
        assert (await tasks.get(old["queued"]) or {})["status"] == "cancelled"
        assert (await tasks.list_tasks(project_name="demo"))["total"] == 0
        [row] = await UsageRepository(session).fetch_summary_rows(filters=UsageFilters(project_name="demo-2"))
        assert row.cost_amount == 1.5


async def test_restore_uses_a_suffix_when_create_claims_the_name_mid_restore(tmp_path, monkeypatch, file_db_factory):
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")
    original_dir = manager.get_project_path("demo")
    (original_dir / "source" / "chapter.txt").write_text("old text", encoding="utf-8")
    old = await seed_project_records(file_db_factory)

    with build_projects_client(monkeypatch, manager, session_factory=file_db_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 200
        [entry] = client.get(DELETED).json()["projects"]
        restore_url = f"{DELETED}/{quote(entry['id'], safe='')}/restore"
        checked, claimed = threading.Event(), threading.Event()
        real_exists, real_mkdir = Path.exists, Path.mkdir

        def wait_for_create_after_free_name_check(path):
            exists = real_exists(path)
            if path == original_dir and not exists and not checked.is_set():
                checked.set()
                assert claimed.wait(10)
            return exists

        def observe_create_claim(path, *args, **kwargs):
            if path.parent == original_dir and not claimed.is_set():
                claimed.set()
            real_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "exists", wait_for_create_after_free_name_check)
        monkeypatch.setattr(Path, "mkdir", observe_create_claim)
        with ThreadPoolExecutor(max_workers=2) as requests:
            restore = requests.submit(client.post, restore_url)
            assert checked.wait(10)
            create = requests.submit(
                client.post,
                "/api/v1/projects",
                json={"name": "demo", "title": "New", "generation_mode": "storyboard"},
            )
            assert claimed.wait(10)
            restored = restore.result(timeout=10)
            created = create.result(timeout=10)

        assert created.status_code == 200, created.text
        assert restored.status_code == 200, restored.text
        assert created.json()["name"] == "demo"
        assert restored.json() == {"name": "demo-2"}
        assert client.get(DELETED).json()["projects"] == []

    assert manager.load_project("demo")["title"] == "New"
    assert manager.load_project("demo-2")["title"] == "Old"
    assert not (original_dir / "source" / "chapter.txt").exists()
    assert (manager.get_project_path("demo-2") / "source" / "chapter.txt").read_text(encoding="utf-8") == "old text"
    async with file_db_factory() as session:
        tasks = TaskRepository(session)
        assert (await tasks.list_tasks(project_name="demo"))["total"] == 0
        assert (await tasks.get(old["queued"]) or {})["project_name"] == "demo-2"
        assert (await tasks.get(old["running"]) or {})["project_name"] == "demo-2"
        usage = UsageRepository(session)
        assert await usage.fetch_summary_rows(filters=UsageFilters(project_name="demo")) == []
        [row] = await usage.fetch_summary_rows(filters=UsageFilters(project_name="demo-2"))
        assert row.cost_amount == 1.5


async def test_create_reports_name_conflict_when_restore_claims_the_name_first(tmp_path, monkeypatch, file_db_factory):
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")
    original_dir = manager.get_project_path("demo")
    (original_dir / "source" / "chapter.txt").write_text("old text", encoding="utf-8")
    old = await seed_project_records(file_db_factory)

    pause_session = threading.Event()
    restore_session_opening, finish_restore = threading.Event(), threading.Event()

    class PausedRestoreSession(AsyncSession):
        async def __aenter__(self):
            if pause_session.is_set() and not restore_session_opening.is_set():
                restore_session_opening.set()
                assert await asyncio.to_thread(finish_restore.wait, 10)
            return await super().__aenter__()

    session_factory = async_sessionmaker[AsyncSession](
        file_db_factory.kw["bind"], class_=PausedRestoreSession, expire_on_commit=False
    )
    with build_projects_client(monkeypatch, manager, session_factory=session_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 200
        [entry] = client.get(DELETED).json()["projects"]
        restore_url = f"{DELETED}/{quote(entry['id'], safe='')}/restore"
        with ThreadPoolExecutor(max_workers=1) as requests:
            pause_session.set()
            restore = requests.submit(client.post, restore_url)
            try:
                assert restore_session_opening.wait(10)
                assert original_dir.is_dir()
                created = client.post(
                    "/api/v1/projects", json={"name": "demo", "title": "New", "generation_mode": "storyboard"}
                )
                assert created.status_code == 400, created.text
                assert created.json()["detail"] == "项目 'demo' 已存在"
            finally:
                finish_restore.set()
            restored = restore.result(timeout=10)

        assert restored.status_code == 200, restored.text
        assert restored.json() == {"name": "demo"}
        assert client.get(DELETED).json()["projects"] == []

    assert manager.list_projects() == ["demo"]
    assert manager.load_project("demo")["title"] == "Old"
    assert (original_dir / "source" / "chapter.txt").read_text(encoding="utf-8") == "old text"
    async with file_db_factory() as session:
        assert (await TaskRepository(session).get(old["running"]) or {})["project_name"] == "demo"


def _files_size(root) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and not path.is_symlink())


async def test_deleted_projects_report_the_space_they_take(tmp_path, monkeypatch, db_factory):
    """列表给出每个已删除项目目录占用的字节数，包括里面的子目录。"""
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")
    project_dir = manager.get_project_path("demo")
    (project_dir / "source" / "chapter.txt").write_bytes(b"x" * 5000)
    expected = _files_size(project_dir)

    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 200
        [entry] = client.get(DELETED).json()["projects"]

    assert entry["size"] == expected
    assert entry["size"] >= 5000


async def test_deleted_project_sizes_are_counted_off_the_event_loop(tmp_path, monkeypatch, db_factory):
    """统计目录大小是阻塞的磁盘遍历，不在事件循环线程上执行。"""
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")
    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 200

    loop_thread = threading.get_ident()
    counted_on: list[int] = []
    real_tree_size = deleted_projects.tree_size

    def recording_tree_size(root):
        counted_on.append(threading.get_ident())
        return real_tree_size(root)

    monkeypatch.setattr(deleted_projects, "tree_size", recording_tree_size)
    listing = await projects.list_deleted_projects()

    assert listing["projects"][0]["size"] > 0
    assert counted_on
    assert loop_thread not in counted_on


async def test_restore_cancelled_while_moving_out_still_finishes(tmp_path, monkeypatch, db_factory):
    """目录移出途中请求被取消：恢复照常做完，记录改回、迁移照跑，不会只留下移回的目录。"""
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")
    old = await seed_project_records(db_factory)
    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 200
        [entry] = client.get(DELETED).json()["projects"]

    moving, release = threading.Event(), threading.Event()
    real_move_out, real_migrate = deleted_projects.move_out, deleted_projects.migrate_restored
    migrated: list[str] = []

    def slow_move_out(layout, entry_id):
        moving.set()
        release.wait(5)
        return real_move_out(layout, entry_id)

    def recording_migrate(project_dir, **kwargs):
        migrated.append(project_dir.name)
        real_migrate(project_dir, **kwargs)

    monkeypatch.setattr(deleted_projects, "move_out", slow_move_out)
    monkeypatch.setattr(deleted_projects, "migrate_restored", recording_migrate)
    restore = asyncio.create_task(projects.restore_deleted_project(entry["id"]))
    assert await asyncio.to_thread(moving.wait, 5)
    restore.cancel()
    release.set()

    assert await restore == {"name": "demo"}
    assert migrated == ["demo"]
    assert manager.load_project("demo")["title"] == "Old"
    assert deleted_projects.list_entries(manager.layout) == []
    async with db_factory() as session:
        assert (await TaskRepository(session).get(old["running"]) or {})["project_name"] == "demo"


async def test_project_stays_in_place_when_its_records_cannot_be_retired(tmp_path, monkeypatch, db_factory):
    """目录已移走而记录提交失败时，目录移回原处，记录仍挂在原项目名下。"""
    manager = ProjectManager(tmp_path)
    _create(manager, "Old")
    old = await seed_project_records(db_factory)

    async def failing_commit(awaitable):
        awaitable.close()
        raise OSError("database disk is full")

    monkeypatch.setattr(project_retirement, "run_noninterruptible_async", failing_commit)
    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        assert client.delete("/api/v1/projects/demo").status_code == 500

    assert manager.load_project("demo")["title"] == "Old"
    assert deleted_projects.list_entries(manager.layout) == []
    async with db_factory() as session:
        assert (await TaskRepository(session).get(old["queued"]) or {})["project_name"] == "demo"


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
    assert deleted_projects.purge_expired(layout, now=now) == ["old#deleted-20261001T000000Z"]
    assert [entry.name for entry in deleted_projects.list_entries(layout)] == ["fresh"]
