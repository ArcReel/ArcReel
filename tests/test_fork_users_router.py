"""用户管理 router 测试 — fork 私有"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from server.auth import CurrentUserInfo, get_current_user
from server.routers import fork_users as users_router


@pytest.fixture
async def env(monkeypatch):
    """Per-test in-memory DB + admin override."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr("server.routers.fork_users.async_session_factory", factory)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="boot-admin", sub="admin", role="admin")
    app.include_router(users_router.router, prefix="/api/v1")

    yield {"client": TestClient(app), "factory": factory}
    await engine.dispose()


class TestUsersRouter:
    def test_list_empty(self, env):
        r = env["client"].get("/api/v1/users")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_and_list(self, env):
        r = env["client"].post("/api/v1/users", json={"username": "bob", "role": "user"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["username"] == "bob"
        assert body["role"] == "user"
        assert body["is_active"] is True

        r2 = env["client"].get("/api/v1/users")
        assert r2.status_code == 200
        names = [u["username"] for u in r2.json()]
        assert names == ["bob"]

    def test_create_rejects_reserved_name(self, env):
        r = env["client"].post("/api/v1/users", json={"username": "admin", "role": "user"})
        assert r.status_code == 400

    def test_create_rejects_uppercase(self, env):
        r = env["client"].post("/api/v1/users", json={"username": "Bob", "role": "user"})
        assert r.status_code == 400

    def test_create_rejects_double_underscore(self, env):
        r = env["client"].post("/api/v1/users", json={"username": "ali__ce", "role": "user"})
        assert r.status_code == 400

    def test_create_rejects_invalid_role(self, env):
        r = env["client"].post("/api/v1/users", json={"username": "bob", "role": "owner"})
        assert r.status_code == 400

    def test_duplicate_username_returns_409(self, env):
        env["client"].post("/api/v1/users", json={"username": "bob", "role": "user"})
        r = env["client"].post("/api/v1/users", json={"username": "bob", "role": "user"})
        assert r.status_code == 409

    def test_change_role(self, env):
        created = env["client"].post("/api/v1/users", json={"username": "carol", "role": "user"}).json()
        r = env["client"].patch(f"/api/v1/users/{created['id']}/role", json={"role": "admin"})
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_cannot_demote_self(self, env):
        # 创建一个 admin 行，再把 fake user 的 id 切换到它
        created = env["client"].post("/api/v1/users", json={"username": "rooty", "role": "admin"}).json()
        admin_id = created["id"]

        from server.fork_permissions import require_admin

        env["client"].app.dependency_overrides[require_admin] = lambda: CurrentUserInfo(
            id=admin_id, sub="rooty", role="admin"
        )
        r = env["client"].patch(f"/api/v1/users/{admin_id}/role", json={"role": "user"})
        assert r.status_code == 400

    def test_delete_user(self, env):
        created = env["client"].post("/api/v1/users", json={"username": "dora", "role": "user"}).json()
        r = env["client"].delete(f"/api/v1/users/{created['id']}")
        assert r.status_code == 204
        assert env["client"].get("/api/v1/users").json() == []
