"""FastAPI 启动：数据根布局迁移先于一切遍历项目的步骤，随后跑完项目 schema 迁移与过期备份回收。"""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import lib.db
import server.app as app_module
from lib.infra.data_root_layout import DataRootLayout
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import CURRENT_SCHEMA_VERSION
from server.routers import assistant as assistant_router


async def _noop_async(*args, **kwargs):
    """No-op coroutine for mocking async startup steps."""


class _FakeWorker:
    async def start(self):
        pass

    async def stop(self):
        pass


def _seed_stale_project(projects_root: Path) -> tuple[Path, Path]:
    """种一个落后版本的项目，外加一份 8 天前的旧版备份。"""
    project_dir = projects_root / "p1"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps({"schema_version": 1, "name": "p1", "video_backend": "seedance/x", "image_backend": "vertex/y"}),
        encoding="utf-8",
    )
    stale_backup = project_dir / "project.json.bak.v1-100000000"
    stale_backup.write_text("recovery", encoding="utf-8")
    expired = time.time() - 8 * 86400
    os.utime(stale_backup, (expired, expired))
    return project_dir, stale_backup


@pytest.mark.asyncio
async def test_startup_migrates_projects_and_reaps_stale_backups(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    project_dir, stale_backup = _seed_stale_project(DataRootLayout(data_root).projects_dir)

    # 数据目录指向 tmp：迁移与备份回收都照真实跑，落点是本用例种下的项目。
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(data_root))
    monkeypatch.setattr("lib.project.project_manager.get_project_manager", lambda: ProjectManager(data_root))
    monkeypatch.setattr(app_module, "ensure_auth_password", lambda: "test")
    monkeypatch.setattr(app_module, "init_db", _noop_async)
    monkeypatch.setattr(lib.db, "init_db", _noop_async)
    monkeypatch.setattr(app_module, "create_generation_worker", _FakeWorker)
    monkeypatch.setattr(assistant_router.assistant_service, "startup", _noop_async)
    monkeypatch.setattr(assistant_router.assistant_service, "shutdown", _noop_async)
    monkeypatch.setattr(app_module, "migrate_local_transcripts_to_store", _noop_async)

    app = app_module.app
    app.state = SimpleNamespace()

    async with app_module.lifespan(app):
        pass

    migrated = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert not stale_backup.exists()


@pytest.mark.asyncio
async def test_data_root_layout_migration_runs_before_every_project_walk(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    staged_project, stale_backup = _seed_stale_project(tmp_path / "staged")
    (staged_project / "source").mkdir()
    source_bytes = "春天来了".encode("gbk")
    (staged_project / "source" / "novel.txt").write_bytes(source_bytes)
    project_dir = DataRootLayout(data_root).projects_dir / staged_project.name
    session_imported = False

    async def migrate_layout(root: Path, **_kwargs) -> None:
        assert root == data_root.resolve()
        project_dir.parent.mkdir(parents=True, exist_ok=True)
        staged_project.rename(project_dir)

    async def import_transcripts(_store, **_kwargs) -> None:
        nonlocal session_imported
        assert project_dir.is_dir()
        session_imported = True

    monkeypatch.setenv("ARCREEL_DATA_DIR", str(data_root))
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE", "db")
    monkeypatch.setattr(app_module, "migrate_data_root_layout", migrate_layout)
    monkeypatch.setattr(app_module, "migrate_local_transcripts_to_store", import_transcripts)
    monkeypatch.setattr("lib.project.project_manager.get_project_manager", lambda: ProjectManager(data_root))
    monkeypatch.setattr(app_module, "ensure_auth_password", lambda: "test")
    monkeypatch.setattr(app_module, "init_db", _noop_async)
    monkeypatch.setattr(lib.db, "init_db", _noop_async)
    monkeypatch.setattr(app_module, "create_generation_worker", _FakeWorker)
    monkeypatch.setattr(assistant_router.assistant_service, "startup", _noop_async)
    monkeypatch.setattr(assistant_router.assistant_service, "shutdown", _noop_async)

    app = app_module.app
    app.state = SimpleNamespace()

    async with app_module.lifespan(app):
        pass

    assert session_imported
    migrated_source = (project_dir / "source" / "novel.txt").read_bytes()
    migrated_source.decode("utf-8")
    assert migrated_source != source_bytes
    assert (project_dir / "source" / "raw" / "novel.txt").read_bytes() == source_bytes
    assert (
        json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["schema_version"]
        == CURRENT_SCHEMA_VERSION
    )
    assert not (project_dir / stale_backup.name).exists()
    assert (project_dir / "CLAUDE.md").is_file()
