"""资产库 owner 隔离回归测试（fork-private）。

这些用例对应 [server/fork_assets_visibility.py](../server/fork_assets_visibility.py)
中的可见性策略，与上游 [tests/test_assets_router.py](test_assets_router.py)
解耦。fixture 与上游测试同形以便对照阅读，但完全独立。
"""

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
async def _env(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    pm = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(assets, "async_session_factory", factory)
    monkeypatch.setattr(assets, "pm", pm)

    app = FastAPI()
    app.include_router(assets.router, prefix="/api/v1")
    # 默认 admin，单条用例内通过 _as 切换身份
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="admin", sub="admin", role="admin")

    yield {"app": app, "pm": pm}
    await engine.dispose()


def _as(app: FastAPI, *, sub: str, role: str = "user") -> TestClient:
    """构造按指定身份发请求的 TestClient。dependency_overrides 是 dict，
    后写覆盖前写，因此每次调用前重置。"""
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id=sub, sub=sub, role=role)
    return TestClient(app)


class TestAssetVisibility:
    def test_user_cannot_list_or_read_other_users_assets(self, _env):
        app = _env["app"]
        alice = _as(app, sub="alice")
        created = alice.post(
            "/api/v1/assets",
            data={"type": "character", "name": "王小明", "description": "alice asset"},
        )
        assert created.status_code == 200, created.text
        asset_id = created.json()["asset"]["id"]

        bob = _as(app, sub="bob")
        assert bob.get("/api/v1/assets?type=character").json()["items"] == []
        assert bob.get(f"/api/v1/assets/{asset_id}").status_code == 404
        assert bob.patch(f"/api/v1/assets/{asset_id}", json={"description": "hijack"}).status_code == 404
        assert bob.delete(f"/api/v1/assets/{asset_id}").status_code == 204  # 静默 no-op
        # alice 数据未被影响（重新切回 alice 身份：override 是共享 dict）
        alice = _as(app, sub="alice")
        still = alice.get(f"/api/v1/assets/{asset_id}")
        assert still.status_code == 200
        assert still.json()["asset"]["description"] == "alice asset"

    def test_admin_sees_all_users_assets(self, _env):
        app = _env["app"]
        _as(app, sub="alice").post("/api/v1/assets", data={"type": "prop", "name": "A"})
        _as(app, sub="bob").post("/api/v1/assets", data={"type": "prop", "name": "B"})
        admin = _as(app, sub="root", role="admin")
        items = admin.get("/api/v1/assets?type=prop").json()["items"]
        assert {i["name"] for i in items} == {"A", "B"}

    def test_duplicate_type_name_across_users_still_409(self, _env):
        # 不动 schema 的妥协：(type, name) 仍然全局唯一。
        # 错误信息只回 "已占用"，不携带其他用户资产数据。
        app = _env["app"]
        assert _as(app, sub="alice").post("/api/v1/assets", data={"type": "prop", "name": "玉佩"}).status_code == 200
        r = _as(app, sub="bob").post("/api/v1/assets", data={"type": "prop", "name": "玉佩"})
        assert r.status_code == 409

    def test_library_sentinel_hidden_in_source_project(self, _env):
        """直接上传到库的资产，对外返回的 source_project 不暴露 @library 哨兵。"""
        app = _env["app"]
        alice = _as(app, sub="alice")
        r = alice.post("/api/v1/assets", data={"type": "scene", "name": "X"})
        assert r.status_code == 200
        assert r.json()["asset"]["source_project"] is None


class TestProjectAccess:
    def test_user_cannot_import_asset_from_other_users_project(self, _env):
        app, pm = _env["app"], _env["pm"]
        pm.create_project("bob__demo")
        pm.create_project_metadata("bob__demo", "Demo")
        pm.add_project_character("bob__demo", "王", "d", "")

        r = _as(app, sub="alice").post(
            "/api/v1/assets/from-project",
            json={"project_name": "bob__demo", "resource_type": "character", "resource_id": "王"},
        )
        assert r.status_code == 403

    def test_user_cannot_apply_assets_to_other_users_project(self, _env):
        app, pm = _env["app"], _env["pm"]
        pm.create_project("bob__target")
        pm.create_project_metadata("bob__target", "Target")

        alice = _as(app, sub="alice")
        created = alice.post("/api/v1/assets", data={"type": "scene", "name": "A"})
        assert created.status_code == 200, created.text
        asset_id = created.json()["asset"]["id"]

        r = alice.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [asset_id],
                "target_project": "bob__target",
                "conflict_policy": "skip",
            },
        )
        assert r.status_code == 403
