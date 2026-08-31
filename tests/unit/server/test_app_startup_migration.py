"""FastAPI 启动跑完项目 schema 迁移与过期备份回收。"""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import lib.db
import server.app as app_module
from lib.project_migrations.runner import CURRENT_SCHEMA_VERSION
from server.routers import assistant as assistant_router


async def _noop_async(*args, **kwargs):
    """No-op coroutine for mocking async startup steps."""


class _FakeWorker:
    async def start(self):
        pass

    async def stop(self):
        pass

    def request_cancel(self, _task_id: str) -> bool:
        return False


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
    projects_root = tmp_path / "projects"
    project_dir, stale_backup = _seed_stale_project(projects_root)

    # 数据目录指向 tmp：迁移与备份回收都照真实跑，落点是本用例种下的项目。
    monkeypatch.setattr(app_module, "app_data_dir", lambda: projects_root)
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
