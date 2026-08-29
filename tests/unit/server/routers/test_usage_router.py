import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from server.auth import CurrentUserInfo, get_current_user
from server.routers import usage
from tests.auth_deps import AUTH_DEPENDENCIES


@pytest.fixture
async def _usage_env(db_factory, monkeypatch):
    async with db_factory() as session:
        repo = UsageRepository(session)
        cid1 = await repo.start_call(project_name="demo", call_type="image", model="gemini-3.1-flash-image-preview")
        await repo.finish_call(cid1, status="success", settlement=SettlementInput())
        cid2 = await repo.start_call(project_name="demo", call_type="video", model="veo-3")
        await repo.finish_call(cid2, status="success", settlement=SettlementInput())
        cid3 = await repo.start_call(project_name="demo", call_type="video", model="veo-3")
        await repo.finish_call(cid3, status="success", settlement=SettlementInput())
        cid4 = await repo.start_call(project_name="demo2", call_type="image", model="gemini-3.1-flash-image-preview")
        await repo.finish_call(cid4, status="success", settlement=SettlementInput())
        cid5 = await repo.start_call(
            project_name="demo2", call_type="text", model="doubao-seed-2-0-pro-260215", provider="ark"
        )
        await repo.finish_call(cid5, status="success", settlement=SettlementInput())
        cid6 = await repo.start_call(
            project_name="demo2", call_type="text", model="some-model", provider="not-in-registry"
        )
        await repo.finish_call(cid6, status="success", settlement=SettlementInput())

    monkeypatch.setattr(usage, "async_session_factory", db_factory)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(usage.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

    return TestClient(app)


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
        assert calls.json()["total"] == 6

        projects = client.get("/api/v1/usage/projects")
        assert projects.status_code == 200
        assert set(projects.json()["projects"]) == {"demo", "demo2"}

    @pytest.mark.parametrize(
        ("accept_language", "expected"),
        [("zh", "火山方舟"), ("en", "Volcengine Ark"), ("vi", "Volcengine Ark")],
    )
    def test_grouped_provider_display_name_follows_locale(self, _usage_env, accept_language, expected):
        """按供应商分组的用量统计里，内置供应商名跟随请求语言。"""
        resp = _usage_env.get(
            "/api/v1/usage/stats?group_by=provider",
            headers={"accept-language": accept_language},
        )
        assert resp.status_code == 200
        names = {s["provider"]: s["display_name"] for s in resp.json()["stats"]}
        assert names["ark"] == expected
        # 译名表里没有的供应商（自定义供应商用用户自填的名字）保留仓储写入的原名。
        assert names["not-in-registry"] == "not-in-registry"
