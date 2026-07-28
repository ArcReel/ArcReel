import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from server.auth import CurrentUserInfo, get_current_user
from server.routers import usage


@pytest.fixture
async def _usage_env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repo = UsageRepository(session)
        cid1 = await repo.start_call(project_name="demo", call_type="image", model="gemini-3.1-flash-image-preview")
        await repo.finish_call(cid1, status="success", settlement=SettlementInput())
        cid2 = await repo.start_call(project_name="demo", call_type="video", model="veo-3")
        await repo.finish_call(cid2, status="success", settlement=SettlementInput())
        cid3 = await repo.start_call(project_name="demo", call_type="video", model="veo-3")
        await repo.finish_call(cid3, status="success", settlement=SettlementInput())
        cid4 = await repo.start_call(project_name="demo2", call_type="image", model="gemini-3.1-flash-image-preview")
        await repo.finish_call(cid4, status="success", settlement=SettlementInput())

    monkeypatch.setattr(usage, "async_session_factory", factory)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(usage.router, prefix="/api/v1")

    yield TestClient(app)
    await engine.dispose()


class TestUsageRouter:
    def test_usage_endpoints(self, _usage_env):
        client = _usage_env
        stats = client.get("/api/v1/usage/stats?project_name=demo")
        assert stats.status_code == 200
        assert stats.json()["total_count"] == 3

        calls = client.get("/api/v1/usage/calls?page=1&page_size=10")
        assert calls.status_code == 200
        assert calls.json()["page"] == 1
        assert calls.json()["page_size"] == 10
        assert calls.json()["total"] == 4

        projects = client.get("/api/v1/usage/projects")
        assert projects.status_code == 200
        assert set(projects.json()["projects"]) == {"demo", "demo2"}
