"""Vertex 凭证文件位置：经 ConfigResolver 的供应商配置取到的凭证文件能被加载。

位置由凭证 id 经数据根布局推导；推导位置没有文件时回退到凭证记录上的路径（存量凭证）。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import ConfigResolver
from lib.db import get_async_session
from lib.db.repositories.credential_repository import CredentialRepository
from lib.infra.app_data_dir import reset_for_tests
from lib.infra.data_root_layout import DataRootLayout
from server.routers import providers
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DataRootLayout:
    data_root = tmp_path / "data"
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(data_root))
    # 建库夹具可能已按默认数据根解析过一次。
    reset_for_tests()
    return DataRootLayout.current()


@pytest.fixture
async def bound_resolver(db_session: AsyncSession, db_factory: async_sessionmaker[AsyncSession]) -> ConfigResolver:
    return ConfigResolver(db_factory, _bound_session=db_session)


def _write_service_account(path: Path, project_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"project_id": project_id}), encoding="utf-8")


async def _active_vertex_credential(db_session: AsyncSession, *, credentials_path: str | None) -> int:
    repo = CredentialRepository(db_session)
    cred = await repo.create(provider="gemini-vertex", name="Vertex", credentials_path=credentials_path)
    await repo.activate(cred.id, "gemini-vertex")
    await db_session.flush()
    return cred.id


def _loaded_project_id(config: dict[str, str]) -> str:
    return json.loads(Path(config["credentials_path"]).read_text(encoding="utf-8"))["project_id"]


async def test_credential_file_is_found_by_id_even_when_recorded_path_is_stale(
    tmp_path: Path, layout: DataRootLayout, db_session: AsyncSession, bound_resolver: ConfigResolver
) -> None:
    cred_id = await _active_vertex_credential(
        db_session, credentials_path=str(tmp_path / "moved-away" / "vertex_keys" / "vertex_cred_1.json")
    )
    _write_service_account(layout.vertex_credential_path(cred_id), "by-id")

    config = await bound_resolver.provider_config("gemini-vertex")

    assert _loaded_project_id(config) == "by-id"


async def test_existing_credential_without_file_at_derived_location_uses_recorded_path(
    tmp_path: Path, layout: DataRootLayout, db_session: AsyncSession, bound_resolver: ConfigResolver
) -> None:
    recorded = tmp_path / "legacy" / "vertex_credentials.json"
    _write_service_account(recorded, "recorded")
    await _active_vertex_credential(db_session, credentials_path=str(recorded))

    config = await bound_resolver.provider_config("gemini-vertex")

    assert _loaded_project_id(config) == "recorded"


async def test_uploaded_credential_is_stored_in_data_root_and_loadable(
    layout: DataRootLayout, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    app = FastAPI()

    async def _session():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/providers/gemini-vertex/credentials/upload",
            files={"file": ("sa.json", json.dumps({"project_id": "uploaded"}), "application/json")},
        )
        assert resp.status_code == 201
        activated = await client.post(f"/api/v1/providers/gemini-vertex/credentials/{resp.json()['id']}/activate")
        assert activated.status_code == 204

    config = await ConfigResolver(db_factory).provider_config("gemini-vertex")

    assert _loaded_project_id(config) == "uploaded"
    assert Path(config["credentials_path"]).is_relative_to(layout.root)
