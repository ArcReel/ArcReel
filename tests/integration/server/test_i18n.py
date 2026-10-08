"""请求级显示名目录：路由依赖加载失败时退回内置名称，不改变路由自身的响应。"""

from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.db import get_async_session
from lib.i18n import _
from server.i18n import DisplayNamesCatalog


async def test_catalog_query_failure_falls_back_to_builtin_names(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/missing/db.sqlite")
    sessions = async_sessionmaker(engine)

    async def _unavailable_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[get_async_session] = _unavailable_session

    @app.get("/names")
    async def _names(names: DisplayNamesCatalog) -> dict[str, str]:
        return {"builtin": names.provider("gemini-aistudio"), "custom": names.provider("custom-1")}

    try:
        response = TestClient(app).get("/names", headers={"Accept-Language": "en"})

        assert response.status_code == 200
        assert response.json()["custom"] == _("deleted_provider_display_name", locale="en")
        assert response.json()["builtin"] != _("deleted_provider_display_name", locale="en")
    finally:
        await engine.dispose()
