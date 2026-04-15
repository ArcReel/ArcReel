"""assets 路由基础 CRUD 测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.routers import assets


@pytest.fixture
async def _assets_env(tmp_path, monkeypatch):
    # 1) per-test in-memory DB
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # 2) per-test ProjectManager pointed at tmp_path/projects
    pm = ProjectManager(tmp_path / "projects")

    # 3) monkeypatch symbols used inside assets router
    monkeypatch.setattr(assets, "async_session_factory", factory)
    monkeypatch.setattr(assets, "pm", pm)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(assets.router, prefix="/api/v1")

    yield {"client": TestClient(app), "pm": pm}
    await engine.dispose()


class TestAssetsCRUD:
    def test_create_and_list(self, _assets_env):
        client = _assets_env["client"]
        r = client.post(
            "/api/v1/assets",
            data={"type": "character", "name": "王小明", "description": "白衣少年"},
        )
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset"]["id"]
        assert asset_id

        r2 = client.get("/api/v1/assets?type=character")
        assert r2.status_code == 200
        assert len(r2.json()["items"]) == 1
        assert r2.json()["items"][0]["id"] == asset_id

    def test_duplicate_type_name_returns_409(self, _assets_env):
        client = _assets_env["client"]
        r1 = client.post("/api/v1/assets", data={"type": "prop", "name": "玉佩"})
        assert r1.status_code == 200, r1.text
        r = client.post("/api/v1/assets", data={"type": "prop", "name": "玉佩"})
        assert r.status_code == 409

    def test_patch_and_delete(self, _assets_env):
        client = _assets_env["client"]
        r = client.post("/api/v1/assets", data={"type": "scene", "name": "A"})
        assert r.status_code == 200, r.text
        aid = r.json()["asset"]["id"]

        r2 = client.patch(f"/api/v1/assets/{aid}", json={"description": "new"})
        assert r2.status_code == 200
        assert r2.json()["asset"]["description"] == "new"

        r3 = client.delete(f"/api/v1/assets/{aid}")
        assert r3.status_code == 204

        r4 = client.get(f"/api/v1/assets/{aid}")
        assert r4.status_code == 404

    def test_invalid_type_returns_400(self, _assets_env):
        client = _assets_env["client"]
        r = client.post("/api/v1/assets", data={"type": "invalid", "name": "X"})
        assert r.status_code == 400

    def test_list_filters_by_q(self, _assets_env):
        client = _assets_env["client"]
        client.post("/api/v1/assets", data={"type": "character", "name": "王小明"})
        client.post("/api/v1/assets", data={"type": "character", "name": "李小红"})
        r = client.get("/api/v1/assets?type=character&q=小明")
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1

    def test_create_conflict_does_not_leave_orphan_file(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        img_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

        # First create: succeeds
        r1 = client.post(
            "/api/v1/assets",
            data={"type": "prop", "name": "玉佩"},
            files={"image": ("a.png", img_bytes, "image/png")},
        )
        assert r1.status_code == 200

        global_dir = pm.get_global_assets_root() / "prop"
        files_after_first = list(global_dir.iterdir())

        # Duplicate create with image: must 409 AND not increase file count
        r2 = client.post(
            "/api/v1/assets",
            data={"type": "prop", "name": "玉佩"},
            files={"image": ("b.png", img_bytes, "image/png")},
        )
        assert r2.status_code == 409
        files_after_dup = list(global_dir.iterdir())
        assert len(files_after_dup) == len(files_after_first), "duplicate upload must not leave orphan files"
